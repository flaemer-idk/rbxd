# Standard library imports
import enum
import os
import shutil
import subprocess
import sys

# Local application imports
import functools
import util.versions

MADE_WITH_PYINSTALLER = hasattr(sys, '_MEIPASS')

def convert_to_winepath(path: str) -> str:
    if shutil.which('winepath') is None:
        return path
    
    # Копируем окружение и гарантируем настройки для winepath
    env = os.environ.copy()
    if 'WINEPREFIX' not in env:
        env['WINEPREFIX'] = '/idkselfhost/Roblox/wine/.wine-rfd'
    if 'WINEDEBUG' not in env:
        env['WINEDEBUG'] = '-all'
        
    return subprocess.check_output(['winepath', '-w', path], env=env, text=True).strip()

@functools.cache
def get_rfd_top_dir() -> str:
    # PATCH: Отвязка от __file__ для совместимости с read-only /nix/store.
    # Используем RFD_DATA_DIR, которую передаст rbxdserver, либо текущую папку.
    return os.environ.get('RFD_DATA_DIR', os.getcwd())

@functools.cache
def get_code_dir() -> str:
    # ПАТЧ: Статический корень проекта (папка rfd-fork, содержащая Source/ и Roblox/)
    # Относительно этого пути безопасно читать неизменяемые файлы (например, Roblox)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    match (MADE_WITH_PYINSTALLER, d):
        case (_, dir_type.RŌBLOX):
            # ПАТЧ: Roblox всегда ищется в папке исходного кода, а не в RFD_DATA_DIR.
            # Это исключает лишнее повторное скачивание Roblox сервером.
            return [get_code_dir(), 'Roblox']
        case (True, dir_type.MISC):
            return [get_rfd_top_dir()]
        case (False, dir_type.MISC):
            return [get_rfd_top_dir()]
        case (True, dir_type.WORKING_DIR):
            return [os.getcwd()]
        case (False, dir_type.WORKING_DIR):
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