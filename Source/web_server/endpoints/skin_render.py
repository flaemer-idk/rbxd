# Рендер иконок аватара (avatar.png / headshot.png) из скина игрока.
#
# Подход: рисуем 2D-композицию в стиле Roblox-превью — силуэт тела, залитый
# цветами из `colors` скина (BrickColor ID -> HEX, палитра совпадает с
# RFD-N/настоящим Roblox), плюс плашки с названиями надетых ассетов.
# 3D-рендер не делаем (для него нужен движок); цель — чтобы UI клиента
# (GetUserThumbnailAsync) показывал осмысленную картинку, а не placeholder.
#
# Зависимость: Pillow. Если не установлена — рендер отдаёт None, и
# image.py откатывается на placeholder (как было).
#
# Кэш: готовые картинки кладутся в ImageCache/skins/<bucket>/<sha>. Ключ
# кэша — sha256 от сериализованного скина, так что смена скина инвалидит
# кэш автоматически.

from __future__ import annotations

import hashlib
import io
import json
import os
from typing import Any

from game_config import obj_type
from web_server.endpoints import avatar as avatar_endpoint


# Палитра BrickColor -> HEX (взята из RFD-N, совпадает с настоящим Roblox).
BODY_COLOR_HEX_BY_ID = {
    1: "F2F3F3", 5: "D7C59A", 9: "E8BAC8", 11: "80BBDC", 18: "CC8E69",
    21: "C4281C", 23: "0D69AC", 24: "F5CD30", 26: "1B2A35", 28: "287F47",
    29: "A1C48C", 37: "4B974B", 38: "A05F35", 45: "B4D2E4", 101: "DA867A",
    102: "6E99CA", 104: "6B327C", 105: "E29B40", 106: "DA8541", 107: "008F9C",
    119: "A4BD47", 125: "EAB892", 133: "D5733D", 135: "74869D", 141: "27462D",
    151: "789082", 153: "957977", 192: "694028", 194: "A3A2A5", 199: "635F62",
    208: "E5E4DF", 217: "7C5C46", 226: "FDEA8D", 305: "527CAE", 310: "5B9A4C",
    317: "7C9C6B", 321: "A75E9B", 330: "FF98DC", 334: "F8D96D", 351: "BC9B5D",
    352: "C7AC78", 359: "AF9483", 361: "564236", 364: "5A4C42",
    1001: "F8F8F8", 1002: "CDCDCD", 1003: "111111", 1004: "FF0000",
    1006: "B480FF", 1007: "A34B4B", 1008: "C1BE42", 1009: "FFFF00",
}

DEFAULT_BODY_COLOR_HEX = "F2F3F3"
BACKGROUND_COLOR = (248, 248, 248, 0)

# Размер холста для рендера «по умолчанию» (когда рендерим по content_hash,
# а не под конкретный запрошенный размер). image.py потом отресайзит до
# нужного размера через _resize_image_bytes.
_DEFAULT_RENDER_SIZE = 420

# Ключи цвета тела в скине rbxd -> часть тела.
BODY_COLOR_KEYS = (
    ("head", "head"),
    ("torso", "torso"),
    ("left_arm", "left_arm"),
    ("right_arm", "right_arm"),
    ("left_leg", "left_leg"),
    ("right_leg", "right_leg"),
)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#").upper()
    if len(hex_color) != 6:
        hex_color = DEFAULT_BODY_COLOR_HEX
    try:
        return (
            int(hex_color[0:2], 16),
            int(hex_color[2:4], 16),
            int(hex_color[4:6], 16),
        )
    except ValueError:
        return _hex_to_rgb(DEFAULT_BODY_COLOR_HEX)


def _body_color_rgb(color_id: Any) -> tuple[int, int, int]:
    try:
        color_id_int = int(color_id)
    except (TypeError, ValueError):
        color_id_int = 1
    return _hex_to_rgb(BODY_COLOR_HEX_BY_ID.get(color_id_int, DEFAULT_BODY_COLOR_HEX))


