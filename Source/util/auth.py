# Standard library imports
from typing import TypedDict

# Local application imports
from web_server import _logic as web_server_logic

'''
Личность Studio.

Аутентификации как таковой нет: студийный пользователь задаётся флагом
`-u`/`--user_code` при запуске (`python3 _main.py studio -u flaemer`),
ровно так же, как у игрового плеера. Решение пользователя — база
`data/studio-users.toml`, пароли, токены и куки убраны как лишний оверхед
для LAN-проекта.

`user_code` связывает студийную сессию с игровой личностью: тот же
id_number/username/скин, что и при заходе в плейс игроком
(см. `web_server/endpoints/join_data.py:init_player`).

Если флаг не передан, вебсервер один раз за сессию разрешает user_code
через хук `server_core.retrieve_default_user_code()` — тот же механизм,
что выдаёт user_code плееру через `/rfd/default-user-code`.

Игровой клиент (join.ashx / user_code / RCC) этой подсистемой не трогается:
в игровом (RCC) режиме студийной личности нет вовсе.
'''


class studio_user(TypedDict):
    '''
    Сессия пользователя Studio. `user_code` — ключ игровой личности.
    '''
    user_code: str


def is_studio_mode(
    self: web_server_logic.web_server_handler,
) -> bool:
    '''
    Студийная личность существует только у вебсервера, поднятого в
    studio-режиме (`python3 _main.py studio ...`). В игровом (RCC)
    режиме auth отсутствует.
    '''
    return getattr(self.server, 'server_mode', None) == \
        web_server_logic.server_mode.STUDIO


def get_current_studio_user(
    self: web_server_logic.web_server_handler,
) -> studio_user | None:
    '''
    Пользователь Studio для этого запроса: user_code с флага `-u`
    (или дефолт из хука, разрешённый при старте вебсервера).
    '''
    if not is_studio_mode(self):
        return None
    user_code = getattr(self.server, 'user_code', None)
    if not user_code:
        return None
    return studio_user(user_code=str(user_code))


def get_studio_player_identity(
    self: web_server_logic.web_server_handler,
) -> tuple[int, str] | None:
    '''
    Связывает студийного пользователя с игровой личностью: тот же user_code
    → тот же id_number/username, что и при заходе в плейс игроком.

    Возвращает None вне studio-режима или если пользователь не разрешён
    конфигом плейса.
    '''
    from web_server.endpoints import join_data

    record = get_current_studio_user(self)
    if record is None:
        return None
    return join_data.init_player(self.game_config, record['user_code'])
