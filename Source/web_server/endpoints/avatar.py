import os
import json
import shutil
import re
import threading
import urllib.request
import urllib.error
from web_server._logic import web_server_handler, server_path
from config_type.types import structs, wrappers
import util.versions as versions
import util.resource
import catalog_cache
from game_config import obj_type

# Стандартный шаблон скина, если папка или default.json отсутствуют
DEFAULT_AVATAR = {
    "type": "R6",
    "items": [],
    "bundles": [],
    "scales": {
        "height": 1.0,
        "width": 1.0,
        "head": 1.0,
        "depth": 1.0,
        "proportion": 0.0,
        "body_type": 0.0
    },
    "colors": {
        "head": 1,
        "left_arm": 1,
        "left_leg": 1,
        "right_arm": 1,
        "right_leg": 1,
        "torso": 1
    }
}

# ---------------------------------------------------------------------------
# Бандлы
#
# В скине (`data/skins/<user_code>.json`, корень данных — `<rbxd>/data`) можно
# указать поле `"bundles"` — id бандла
# или его URL целиком:
#
#     "bundles": [678, "https://www.roblox.com/bundles/678/Pile-O-Sweet-Potato-Fries"]
#
# Бандл разворачивается через `catalog.roblox.com/v1/bundles/<id>/details` и
# раскладывается по назначению (кэш — таблица `bundles` в `_.sqlite`, поэтому
# каталог запрашивается один раз, а не на каждый запрос аватара):
#
#   - анимации локомоции (AssetType 48–55)  -> `animationAssetIds` (v463);
#   - части тела (Head/Torso/руки/ноги)     -> элементы с реальным
#     `assetTypeId` в `assetAndAssetTypeIds` (v463) — именно так клиент
#     замещает конечности R15;
#   - всё остальное (шляпы, аксессуары)     -> обычные элементы аватара;
#     в v347 (без поля типов) идут только аксессуары.
# ---------------------------------------------------------------------------

BUNDLE_CACHE_TTL = 30 * 24 * 3600  # 30 дней; устаревший кэш используется при ошибке сети


class bundle_asset_type:
    HEAD = 17
    TORSO = 27
    RIGHT_ARM = 28
    LEFT_ARM = 29
    LEFT_LEG = 30
    RIGHT_LEG = 31


BODY_PART_ASSET_TYPES = frozenset({
    bundle_asset_type.HEAD,
    bundle_asset_type.TORSO,
    bundle_asset_type.RIGHT_ARM,
    bundle_asset_type.LEFT_ARM,
    bundle_asset_type.LEFT_LEG,
    bundle_asset_type.RIGHT_LEG,
})

# AssetType -> ключ в `animationAssetIds` (названия локомоций Animate-скрипта).
ANIMATION_NAMES = {
    48: 'climb',
    50: 'fall',
    51: 'idle',
    52: 'jump',
    53: 'run',
    54: 'swim',
    55: 'walk',
}

# Для "голых" id из `items` тип неизвестен; 8 (Hat) — историческое поведение
# этого эндпойнта, при котором клиент надевает их как аксессуары.
DEFAULT_ASSET_TYPE = 8

BUNDLE_URL_PATTERN = re.compile(r'/bundles/(\d+)')

# Ссылки на ассеты каталога, которые юзер может вставлять в `items` целиком:
# `roblox.com/catalog/146176342/Blue-Collar-Cat-Left-Arm`,
# `roblox.com/library/146176342/Name` (старый формат).
ASSET_URL_PATTERN = re.compile(r'/(?:catalog|library)/(\d+)')

def parse_asset_spec(spec) -> int | None:
    '''
    Элемент `items`: id или ссылка целиком (`678`,
    `"678"`, `"https://www.roblox.com/catalog/146176342/Name"`).
    '''
    if isinstance(spec, bool):
        return None
    if isinstance(spec, int):
        return spec
    if isinstance(spec, float) and spec.is_integer():
        return int(spec)
    if isinstance(spec, str):
        spec = spec.strip()
        if spec.isdigit():
            return int(spec)
        match = ASSET_URL_PATTERN.search(spec)
        if match is not None:
            return int(match[1])
    return None


class resolved_bundles:
    def __init__(self) -> None:
        self.typed_items: list[dict] = []
        self.accessory_ids: list[int] = []
        self.animations: dict[str, int] = {}
        self._seen_ids: set[int] = set()