def _skin_cache_key(skin_raw: dict[str, Any], headshot: bool) -> str:
    '''
    Ключ кэша — sha256 от скина + флага headshot. Смена любого поля скина
    (цвета, ассеты, масштабы) даёт новый ключ.
    '''
    payload = json.dumps(
        {"skin": skin_raw, "headshot": headshot},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _collect_asset_names(skin_raw: dict[str, Any]) -> list[str]:
    '''
    Имена надетых ассетов для подписей на плашках. Берём из catalog_cache
    (он уже хранит (asset_type, name)); для URL'ов достаём id из ссылки.
    '''
    names: list[str] = []
    specs: list[Any] = []
    specs.extend(skin_raw.get("items", []) or [])
    for bundle_spec in (skin_raw.get("bundles", []) or []):
        bundle_id = avatar_endpoint.parse_bundle_spec(bundle_spec)
        if bundle_id is None:
            continue
        try:
            items = avatar_endpoint.resolve_bundle(bundle_id)
        except Exception:
            items = []
        names.extend(name for _aid, _atype, name in items)

    for spec in specs:
        asset_id = avatar_endpoint.parse_asset_spec(spec)
        if asset_id is None:
            continue
        try:
            details = avatar_endpoint.resolve_asset_entry(asset_id)
        except Exception:
            details = None
        if details is not None:
            names.append(details[1])
    return names


def _draw_avatar(
    skin_raw: dict[str, Any],
    width: int,
    height: int,
    *,
    headshot: bool,
) -> bytes | None:
    '''
    Рисует PNG. Возвращает байты или None, если Pillow нет.
    '''
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    image_obj = Image.new("RGBA", (width, height), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image_obj)

    raw_colors = skin_raw.get("colors", {}) or {}
    colors = {
        key: _body_color_rgb(raw_colors.get(key, 1))
        for _field, key in BODY_COLOR_KEYS
    }
    raw_scales = skin_raw.get("scales", {}) or {}

    def scale_of(name: str, default: float = 1.0) -> float:
        try:
            value = float(raw_scales.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(0.5, min(value, 2.0))

    if headshot:
        # Headshot: только голова по центру, крупно.
        head_size = int(min(width, height) * 0.62)
        head_x = (width - head_size) // 2
        head_y = int(height * 0.16)
        draw.ellipse(
            [head_x, head_y, head_x + head_size, head_y + head_size],
            fill=colors["head"] + (255,),
        )
        # Глаза — две точки, чтобы голова не была пустой.
        eye_r = max(2, head_size // 28)
        eye_y = head_y + int(head_size * 0.42)
        draw.ellipse(
            [head_x + int(head_size * 0.30) - eye_r, eye_y - eye_r,
             head_x + int(head_size * 0.30) + eye_r, eye_y + eye_r],
            fill=(30, 30, 30, 255),
        )
        draw.ellipse(
            [head_x + int(head_size * 0.70) - eye_r, eye_y - eye_r,
             head_x + int(head_size * 0.70) + eye_r, eye_y + eye_r],
            fill=(30, 30, 30, 255),
        )
    else:
        # Полный аватар: голова + торс + руки + ноги (стиль R6, пропорции
        # из scales). Координаты считаем от ширины холста.
        unit = width / 6.0
        head_r = unit * 1.05 * scale_of("head")
        head_cx = width / 2.0
        head_cy = unit * 1.35
        draw.ellipse(
            [head_cx - head_r, head_cy - head_r, head_cx + head_r, head_cy + head_r],
            fill=colors["head"] + (255,),
        )

        torso_w = unit * 2.0 * scale_of("width")
        torso_h = unit * 2.4 * scale_of("height")
        torso_x = (width - torso_w) / 2.0
        torso_y = head_cy + head_r * 0.85
        draw.rounded_rectangle(
            [torso_x, torso_y, torso_x + torso_w, torso_y + torso_h],
            radius=unit * 0.22,
            fill=colors["torso"] + (255,),
        )

        arm_w = unit * 0.72
        arm_h = torso_h * 0.95
        arm_y = torso_y + unit * 0.12
        draw.rounded_rectangle(
            [torso_x - arm_w * 1.06, arm_y, torso_x - arm_w * 0.06, arm_y + arm_h],
            radius=arm_w * 0.45,
            fill=colors["right_arm"] + (255,),
        )
        draw.rounded_rectangle(
            [torso_x + torso_w + arm_w * 0.06, arm_y,
             torso_x + torso_w + arm_w * 1.06, arm_y + arm_h],
            radius=arm_w * 0.45,
            fill=colors["left_arm"] + (255,),
        )

        leg_w = (torso_w / 2.0) * 0.86
        leg_h = unit * 2.2 * scale_of("height")
        leg_y = torso_y + torso_h
        draw.rounded_rectangle(
            [torso_x + torso_w * 0.07, leg_y,
             torso_x + torso_w * 0.07 + leg_w, leg_y + leg_h],
            radius=leg_w * 0.4,
            fill=colors["left_leg"] + (255,),
        )
        draw.rounded_rectangle(
            [torso_x + torso_w - torso_w * 0.07 - leg_w, leg_y,
             torso_x + torso_w - torso_w * 0.07, leg_y + leg_h],
            radius=leg_w * 0.4,
            fill=colors["right_leg"] + (255,),
        )

    output = io.BytesIO()
    image_obj.save(output, "PNG")
    return output.getvalue()


def render_skin_image(
    id_num: int,
    game_config: obj_type,
    width: int,
    height: int,
    *,
    headshot: bool,
) -> bytes | None:
    '''
    Рендерит картинку для юзера `id_num` на основе его скина.
    Возвращает PNG-байты или None, если Pillow нет / скин не найден
    (в этом случае image.py откатывается на placeholder).
    '''
    try:
        skin_raw = avatar_endpoint.get_skin_raw(id_num, game_config)
    except Exception:
        return None
    if not isinstance(skin_raw, dict):
        return None

    return _draw_avatar(skin_raw, width, height, headshot=headshot)


def skin_image_cache_key(
    id_num: int,
    game_config: obj_type,
    *,
    headshot: bool,
) -> str | None:
    '''
    Стабильный ключ кэша для скина юзера. None, если скин не читается.
    '''
    try:
        skin_raw = avatar_endpoint.get_skin_raw(id_num, game_config)
    except Exception:
        return None
    if not isinstance(skin_raw, dict):
        return None
    return _skin_cache_key(skin_raw, headshot)


def render_cached_image(
    game_config: obj_type,
    content_hash: str,
) -> bytes | None:
    '''
    Рендер по уже вычисленному ключу кэша. Используется image.py, когда
    картинку запросили напрямую (по content_hash из `_get_user_thumbnail_hash`),
    а в кэше её ещё нет.

    Идём по всем скинам из data/skins и сравниваем ключи — это дёшево,
    скинов немного, а чтение JSON быстрое.
    '''
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return None

    for headshot in (False, True):
        for skin_raw in _iter_all_skins(game_config):
            if _skin_cache_key(skin_raw, headshot) == content_hash:
                return _draw_avatar(
                    skin_raw,
                    _DEFAULT_RENDER_SIZE,
                    _DEFAULT_RENDER_SIZE,
                    headshot=headshot,
                )
    return None


def _iter_all_skins(game_config: obj_type) -> list[dict[str, Any]]:
    '''
    Все валидные скины из data/skins/<user_code>.json.
    '''
    try:
        skins_dir = _skins_dir(game_config)
        if not os.path.isdir(skins_dir):
            return []
        result: list[dict[str, Any]] = []
        for file_name in sorted(os.listdir(skins_dir)):
            if not file_name.endswith(".json"):
                continue
            file_path = os.path.join(skins_dir, file_name)
            try:
                with open(file_path, "r", encoding="utf-8") as file_obj:
                    skin_raw = json.load(file_obj)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(skin_raw, dict):
                result.append(skin_raw)
        return result
    except Exception:
        return []


def _skins_dir(game_config: obj_type) -> str:
    import util.resource
    base_dir = util.resource.get_rfd_top_dir()
    return os.path.join(base_dir, "data", "skins")
