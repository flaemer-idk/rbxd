# Standard library imports
import json
import re
import time

# Local application imports
import util.auth
from web_server._logic import web_server_handler, server_path


'''
Studio-facing web surface.

Раньше здесь лежал MOCK_DB с одним захардкоженным пользователем `'67'` и
паролем открытым текстом. Теперь auth настоящий (см. `util/auth.py`):
пользователи живут в `data/studio-users.toml`, пароли — sha256+salt,
кука `.ROBLOSECURITY` host-only.

Прозрачный авто-логин: в studio-режиме при отсутствии куки сервер молча
выдаёт сессию пользователя по умолчанию (`default_user` из TOML), поэтому
диалог логина в Studio не появляется вовсе.
'''


def _send_auth_error(
    self: web_server_handler,
    message: str,
    status: int = 401,
) -> None:
    self.send_json({'errors': [{'code': 0, 'message': message}]}, status)


def _read_json_payload(self: web_server_handler) -> dict:
    raw_content = self.read_content()
    if not raw_content:
        return {}
    try:
        payload = json.loads(raw_content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _extract_username(payload: dict) -> str:
    username = (
        payload.get('username') or
        payload.get('Username') or
        payload.get('cvalue') or
        payload.get('value')
    )
    return str(username).strip() if username is not None else ''


def _extract_password(payload: dict) -> str:
    password = payload.get('password') or payload.get('Password')
    return str(password) if password is not None else ''


@server_path('/v2/login', commands={'POST', 'GET'})
def _(self: web_server_handler) -> bool:
    payload = _read_json_payload(self)
    username = _extract_username(payload)
    password = _extract_password(payload)

    if not username or not password:
        _send_auth_error(self, 'Username and password are required.', 400)
        return True

    users = util.auth.get_users()
    record = users.get_by_username(username)

    if record is None or not users.verify_password(record, password):
        self.send_response(401)
        util.auth.clear_auth_cookie(self)
        _send_auth_error(self, 'Incorrect username or password.', status=None)
        return True

    identity = util.auth.get_studio_player_identity(self)
    user_id = identity[0] if identity is not None else 0

    token = record.get('token') or users.issue_token(username)
    headers = {'Set-Cookie': util.auth.make_cookie_header(token)} if token else None

    self.send_json({
        'membershipType': 4,
        'username': username,
        'isUnder13': False,
        'countryCode': 'US',
        'userId': user_id,
        'displayName': username,
    }, headers=headers)
    return True


@server_path('/v2/signup', commands={'POST', 'GET'})
def _(self: web_server_handler) -> bool:
    payload = _read_json_payload(self)
    username = _extract_username(payload)
    password = _extract_password(payload)

    if not username or not password:
        _send_auth_error(self, 'Username and password are required.', 400)
        return True

    users = util.auth.get_users()
    record = users.add_user(username, password)
    if record is None:
        _send_auth_error(self, 'Username is already in use.', 409)
        return True

    token = users.issue_token(username)
    headers = {'Set-Cookie': util.auth.make_cookie_header(token)} if token else None

    self.send_json({
        'membershipType': 4,
        'username': username,
        'isUnder13': False,
        'countryCode': 'US',
        'userId': 0,
        'displayName': username,
    }, headers=headers)
    return True


@server_path('/v1/users/authenticated')
def _(self: web_server_handler) -> bool:
    record = util.auth.get_current_studio_user(self)
    if record is None:
        _send_auth_error(self, 'You are not logged in.')
        return True

    self.send_json(
        {
            'id': 0,
            'name': record['user_code'],
            'displayName': record['user_code'],
        },
        headers=util.auth.studio_auth_headers(self),
    )
    return True


@server_path('/game/GetCurrentUser.ashx')
def _(self: web_server_handler) -> bool:
    # HACK: Studio 2021E, по всей видимости, не работает без этой задержки
    # (наследие мока — оставлено, чтобы не сломать рабочее поведение).
    time.sleep(2)

    identity = util.auth.get_studio_player_identity(self)
    self.send_json(
        identity[0] if identity is not None else 0,
        headers=util.auth.studio_auth_headers(self),
    )
    return True


@server_path(r'/Users/(\d+)', regex=True)
def _(self: web_server_handler, match: re.Match[str]) -> bool:
    # Раньше был статический `/Users/1630228` с захардкоженным id.
    # Отдаём тот жеbare id, что и раньше (Studio 2021E ожидает именно число),
    # но берем его из сессии, а не из константы.
    requested_id = int(match[1])
    identity = util.auth.get_studio_player_identity(self)
    auth_headers = util.auth.studio_auth_headers(self)

    # Чужой id: отдаём как есть — это может быть запрос об игроке плейса.
    # Свой id — тоже число (Studio 2021E ожидает именно число, не JSON-модель).
    self.send_json(requested_id, headers=auth_headers)
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
        }, headers=util.auth.studio_auth_headers(self))
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
    self.send_data(self.hostname + '/login/negotiate.ashx')
    return True


@server_path('/login/forgotPasswordOrUsername/')
def _(self: web_server_handler) -> bool:
    # Studio ссылается на эту страницу из диалога логина; локально
    # восстановление пароля не предусмотрено (файл правится руками).
    self.send_data(
        b'<html><body><h1>Password reset is not available.</h1>'
        b'<p>Edit data/studio-users.toml on the server.</p></body></html>',
        headers={'content_type': 'text/html; charset=utf-8'},
    )
    return True


@server_path('/login/return-to-studio')
def _(self: web_server_handler) -> bool:
    self.send_redirect('/')
    return True