def parse_bundle_spec(spec) -> int | None:
    '''
    Принимает id бандла или URL целиком: `678`, `"678"`,
    `"https://www.roblox.com/bundles/678/Pile-O-Sweet-Potato-Fries"`.
    '''
    if isinstance(spec, bool):
        return None
    if isinstance(spec, int):
        return spec
    if isinstance(spec, float) and spec.is_integer():
        return int(spec)
    if isinstance(spec, str):
        spec = spec.strip()
        if spec.isdigit():
            return int(spec)
        match = BUNDLE_URL_PATTERN.search(spec)
        if match is not None:
            return int(match[1])
    return None


def fetch_bundle(bundle_id: int) -> list[tuple[int, int, str]]:
    '''
    Запрашивает состав бандла у каталога Roblox.
    Возвращает список `(asset_id, asset_type, name)`.
    '''
    url = f'https://catalog.roblox.com/v1/bundles/{bundle_id}/details'
    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Roblox/WinInet',
            'Accept': 'application/json',
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read())

    items: list[tuple[int, int, str]] = []
    for item in data.get('items', []):
        asset_id = item.get('id')
        asset_type = item.get('assetType')
        if not isinstance(asset_id, int) or not isinstance(asset_type, int):
            continue
        name = item.get('name')
        items.append((
            asset_id,
            asset_type,
            '' if name is None else str(name),
        ))
    return items


def resolve_bundle(bundle_id: int) -> list[tuple[int, int, str]]:
    '''
    Состав бандла с кэшированием в sqlite: свежий кэш отдаётся сразу,
    при промахе/устаревании — запрос в каталог с записью в кэш, при ошибке
    сети — устаревший кэш (лучше старые данные, чем никакие).
    '''
    database = catalog_cache.get_catalog_cache().bundles

    cached = database.get(bundle_id, max_age=BUNDLE_CACHE_TTL)
    if cached is not None:
        return cached

    try:
        items = fetch_bundle(bundle_id)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f'Error fetching bundle {bundle_id}: {e}')
        stale = database.get(bundle_id, max_age=None)
        return stale if stale is not None else []

    database.put(bundle_id, items)
    return items


def fetch_asset_details(asset_id: int) -> tuple[int, str] | None:
    '''
    Тип и название одиночного ассета из каталога.
    '''
    url = f'https://catalog.roblox.com/v1/catalog/items/{asset_id}/details?itemType=Asset'
    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Roblox/WinInet',
            'Accept': 'application/json',
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read())

    asset_type = data.get('assetType')
    if not isinstance(asset_type, int):
        return None
    name = data.get('name')
    return (asset_type, '' if name is None else str(name))


def resolve_asset_entry(asset_id: int) -> tuple[int, str] | None:
    '''
    Тип одиночного ассета из `items` с кэшем в sqlite (как у бандлов):
    свежий кэш -> каталог -> устаревший кэш -> ничего.
    '''
    database = catalog_cache.get_catalog_cache().assets

    cached = database.get(asset_id, max_age=BUNDLE_CACHE_TTL)
    if cached is not None:
        return cached

    try:
        details = fetch_asset_details(asset_id)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f'Error fetching asset {asset_id} details: {e}')
        stale = database.get(asset_id, max_age=None)
        return stale
    if details is None:
        return None

    (asset_type, name) = details
    database.put(asset_id, asset_type, name)
    return details


def classify_asset(result: resolved_bundles, asset_id: int, asset_type: int) -> None:
    '''
    Кладёт один ассет в правильную корзину (с дедупликацией):
    анимация -> `animationAssetIds`; часть тела -> типизированный элемент
    (v463); аксессуар -> туда же + в `accessory_ids` (v347).
    '''
    if asset_id in result._seen_ids:
        return
    result._seen_ids.add(asset_id)

    animation_name = ANIMATION_NAMES.get(asset_type)
    if animation_name is not None:
        # Анимации уходят и в `animationAssetIds`, и в типизированный
        # список (как в настоящем avatar API), чтобы клиент 2021E
        # подхватил их любым из двух механизмов.
        result.animations[animation_name] = asset_id
        result.typed_items.append({
            'assetId': asset_id,
            'assetTypeId': asset_type,
        })
        return

    result.typed_items.append({
        'assetId': asset_id,
        'assetTypeId': asset_type,
    })
    if asset_type not in BODY_PART_ASSET_TYPES:
        # В v347 поле типов нет: туда идут только аксессуары.
        result.accessory_ids.append(asset_id)


