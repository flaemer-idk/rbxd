# AGENTS.md — rbxd (ядро RFD-форка)

> Форк [Roblox Freedom Distribution](https://github.com/Windows81/Roblox-Freedom-Distribution)
> (upstream v0.67.1, `util/const.py:GIT_RELEASE_VERSION`). Самописный «самохост» Roblox:
> Python подменяет весь онлайн-стек Roblox, а клиент/сервер/Studio — оригинальные бинарники
> под Wine/Proton. Заточено под NixOS (см. `shell.nix`).
> Связанные проекты `rbxdclient` (Vala GUI) и `rbxdserver` (Go) дёргают этот код как
> дочерний процесс — подробности в корневом `../AGENTS.md`.

## Как это вообще работает (mental model)

1. Точка входа одна: `python3 Source/_main.py <mode> [flags]`.
2. `launcher/` парсит CLI и собирает **список аргументов** — каждый элемент этого списка
   соответствует одному **routine entry**.
3. `routines.routine(*arg_list)` в цикле вызывает у каждого entry `process()` (синхронно,
   по порядку) — это «раскочегаривает» вебсервер, RCC, клиента и т.д.
4. Дальше `routine.wait()` — каждый entry джоинит свои потоки.
5. Вебсервер (порт 2005, HTTPS) отдаёт ~114 фейковых эндпойнтов api.roblox.com; клиентский
   `AppSettings.xml` указывает на него `BaseUrl`.

## Глоссарий (термины RFD, без которых ничего не понять)

- **RCC / RCCService.exe** — «Rōblox Cloud Compute», настоящий game-сервер Roblox. Его и имеют в
  виду, когда говорят «сервер запущен». Слушает UDP `rcc_port`, ходит в вебсервер по HTTPS.
- **Вебсервер** — Python-`http.server` на `web_port` (дефолт 2005), который притворяется
  api.roblox.com. Не путать с RCC: вебсервер — это «обвязка», RCC — сама игра.
- **place / плейс** — игровой мир: `.rbxl`/`.rbxlx` файл + `GameConfig.toml` рядом.
- **place_iden** — числовой идентификатор плейса в терминах Roblox (дефолт `1818`,
  `util/const.py:PLACE_IDEN_CONST`). Под таким ID плейс ложится в AssetCache и раздаётся через `/asset/?id=`.
- **user_code** — строка-«пользователь» (не имя!). Из неё через хуки конфига получаются
  username, user_id, аватар, группы и т.д. Передаётся в `-j` URL и в `/game/join.ashx`.
- **backend** — чем запускаются exe'шники: `wine` / `proton` (umu-run) / `windows` (нативно).
- **fvars / FFlags** — fast-flags Roblox, JSON в `ClientSettings/*.json`. Эндпойнт `/Setting/QuietGet/…`.
- **bin_subtype** — `Player` / `Server` / `Studio`; определяет подтри каталога `Source/Roblox/<version>/`.
- **AssetCache** — локальный склад ассетов (`game_setup.asset_cache.dir_path`, по умолчанию `./AssetCache`);
  файлы именуются по шаблону `name_template` (дефолт — 11-значный номер).
- **persistence** — sqlite-база (`game_setup.persistence.sqlite_path`, дефолт `_.sqlite`) с таблицами
  players/persistence/badges/funds/gamepasses/devproducts.
- **entry / routine** — единица исполнения и их группа (см. иерархию ниже).
- **cage** — headless Wayland-композитор, в который на Linux заворачивается RCC, чтобы
  виндовый код мог «нарисовать» окно в headless-окружении.

## Полный CLI-контракт (все моды и флаги)

Моды (`launch_mode`): `server` · `webserver` · `studio` · `player` · `serialise` · `download` · `test` · `cookie`.
Общие флаги для `server`/`player`/`studio` (из `args_aux/`):

| Флаг | Значение |
|---|---|
| `--backend {wine,proton,windows}` | **обязателен не на Windows**; дефолт `windows` только на win32 |
| `--proton-path` | путь к сборке Proton (только для `proton`) |
| `--wine-path` | свой `wine` (только для `wine`) |
| `--wine-prefix` | WINEPREFIX (только для `wine`) |
| `--clear_temp_cache` | очистить кэш перед запуском |
| `--skip_download` | не пытаться докачать бинарники |
| `--debug` / `--debug_all` | подключить x96dbg к процессу(ам) |

Мод `server` (`args_launch_mode/server.py`):

| Флаг | Дефолт | Заметка |
|---|---|---|
| `--config_path`/`--config`/`-cp` | `./GameConfig.toml` | `nargs=*` — **можно несколько конфигов** |
| `--place_path`/`--place`/`-pl` | — | mutex с `-cp`; тогда конфиг генерится из place-файла |
| `--ipv4_only`/`--ipv4-only`, `--ipv6_only`/`--ipv6-only` | оба off | иначе вебсервер слушает и v4, и v6 |
| `--rcc_port`/`--port`/`-rp` | авто | `nargs=*`; незаполненные порты добиваются подряд от 2004 |
| `--web_port`/`--webserver_port`/`-wp`/`-p` | 2005 | то же |
| `--run_client`/`-rc`/`--run_player` | off | поднять ещё и клиента (`launch_delay=3`) |
| `--user_code`/`-u` | None | user_code для этого клиента |
| `--skip_rcc` / `--skip_web` | off | mutex: запустить только вебсервер / только RCC |
| `--quiet`/`-q`, `--loud` | — | громкость логов (взаимоисключающие) |
| `--no_colour`/`--no_color` | off | убрать ANSI |
| `--rcc_log_options`/`--rcc_log`/`-log` | None | фильтр FLog-типов RCC (`choices=LOG_LEVEL_LIST`) |

Мод `webserver` (`args_launch_mode/webserver.py`) — вебсервер без RCC и вообще без
бинарников (Wine/Proton/`--backend` не нужны и не принимаются):

| Флаг | Дефолт | Заметка |
|---|---|---|
| `--config_path`/`--config`/`-cp` | — | `nargs=*`; **без него** генерится CDN-конфиг (см. ниже) |
| `--ipv4_only`/`--ipv4-only`, `--ipv6_only`/`--ipv6-only` | оба off | иначе слушает и v4, и v6 |
| `--web_port`/`--webserver_port`/`-wp`/`-p` | 2005 | `nargs=*`, добиваются подряд |
| `--quiet`/`-q`, `--loud`, `--no_colour` | — | как у `server` |

Два применения:
- **с `--config`** — веб одного плейса (то же, что `server --skip_rcc`): так rbxdserver
  поднимает веб-часть сессии отдельным процессом, а RCC цепляется к нему через
  `server --skip_web`;
- **без `--config`** — CDN-инстанс: синтетический конфиг `generate_cdn_config()`
  (`game_config/__init__.py`), версия v347, общий пул `data/Assets`, состояние в
  `data/CDN/` (AssetCache + sqlite). Плейс-файла нет, `rbxl_uri` не извлекается.
  Это «раздача ассетов/скинов/превью» для внешних клиентов (rbxdclient).

Остальные моды: `player` — `--rcc_host`/`--host`/`-rh`, `--rcc_port`/`-rp`, `--web_host`/`-wh`/`-h`,
`--web_port`/`-wp`/`-p`, `--user_code`/`-u`, `--quiet`, `--loud`. `studio` — `--config`/`-cp`,
`--place`/`-pl`, `--web_port`/`-wp`/`-p`, `--quiet`, `--skip_web`, `--skip_studio`.
`download` — `--rbx_version`/`-v`, `--bin_subtype`/`-b`.
`serialise` — `--load`/`--read`/`-r`, `--save`/`--write`/`-w`, `--method`/`-m`.
`test` — позиционный `tests_to_run` (`nargs=*`, дефолт `tester.DEFAULT_TEST_NAMES`).
`cookie` — `--verbose`/`--show`/`-v`.

**Несколько конфигов в одном процессе**: `server.py` итерирует `zip_longest(web_port_gen,
rcc_port_gen, game_configs)` — на каждый плейс поднимается свой вебсервер (v4+v6) и свой RCC.
То есть «один rbxd-процесс = N независимых плейсов на разных портах» — это умеет из коробки.

## Dataflow: как игрок подключается к плейсу

```
[player режим]                     [server режим]
  RobloxPlayerBeta.exe               RCCService.exe
       │  -a /login/negotiate.ashx      │  PlaceFetchUrl = <web>/asset/?id=<place_iden>
       │  -j /game/PlaceLauncher.ashx?  │  MachineAddress = <web_host> (НЕ rcc!)
       │      MachineAddress=<rcc_host> │
       │      ServerPort=<rcc_port>     │
       ▼                               ▼
  ┌─────────────────────────────────────────────┐
  │  вебсервер (HTTPS, web_port)                  │
  │  negotiate → создаёт сессию                   │
  │  PlaceLauncher → join.ashx:                   │
  │     init_player(user_code) → sqlite players   │
  │     отдаёт MachineAddress/ServerPort/UserId   │
  │  RCC дальше сам дёргает avatar-fetch,         │
  │  asset/?id=, persistence, marketplace…        │
  └─────────────────────────────────────────────┘
```

Важно: **RCC обращается к вебсерверу, а не клиент к RCC напрямую по TCP** — клиент только
узнаёт у вебсервера адрес/порт RCC, а потом соединяется с RCC по UDP.

## Что rbxd пишет на диск (результат работы)

- `Source/Roblox/<ver>/{Player,Server,Studio}/AppSettings.xml` — `BaseUrl` на вебсервер
  (переписывается при каждом старте, `save_app_settings`).
- `Source/Roblox/<ver>/Server/GameServer.json` — конфиг RCC: `PlaceId`, `MachineAddress`,
  `PreferredPort`, `PlaceFetchUrl` (генерится в `rcc.save_gameserver`).
- `Source/Roblox/<ver>/{Server,Player}/ClientSettings/*.json` — FFlags (`update_fvars`,
  в т.ч. принудительный Vulkan/D3D11 для клиента).
- `…/Content/Scripts/CoreScripts/RFDStarterScript.lua` — стартовый скрипт (`startup_scripts`).
- `AssetCache/` — place-файл (парсится через `serialisers`), иконка, ассеты.
- `_.sqlite` — персистентность игры.
- `logs/`, `LocalStorage/`, `InstalledPlugins/`, `placeIDEState/`, `ClientSettings/` (корневые,
  для Studio) — создаются через `DIRS_TO_ADD`.

## Подводные камни (gotchas)

- `-h` — это `--rcc_host`, а не помощь! Помощь — `--help` или `-?` (намеренно, см. `launcher/__init__.py`).
- `functools.cache` на `get_cached_config` / `read_file_data` / `retr_version` — конфиг и
  бинарные данные кэшируются на процесс; «перечитать GameConfig» без рестарта не выйдет.
- Датаclass'ы с `unsafe_hash=True` + `functools.cache` на методах —_entry'ы кладутся в `set()`
  в `server.py`, поэтому хеш есть; не добавляйте мутабельных полей в хешируемую часть.
- `popen_entry.stop()` терминит только `popen_mains`; дочерние процессы Wine могут выжить —
  поэтому наружные запускатели (rbxdserver) добивают их сами по pgid.
- `bin_entry` для IPv6-адреса превращает его в IPv4-mapped вид (`[…85.195.213.22]`), потому что
  CoreScripts Roblox не любят BaseUrl без точек (`player.__post_init__`).
- Вебсервер всегда HTTPS; клиенты доверяют любому сертификату (`get_none_ssl`,
  `ssl._create_unverified_context`). Исключение — Studio под Wine: её schannel
  валидирует цепочку, поэтому CA из `<data>/ssl/ca.pem` вшивается в префикс
  (`scripts/install_ca_to_wineprefix.py`, детали — INTEGRATION.md §12).
  Сертификат **стабильный**: кеш `<data>/ssl/` (`ca.pem`/`server.pem`/`server.key`),
  `RFD_EPHEMERAL_SSL=1` возвращает per-run генерацию.
- `is_privileged` = loopback-адрес пира; `/rfd/data-transfer` работает только с локального RCC.

## Контракт для rbxdclient / rbxdserver (если их переписывать)

Подробно — в **`INTEGRATION.md`** рядом: точные команды запуска, семантика портов и
готовности, `/rfd/*`-эндпойнты, что именно стоит выбросить при реврайте (pgrep/pkill,
хак `path == ""`, TOCTOU портов). Готовность вебсервера — HTTP `GET /rfd/status`
(JSON: версия rbxd, roblox_version, place_iden, server_mode, uptime) или `GET /`;
готовность RCC — строка `RFD_RCC_READY` в его stdout (печатаётся при
`LogAction.READY`, т.е. `Finished initializing game`; порт RCC — UDP, TCP-поллинг
не работает).

## Структура (что где искать)

```
Source/_main.py                     — вход; ставит WINEDEBUG=-all; launcher.read_eval_loop()
Source/launcher/__init__.py         — read_eval_loop / perform_with_args; REPE-цикл, если аргументов нет
Source/launcher/subparsers/_logic.py — launch_mode enum + реестр add_args/serialise_args
Source/launcher/subparsers/args_launch_mode/<mode>.py — флаги и сериализация для server/webserver/player/studio/…
Source/launcher/subparsers/args_aux/*.py             — общие флаги: backend, download, clear_cache, debug
Source/routines/_logic.py           — ИЕРАРХИЯ entry-классов (см. ниже) — самое важное
Source/routines/web.py              — HTTP-сервер (ThreadingHTTPServer + trustme-сертификат)
Source/routines/rcc/__init__.py     — RCCService.exe (game-сервер), GameServer.json, парсинг stdout
Source/routines/rcc/log_action.py   — RESTART/TERMINATE/READY по выводу RCC
Source/routines/rcc/startup_scripts.py — генерация RFDStarterScript.lua
Source/routines/player/__init__.py  — RobloxPlayerBeta.exe, PlaceLauncher.ashx, user_code
Source/routines/studio/__init__.py  — RobloxStudioBeta.exe, -localPlaceFile
Source/routines/cookie.py           — показать .ROBLOSECURITY-куку
Source/web_server/_logic.py         — http.server-подобие: server_path-реестр, маршрутизация
Source/web_server/endpoints/*.py    — 21 модулей, ~120 роутов «api.roblox.com»
Source/game_config/{__init__,structure}.py — GameConfig.toml: схема + парсинг (TOML/JSON)
Source/config_type/                 — типы конфига: wrappers (uri_obj, path_str, counter), structs
Source/assets/                      — кэш ассетов + сериализаторы (rbxl/rbxlx/mesh/csg/video/thumbnail)
Source/storage/                     — sqlite-персистентность: players, persistence, badges, funds, gamepasses, devproducts
Source/pretasks/{download,clear_cache}.py — авто-скачивание бинарников, чистка кэша
Source/util/{const,resource,versions}.py  — константы, разрешение путей, маппинг версий
Source/vendored/                    — tqdm + sqlite_worker (встроены намертво с 0.66.5)
Source/tester/test_*.py             — тесты (pytest-стиль): asset, logger, serialise, server
Source/ssl/                       — старые статические сертификаты, КОДОМ НЕ ИСПОЛЬЗУЮТСЯ
                                    (рабочий кеш — <rbxd>/data/ssl/, см. gotchas; это ручной бэкап)
scripts/install_ca_to_wineprefix.py — CA вебсервера → реестр wine-префикса (Studio v463)
Source/Roblox/v347/, v463/          — ~1 ГБ БИНАРНИКОВ, В .gitignore (см. pretasks/download.py)
```

**Скины (форковая фича!)** — `web_server/endpoints/avatar.py:get_avatar` читает аватар из
`data/skins/<user_code>.json` (корень данных фиксирован: `<rbxd>/data`, не cwd), а не
только через конфиг-хук `retrieve_avatar`. При первом заходе
`data/skins/default.json` копируется в `data/skins/<ник>.json`; формат —
`{"type":"R6"|"R15","items":[id|URL…],"bundles":[678|URL…],"scales","colors"}`:
и `items`, и `bundles` принимают **ссылки с сайта целиком** (`roblox.com/catalog/<id>/Name`,
`roblox.com/bundles/<id>/Name`) — id вытаскивается сам. Тип ассета спрашивается
у каталога (`catalog.roblox.com/v1/catalog/items/<id>/details`) и кэшируется в
глобальном `data/catalog.sqlite` (`catalog_cache.py`; таблицы `bundles`/`assets`,
TTL 30 дней, при ошибке сети — устаревший кэш). Раскладка по `assetType`:
анимации (48–55) → `animationAssetIds`, части тела (17/27–31) → типизированные
элементы `assetAndAssetTypeIds` (v463, замена конечностей R15), аксессуары →
`accessoryVersionIds` (v347). Скин читается **на каждый запрос** — смена скина
работает вживую. Подробности — `../rbxdserver/DESIGN.md` (раздел «Скины»).

**Toolbox (форковая фича!)** — локальная библиотека ассетов для Studio:
`data/Toolbox/<Категория>/<имя>.rbxm` (+ опционально `<имя>.png` с тем же
именем — превью). Категории — подпапки Models, Meshes, Images, AudioVideo
(создаются сами при первом скане; посторонние подпапки тоже сканируются).
Сканер `assets/toolbox.py` пересканирует папку на каждый запрос — без кэшей
и лимитов, файлы можно менять на лету. id локальных ассетов — от
`90_000_000_000_000` (14 цифр, с реальными ассетами Roblox не пересекаются),
считается хешем от «категория/имя», так что добавление файлов не сдвигает
id старых. Эндпойнты `web_server/endpoints/toolbox.py`:
`/ide/toolbox/items` — формат старого веб-тулбокса `{TotalResults, Results}`
(параметры num/page/keyword/category сняты с живого лога студии; алиасы
FreeModels/FreeDecals/FreeAudio мапятся на папки), `/model-thumbnails` —
png рядом или серая заглушка, `/ide/clienttoolbox` — страница тулбокса для
встроенного браузера студии: вкладки, поиск, пагинация, вставка через
`window.external.Insert/StartDrag` (мост, как у ревайвлов). `/asset/?id=`
и `marketplace/productinfo` понимают локальные id: хук в
`assets/__init__.py:get_asset` + ветка в `endpoints/marketplace.py`.

**Личность Studio (форковая фича!)** — без аутентификации: пользователь
задаётся флагом `-u`/`--user_code` при запуске студии
(`python3 _main.py studio -u flaemer`), тем же механизмом, что и у плеера.
Без флага user_code разрешается один раз за сессию через хук
`server_core.retrieve_default_user_code()` (тот же, что отдаёт
`/rfd/default-user-code` плееру). `util/auth.py` — только разрешение
`user_code → (id, username)` через `join_data.init_player`; пароли, база
`studio-users.toml`, токены и куки `.ROBLOSECURITY` убраны. В игровом
(RCC) режиме студийной личности нет. Эндпойнты логина
(`endpoints/studio.py`) остались ради Studio, но любую учётку отображают
на пользователя из `-u`.

Корневые файлы: `CHANGELOG.md` (апстримный), `shell.nix` (NixOS dev-shell: umu-launcher,
wineWow64, cage, winetricks), `pyrightconfig.json` (include: `Source`), `.python-version` (3.13),
`repomix.config.json` + `.repomixignore` (упаковка кода для скармливания LLM —
это и есть «подготовка карты для агентов»), `skins/*.json` + `skinpacksidk` (скинпаки аватаров,
в git не идут), `settings.json` (конфиг редактора Zed, untracked).

## Иерархия entry-классов — ядро логики (`routines/_logic.py`)

```
base_entry            — threads[], wait()/stop()/kill(); two-stage: process() (sync) → wait() (async)
├── popen_entry       — subprocess-обёртка. init_popen() собирает команду по backend:
│                        windows → прямой запуск exe
│                        proton  → umu-run (PROTONPATH, UMU_RUNTIME_UPDATE=0)
│                        wine    → wine (WINEPREFIX, выкидывает STEAM_COMPAT_*)
│                        + на Linux для SERVER оборачивается в `cage --` (headless),
│                          если не задано env RFD_NO_CAGE=1
├── loggable_entry    — ссылка на logger
├── bin_entry(popen_entry, loggable_entry)
│   — версионный бинарь: save_app_settings() (AppSettings.xml → BaseUrl),
│     make_aux_directories(), update_fvars() (ClientAppSettings.json),
│     get_versioned_path() → Source/Roblox/<version>/<Player|Server|Studio>/…
└── gameconfig_entry  — держит game_config (объект GameConfig.toml)

Конкретные entry:
  routines/web.obj_type     (bin? нет — чистый HTTP-сервер, server_mode RCC|STUDIO)
  routines/rcc.obj_type     (bin_entry: RCCService.exe, BIN_SUBTYPE=Server)
  routines/player.obj_type  (bin_entry: RobloxPlayerBeta.exe, BIN_SUBTYPE=Player)
  routines/studio.obj_type  (bin_entry: RobloxStudioBeta.exe, BIN_SUBTYPE=Studio)
```

Важные сайд-эффекты: `popen_entry.restart()` убивает процесс и снова зовёт `bootstrap()`;
`rcc` перезапускается сам при изменении place-файла (`maybe_track_file_changes`, опрос
mtime каждую секунду) или по сигналу из stdout (`log_action`).

## Запуск

```sh
# Сервер + вебсервер + клиент на этой машине (то, что делает rbxdclient в local-режиме):
python3 Source/_main.py server --config <place>/GameConfig.toml \
  --port <rcc_port> --web_port <port> --ipv4-only --backend proton --run_client

# То же через wine (то, что делает rbxdserver):
python3 Source/_main.py server --config … --backend wine --wine-prefix <prefix>

# Ключевые моды: server | webserver | player | studio | download | serialise | test | cookie
# Общие флаги:  --backend {wine,proton,windows}  --proton-path  --wine-path  --wine-prefix
python3 Source/_main.py server --help   # помощь (здесь это -? / --help; -h занят под --rcc_host)

# Веб-часть сессии плейса отдельным процессом (RCC потом цепляется через --skip_web):
python3 Source/_main.py webserver --config <place>/GameConfig.toml --web_port <port> --ipv4-only

# Постоянный CDN-веб (ассеты/скины/превью для клиентов, без плейса и Wine):
python3 Source/_main.py webserver --ipv4-only --web_port 8090
```

Константы (`util/const.py`): `RFD_DEFAULT_PORT = 2005`, `PLACE_IDEN_CONST = 1818`,
`THUMBNAIL_ID_CONST = 'rfd-thumbnail'`, версии форка (`GIT_RELEASE_VERSION = 0.67.1`,
`ZIPPED_RELEASE_VERSION = 0.67.2-binaries`).

## Конфиг плейса: GameConfig.toml

Схема — в `Source/game_config/structure.py` (дефолты прописаны прямо в аннотациях).
Главные секции:

- `game_setup.roblox_version` — `v347` (2018M/2018/v348) или `v463` (2021E/2021);
  парсится в `util/versions.py:VERSION_MAP` (принимает и алиасы).
- `game_setup.asset_cache` — `dir_path`, `name_template`, `clear_on_start`.
- `game_setup.persistence` — `sqlite_path`, `clear_on_start`.
- `server_core.place_file` — `rbxl_uri` (локальный путь или http(s)://), `enable_saveplace`,
  `track_file_changes` (авторестарт RCC при mtime-изменении).
- `server_core.metadata` — `title`, `description`, `creator_name`, `icon_uri`.
- `server_core.*` — **callable-хуки** (в TOML можно вписать Python-выражение!):
  `check_user_allowed`, `check_user_has_admin`, `retrieve_username`, `retrieve_user_id`,
  `retrieve_avatar`, `retrieve_groups`, `retrieve_default_funds`, `filter_text`,
  `retrieve_membership_type`, `retrieve_default_user_code`.
- `remote_data` — `gamepasses`, `devproducts`, `badges`, `asset_redirects`.

Парсер принимает TOML и JSON; `get_cached_config(path)` — закэширован (`functools.cache`);
`generate_config(rbxl_file)` — скелет конфига из одного place-файла.

## Вебсервер: как добавляются эндпойнты

Декоратор `@web_server._logic.server_path(path, regex=False, versions=ALL, commands={'POST','GET'})`
регистрирует функцию в глобальном `SERVER_FUNCS` с ключом `(mode, version, path, command)`.
Маршрутизация: сначала точное совпадение (`__open_from_static`), потом regex по всем
(`__open_from_regex`). Версия берётся из `game_config.game_setup.roblox_version`.
`is_privileged` = запрос пришёл с loopback (не доверять Host-заголовку).
SSL-сертификат — self-signed через `trustme` в рантайме (`web_server_ssl.get_context`).

Модули эндпойнтов: `assets, avatar, badges, data_transfer, funds, groups, join_data,
marketplace, persistence, player_info, save_place, setup_player, text_filter, studio,
misc, fvars` (+ `__init__` с корнем `/`). Самые жирные: `fvars.py` (1150 строк — FFlag'и),
`misc.py`, `marketplace.py`, `avatar.py`.

## Форк vs апстрим — что менялось (коммиты `aab3a2c9`, `12841efe`, `765ceae2`, `a27dbbcc`, `6021960d`, `0ea6b466`, `a101a32b`)

- **Backend-система**: `--backend {wine,proton,windows}` (`args_aux/backend.py`) +
  переписанный `popen_entry.init_popen` — это основное отличие от апстрима.
- **cage**: сервер на Linux запускается в headless-композиторе; отрубается `RFD_NO_CAGE=1`
  (это нужно GUI-клиенту и Go-серверу, чтобы видеть окно/логи).
- `README.md` заменён на заметки форка (апстримный README на 1057 строк удалён в `a27dbbcc`).
- Сильно перелопачены: `endpoints/avatar.py` (455 строк diff), `endpoints/misc.py` (487),
  `endpoints/studio.py` (274), `routines/studio/`, `endpoints/fvars.py` (выкинуто 485 строк),
  `endpoints/setup_rcc.py` (удалён в `6021960d`, возвращён в `12841efe` — сейчас жив).
- Удалено `.VSCodeCounter/`, добавлены `repomix.config.json`, `shell.nix`, `skins/`.
- В `git status` висят непроверженные правки: `assets/extractor.py`,
  `launcher/.../studio.py`, `pretasks/download.py`, `util/const.py` + untracked
  `settings.json`, `skinpacksidk`.

## Договорённости для агентов

- **Не трогай `Source/Roblox/**`** — это гигабайт бинарников, они в `.gitignore` и
  докачиваются `pretasks/download.py` с GitHub-релизов апстрима. Коммитить их нельзя.
- `Source/vendored/*` — замороженные копии зависимостей; не обновлять без причины
  (апстрим намеренно встроил их в 0.66.5).
- Разрешение путей — только через `util.resource`: `get_code_dir()` (где лежит `Source`),
  `get_rfd_top_dir()` = env `RFD_DATA_DIR` или cwd; Roblox-пути —
  `retr_rōblox_full_path(version, bin_subtype, …)`. Не хардкодить пути.
- Версии Roblox — только через `util.versions.rōblox` (`from_name` принимает алиасы).
  Добавление новой версии = правка enum'а **и** скачивание бинарников.
- Регэксп/маппинг `roblox_version` дублирован в `rbxdserver` (Go) и `rbxdclient` (Vala) —
  список плейсов строится **офлайн**, поэтому `GET /rfd/roblox-version` (только активный
  плейс) дубль не заменяет. Менять синхронно.
- Изменение схемы GameConfig → править `structure.py` (там же дефолты), клиентский парсер
  тоже читает этот TOML.
- Типизация: pyright настроен на `Source` (многие проверки приглушены в
  `pyrightconfig.json`); стиль — dataclasses с `kw_only=True, unsafe_hash=True`,
  `typing.override`. Python 3.13.
- Тесты в `Source/tester/` — запускать перед правками сериализаторов/ассетов.
- Документация — русский, ASCII-схемы, коротко. Этот файл — карта для следующего агента:
  обновляй его (а не только код), если структура проекта меняется.
