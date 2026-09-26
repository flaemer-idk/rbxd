# Image CDN / thumbnails
#
# Портировано из RFD-N (abricoshka/RFD-N) и переработано под rbxd:
#   - выкинут util.auth (всё открыто, как и остальные эндпойнты rbxd);
#   - выкинуты мёртвые для rbxd storage-модули RFD-N (universe/place/placeicon/
#     userthumbnail/user — каталог игр у нас не ведётся, БД-юзеров нет):
#       * place icon     -> берётся из AssetCache плейса (icon_uri), иначе placeholder;
#       * user thumbnail -> всегда placeholder (см. IDEAS.md: рендер скинов в headshot);
#       * avatar-3d      -> выкинут (был хардкод t3.rbxcdn.com);
#   - разрешение username -> user_id идёт через родную таблицу players rbxd.
#
# Что осталось от RFD-N без изменений: sha256/512 кэш картинок на диске,
# ресайз через PIL (с fallback на оригинал, если PIL не установлен),
# batch-API, безопасный guess content-type, плейсхолдеры.

import gzip
import hashlib
import io
import json
import os
import random
import re
import ssl
import urllib.request
from datetime import UTC, datetime
from typing import Any

import assets.const
import assets.returns as returns
import util.const
from web_server._logic import server_path, web_server_handler


DEFAULT_IMAGE_SIZES = [36, 48, 50, 60, 75, 100, 128, 150, 180, 200, 256, 324, 352, 396, 420, 480, 500, 512, 576, 640, 700, 720, 768, 1280]
SQUARE_IMAGE_SIZES = [36, 48, 50, 60, 75, 100, 128, 150, 180, 200, 256, 352, 396, 420, 480, 500, 512, 576, 640, 700, 720, 768, 1280]
GAME_ICON_SIZES = [50, 128, 150, 256, 420, 512]
PLACE_ICON_SIZES = [48, 60, 100, 128, 150, 180, 256, 324, 352, 420, 512, 576]
BATCH_ALLOWED_TYPES = {"Avatar", "AvatarHeadShot", "GameIcon", "GameThumbnail", "Asset", "GroupIcon"}
MAX_BATCH_REQUESTS = 15
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
IMAGE_CACHE_DIR_NAME = "ImageCache"

GAME_ICON_PLACEHOLDER_REL_PATH = "img/placeholder/icon_one.png"
GAME_BANNER_PLACEHOLDER_REL_PATH = "img/placeholder/icon_two.png"
# Физически лежит в web_server/static/ContentDeleted.png — это не плейсхолдер
# юзера, а отдельная заглушка «контент удалён», поэтому и живёт отдельно.
CONTENT_DELETED_PLACEHOLDER_REL_PATH = "ContentDeleted.png"

# Плейсхолдеры юзеров — это папки с произвольным набором картинок:
#   img/placeholder/avatar_placeholder/<любое имя>.png
#   img/placeholder/headshot_placeholder/<любое имя>.png
# Имена файлов могут быть любыми (image.png, anapa2007.png, …) — при каждом
# запросе из папки случайно выбирается одна картинка. Содержимое папки
# читается с диска заново на каждый запрос, поэтому картинки можно
# добавлять, удалять и заменять на лету — без рестарта сервера.
USER_AVATAR_PLACEHOLDER_DIR = "img/placeholder/avatar_placeholder"
USER_HEADSHOT_PLACEHOLDER_DIR = "img/placeholder/headshot_placeholder"
USER_PLACEHOLDER_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
)

# Плейсхолдеры никогда не кэшируются (ни в памяти процесса, ни на клиенте):
# их могут удалить/переместить/заменить в любой момент, и ответ должен
# отражать текущее состояние диска, а не то, что было прочитано первым.
PLACEHOLDER_CACHE_CONTROL = "no-store"


def _get_header(self: web_server_handler, name: str) -> str | None:
    return (
        self.headers.get(name)
        or self.headers.get(name.lower())
        or self.headers.get(name.upper())
    )


def _guess_image_content_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    return "application/octet-stream"


def _is_image_data(data: bytes) -> bool:
    return _guess_image_content_type(data).startswith("image/")


def _static_root() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


def _static_file_path(relative_path: str) -> str:
    return os.path.join(_static_root(), *relative_path.replace("\\", "/").split("/"))


def _resolve_user_placeholder_rel_path(headshot: bool) -> str | None:
    '''
    Случайный плейсхолдер юзера: любой файл-картинка из папки
    avatar_placeholder/ или headshot_placeholder/. Выдаётся случайно при
    каждом вызове, без привязки к userId — перезаход даёт новую картинку.

    Папка сканируется заново при каждом вызове (никакого кэша!), поэтому
    файлы можно добавлять, удалять и переименовывать на лету.

    Возвращает None, если папки нет или в ней нет ни одной картинки — тогда
    caller откатывается на ContentDeleted.png, а если и его нет — на 404.
    '''
    directory_rel = (
        USER_HEADSHOT_PLACEHOLDER_DIR if headshot
        else USER_AVATAR_PLACEHOLDER_DIR
    )
    directory_path = _static_file_path(directory_rel)
    if not os.path.isdir(directory_path):
        return None
    try:
        entries = sorted(os.listdir(directory_path))
    except OSError:
        return None
    available = [
        f"{directory_rel}/{name}"
        for name in entries
        if name.lower().endswith(USER_PLACEHOLDER_EXTENSIONS)
        and os.path.isfile(os.path.join(directory_path, name))
    ]
    if not available:
        return None
    return random.choice(available)