def resolve_avatar_items(
    item_ids,
    bundle_specs,
) -> resolved_bundles:
    '''
    Собирает полный аватар из скина: одиночные `items` (тип спрашивается у
    каталога и кэшируется) плюс развёрнутые `bundles`.
    '''
    result = resolved_bundles()
    result._seen_ids = set()

    for asset_id in item_ids if isinstance(item_ids, list) else []:
        if not isinstance(asset_id, int):
            continue
        details = resolve_asset_entry(asset_id)
        if details is None:
            # Тип неизвестен (нет сети и кэша) — историческое поведение,
            # чтобы скин не «пропал»: клиент наденет его как аксессуар.
            classify_asset(result, asset_id, DEFAULT_ASSET_TYPE)
            continue
        (asset_type, _name) = details
        classify_asset(result, asset_id, asset_type)

    for spec in bundle_specs if isinstance(bundle_specs, list) else []:
        bundle_id = parse_bundle_spec(spec)
        if bundle_id is None:
            print(f'Warning: unrecognised bundle spec {spec!r} in skin.')
            continue

        for (asset_id, asset_type, _name) in resolve_bundle(bundle_id):
            classify_asset(result, asset_id, asset_type)

    return result


# ---------------------------------------------------------------------------
# Скины
# ---------------------------------------------------------------------------

# Вебсервер многопоточный (`http.server.ThreadingHTTPServer`): RCC и клиент
# могут одновременно прийти за аватаром одного и того же игрока. Лок ниже
# гарантирует, что `default.json` и персональный скин создаются ровно один
# раз, а атомарная замена файлов — что параллельный читатель не наткнётся на
# наполовину записанный JSON.
_skin_file_lock = threading.Lock()


def _atomic_write_json(path: str, payload: object) -> None:
    '''
    Записывает JSON во временный файл и атомарно подменяет целевой
    (`os.replace` в пределах одной ФС неразрывна).
    '''
    tmp_path = f'{path}.tmp.{os.getpid()}.{threading.get_ident()}'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=4)
    os.replace(tmp_path, path)


def _atomic_copy(src_path: str, dst_path: str) -> None:
    '''
    Копирует файл атомарно — обычный `shutil.copy` пишет его по частям, и
    другой поток может прочитать обрезанный JSON.
    '''
    tmp_path = f'{dst_path}.tmp.{os.getpid()}.{threading.get_ident()}'
    shutil.copy(src_path, tmp_path)
    os.replace(tmp_path, dst_path)


def get_user_code(id_num: int, game_config: obj_type) -> str | None:
    database = game_config.storage.players
    user_code = database.get_player_field_from_index(
        database.player_field.IDEN_NUM,
        id_num,
        database.player_field.USERCODE,
    )
    if user_code is None:
        return None
    if not isinstance(user_code, str):
        # Контракт базы/хуков нарушился (например, вернулся кортеж) — лучше
        # безопасное имя, чем падение всего эндпойнта аватара.
        print(
            f"Warning: user_code for id {id_num} is {type(user_code).__name__}, "
            f"expected str; using fallback name."
        )
        return None
    return user_code


def get_skin_raw(id_num: int, game_config: obj_type) -> dict:
    '''
    Читает JSON скина игрока, создавая `default.json` и персональную копию
    при первом заходе. Возвращает сырой словарь (при любых ошибках — шаблон).
    '''
    user_code = get_user_code(id_num, game_config)

    # Если код пользователя не найден в базе данных, используем временное имя
    if user_code is None:
        user_code = f"Player_{id_num}"

    # Корень данных фиксирован (`<rbxd>/data`, см. util.resource.get_rfd_top_dir),
    # а не текущая рабочая директория — скины глобальны и не зависят от того,
    # откуда запущен процесс.
    skins_dir = util.resource.retr_full_path(util.resource.dir_type.MISC, 'skins')
    os.makedirs(skins_dir, exist_ok=True)

    default_json_path = os.path.join(skins_dir, 'default.json')

    # Очищаем имя пользователя от недопустимых в путях символов
    safe_user_code = re.sub(r'[^a-zA-Z0-9_\-]', '_', user_code)
    if not safe_user_code:
        # Имя состояло только из «нелатиницы» — не оставляем файл `.json`.
        safe_user_code = f'Player_{id_num}'
    user_json_path = os.path.join(skins_dir, f'{safe_user_code}.json')

    # Создание файлов гоняется между потоками запросов, поэтому вся проверка
    # «есть ли файл» + запись идут под одним локом; читают снапшот уже без лока.
    with _skin_file_lock:
        # Если default.json не существует, создаем его с базовым шаблоном
        if not os.path.exists(default_json_path):
            try:
                _atomic_write_json(default_json_path, DEFAULT_AVATAR)
            except Exception as e:
                print(f"Error creating default.json: {e}")

        # Если файла скина для конкретного игрока нет, копируем default.json под его именем
        if not os.path.exists(user_json_path) and os.path.exists(default_json_path):
            try:
                _atomic_copy(default_json_path, user_json_path)
            except Exception as e:
                print(f"Error copying default.json to {safe_user_code}.json: {e}")

    # Пытаемся прочитать JSON-файл скина игрока
    try:
        target_path = user_json_path if os.path.exists(user_json_path) else default_json_path
        with open(target_path, 'r', encoding='utf-8') as f:
            avatar_raw = json.load(f)
    except Exception as e:
        print(f"Error reading skin JSON for {user_code}: {e}. Using hardcoded default.")
        return dict(DEFAULT_AVATAR)

    if not isinstance(avatar_raw, dict):
        return dict(DEFAULT_AVATAR)
    return avatar_raw


