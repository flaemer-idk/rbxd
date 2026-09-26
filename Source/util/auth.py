# Standard library imports
import hashlib
import os
import secrets
import threading
import tomllib
from http.cookies import SimpleCookie
from typing import Any, TypedDict

# Local application imports
import util.resource
from web_server import _logic as web_server_logic

'''
Studio-only authentication for rbxd.

Пользователи Studio (login/password) живут в TOML-файле
`<data>/studio-users.toml` рядом со скинами и каталогом:

    default_user = "flaemer"

    [users.flaemer]
    user_code = "flaemer"          # связка с игровой личностью (players/skins)
    salt = "<hex>"
    password_hash = "<hex>"        # sha256(salt + password)
    token = "<token>"              # бессрочный, генерируется один раз

Правила (согласно решению пользователя):
  * auth нужен ТОЛЬКО Studio. Игровой клиент (join.ashx / user_code) не трогается.
  * прозрачный авто-логин работает только когда вебсервер поднят в studio-режиме
    (`python3 _main.py studio ...`): при отсутствии куки сервер молча выдаёт
    сессию пользователя `default_user`. В игровом (RCC) режиме auth отсутсвует.
  * кука `.ROBLOSECURITY` выдаётся host-only (без атрибута Domain), поэтому она
    уходит только на наш вебсервер и никогда — на настоящий roblox.com. Реальная
    кука для скачивания ассетов лежит в `data/env.env` (см. `assets/extractor.py`)
    и этой подсистемой не затрагивается.
'''


STUDIO_USERS_FILE_NAME = 'studio-users.toml'
AUTH_COOKIE_NAME = '.ROBLOSECURITY'
SALT_NUM_BYTES = 16
TOKEN_NUM_BYTES = 36

# Атрибут на handler: токен, который нужно приклеить к ответу при прозрачном
# авто-логине (см. `studio_auth_headers`).
STUDIO_AUTOLOGIN_ATTR = '_studio_autologin_token'

# Кука host-only: не указываем Domain, чтобы она не уходила на настоящий roblox.com.
COOKIE_HEADER_ATTRIBUTES = {
    'path': '/',
    'httponly': True,
    'samesite': 'Lax',
}


class studio_user(TypedDict):
    '''
    Запись пользователя Studio.

    `user_code` связывает студийный аккаунт с игровой личностью: именно с этим
    user_code игрок зайдёт в плейс (тот же id_number/username/скин, что и у
    студийного пользователя при плейтесте).
    '''
    user_code: str
    salt: str
    password_hash: str
    token: str