def _send_user_placeholder(
    self: web_server_handler,
    headshot: bool,
    target_width: int | None = None,
    target_height: int | None = None,
    *,
    cache_control: str = PLACEHOLDER_CACHE_CONTROL,
) -> bool:
    '''
    Отдаёт случайный плейсхолдер юзера. Цепочка fallback:
      1. любая картинка из avatar_placeholder/ / headshot_placeholder/ (рандом)
      2. ContentDeleted.png
      3. 404 — главное, чтобы сервер не упал.
    '''
    rel_path = _resolve_user_placeholder_rel_path(headshot)
    if rel_path is None and os.path.isfile(
        _static_file_path(CONTENT_DELETED_PLACEHOLDER_REL_PATH)
    ):
        rel_path = CONTENT_DELETED_PLACEHOLDER_REL_PATH
    if rel_path is None:
        # Нет вообще ничего — клиент покажет пустую иконку, но сервер
        # продолжит работать.
        self.send_error(404)
        return True
    return _send_placeholder_image(
        self,
        rel_path,
        target_width,
        target_height,
        cache_control=cache_control,
    )


def _read_static_file(relative_path: str) -> bytes | None:
    # Намеренно БЕЗ functools.cache: кэш на процесс пережил бы удаление и
    # перемещение файлов (именно так получалось «удалил плейсхолдеры, а они
    # всё равно отдаются»). Читаем с диска при каждом обращении — плейсхолдеры
    # маленькие, а сервер локальный, так что это дёшево. Если файла нет
    # (например, его стёрли между isfile и open) — возвращаем None, чтобы
    # caller откатился на fallback, а не ронял запрос.
    try:
        with open(_static_file_path(relative_path), "rb") as file_obj:
            return file_obj.read()
    except OSError:
        return None


def _build_static_url(self: web_server_handler, relative_path: str) -> str:
    return f"{self.hostname}/static/{relative_path.replace(os.sep, '/')}"


def _image_cache_root(self: web_server_handler) -> str:
    asset_cache_dir = os.path.abspath(self.game_config.asset_cache.dir_path)
    return os.path.join(os.path.dirname(asset_cache_dir), IMAGE_CACHE_DIR_NAME)


def _cache_file_path(self: web_server_handler, bucket: str, key: str) -> str:
    key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return os.path.join(
        _image_cache_root(self),
        bucket,
        key_digest[:2],
        key_digest[2:4],
        key_digest,
    )


def _read_cached_image(self: web_server_handler, bucket: str, key: str) -> bytes | None:
    path = _cache_file_path(self, bucket, key)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as file_obj:
        return file_obj.read()