def parse_avatar(avatar_raw: dict) -> structs.avatar_data:
    '''
    Парсит сырой словарь скина в структуру для движка.
    Не знает про бандлы — они разворачиваются отдельно (`resolve_bundles`).
    '''
    # Безопасно парсим значения и приводим их к типам данных, которые ожидает движок RFD
    try:
        raw_type = avatar_raw.get("type", "R15")
        # Приводим к enum-типу (R6 или R15)
        parsed_type = structs.avatar_type(raw_type)

        # Получаем массивы ID надетых ассетов; каждый элемент может быть
        # голым id или ссылкой с каталога целиком.
        parsed_items: list[int] = []
        for item in avatar_raw.get("items", []):
            asset_id = parse_asset_spec(item)
            if asset_id is None:
                print(f"Warning: unrecognised skin item {item!r}.")
                continue
            parsed_items.append(asset_id)

        # Парсим масштабы тела
        raw_scales = avatar_raw.get("scales", DEFAULT_AVATAR["scales"])
        parsed_scales = structs.avatar_scales(
            height=float(raw_scales.get("height", 1.0)),
            width=float(raw_scales.get("width", 1.0)),
            head=float(raw_scales.get("head", 1.0)),
            depth=float(raw_scales.get("depth", 1.0)),
            proportion=float(raw_scales.get("proportion", 0.0)),
            body_type=float(raw_scales.get("body_type", 0.0))
        )

        # Парсим цвета частей тела (BrickColor ID)
        raw_colors = avatar_raw.get("colors", DEFAULT_AVATAR["colors"])
        parsed_colors = structs.avatar_colors(
            head=int(raw_colors.get("head", 1)),
            left_arm=int(raw_colors.get("left_arm", 1)),
            left_leg=int(raw_colors.get("left_leg", 1)),
            right_arm=int(raw_colors.get("right_arm", 1)),
            right_leg=int(raw_colors.get("right_leg", 1)),
            torso=int(raw_colors.get("torso", 1))
        )

        return structs.avatar_data(
            type=parsed_type,
            items=parsed_items,
            scales=parsed_scales,
            colors=parsed_colors
        )
    except Exception as e:
        print(f"Error parsing skin structure: {e}")
        # Защитный фоллбек, чтобы Студия или сервер не упали при ошибке в JSON
        return structs.avatar_data(
            type=structs.avatar_type.R15,
            items=[],
            scales=structs.avatar_scales(**DEFAULT_AVATAR["scales"]),
            colors=structs.avatar_colors(**DEFAULT_AVATAR["colors"])
        )

def get_avatar(id_num: int, game_config: obj_type) -> structs.avatar_data:
    '''Совместимая обёртка: скин без разворота бандлов.'''
    return parse_avatar(get_skin_raw(id_num, game_config))