class studio_users:
    '''
    Леничаемое чтение/запись TOML-базы пользователей Studio.

    Токены кэшируются в памяти на процесс; сама база читается с диска при
    первом обращении и перечитывается после каждой записи.
    '''

    def __init__(self) -> None:
        super().__init__()
        self.lock = threading.Lock()
        self.default_user: str | None = None
        self.users: dict[str, studio_user] = {}
        self.token_index: dict[str, str] = {}
        self.loaded = False
        self.load()

    # -- persisted storage ---------------------------------------------------

    def _file_path(self) -> str:
        return util.resource.retr_full_path(
            util.resource.dir_type.MISC,
            STUDIO_USERS_FILE_NAME,
        )

    def load(self) -> None:
        with self.lock:
            path = self._file_path()
            if not os.path.isfile(path):
                # Первоначальное заполнение: готовый пользователь flaemer/1234.
                self.default_user = 'flaemer'
                self.users = {
                    'flaemer': self._make_user_record(
                        username='flaemer',
                        password='1234',
                        user_code='flaemer',
                    ),
                }
                self._write_without_lock()
            else:
                with open(path, 'rb') as f:
                    raw = tomllib.load(f)
                self.default_user = raw.get('default_user')
                users_raw = raw.get('users', {})
                self.users = {
                    str(name): self._normalise_record(str(name), dict(record))
                    for name, record in users_raw.items()
                }
                if not self.users:
                    # Пустой файл без пользователей — возвращаем заглушку,
                    # иначе Studio вообще никто не сможет использовать.
                    self.users = {
                        'flaemer': self._make_user_record(
                            username='flaemer',
                            password='1234',
                            user_code='flaemer',
                        ),
                    }
                if self.default_user not in self.users:
                    self.default_user = next(iter(self.users))

            self.token_index = {
                record['token']: name
                for name, record in self.users.items()
                if record.get('token')
            }
            self.loaded = True

    def _write_without_lock(self) -> None:
        path = self._file_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = [
            '# Studio users: login/password for RobloxStudio.',
            '# Auth is Studio-only; the game client is unaffected.',
            '',
            f'default_user = "{self.default_user}"',
            '',
        ]
        for name, record in self.users.items():
            lines.append(f'[users.{name}]')
            lines.append(f'user_code = "{record["user_code"]}"')
            lines.append(f'salt = "{record["salt"]}"')
            lines.append(f'password_hash = "{record["password_hash"]}"')
            if record.get('token'):
                lines.append(f'token = "{record["token"]}"')
            lines.append('')

        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

    def save(self) -> None:
        with self.lock:
            self._write_without_lock()

    # -- record helpers ------------------------------------------------------

    def _hash_password(self, password: str, salt_hex: str) -> str:
        return hashlib.sha256(
            (salt_hex + password).encode('utf-8'),
        ).hexdigest()

    def _make_user_record(
        self,
        username: str,
        password: str,
        user_code: str | None = None,
    ) -> studio_user:
        salt_hex = secrets.token_hex(SALT_NUM_BYTES)
        return studio_user(
            user_code=user_code if user_code is not None else username,
            salt=salt_hex,
            password_hash=self._hash_password(password, salt_hex),
            token='',
        )

    def _normalise_record(self, name: str, raw: dict[str, Any]) -> studio_user:
        '''
        Дописывает недостающие поля (токен, user_code) к записи из файла.
        '''
        record = studio_user(
            user_code=str(raw.get('user_code', name)),
            salt=str(raw.get('salt', '')),
            password_hash=str(raw.get('password_hash', '')),
            token=str(raw.get('token', '') or ''),
        )
        return record

    # -- lookups -------------------------------------------------------------

    def get_by_username(self, username: str) -> studio_user | None:
        if not self.loaded:
            self.load()
        return self.users.get(username)

    def get_by_token(self, token: str | None) -> studio_user | None:
        if not token:
            return None
        if not self.loaded:
            self.load()
        name = self.token_index.get(token)
        if name is None:
            return None
        return self.users.get(name)

    def verify_password(self, record: studio_user, password: str) -> bool:
        if not record['salt'] or not record['password_hash']:
            return False
        expected = self._hash_password(password, record['salt'])
        # Compare in constant time to avoid leaking information.
        return secrets.compare_digest(expected, record['password_hash'])

    # -- mutations -----------------------------------------------------------

    def issue_token(self, username: str) -> str | None:
        '''
        Создаёт (или перевыдаёт) бессрочный токен иpersistит его в TOML.
        '''
        record = self.get_by_username(username)
        if record is None:
            return None

        with self.lock:
            old_token = record.get('token')
            if old_token:
                self.token_index.pop(old_token, None)
            token = secrets.token_urlsafe(TOKEN_NUM_BYTES)
            record['token'] = token
            self.token_index[token] = username
            self._write_without_lock()
        return token

    def add_user(
        self,
        username: str,
        password: str,
        user_code: str | None = None,
    ) -> studio_user | None:
        '''
        Регистрация нового пользователя Studio. Возвращает None, если имя занято.
        '''
        if not username:
            return None
        with self.lock:
            if not self.loaded:
                self.load()
            if username in self.users:
                return None
            record = self._make_user_record(
                username=username,
                password=password,
                user_code=user_code,
            )
            self.users[username] = record
            if self.default_user is None:
                self.default_user = username
            self._write_without_lock()
        return record

    def get_default_user(self) -> studio_user | None:
        if not self.loaded:
            self.load()
        if self.default_user is None:
            return None
        return self.users.get(self.default_user)


# Module-level singleton: one users-file per process (the `data` root is fixed).
_db: studio_users | None = None
_db_lock = threading.Lock()


def get_users() -> studio_users:
    global _db
    with _db_lock:
        if _db is None:
            _db = studio_users()
    return _db


# -- request-level helpers ---------------------------------------------------

def parse_cookies(header: str | None) -> dict[str, str]:
    if not header:
        return {}
    cookie = SimpleCookie()
    cookie.load(header)
    return {key: morsel.value for key, morsel in cookie.items()}


