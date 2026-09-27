# Standard library imports
import dataclasses
import hashlib
import os

# Internal or local application imports
import util.resource


TOOLBOX_ID_BASE = 90_000_000_000_000
'''
Локальные ассеты тулбокса получают id из этого диапазона: 14 цифр против
~11 цифр у настоящих ассетов Roblox, поэтому пересечение невозможно.
'''

TOOLBOX_ID_SPACE = 10**12

ASSET_EXTENSIONS = ('.rbxm', '.rbxmx')
PREVIEW_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif')

# Категории — это подпапки в data/Toolbox. Они же создаются при первом
# сканировании, чтобы структуру было видно сразу. Папки с другими именами
# тоже сканируются, просто студийные категории на них не ссылаются.
DEFAULT_CATEGORIES = ('Models', 'Meshes', 'Images', 'AudioVideo')

# TypeId для ответа /ide/toolbox/items: Model, Mesh, Decal, Audio.
CATEGORY_TYPE_IDS = {
    'Models': 10,
    'Meshes': 4,
    'Images': 13,
    'AudioVideo': 3,
}

# Как студийные категории мапятся на наши папки. В живом логе студия
# запрашивает 'FreeModels'; остальные — алиасы на все случаи.
CATEGORY_ALIASES = {
    'freemodels': 'Models',
    'models': 'Models',
    'model': 'Models',
    'meshes': 'Meshes',
    'mesh': 'Meshes',
    'images': 'Images',
    'image': 'Images',
    'freedecals': 'Images',
    'decals': 'Images',
    'decal': 'Images',
    'audiovideo': 'AudioVideo',
    'audio': 'AudioVideo',
    'freeaudio': 'AudioVideo',
    'sound': 'AudioVideo',
    'sounds': 'AudioVideo',
    'music': 'AudioVideo',
    'video': 'AudioVideo',
}

# Подмена корня для тестов; None — обычный data/Toolbox.
_root_override: str | None = None


def get_root() -> str:
    if _root_override is not None:
        return _root_override
    return util.resource.retr_full_path(util.resource.dir_type.MISC, 'Toolbox')


@dataclasses.dataclass(frozen=True)
class toolbox_entry:
    id_num: int
    name: str            # имя файла без расширения
    category: str        # имя папки-категории
    asset_path: str      # полный путь к .rbxm
    preview_path: str | None


def is_toolbox_id(asset_id: object) -> bool:
    return isinstance(asset_id, int) and asset_id >= TOOLBOX_ID_BASE


def _id_for_rel_path(rel_path: str) -> int:
    digest = hashlib.sha256(rel_path.encode('utf-8')).digest()
    return TOOLBOX_ID_BASE + int.from_bytes(digest[:8], 'big') % TOOLBOX_ID_SPACE


def _preview_for(asset_path: str) -> str | None:
    '''
    Превью — картинка с тем же именем, что и ассет, лежащая рядом.
    '''
    stem = os.path.splitext(asset_path)[0]
    for ext in PREVIEW_EXTENSIONS:
        candidate = stem + ext
        if os.path.isfile(candidate):
            return candidate
    return None


def scan() -> list[toolbox_entry]:
    '''
    Сканирует data/Toolbox/<Категория>/*.rbxm заново при каждом вызове:
    файлы можно добавлять, удалять и переименовывать на лету, никакого
    кэша и лимитов на количество. Список отсортирован по (категория, имя),
    так что пагинация стабильна между запросами.

    id считается как хеш от «категория/имя»: он не зависит от порядка
    файлов, поэтому добавление нового ассета не сдвигает id старых.
    '''
    root = get_root()
    if not os.path.isdir(root):
        try:
            os.makedirs(root)
        except OSError:
            return []
    for category in DEFAULT_CATEGORIES:
        try:
            os.makedirs(os.path.join(root, category), exist_ok=True)
        except OSError:
            pass

    try:
        category_dirs = sorted(
            name for name in os.listdir(root)
            if os.path.isdir(os.path.join(root, name))
        )
    except OSError:
        return []

    entries: list[toolbox_entry] = []
    used_ids: set[int] = set()
    for category in category_dirs:
        category_path = os.path.join(root, category)
        try:
            file_names = sorted(os.listdir(category_path))
        except OSError:
            continue
        for file_name in file_names:
            asset_path = os.path.join(category_path, file_name)
            if not os.path.isfile(asset_path):
                continue
            stem, ext = os.path.splitext(file_name)
            if ext.lower() not in ASSET_EXTENSIONS:
                continue
            # Коллизии хешей практически невозможны, но на всякий случай
            # пересаливаем — тоже детерминированно.
            salt = 0
            while True:
                id_num = _id_for_rel_path(f'{salt}:{category}/{stem}')
                if id_num not in used_ids:
                    break
                salt += 1
            used_ids.add(id_num)
            entries.append(toolbox_entry(
                id_num=id_num,
                name=stem,
                category=category,
                asset_path=asset_path,
                preview_path=_preview_for(asset_path),
            ))
    return entries


def filter_entries(
    entries: list[toolbox_entry],
    category_param: str,
    keyword: str,
) -> list[toolbox_entry]:
    '''
    Фильтр по студийной категории и подстроке имени (без учёта регистра).
    Пустая категория или неизвестный алиас — все категории.
    '''
    category = CATEGORY_ALIASES.get(category_param.strip().lower())
    if category is not None:
        entries = [e for e in entries if e.category == category]

    keyword = keyword.strip().lower()
    if keyword:
        entries = [e for e in entries if keyword in e.name.lower()]
    return entries


def find_entry(asset_id: int) -> toolbox_entry | None:
    if not is_toolbox_id(asset_id):
        return None
    for entry in scan():
        if entry.id_num == asset_id:
            return entry
    return None


def load_asset_bytes(asset_id: int) -> bytes | None:
    entry = find_entry(asset_id)
    if entry is None:
        return None
    try:
        with open(entry.asset_path, 'rb') as f:
            return f.read()
    except OSError:
        return None
