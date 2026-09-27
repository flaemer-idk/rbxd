# Standard library imports
import json
import re

# Local application imports
import util.auth
from web_server._logic import web_server_handler, server_path


'''
Studio-facing web surface.

Аутентификации нет: личность Studio задаётся флагом `-u`/`--user_code`
(см. `util/auth.py`). Эндпойнты логина оставлены, потому что Studio
обращается к ним сама, но любая учётка просто отображается на
пользователя из `-u`. Прозрачный «автологин» — это само отсутствие
авторизации: все identity-эндпойнты отвечают личностью без всяких кук.
'''


def _send_auth_error(
    self: web_server_handler,
    message: str,
    status: int = 401,
) -> None:
    # `errors[]` — формат современных Roblox API; `message` — плоский формат,
    # который понимают старые парсеры Studio. Отдаём оба.
    self.send_json({
        'errors': [{'code': 0, 'message': message}],
        'message': message,
    }, status)


def _send_login_success(self: web_server_handler) -> None:
    '''
    Успешный логин/сигнап: вложенный объект `user` (формат POST /v2/login
    auth.roblox.com). Плоский `{userId, username, ...}` Studio 2021E не
    парсит — в её логах это `StudioLogin.End.Failure.LoginParse`.
    '''
    record = util.auth.get_current_studio_user(self)
    identity = util.auth.get_studio_player_identity(self)
    if identity is not None:
        (user_id, username) = identity
    elif record is not None:
        # user_code не разрешён конфигом плейса — личность не создалась.
        (user_id, username) = (0, record['user_code'])
    else:
        (user_id, username) = (0, '')

    self.send_json({
        'user': {
            'id': user_id,
            'name': username,
            'displayName': username,
        },
        'isBanned': False,
        'isUnder13': False,
    })


@server_path('/v2/login', commands={'POST', 'GET'})
def _(self: web_server_handler) -> bool:
    # Пароли не проверяем: пользователь один — из `-u`. Любая учётка
    # из диалога логина Studio отображается на него.
    _send_login_success(self)
    return True


@server_path('/v2/signup', commands={'POST', 'GET'})
def _(self: web_server_handler) -> bool:
    # Регистрация не нужна (пользователь задаётся флагом); отвечаем как
    # при логине, чтобы диалог Studio не падал.
    _send_login_success(self)
    return True


@server_path('/v1/users/authenticated')
def _(self: web_server_handler) -> bool:
    identity = util.auth.get_studio_player_identity(self)
    if identity is None:
        _send_auth_error(self, 'You are not logged in.')
        return True

    self.send_json({
        # `id: 0` Studio читает как «не залогинен» — отдаём реальный id.
        'id': identity[0],
        'name': identity[1],
        'displayName': identity[1],
    })
    return True


@server_path('/v2/logout', commands={'POST', 'GET'})
def _(self: web_server_handler) -> bool:
    self.send_json({})
    return True


@server_path('/game/GetCurrentUser.ashx')
def _(self: web_server_handler) -> bool:
    # Старый студийный flow (2016-2018): `/login/RequestAuth.ashx` возвращает
    # URL этого эндпойнта, и Studio POST-ит учётку прямо сюда (контракт
    # Epic.VIP). Успех = 200 + числовой id; учётку не проверяем.
    identity = util.auth.get_studio_player_identity(self)
    self.send_json(identity[0] if identity is not None else 0)
    return True


@server_path(r'/Users/(\d+)', regex=True)
def _(self: web_server_handler, match: re.Match[str]) -> bool:
    # Studio ожидает просто число (не JSON-модель пользователя).
    # Чужой id отдаём как есть — это может быть запрос об игроке плейса.
    self.send_json(int(match[1]))
    return True


@server_path('/users/account-info')
def _(self: web_server_handler) -> bool:
    # Студийный путь: сессия есть → отдаём реальные поля.
    identity = util.auth.get_studio_player_identity(self)
    if identity is not None:
        (user_id_num, username) = identity
        funds = self.server.storage.funds.check(user_id_num) or 0

        self.send_json({
            'UserId': user_id_num,
            'Username': username,
            'DisplayName': username,
            'HasPasswordSet': True,
            'Email': {'Value': 'n**@roblox.com', 'IsVerified': True},
            'AgeBracket': 0,
            'Roles': ['BetaTester', 'Beta17', 'Soothsayer'],
            'MembershipType': 0,
            'RobuxBalance': funds,
            'NotificationCount': 0,
            'EmailNotificationEnabled': False,
            'PasswordNotificationEnabled': False,
            'CountryCode': 'RU',
        })
        return True

    # Игровой путь (RCC-режим): userId приходит из `Roblox-Session-Id`.
    try:
        user_id_num = json.loads(self.headers['Roblox-Session-Id'])['UserId']
    except (KeyError, TypeError, json.JSONDecodeError):
        return True

    funds = self.server.storage.funds.check(user_id_num) or 0
    self.send_json({
        'Roles': ['Soothsayer', 'BetaTester'],
        'UserId': user_id_num,
        'RobuxBalance': funds,
    })
    return True


@server_path('/studio/e.png')
def _(self: web_server_handler) -> bool:
    self.send_data(b'')
    return True


@server_path('/login/RequestAuth.ashx')
def _(self: web_server_handler) -> bool:
    # Старый студийный flow: ответ — URL, на который Studio шлёт учётку
    # (контракт Epic.VIP: GetCurrentUser.ashx, НЕ negotiate).
    self.send_data(self.hostname + '/game/GetCurrentUser.ashx')
    return True


@server_path('/login/forgotPasswordOrUsername/')
def _(self: web_server_handler) -> bool:
    # Studio ссылается на эту страницу из диалога логина; паролей больше
    # нет — личность задаётся флагом `-u` при запуске студии.
    self.send_data(
        b'<html><body><h1>Passwords are not used.</h1>'
        b'<p>The Studio user is set with the `-u`/`--user_code` launch flag.</p></body></html>',
        headers={'content_type': 'text/html; charset=utf-8'},
    )
    return True


@server_path('/login/return-to-studio')
def _(self: web_server_handler) -> bool:
    self.send_redirect('/')
    return True