def _write_cached_image(
    self: web_server_handler,
    bucket: str,
    key: str,
    data: bytes,
) -> None:
    path = _cache_file_path(self, bucket, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as file_obj:
        file_obj.write(data)


def _download_binary(url: str) -> bytes | None:
    ssl_context = None
    if url.startswith("https://"):
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(url, timeout=10, context=ssl_context) as response:
        return response.read()


def _load_asset_bytes(
    self: web_server_handler,
    asset_key: int | str,
) -> bytes | None:
    # Превью ассета лежит в AssetCache под ключом `rbxthmb-<id>.png`
    # (префикс THUMB_PREFIX), сам ассет — под голым id. Пробуем оба.
    candidate_keys: list[int | str] = [asset_key]
    if isinstance(asset_key, int):
        candidate_keys.append(f"{assets.const.THUMB_PREFIX}{asset_key}.png")

    for key in candidate_keys:
        asset = self.game_config.asset_cache.get_asset(
            key,
            bypass_blocklist=self.is_privileged,
        )
        if isinstance(asset, returns.ret_data):
            return asset.data
        if isinstance(asset, returns.ret_relocate):
            try:
                return _download_binary(asset.url)
            except Exception:
                continue
    return None


def _load_original_image(
    self: web_server_handler,
    content_hash: str,
) -> bytes | None:
    cached_image = _read_cached_image(self, "originals", content_hash)
    if cached_image is not None:
        if not _is_image_data(cached_image):
            return None
        return cached_image
    # Иконка плейса: её хэш — sha256 от байтов из AssetCache (см.
    # _get_place_icon_hash). Пробуем достать напрямую из кэша плейса.
    place_icon = _load_place_icon_bytes(self, content_hash)
    if place_icon is not None and _is_image_data(place_icon):
        _write_cached_image(self, "originals", content_hash, place_icon)
        return place_icon

    asset_bytes = _load_asset_bytes(self, content_hash)
    if asset_bytes is None:
        return None
    if not _is_image_data(asset_bytes):
        return None

    _write_cached_image(self, "originals", content_hash, asset_bytes)
    return asset_bytes


def _load_place_icon_bytes(
    self: web_server_handler,
    content_hash: str,
) -> bytes | None:
    '''
    Достаёт иконку плейса из AssetCache, если её sha256 совпадает с
    запрошенным content_hash. Иначе None (отдадится placeholder).
    '''
    asset_cache = self.game_config.asset_cache
    thumbnail_data = asset_cache.get_asset(util.const.THUMBNAIL_ID_CONST)
    if not isinstance(thumbnail_data, returns.ret_data):
        return None
    if hashlib.sha256(thumbnail_data.data).hexdigest() != content_hash:
        return None
    return thumbnail_data.data


def _resize_image_bytes(
    image_bytes: bytes,
    target_width: int,
    target_height: int,
) -> tuple[bytes, str, bool]:
    content_type = _guess_image_content_type(image_bytes)
    try:
        from PIL import Image  # pyright: ignore[reportMissingImports]
    except ImportError:
        return image_bytes, content_type, False

    try:
        image_obj = Image.open(io.BytesIO(image_bytes))
        resampling_attr = getattr(Image, "Resampling", Image)
        resampling = getattr(resampling_attr, "LANCZOS", getattr(Image, "LANCZOS", 1))
        image_obj = image_obj.convert("RGBA")
        image_obj = image_obj.resize((int(target_width), int(target_height)), resampling)

        output = io.BytesIO()
        image_obj.save(output, "PNG")
        return output.getvalue(), "image/png", True
    except Exception:
        return image_bytes, content_type, False


def _build_variant_hash(content_hash: str, target_width: int, target_height: int) -> str:
    return hashlib.sha512(
        f"{content_hash}-{target_width}-{target_height}-v3".encode("utf-8"),
    ).hexdigest()


def _load_variant_image(
    self: web_server_handler,
    image_content_hash: str,
    target_width: int,
    target_height: int,
    cropped_hash: str,
    skip_cache_cropped_image: bool = False,
) -> tuple[bytes, str] | None:
    if not skip_cache_cropped_image:
        cached_variant = _read_cached_image(self, "variants", cropped_hash)
        if cached_variant is not None:
            return cached_variant, _guess_image_content_type(cached_variant)

    original_image = _load_original_image(self, image_content_hash)
    if original_image is None:
        return None

    resized_image, content_type, resized = _resize_image_bytes(
        original_image,
        target_width,
        target_height,
    )
    if resized and not skip_cache_cropped_image:
        _write_cached_image(self, "variants", cropped_hash, resized_image)
    return resized_image, content_type


def _send_image_response(
    self: web_server_handler,
    image_bytes: bytes,
    content_type: str,
    cache_control: str = "max-age=120",
) -> None:
    self.send_response(200)
    self.send_header("Cache-Control", cache_control)
    self.send_data(
        image_bytes,
        status=None,
        headers={"Content-Type": content_type},
    )


def _send_placeholder_image(
    self: web_server_handler,
    relative_path: str,
    target_width: int | None = None,
    target_height: int | None = None,
    *,
    cache_control: str = PLACEHOLDER_CACHE_CONTROL,
) -> bool:
    placeholder_bytes = _read_static_file(relative_path)
    if placeholder_bytes is None:
        # Файл удалили/переместили, пока его выбирали. Падать нельзя —
        # отдаём 404, клиент покажет пустую иконку.
        self.send_error(404)
        return True
    content_type = _guess_image_content_type(placeholder_bytes)
    if target_width is not None and target_height is not None:
        placeholder_bytes, content_type, _resized = _resize_image_bytes(
            placeholder_bytes,
            target_width,
            target_height,
        )

    _send_image_response(
        self,
        placeholder_bytes,
        content_type,
        cache_control=cache_control,
    )
    return True


def handle_resolution_check(
    self: web_server_handler,
    width_parameters_name: list[str] = ["width", "x"],
    height_parameters_name: list[str] = ["height", "y"],
    allowed_widths: list[int] = SQUARE_IMAGE_SIZES,
    allowed_heights: list[int] = SQUARE_IMAGE_SIZES,
    must_be_square: bool = True,
    can_round_to_nearest: bool = True,
) -> tuple[int, int] | None:
    width: int | None = None
    height: int | None = None

    for width_parameter_name in width_parameters_name:
        if width_parameter_name not in self.query:
            continue
        try:
            width = int(self.query[width_parameter_name])
        except ValueError:
            self.send_error(400)
            return None
        break

    for height_parameter_name in height_parameters_name:
        if height_parameter_name not in self.query:
            continue
        try:
            height = int(self.query[height_parameter_name])
        except ValueError:
            self.send_error(400)
            return None
        break

    if width is None or height is None:
        self.send_error(400)
        return None

    if must_be_square and width != height:
        self.send_error(400)
        return None

    if not can_round_to_nearest and (
        width not in allowed_widths or
        height not in allowed_heights
    ):
        self.send_error(400)
        return None

    if can_round_to_nearest:
        if width not in allowed_widths:
            width = min(allowed_widths, key=lambda allowed: abs(allowed - width))
        if height not in allowed_heights:
            height = min(allowed_heights, key=lambda allowed: abs(allowed - height))

    return width, height


def handle_image_resize(
    self: web_server_handler,
    image_content_hash: str,
    target_width: int,
    target_height: int,
    cropped_hash: str,
    cache_control: str = "max-age=120",
    skip_cache_cropped_image: bool = False,
    placeholder_path: str | None = None,
) -> bool:
    resized_image = _load_variant_image(
        self,
        image_content_hash,
        target_width,
        target_height,
        cropped_hash,
        skip_cache_cropped_image=skip_cache_cropped_image,
    )
    if resized_image is None:
        if placeholder_path is not None:
            # Плейсхолдер всегда отдаётся без кэша — какой бы cache_control
            # ни просили для настоящей картинки.
            return _send_placeholder_image(
                self,
                placeholder_path,
                target_width,
                target_height,
                cache_control=PLACEHOLDER_CACHE_CONTROL,
            )
        self.send_error(404)
        return True

    image_bytes, content_type = resized_image
    _send_image_response(
        self,
        image_bytes,
        content_type,
        cache_control=cache_control,
    )
    return True


def _send_stored_image(
    self: web_server_handler,
    content_hash: str | None,
    target_width: int | None = None,
    target_height: int | None = None,
    *,
    placeholder_path: str | None = None,
    cache_control: str = "max-age=120",
) -> bool:
    final_content_hash = content_hash
    if final_content_hash is None:
        if placeholder_path is not None:
            return _send_placeholder_image(
                self,
                placeholder_path,
                target_width,
                target_height,
                cache_control=PLACEHOLDER_CACHE_CONTROL,
            )
        self.send_error(404)
        return True

    if target_width is not None and target_height is not None:
        return handle_image_resize(
            self,
            final_content_hash,
            target_width,
            target_height,
            _build_variant_hash(final_content_hash, target_width, target_height),
            cache_control=cache_control,
            placeholder_path=placeholder_path,
        )

    original_image = _load_original_image(self, final_content_hash)
    if original_image is None:
        if placeholder_path is not None:
            return _send_placeholder_image(
                self,
                placeholder_path,
                cache_control=PLACEHOLDER_CACHE_CONTROL,
            )
        self.send_error(404)
        return True

    _send_image_response(
        self,
        original_image,
        _guess_image_content_type(original_image),
        cache_control=cache_control,
    )
    return True


def _read_json_body(self: web_server_handler) -> Any | None:
    raw_body = self.read_content()
    if not raw_body:
        return None

    if _get_header(self, "Content-Encoding") == "gzip":
        raw_body = gzip.decompress(raw_body)

    return json.loads(raw_body.decode("utf-8"))


def _read_image_upload(self: web_server_handler) -> tuple[bytes, str] | None:
    body = self.read_content()
    if not body:
        self.send_json({"errors": [{"code": 0, "message": "Image body is empty"}]}, 400)
        return None
    if len(body) > MAX_IMAGE_UPLOAD_BYTES:
        self.send_json({"errors": [{"code": 0, "message": "Image body is too large"}]}, 413)
        return None

    content_type = _guess_image_content_type(body)
    if not content_type.startswith("image/"):
        self.send_json({"errors": [{"code": 0, "message": "Body must contain a supported image"}]}, 415)
        return None
    return body, content_type


def _store_uploaded_image(self: web_server_handler, body: bytes) -> str:
    content_hash = hashlib.sha512(body).hexdigest()
    _write_cached_image(self, "originals", content_hash, body)
    return content_hash


def _current_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _require_privileged_image_upload(self: web_server_handler) -> bool:
    # В rbxd всё открыто (см. политику user_code без паролей), но загрузку
    # картинок всё равно оставляем только для loopback-запросов — это
    # защита от того, чтобы кто угодно не записал файл на диск.
    if self.is_privileged:
        return True
    self.send_json({"errors": [{"code": 0, "message": "Image uploads require a privileged request"}]}, 403)
    return False


# ---------------------------------------------------------------------------
# Разрешение пользователей и иконок плейсов — адаптация под модель rbxd.
# ---------------------------------------------------------------------------


def _resolve_user_id(self: web_server_handler) -> int | None:
    user_id = self.query.get("userId")
    if user_id is not None:
        try:
            return int(user_id)
        except ValueError:
            return None

    # В rbxd нет БД-таблицы `user` из RFD-N; имя -> iden_num ищем по таблице
    # `players`, в которую игрок попадает при первом заходе на плейс.
    username = self.query.get("username")
    if username is None:
        return None
    return self.server.storage.players.get_player_field_from_index(
        self.server.storage.players.player_field.USERNAME.value,
        username,
    )


def _user_exists(self: web_server_handler, user_id: int) -> bool:
    # В rbxd игрок идентифицируется user_code, а не голым id. Если пришёл
    # числовой id, считаем что игрок существует — он мог быть создан
    # конфиг-хуком retrieve_user_id ещё до записи в `players`.
    return True


def _get_place_icon_hash(
    self: web_server_handler,
    target_id: int,
    *,
    prefer_universe: bool,
) -> tuple[str | None, int | None]:
    # Каталога universes/places у rbxd нет (один плейс на сервер). Иконка
    # плейса берётся из AssetCache по THUMBNAIL_ID_CONST — туда её кладёт
    # установка плейса (`asset_cache` плейса, см. setup_player/setup_rcc).
    # Если иконки нет — отдаётся placeholder.
    asset_cache = self.game_config.asset_cache
    thumbnail_data = asset_cache.get_asset(util.const.THUMBNAIL_ID_CONST)
    if isinstance(thumbnail_data, returns.ret_data):
        # Ключ кэша — sha256 от байтов картинки, как и для остальных.
        return hashlib.sha256(thumbnail_data.data).hexdigest(), target_id
    return None, target_id


def _build_avatar_image_url(
    self: web_server_handler,
    user_id: int,
    width: int,
    height: int,
) -> str:
    return f"{self.hostname}/avatar-thumbnail/image?userId={user_id}&x={width}&y={height}"


def _build_headshot_image_url(
    self: web_server_handler,
    user_id: int,
    width: int,
    height: int,
) -> str:
    return f"{self.hostname}/headshot-thumbnail/image?userId={user_id}&x={width}&y={height}"


def _build_game_icon_url(
    self: web_server_handler,
    place_id: int | None,
    width: int,
    height: int,
) -> str:
    if place_id is None:
        return f"{self.hostname}/Thumbs/GameIcon.ashx?x={width}&y={height}"
    return f"{self.hostname}/Thumbs/GameIcon.ashx?assetId={place_id}&x={width}&y={height}"


def _build_asset_thumbnail_url(
    self: web_server_handler,
    asset_id: int,
    width: int,
    height: int,
) -> str:
    return f"{self.hostname}/Game/Tools/ThumbnailAsset.ashx?aid={asset_id}&fmt=png&wd={width}&ht={height}"


def _build_game_thumbnail_url(
    self: web_server_handler,
    asset_id: int,
    width: int,
    height: int,
) -> str:
    return (
        f"{self.hostname}/asset-thumbnail/image?"
        f"assetId={asset_id}&width={width}&height={height}"
    )


def _build_placeholder_batch_url(
    self: web_server_handler,
    request_type: str,
    width: int,
    height: int,
) -> str:
    if request_type == "Avatar":
        rel_path = _resolve_user_placeholder_rel_path(headshot=False)
        if rel_path is not None:
            return _build_static_url(self, rel_path)
        return _build_static_url(self, CONTENT_DELETED_PLACEHOLDER_REL_PATH)
    if request_type == "AvatarHeadShot":
        rel_path = _resolve_user_placeholder_rel_path(headshot=True)
        if rel_path is not None:
            return _build_static_url(self, rel_path)
        return _build_static_url(self, CONTENT_DELETED_PLACEHOLDER_REL_PATH)
    if request_type == "GameIcon":
        return _build_game_icon_url(self, None, width, height)
    return _build_static_url(self, GAME_BANNER_PLACEHOLDER_REL_PATH)


@server_path(r"/rfd/image-cdn/v1/(?P<image_key>[A-Za-z0-9]+)", regex=True, commands={"GET"})
def serve_cdn_image(self: web_server_handler, match: re.Match[str]) -> bool:
    image_key = match.group("image_key")
    image_bytes = _read_cached_image(self, "variants", image_key)
    if image_bytes is None:
        image_bytes = _read_cached_image(self, "originals", image_key)
    if image_bytes is None:
        self.send_error(404)
        return True

    _send_image_response(
        self,
        image_bytes,
        _guess_image_content_type(image_bytes),
        cache_control="public, max-age=31536000",
    )
    return True


@server_path("/avatar-thumbnail/image", commands={"GET"})
@server_path("/Thumbs/Avatar.ashx", commands={"GET"})
@server_path("/thumbs/avatar.ashx", commands={"GET"})
def avatar_thumbnail_image(self: web_server_handler) -> bool:
    size_pair = handle_resolution_check(
        self,
        width_parameters_name=["x", "width"],
        height_parameters_name=["y", "height"],
        allowed_widths=SQUARE_IMAGE_SIZES,
        allowed_heights=SQUARE_IMAGE_SIZES,
        must_be_square=True,
        can_round_to_nearest=True,
    )
    if size_pair is None:
        return True

    target_width, target_height = size_pair
    return _send_user_placeholder(
        self,
        headshot=False,
        target_width=target_width,
        target_height=target_height,
    )


@server_path("/headshot-thumbnail/json", commands={"GET"})
@server_path("/avatar-thumbnail/json", commands={"GET"})
def avatar_thumbnail_json(self: web_server_handler) -> bool:
    user_id = _resolve_user_id(self)
    rel_path = _resolve_user_placeholder_rel_path(headshot=False)
    if rel_path is None and os.path.isfile(
        _static_file_path(CONTENT_DELETED_PLACEHOLDER_REL_PATH)
    ):
        rel_path = CONTENT_DELETED_PLACEHOLDER_REL_PATH
    fallback_url = (
        _build_static_url(self, rel_path)
        if rel_path is not None
        else f"{self.hostname}/avatar-placeholder"
    )
    if user_id is None:
        self.send_json({"Final": True, "Url": fallback_url})
        return True

    size_pair = handle_resolution_check(
        self,
        width_parameters_name=["width", "x"],
        height_parameters_name=["height", "y"],
        allowed_widths=SQUARE_IMAGE_SIZES,
        allowed_heights=SQUARE_IMAGE_SIZES,
        must_be_square=True,
        can_round_to_nearest=True,
    )
    if size_pair is None:
        return True

    target_width, target_height = size_pair
    self.send_json({
        "Final": True,
        "Url": _build_avatar_image_url(self, user_id, target_width, target_height),
    })
    return True


@server_path("/avatar-placeholder", commands={"GET"})
@server_path("/headshot-placeholder", commands={"GET"})
def user_placeholder_image(self: web_server_handler) -> bool:
    '''
    Прямая отдача плейсхолдера юзера (без userId). Используется как
    fallback в JSON-ответах, когда настоящих картинок нет.
    '''
    headshot = self.path.startswith("/headshot")
    size_pair = None
    if "x" in self.query or "width" in self.query:
        size_pair = handle_resolution_check(
            self,
            width_parameters_name=["x", "width"],
            height_parameters_name=["y", "height"],
            allowed_widths=SQUARE_IMAGE_SIZES,
            allowed_heights=SQUARE_IMAGE_SIZES,
            must_be_square=True,
            can_round_to_nearest=True,
        )
    if size_pair is None:
        return _send_user_placeholder(self, headshot=headshot)
    target_width, target_height = size_pair
    return _send_user_placeholder(
        self,
        headshot=headshot,
        target_width=target_width,
        target_height=target_height,
    )


@server_path("/Thumbs/GameIcon.ashx", commands={"GET"})
@server_path("/Thumbs/PlaceIcon.ashx", commands={"GET"})
def place_icon_image(self: web_server_handler) -> bool:
    asset_id = self.query.get("assetId") or self.query.get("assetid")
    content_hash: str | None = None

    if asset_id is not None:
        try:
            requested_id = int(asset_id)
        except ValueError:
            self.send_error(400)
            return True

        content_hash, _place_id = _get_place_icon_hash(
            self,
            requested_id,
            prefer_universe=False,
        )

    if "x" not in self.query and "y" not in self.query and "width" not in self.query and "height" not in self.query:
        return _send_stored_image(
            self,
            content_hash,
            placeholder_path=GAME_ICON_PLACEHOLDER_REL_PATH,
        )

    size_pair = handle_resolution_check(
        self,
        width_parameters_name=["x", "width"],
        height_parameters_name=["y", "height"],
        allowed_widths=PLACE_ICON_SIZES,
        allowed_heights=PLACE_ICON_SIZES,
        must_be_square=False,
        can_round_to_nearest=True,
    )
    if size_pair is None:
        return True

    target_width, target_height = size_pair
    return _send_stored_image(
        self,
        content_hash,
        target_width,
        target_height,
        placeholder_path=GAME_ICON_PLACEHOLDER_REL_PATH,
    )


@server_path("/v1/games/icons", commands={"GET"})
def get_game_icons(self: web_server_handler) -> bool:
    universe_ids_csv = self.query.get("universeIds")
    if universe_ids_csv is None:
        self.send_json({"errors": [{"code": 4, "message": "The requested Ids are invalid, of an invalid type or missing."}]}, 400)
        return True

    universe_ids = universe_ids_csv.split(",")
    if len(universe_ids) > 100:
        self.send_json({"errors": [{"code": 1, "message": "There are too many requested Ids."}]}, 400)
        return True

    requested_size = self.query.get("size") or "50x50"
    try:
        size_width, size_height = requested_size.split("x", 1)
        target_width = min(GAME_ICON_SIZES, key=lambda allowed: abs(allowed - int(size_width)))
        target_height = min(GAME_ICON_SIZES, key=lambda allowed: abs(allowed - int(size_height)))
    except Exception:
        self.send_json({"errors": [{"code": 3, "message": "The requested size is invalid. Please see documentation for valid thumbnail size parameter name and format."}]}, 400)
        return True

    processed_requests = []
    for universe_id in universe_ids:
        try:
            universe_id_num = int(universe_id)
        except ValueError:
            continue

        content_hash, place_id = _get_place_icon_hash(
            self,
            universe_id_num,
            prefer_universe=True,
        )
        if place_id is None and content_hash is None:
            continue

        processed_requests.append({
            "targetId": universe_id_num,
            "state": "Completed",
            "imageUrl": _build_game_icon_url(self, place_id, target_width, target_height),
            "version": "TN3",
        })

    self.send_json({"data": processed_requests})
    return True


@server_path("/asset-thumbnail/json", commands={"GET"})
def asset_thumbnail_json(self: web_server_handler) -> bool:
    requested_size = self.query.get("size") or "768x432"
    try:
        size_width, size_height = requested_size.split("x", 1)
        target_width = min(DEFAULT_IMAGE_SIZES, key=lambda allowed: abs(allowed - int(size_width)))
        target_height = min(DEFAULT_IMAGE_SIZES, key=lambda allowed: abs(allowed - int(size_height)))
    except Exception:
        self.send_json({"Final": False, "Url": _build_static_url(self, GAME_BANNER_PLACEHOLDER_REL_PATH)})
        return True

    asset_id = self.query.get("assetId") or self.query.get("assetid")
    if asset_id is None:
        self.send_json({"Final": True, "Url": _build_static_url(self, GAME_BANNER_PLACEHOLDER_REL_PATH)})
        return True

    try:
        asset_id_num = int(asset_id)
    except ValueError:
        self.send_json({"Final": True, "Url": _build_static_url(self, GAME_BANNER_PLACEHOLDER_REL_PATH)})
        return True

    self.send_json({
        "Final": True,
        "Url": _build_asset_thumbnail_url(self, asset_id_num, target_width, target_height),
    })
    return True


@server_path("/asset-thumbnail/image", commands={"GET"})
@server_path("/thumbs/asset.ashx", commands={"GET"})
@server_path("/Thumbs/Asset.ashx", commands={"GET"})
def asset_thumbnail_image(self: web_server_handler) -> bool:
    asset_id = self.query.get("assetId") or self.query.get("assetid")
    if asset_id is None:
        return _send_placeholder_image(self, GAME_BANNER_PLACEHOLDER_REL_PATH)

    try:
        asset_id_num = int(asset_id)
    except ValueError:
        self.send_error(400)
        return True

    size_pair = handle_resolution_check(
        self,
        width_parameters_name=["x", "width"],
        height_parameters_name=["y", "height"],
        allowed_widths=DEFAULT_IMAGE_SIZES,
        allowed_heights=DEFAULT_IMAGE_SIZES,
        must_be_square=False,
        can_round_to_nearest=True,
    )
    if size_pair is None:
        return True

    target_width, target_height = size_pair
    asset_bytes = _load_asset_bytes(self, asset_id_num)
    if asset_bytes is None or not _is_image_data(asset_bytes):
        return _send_placeholder_image(
            self,
            GAME_BANNER_PLACEHOLDER_REL_PATH,
            target_width,
            target_height,
        )

    asset_key = f"asset-{asset_id_num}"
    _write_cached_image(self, "originals", asset_key, asset_bytes)
    return _send_stored_image(
        self,
        asset_key,
        target_width,
        target_height,
        placeholder_path=GAME_BANNER_PLACEHOLDER_REL_PATH,
    )


@server_path("/Game/Tools/ThumbnailAsset.ashx", commands={"GET"})
def thumbnail_asset(self: web_server_handler) -> bool:
    asset_id = self.query.get("aid")
    if asset_id is None:
        self.send_error(400)
        return True

    try:
        asset_id_num = int(asset_id)
    except ValueError:
        self.send_error(400)
        return True

    size_pair = None
    if (
        "wd" in self.query or
        "width" in self.query or
        "ht" in self.query or
        "height" in self.query
    ):
        size_pair = handle_resolution_check(
            self,
            width_parameters_name=["wd", "width"],
            height_parameters_name=["ht", "height"],
            allowed_widths=DEFAULT_IMAGE_SIZES,
            allowed_heights=DEFAULT_IMAGE_SIZES,
            must_be_square=False,
            can_round_to_nearest=True,
        )
        if size_pair is None:
            return True

    asset_bytes = _load_asset_bytes(self, asset_id_num)
    if asset_bytes is None or not _is_image_data(asset_bytes):
        if size_pair is None:
            return _send_placeholder_image(self, GAME_BANNER_PLACEHOLDER_REL_PATH)
        target_width, target_height = size_pair
        return _send_placeholder_image(
            self,
            GAME_BANNER_PLACEHOLDER_REL_PATH,
            target_width,
            target_height,
        )

    asset_key = f"asset-{asset_id_num}"
    _write_cached_image(self, "originals", asset_key, asset_bytes)

    if size_pair is None:
        _send_image_response(
            self,
            asset_bytes,
            _guess_image_content_type(asset_bytes),
        )
        return True

    target_width, target_height = size_pair
    return _send_stored_image(
        self,
        asset_key,
        target_width,
        target_height,
        placeholder_path=GAME_BANNER_PLACEHOLDER_REL_PATH,
    )


@server_path("/v1/batch", commands={"POST"})
def batch_image_request(self: web_server_handler) -> bool:
    try:
        json_data = _read_json_body(self)
    except gzip.BadGzipFile:
        self.send_json({"success": False, "message": "Invalid gzip data"}, 400)
        return True
    except Exception:
        self.send_json({"success": False, "message": "Invalid JSON data"}, 400)
        return True

    if json_data is None:
        self.send_json({"success": False, "message": "Missing JSON data"}, 400)
        return True
    if not isinstance(json_data, list):
        self.send_json({"success": False, "message": "JSON body must be an array"}, 400)
        return True

    if len(json_data) > MAX_BATCH_REQUESTS:
        self.send_json({"success": False, "message": "Too many requests"}, 400)
        return True
    if len(json_data) == 0:
        self.send_json({"data": []})
        return True

    processed_requests = []
    for request_obj in json_data:
        if not isinstance(request_obj, dict):
            continue
        if (
            "requestId" not in request_obj or
            "targetId" not in request_obj or
            "type" not in request_obj or
            "size" not in request_obj
        ):
            continue
        if request_obj["type"] not in BATCH_ALLOWED_TYPES:
            continue

        if "x" not in request_obj["size"]:
            continue
        split_size = request_obj["size"].split("x")
        if len(split_size) != 2:
            continue
        try:
            requested_width = int(split_size[0])
            requested_height = int(split_size[1])
        except (TypeError, ValueError):
            continue
        raw_target_id = request_obj.get("targetId")
        try:
            target_id = int(raw_target_id)
        except (TypeError, ValueError):
            target_id = None

        target_width = min(DEFAULT_IMAGE_SIZES, key=lambda allowed: abs(allowed - requested_width))
        target_height = min(DEFAULT_IMAGE_SIZES, key=lambda allowed: abs(allowed - requested_height))
        request_type = request_obj["type"]
        version = None

        if request_type == "Avatar":
            if target_id is None:
                image_url = _build_placeholder_batch_url(
                    self,
                    request_type,
                    target_width,
                    target_height,
                )
            else:
                image_url = _build_avatar_image_url(
                    self,
                    target_id,
                    target_width,
                    target_height,
                )
            version = "TN3"
        elif request_type == "AvatarHeadShot":
            if target_id is None:
                image_url = _build_placeholder_batch_url(
                    self,
                    request_type,
                    target_width,
                    target_height,
                )
            else:
                image_url = _build_headshot_image_url(
                    self,
                    target_id,
                    target_width,
                    target_height,
                )
            version = "1"
        elif request_type == "GameIcon":
            if target_id is None:
                image_url = _build_placeholder_batch_url(
                    self,
                    request_type,
                    target_width,
                    target_height,
                )
                version = None
            else:
                _content_hash, place_id = _get_place_icon_hash(
                    self,
                    target_id,
                    prefer_universe=True,
                )
                if place_id is None and _content_hash is None:
                    image_url = _build_placeholder_batch_url(
                        self,
                        request_type,
                        target_width,
                        target_height,
                    )
                else:
                    image_url = _build_game_icon_url(self, place_id, target_width, target_height)
                version = None
        elif request_type == "GameThumbnail":
            if target_id is None:
                image_url = _build_placeholder_batch_url(
                    self,
                    request_type,
                    target_width,
                    target_height,
                )
            else:
                image_url = _build_game_thumbnail_url(
                    self,
                    target_id,
                    target_width,
                    target_height,
                )
        elif request_type == "Asset":
            if target_id is None:
                image_url = _build_placeholder_batch_url(
                    self,
                    request_type,
                    target_width,
                    target_height,
                )
            else:
                image_url = _build_asset_thumbnail_url(
                    self,
                    target_id,
                    target_width,
                    target_height,
                )
        else:
            continue

        if target_id is None:
            target_id_value = 0
        else:
            target_id_value = target_id

        processed_requests.append({
            "requestId": request_obj["requestId"],
            "targetId": target_id_value,
            "state": "Completed",
            "imageUrl": image_url,
            "version": version,
        })

    self.send_json({"data": processed_requests})
    return True


@server_path("/Thumbs/Head.ashx", commands={"GET"})
@server_path("/headshot-thumbnail/image", commands={"GET"})
def headshot_thumbnail_image(self: web_server_handler) -> bool:
    user_id = self.query.get("userId")
    if user_id is None:
        self.send_error(400)
        return True

    try:
        _user_id_num = int(user_id)
    except ValueError:
        self.send_error(400)
        return True

    size_pair = handle_resolution_check(
        self,
        width_parameters_name=["x", "width"],
        height_parameters_name=["y", "height"],
        allowed_widths=SQUARE_IMAGE_SIZES,
        allowed_heights=SQUARE_IMAGE_SIZES,
        must_be_square=True,
        can_round_to_nearest=True,
    )
    if size_pair is None:
        return True

    target_width, target_height = size_pair
    return _send_user_placeholder(
        self,
        headshot=True,
        target_width=target_width,
        target_height=target_height,
    )


@server_path("/v1/users/avatar-headshot", commands={"GET"})
def multi_avatar_headshot(self: web_server_handler) -> bool:
    # Именно этот эндпойнт дёргает внутриигровой
    # Players:GetUserThumbnailAsync(HeadShot) — список игроков в стиле
    # Tower of Hell. Отдаём всем placeholder (см. IDEAS.md: рендер скинов).
    user_ids_csv = self.query.get("userIds")
    if user_ids_csv is None:
        self.send_json({"errors": [{"code": 4, "message": "The requested Ids are invalid, of an invalid type or missing."}]}, 400)
        return True

    user_ids = user_ids_csv.split(",")
    if len(user_ids) > 100:
        self.send_json({"errors": [{"code": 1, "message": "There are too many requested Ids."}]}, 400)
        return True

    requested_size = self.query.get("size") or "48x48"
    try:
        size_width, size_height = requested_size.split("x", 1)
        target_width = min(DEFAULT_IMAGE_SIZES, key=lambda allowed: abs(allowed - int(size_width)))
        target_height = min(DEFAULT_IMAGE_SIZES, key=lambda allowed: abs(allowed - int(size_height)))
    except Exception:
        self.send_json({"errors": [{"code": 3, "message": "The requested size is invalid. Please see documentation for valid thumbnail size parameter name and format."}]}, 400)
        return True

    processed_requests = []
    for user_id in user_ids:
        try:
            user_id_num = int(user_id)
        except ValueError:
            continue

        processed_requests.append({
            "targetId": user_id_num,
            "state": "Completed",
            "imageUrl": _build_headshot_image_url(self, user_id_num, target_width, target_height),
            "version": "1",
        })

    self.send_json({"data": processed_requests})
    return True


@server_path("/v1/users/avatar", commands={"GET"})
def multi_avatar(self: web_server_handler) -> bool:
    user_ids_csv = self.query.get("userIds")
    if user_ids_csv is None:
        self.send_json({"errors": [{"code": 4, "message": "The requested Ids are invalid, of an invalid type or missing."}]}, 400)
        return True

    user_ids = user_ids_csv.split(",")
    if len(user_ids) > 100:
        self.send_json({"errors": [{"code": 1, "message": "There are too many requested Ids."}]}, 400)
        return True

    requested_size = self.query.get("size") or "48x48"
    try:
        size_width, size_height = requested_size.split("x", 1)
        target_width = min(DEFAULT_IMAGE_SIZES, key=lambda allowed: abs(allowed - int(size_width)))
        target_height = min(DEFAULT_IMAGE_SIZES, key=lambda allowed: abs(allowed - int(size_height)))
    except Exception:
        self.send_json({"errors": [{"code": 3, "message": "The requested size is invalid. Please see documentation for valid thumbnail size parameter name and format."}]}, 400)
        return True

    processed_requests = []
    for user_id in user_ids:
        try:
            user_id_num = int(user_id)
        except ValueError:
            continue

        processed_requests.append({
            "targetId": user_id_num,
            "state": "Completed",
            "imageUrl": _build_avatar_image_url(self, user_id_num, target_width, target_height),
            "version": "TN3",
        })

    self.send_json({"data": processed_requests}, 200)
    return True
