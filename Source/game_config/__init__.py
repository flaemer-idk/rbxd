# pyright: reportImportCycles=false

# Standard library imports
import functools
import json
import os.path
import sys
import tomllib

# Typing imports
from typing import Any, Callable

# Third-party or external imports
import storage

# Local application imports
import data_transfer.transferer
from config_type import _logic
import assets
import assets.serialisers
from assets import asseter
from . import structure
import util.const
import util.resource
import util.versions


PARSERS: dict[str, Callable[[bytes], dict[Any, Any]]] = {
    'toml': lambda f: tomllib.loads(f.decode('utf-8')),
    'json': lambda f: json.loads(f),
}


class obj_type(structure.config_type, _logic.base_type):
    def __init__(self, data_dict: dict[Any, Any], base_dir: str) -> None:
        '''
        High-level call: reads the game configuration data from a file and serialises it.
        '''
        _logic.base_type.__init__(self, data_dict, base_dir)

        structure.config_type.__init__(
            self,
            root=self,
            # `current_typ` must be defined separately since this `__init__` call is a super constructor.
            current_typ=structure.config_type,
            **self.data_dict,
        )

        self.storage = storage.storager(
            self.game_setup.persistence.sqlite_path,
            force_init=self.game_setup.persistence.clear_on_start,
        )

        self.data_transferer = data_transfer.transferer.obj_type()

        # Общий пул ассетов: None в конфиге → `<data>/Assets`; пустая строка
        # или явный путь в конфиге разрешаются относительно каталога конфига.
        shared_dir = self.game_setup.asset_cache.shared_dir_path
        if shared_dir is None:
            shared_dir = os.path.join(
                util.resource.get_rfd_top_dir(), 'Assets',
            )
        elif shared_dir.strip() == '':
            shared_dir = None

        self.asset_cache = asseter(
            dir_path=self.game_setup.asset_cache.dir_path,
            redirect_func=self.remote_data.asset_redirects,
            asset_name_func=self.game_setup.asset_cache.name_template,
            clear_on_start=self.game_setup.asset_cache.clear_on_start,
            shared_dir_path=shared_dir,
            place_iden=self.game_setup.place_iden,
        )

    def retr_version(self) -> util.versions.rōblox:
        return self.game_setup.roblox_version

    def save_place_file(self) -> None:
        '''
        Инжест плейса: читает место из конфига, прогоняет через rbxl-трансформы
        и кладёт в локальный кэш под `place_iden`.
        '''
        place_uri = self.server_core.place_file.rbxl_uri
        raw_data = place_uri.extract()
        if raw_data is None:
            raise Exception(f'Failed to extract data from {place_uri.value}.')

        rbxl_data, _changed = assets.serialisers.parse(
            raw_data, {assets.serialisers.method.rbxl},
        )
        if rbxl_data is None:
            rbxl_data = raw_data

        self.asset_cache.add_asset(self.game_setup.place_iden, rbxl_data)

    def save_thumbnail(self) -> None:
        '''Иконка плейса — в локальный кэш.'''
        icon_uri = self.server_core.metadata.icon_uri
        if icon_uri is None:
            return
        try:
            thumbnail_data = icon_uri.extract() or bytes()
            self.asset_cache.add_asset(
                util.const.THUMBNAIL_ID_CONST, thumbnail_data,
            )
        except Exception:
            print('Warning: thumbnail data not found.')


STDIN_NAME = '-'


def get_config_dir_path(path: str = STDIN_NAME) -> str:
    if path == STDIN_NAME:
        return util.resource.retr_full_path(util.resource.dir_type.WORKING_DIR)
    return os.path.dirname(util.resource.retr_config_full_path(path))


@functools.cache
def read_file_data(path: str) -> bytes:
    # Reads from stdin if `-` is passed in.
    # This takes precedent from FFmpeg.
    if path == STDIN_NAME:
        return sys.stdin.buffer.read()
    file_path = util.resource.retr_config_full_path(path)
    with open(file_path, 'rb') as f:
        return f.read()


@functools.cache
def get_cached_config(path: str = util.resource.DEFAULT_CONFIG_PATH) -> obj_type:
    base_dir = get_config_dir_path(path)
    file_data = read_file_data(path)
    for parse in PARSERS.values():
        try:
            data_dict = parse(file_data)
        except Exception:
            continue
        return obj_type(
            data_dict=data_dict,
            base_dir=base_dir,
        )
    raise Exception(f'The file at {path} is in an invalid format')


@functools.cache
def generate_config(rbxl_file: str, version: util.versions.rōblox = util.versions.rōblox.v463) -> obj_type:
    # The dictionary structure should adjust with changes to the `structure.py` file.
    skeleton = {
        'server_core': {'place_file': {'rbxl_uri': rbxl_file}},
        'game_setup': {'roblox_version': version.name},
    }
    base_dir = util.resource.retr_full_path(util.resource.dir_type.MISC)
    config = obj_type(skeleton, base_dir)
    return config


@functools.cache
def generate_cdn_config() -> obj_type:
    '''
    Синтетический конфиг для безплейсового CDN-вебсервера (режим `webserver`
    без `--config`): версия v347, общий пул ассетов `data/Assets`, состояние
    (кеш, sqlite) — в `data/CDN`, чтобы не засорять cwd.

    `rbxl_uri` здесь никогда не извлекается: веб-рутина не трогает
    `place_file` — это удел bootstrap RCC.
    '''
    skeleton = {
        'server_core': {'place_file': {'rbxl_uri': 'rbxassetid://1818'}},
        'game_setup': {
            'roblox_version': util.versions.rōblox.v347.name,
            'asset_cache': {
                'dir_path': './CDN/AssetCache',
                'shared_dir_path': None,
            },
            'persistence': {
                'sqlite_path': './CDN/_.sqlite',
            },
        },
    }
    base_dir = util.resource.retr_full_path(util.resource.dir_type.MISC)
    os.makedirs(
        os.path.join(base_dir, 'CDN'),
        exist_ok=True,
    )
    config = obj_type(skeleton, base_dir)
    return config