@server_path('/v1.1/avatar-fetch/', versions={versions.rōblox.v347})
def _(self: web_server_handler) -> bool:
    '''
    Character appearance for v347.
    '''
    id_num = int(self.query['userId'])
    skin_raw = get_skin_raw(id_num, self.game_config)
    avatar = parse_avatar(skin_raw)
    resolved = resolve_avatar_items(
        avatar.items,
        skin_raw.get('bundles', []),
    )

    self.send_json({
        "animations": {},
        "resolvedAvatarType": avatar.type.name,
        "accessoryVersionIds": resolved.accessory_ids,
        "equippedGearVersionIds": [],
        "backpackGearVersionIds": [],
        "bodyColors": {
            "HeadColor": avatar.colors.head,
            "LeftArmColor": avatar.colors.left_arm,
            "LeftLegColor": avatar.colors.left_leg,
            "RightArmColor": avatar.colors.right_arm,
            "RightLegColor": avatar.colors.right_leg,
            "TorsoColor": avatar.colors.torso,
        },
        "scales": {
            "Height": avatar.scales.height,
            "Width": avatar.scales.width,
            "Head": avatar.scales.head,
            "Depth": avatar.scales.depth,
            "Proportion": avatar.scales.proportion,
            "BodyType": avatar.scales.body_type,
        },
    })
    return True


@server_path('/v1/avatar', versions={versions.rōblox.v463})
@server_path('/v1/avatar/', versions={versions.rōblox.v463})
@server_path('/v1/avatar-fetch', versions={versions.rōblox.v463})
@server_path('/v1/avatar-fetch/', versions={versions.rōblox.v463})
def _(self: web_server_handler) -> bool:
    '''
    Character appearance for v463.
    '''
    id_num = int(self.query['userId'])
    skin_raw = get_skin_raw(id_num, self.game_config)
    avatar = parse_avatar(skin_raw)
    resolved = resolve_avatar_items(
        avatar.items,
        skin_raw.get('bundles', []),
    )

    self.send_json({
        "resolvedAvatarType": avatar.type.name,
        "equippedGearVersionIds": [],
        "backpackGearVersionIds": [],
        "assetAndAssetTypeIds": resolved.typed_items,
        "animationAssetIds": resolved.animations,
        "bodyColors": {
            "headColorId": avatar.colors.head,
            "leftArmColorId": avatar.colors.left_arm,
            "leftLegColorId": avatar.colors.left_leg,
            "rightArmColorId": avatar.colors.right_arm,
            "rightLegColorId": avatar.colors.right_leg,
            "torsoColorId": avatar.colors.torso,
        },
        "scales": {
            "height": avatar.scales.height,
            "width": avatar.scales.width,
            "head": avatar.scales.head,
            "depth": avatar.scales.depth,
            "proportion": max(avatar.scales.proportion, 1e-2),
            "bodyType": max(avatar.scales.body_type, 1e-2),
        },
        "emotes": [
            {
                "assetId": 3696763549,
                "assetName": "Heisman Pose",
                "position": 1
            },
            {
                "assetId": 3360692915,
                "assetName": "Tilt",
                "position": 2
            },
            {
                "assetId": 3696761354,
                "assetName": "Air Guitar",
                "position": 3
            },
            {
                "assetId": 3576968026,
                "assetName": "Shrug",
                "position": 4
            },
            {
                "assetId": 3576686446,
                "assetName": "Hello",
                "position": 5
            },
            {
                "assetId": 3696759798,
                "assetName": "Superhero Reveal",
                "position": 6
            },
            {
                "assetId": 3360689775,
                "assetName": "Salute",
                "position": 7
            },
            {
                "assetId": 3360686498,
                "assetName": "Stadium",
                "position": 8
            }
        ]
    })
    return True


@server_path('/v1.1/game-start-info', versions={versions.rōblox.v463})
def _(self: web_server_handler) -> bool:
    '''
    https://github.com/Heliodex/Meteorite/blob/76d53e75dace3195c1068e0de66c137376a88bcf/Back/server.mjs#L3718
    '''
    self.send_json({
        "gameAvatarType": "PlayerChoice",
        "allowCustomAnimations": True,
        "universeAvatarCollisionType": "OuterBox",
        "universeAvatarBodyType": "Standard",
        "jointPositioningType": "ArtistIntent",
        "universeAvatarMinScales": {
            "height": -1e17,
            "width": -1e17,
            "head": -1e17,
            "depth": -1e17,
            "proportion": -1e17,
            "bodyType": -1e17,
        },
        "universeAvatarMaxScales": {
            "height": +1e17,
            "width": +1e17,
            "head": +1e17,
            "depth": +1e17,
            "proportion": +1e17,
            "bodyType": +1e17,
        },
        "universeAvatarAssetOverrides": [],
        "moderationStatus": None,
    })
    return True
