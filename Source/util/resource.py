# Standard library imports
import enum
import os
import shutil
import subprocess
import sys

# Local application imports
import functools
import util.versions


def convert_to_winepath(path: str) -> str:
    clean_path = os.path.abspath(os.path.normpath(path))
    if sys.platform == 'win32':
        return clean_path

    if clean_path.startswith('/'):
        return 'Z:' + clean_path.replace('/', '\\')
    return clean_path


def get_code_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@functools.cache
def get_rfd_top_dir() -> str:
    '''
    Корень данных (логи, LocalStorage, кэш каталога, скины): `<rbxd>/data`.
    '''
    return os.path.abspath(os.path.join(get_code_dir(), os.pardir, 'data'))


ENV_FILE_NAME = 'env.env'


def load_env_file() -> None:
    env_path = os.path.join(get_rfd_top_dir(), ENV_FILE_NAME)
    if not os.path.isfile(env_path):
        return

    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            (key, _, value) = line.partition('=')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


class dir_type(enum.Enum):
    RŌBLOX = 0
    WORKING_DIR = 1
    MISC = 2


class bin_subtype(enum.Enum):
    SERVER = 'Server'
    PLAYER = 'Player'
    STUDIO = 'Studio'


DEFAULT_CONFIG_PATH = './GameConfig.toml'


def get_path_pieces(d: dir_type) -> list[str]:
    match d:
        case dir_type.RŌBLOX:
            return [get_rfd_top_dir(), 'Roblox']

        case dir_type.MISC:
            return [get_rfd_top_dir()]

        case dir_type.WORKING_DIR:
            return [os.getcwd()]


def retr_full_path(d: dir_type, *paths: str) -> str:
    full_path = os.path.join(*get_path_pieces(d), *paths)
    return full_path


def retr_rōblox_full_path(
    version: util.versions.rōblox,
    bin_type: bin_subtype,
    *paths: str,
    adjust_for_wine: bool = False,
) -> str:
    result = retr_full_path(
        dir_type.RŌBLOX,
        version.name,
        bin_type.value,
        *paths,
    )
    if adjust_for_wine:
        return convert_to_winepath(result)
    return result


def retr_config_full_path(path: str = DEFAULT_CONFIG_PATH) -> str:
    if os.path.isdir(path):
        path = os.path.join(
            path,
            DEFAULT_CONFIG_PATH,
        )
    elif not os.path.isabs(path):
        path = os.path.join(
            retr_full_path(dir_type.MISC),
            path,
        )
    return os.path.normpath(path)