def get_request_token(
    self: web_server_logic.web_server_handler,
) -> str | None:
    '''
    Токен из куки `.ROBLOSECURITY` (host-only) либо из заголовка.
    '''
    cookies = parse_cookies(self.headers.get('Cookie'))
    token = cookies.get(AUTH_COOKIE_NAME)
    if token:
        return token

    for header_name in ('X-Roblosecurity', 'X-Robloxsecurity', 'Roblosecurity'):
        header_token = self.headers.get(header_name)
        if header_token:
            return header_token.strip()
    return None


def _build_cookie_header(
    name: str,
    value: str,
    max_age: int | None = None,
    expires: str | None = None,
) -> str:
    cookie = SimpleCookie()
    cookie[name] = value
    morsel = cookie[name]
    for key, val in COOKIE_HEADER_ATTRIBUTES.items():
        morsel[key] = val
    if max_age is not None:
        morsel['max-age'] = str(max_age)
    if expires is not None:
        morsel['expires'] = expires
    # No `Domain` attribute: the cookie stays host-only and never leaks to the
    # real roblox.com.
    return cookie.output(header='').strip()


def set_auth_cookie(
    self: web_server_logic.web_server_handler,
    token: str,
) -> None:
    '''
    Внимание: этот метод зовёт `send_header` и должен использоваться ТОЛЬКО
    после `send_response` (иначе кука окажется перед строкой статуса и ответ
    сломается). Для обычных эндпойнтов лучше `studio_auth_headers()`.
    '''
    self.send_header(
        'Set-Cookie',
        _build_cookie_header(AUTH_COOKIE_NAME, token),
    )


def make_cookie_header(token: str) -> str:
    return _build_cookie_header(AUTH_COOKIE_NAME, token)


def make_clear_cookie_header() -> str:
    '''
    Заголовок Set-Cookie, который снимает куку (для `/v2/logout`).
    Прокидывать через `send_json(..., headers=...)` — не через send_header!
    '''
    return _build_cookie_header(
        AUTH_COOKIE_NAME,
        '',
        max_age=0,
        expires='Thu, 01 Jan 1970 00:00:00 GMT',
    )


def studio_auth_headers(
    self: web_server_logic.web_server_handler,
) -> dict[str, str]:
    '''
    Заголовки для ответа, если этот запрос triggered transparent auto-login.
    Возвращать через `send_json(..., headers=...)`, чтобы кука ушла ПОСЛЕ
    строки статуса.
    '''
    token = getattr(self, STUDIO_AUTOLOGIN_ATTR, None)
    if not token:
        return {}
    return {'Set-Cookie': make_cookie_header(token)}


def clear_auth_cookie(
    self: web_server_logic.web_server_handler,
) -> None:
    self.send_header(
        'Set-Cookie',
        _build_cookie_header(
            AUTH_COOKIE_NAME,
            '',
            max_age=0,
            expires='Thu, 01 Jan 1970 00:00:00 GMT',
        ),
    )


def is_studio_mode(
    self: web_server_logic.web_server_handler,
) -> bool:
    '''
    Прозрачный авто-логин разрешён только для вебсервера в studio-режиме.
    В игровом (RCC) режиме вебсервер auth не использует вовсе.
    '''
    return getattr(self.server, 'server_mode', None) == \
        web_server_logic.server_mode.STUDIO


def get_current_studio_user(
    self: web_server_logic.web_server_handler,
) -> studio_user | None:
    '''
    Разрешает пользователя Studio по куке; при отсутствии — прозрачно
    логинит пользователя по умолчанию (только в studio-режиме).
    '''
    users = get_users()

    token = get_request_token(self)
    if token is not None:
        record = users.get_by_token(token)
        if record is not None:
            return record

    if not is_studio_mode(self):
        return None

    default_record = users.get_default_user()
    if default_record is None:
        return None

    # Выдаём бессрочный токен один раз; приклеиваем его к ответу через
    # `studio_auth_headers()`, чтобы кука ушла после строки статуса.
    if not default_record.get('token'):
        users.issue_token(users.default_user or '')
        default_record = users.get_default_user()
        if default_record is None:
            return None

    if default_record.get('token'):
        setattr(self, STUDIO_AUTOLOGIN_ATTR, default_record['token'])
    return default_record


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
