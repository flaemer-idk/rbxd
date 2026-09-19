# INTEGRATION.md — контракт rbxd для rbxdclient / rbxdserver

> Этот файл — **спецификация поверхности интеграции** ядра `rbxd`. Если `rbxdclient`
> (Vala) и `rbxdserver` (Go) переписываются с нуля, новое поколение должно сходиться
> с rbxd ровно по тому, что описано ниже. Всё, что не описано, можно не реализовывать.
> Кодовое правило: **rbxd ничего не знает о наружных запускателях** — он только принимает
> CLI и поднимает вебсервер + бинари. Вся логика «когда готово / кто играет / кого убить»
> сейчас живёт снаружи, и в этом главная причина костылей.

## 1. Окружение запуска (env)

| Переменная | Кому и зачем |
|---|---|
| `RFD_NO_CAGE=1` | **обязательно для наружных запускателей**. Отключает заворачивание Linux-сервера в `cage`. Иначе RCC живёт в отдельном headless-композиторе, его процесс невозможно найти/убить снаружи, а окно не видно. |
| `WINEDEBUG=-all` | `_main.py` ставит сам; не нужна, но и не мешает. |
| `WINEPREFIX` | для `--backend wine`; rbxdserver держит `<data-dir>/wine/.wine-rfd`. |
| `PROTONPATH`, `UMU_RUNTIME_UPDATE=0` | rbxd ставит сам для `--backend proton` (если не задано `--proton-path`). |
| `RFD_DATA_DIR` | переопределяет «корень данных» rbxd (`util.resource.get_rfd_top_dir`); по умолчанию = **cwd процесса**. Поэтому наружный код обязан запускать rbxd из того каталога, где должны лежать `AssetCache/`, `_.sqlite`, `logs/`. |

**cwd = точка монтирования состояния.** `AssetCache`, sqlite, `logs/`, `LocalStorage/`
создаются относительно cwd (или `RFD_DATA_DIR`). Это неявный контракт, который нынешний
rbxdclient нарушить не может, т.к. пишет `local_server.log` рядом — **не повторять**:
в реврайте надо явно задавать cwd через `subprocess(cwd=…)`.

## 2. Команды запуска (эталонные)

```sh
# A) Локальный плейс (то, что должен уметь новый rbxdclient):
python3 <rbxd>/Source/_main.py server \
  --config <place_dir>/GameConfig.toml \
  --web_port <P1> --port <P2> \
  --ipv4-only --backend proton \
  [--run_client --user_code <code>]
# затем, отдельным вызовом (режим игрока-альта или когда сервер уже где-то бежит):
python3 <rbxd>/Source/_main.py player --web_host <host> --web_port <P1> \
  --rcc_host <host> --rcc_port <P2> --user_code <code> --backend proton

# B) Удалённый плейс (то, что делает rbxdserver на серверной машине):
python3 <rbxd>/Source/_main.py server \
  --config <place_dir>/GameConfig.toml \
  --web_port <P1> --port <P2> \
  --ipv4-only --backend wine --wine-prefix <prefix>
```

Заметки:
- `--run_client` — это «запустить игрока в том же процессе». Наружный запускатель почти
  всегда хочет поднять player **отдельным** вызовом `player` — так его процесс видно и им
  можно управлять (текущий rbxdclient так и делает для альтов через `join`).
- `--ipv4-only` сейчас передаёт клиент, т.к. `localhost` резолвится в `::1` первым и
  v463-клиент ломается. Если поднимаете IPv6 — вебсервер слушает оба семейства одновременно
  (две независимые entry, см. `server.py`).
- Канонический порт вебсервера — `2005` (`RFD_DEFAULT_PORT`); `--port`/`-rp` — это
  **RCC-порт**, не вебсерверный. Путаница в названиях — историческая.

## 3. Порты и готовность

```
web_port  TCP+HTTPS  ← главный индикатор «сервер ожил». Поллить TCP-коннектом.
rcc_port  UDP        ← сам game-сервер. TCP-поллинг НЕ сработает (это UDP!).
```

- Порядок подъёма внутри rbxd: **сначала вебсервер, потом RCC** (entries идут в порядке
  `[*web_routine_args, *rcc_routine_args]`, обрабатываются последовательно).
- rbxd **не сообщает о готовности** никак, кроме «открылся TCP-порт вебсервера».
  Алгоритм наружного кода: `bind-free-port` → старт rbxd → TCP-полл `web_port` с таймаутом
  (~120–150 c, т.к. Wine/cage могут тупить) → `GET /` проверить, что отвечает → можно
  подключать player.
- `GET /` отвечает plain text: `Roblox Freedom Distribution webserver <ver> [<roblox_version>]`.
- Дополнительно клиент тормозит сам: `game_setup.ready_delay_sec` (GameConfig, дефолт 3) —
  этим можно заменить слипы в наружном коде.

**Типичный баг-источник:** rbxdclient отслеживает «игрок в игре» через `pgrep
RobloxPlayerBeta` (процесс-снапшот, обрывы, чужие сессии), а готовность сервера —
TCP-поллингом `web_port` (это правильно). rbxdserver ждёт `web_port` до 150 c, но
состояние «RCC упал после старта» узнаёт только через `crash_check` (гонка, см. ISSUES.md).
В реврайте: готовность — TCP `web_port` + `GET /`; live-статус — REST к rbxd
(см. §8, `GET /rfd/status`), а не снимок процессов.

## 4. HTTP-контракт rbxd: что доступно наруже

Вебсервер — всегда **HTTPS с self-signed сертификатом** (`trustme`, генерится в рантайме).
Наружный клиент обязан отключать проверку сертификата.

Эндпойнты, которые имеют смысл для запускателей (всё остальное — для бинарей Roblox):

| Метод | Путь | Auth | Ответ | Зачем |
|---|---|---|---|---|
| `GET` | `/` | нет | `text: RFD webserver <ver> [<rbx_ver>]` | health-check / версия |
| `GET` | `/rfd/roblox-version` | нет | `text: v463` / `v347` | **какую версию клиента запускать** |
| `GET` | `/rfd/default-user-code` | нет | `text: <user_code>` | user_code по умолчанию |
| `GET` | `/rfd/is-player-allowed?userId=<int>` | нет | `text: true/false` | повторная проверка при подключении |
| `POST` | `/rfd/data-transfer` | **loopback only** | JSON | синхронизация данных с RCC (например, игрок дошёл до места — ивент «hold loading screen до первого колла», см. CHANGELOG 0.66.4) |
| `GET` | `/Thumbs/GameIcon.ashx` | нет | image/png | иконка плейса для UI |

`is_privileged` определяется по **loopback-адресу пира**, а не по Host-заголовку —
это единственная «защита» в rbxd. Токенов/auth в rbxd **нет** (Bearer-токен — это
изобретение rbxdserver; rbxd никогда его не проверяет).

## 5. Подключение игрока (что нужно знать, чтобы запустить player)

```
player.exe -a https://<web_host>:<web_port>/login/negotiate.ashx \
           -j https://<web_host>:<web_port>/game/PlaceLauncher.ashx?MachineAddress=<rcc_host>&ServerPort=<rcc_port>&UserCode=<user_code> \
           -t 1
```

- `web_host` и `rcc_host` могут различаться (RCC на другой машине).
- rbxd сам мапит `localhost` → `127.0.0.1` и превращает IPv6 в IPv4-mapped форму
  для `BaseUrl` (CoreScripts Roblox не любят BaseUrl без точек).
- `user_code` — внешний идентификатор игрока; если его не дать, player спросит
  `/rfd/default-user-code` сам.
- В v463 (`2021E`) есть обратная совместимость: query-параметры дублируются в
  `rcc-host-addr`/`rcc-port`/`user-code` — для реврайта это не нужно.

## 6. Остановка и очистка

- rbxd ловит `SIGINT` → `KeyboardInterrupt` → `routine.stop()` → `popen.terminate()`.
- **Wine-процессы переживают terminate().** Наружный код обязан убивать всю группу
  процессов (так делает rbxdserver: `Setpgid` + `SIGTERM`, через 5 c — `SIGKILL`).
- Текущий rbxdclient вместо этого делает `pkill -9 -f RobloxPlayerBeta` —
  это убивает **чужие** сессии на машине; в реврайте — process group, не pkill.
- `RCC` умеет сам рестартовать: по сигналу из stdout (`log_action: RESTART/TERMINATE`)
  и при изменении place-файла (`track_file_changes`). Наружный код должен быть готов
  к тому, что RCC-процесс поменяется, а web_port останется тем же.

## 7. Чего rbxd не умеет (и зачем вообще нужны client/server)

- **Нет статус-эндпойнта**: ни «сколько игроков», ни «RCC жив/мёртв», ни «плейс загружен».
- **Нет presence**: никто не следит, что игрок вышел — автостоп плейса по пустоте
  (15 c grace) целиком выдуман в rbxdserver.
- **Нет аутентификации**: `/places`, `/status`, панели — всё это обёртки rbxdserver;
  сам rbxd открыт любому, кто достучался до `web_port`.
- **Нет multi-place supervision**: rbxd может поднять N плейсов одним процессом
  (несколько `--config`), но наружу не сообщает их порты — слежение целиком на наружном коде.
- **Один живой RCC на версию Roblox на дерево rbxd** (ограничение самого Roblox, не rbxd):
  `GameServer.json` и `RCCFlagOverride.json` (v463) / `RCCService.json` (v347) лежат в общем
  каталоге `Source/Roblox/<version>/Server/`, а не per-place. Два v463-плейса с RCC
  одновременно затрут друг другу файлы. v347 + v463 — уживаются (каталоги разные).
  На практике: «веб 24/7 для всех, RCC для текущего» (см. §10) — это не компромисс, а
  единственно правильный режим.

## 8. План реврайта rbxdclient / rbxdserver (что выкинуть, что добавить)

Принцип: **наружное приложение должно быть тонкой надстройкой над rbxd, а не вторым
супервизором с параллельным состоянием**.

**rbxdserver (Go):**
1. Заменить TCP-поллинг порта на `GET /` (или добавить в rbxd `GET /rfd/status` —
   см. ниже) — меньше Race-условий при «порт открылся, но вебсервер ещё не готов».
2. Убрать `path == ""`-хак «remote плейс» — это клиентский костыль, на серверной
   стороне должен быть явный источник плейсов (`--places` каталог, уже есть).
3. Из `ISSUES.md` (там полный список): path-traversal через `place` slug
   ( `/places/<slug>/../…`), deadlock `onEmpty` под мьютексом сессии, гонка
   `crash_check`, `Supervisor.proc` без лока, расхождение `--web_host` vs `--web_port`
   в генерируемой join-команде панели.
4. Сессии считать не по user-имени (коллизии!), а по connection id.
5. NixOS-модуль: перестать передавать deprecated `--state-dir`.

**rbxdclient (Vala):**
1. Главный источник хрупкости — **состояние в `~/.boblox` размазано по 4 файлам**
   (`config.vala`, `places.vala`, `place_card.vala`, `window.vala`). Вынести в один
   доступ к `Config.get_base_dir()`.
2. `LocalLauncher.start()` вызывает `stop()`, который делает `pkill -9 -f` — убивает
   чужие процессы. Заменить на трекинг pid-группы запущенного им же процесса.
3. TOCTOU портов: «нашли свободный порт» → «запустили» → «порт занят кем-то другим».
   Лечится передачей порта нулём и чтением реального, либо retry-циклом.
4. **Версию клиента можно не парсить регэкспом из TOML** — спросить
   `GET /rfd/roblox-version` у поднятого сервера (эндпойнт уже есть!). Это убирает
   дублирование парсера в трёх проектах разом.
5. `--test` режим (`RFD_NO_CAGE=1`) — оставить, это единственный способ увидеть окно.

**В rbxd (предлагаемые дополнения, ускоряющие реврайт):**
- `GET /rfd/status` → `{state: starting|running|stopping, place, rcc_port, web_port,
  players: N, roblox_version}` — убивает необходимость pgrep и внешнего слежения.
- `GET /rfd/presence` → список игроков онлайн с `last_seen` (см. §11, канал 3). Сейчас
  rbxd — «клетка с золотыми данными»: sqlite `players` хранит всех, кто когда-либо заходил,
  но наружу список онлайн не отдаёт. Эндпойнт решает сразу две задачи: reconciliation
  presence в rbxdserver и реальный список «кто в плейсе» для его веб-панели.
- Флаг `--rbxdserver <url>` для режимов `server` (push join) **и** `player` (push leave —
  обёртка живёт ровно столько, сколько сессия). Подробно — §11.
- Явное логирование «RCC ready» в stdout (сейчас `LogAction.READY` — заглушка `pass`),
  чтобы наружному коду не пришлось гадать по портам.

## 9. Контрактный чеклист нового запускателя

- [ ] Запуск rbxd с явным `cwd` (где будут `AssetCache/`, `_.sqlite`, `logs/`).
- [ ] `RFD_NO_CAGE=1` в env всегда (иначе потеря контроля над процессами).
- [ ] Выделение портов атомарным bind, проверка что вернулся свободный.
- [ ] TCP-полл `web_port` до таймаута → `GET /` → старт player.
- [ ] HTTPS без проверки сертификата.
- [ ] Убийство по process group (`Setpgid`/`kill -- -PGID`), никакого `pkill -f`.
- [ ] Не парсить `GameConfig.toml` самостоятельно — спрашивать `/rfd/roblox-version`.
- [ ] Понимать, что RCC может рестартнуть сам (stdout-сигналы, `track_file_changes`).

## 11. Wishlist: присутствие игроков — доказано на реальном логе

> Ниже — выводы из **реального лога** (плейс f3x, baseplate): игрок зашёл, походил,
> написал в чат, вышел. Смотрите на последовательность эндпойнтов — это и есть контракт.

### Что вебсервер видит (заход — да, выход — нет)

```
POST /login/negotiate.ashx?suggest=1              ← старт сессии
POST /game/PlaceLauncher.ashx?UserCode=flaemer    ← клиент узнал адрес RCC
GET  /game/join.ashx?UserCode=flaemer             ← init_player() → sqlite, id=11049852
GET  /rfd/is-player-allowed?userId=11049852       ← хук check_user_allowed
... рой: avatar-fetch, HandleSocialRequest, asset/?id=, avatar-thumbnail ...
POST /moderation/v2/filtertext                    ← написал в чат (активность!)
POST /rfd/data-transfer                           ← ИГРОК РЕАЛЬНО ЗАСПАВНИЛСЯ
POST /v1.1/Counters/BatchIncrement
───── игрок выходит из плейса ─────
(ТИШИНА. НИ ОДНОГО запроса. Вебсервер не узнаёт об уходе НИКОГДА.)
```

**Вывод 1: `/rfd/data-transfer` — лучший сигнал «игрок в игре».** Negotiate.ashx стреляет
до загрузки клиента и ничего не гарантирует. Data-transfer — это механизм «удержать на
loading screen, пока RCC не передаст данные игрока» (`data_transferer`), т.е. момент,
когда игрок действительно появился. Если делать push-событие — стартовать отсюда.

**Вывод 2: выхода из вебсервера/RCC нет ВООБЩЕ.** Лог обрывается на обычном `BatchIncrement`
— RCC не пишет в stdout ничего при уходе игрока, `log_action.check()` тут бесполезен
(парсинг stdout на эту тему — дохлая идея, проверено). Только клиент знает момент ухода:
его `watch_process_async` срабатывает ровно при смерти процесса игрока.

### Логика — кто чем владеет

```
      RCC (игровой движок)                    rbxdserver
      ───────────────────                    ──────────
      видит и JOIN, и LEAVE                  собирает push'и,
      авторитетно — это сам движок           ведёт состояние,
        (PlayerAdded / PlayerRemoving)       стоп плейса, когда 0
             │
             │ push (через вебсервер rbxd)
             ▼
        rbxd вебсервер → /rfd/player-left → → → → → → rbxdserver
```

Ключевая мысль пересмотрена: раньше я искал JOIN с одной стороны и LEAVE с другой
(«нельзя получить из одного источника»). Теперь **и то, и другое приходит из RCC** —
единственного участника, который знает правду наверняка. Фолбэки (обёртка player,
pull presence, трекер тишины) остаются на случай, если сам RCC умер.

### Конечный автомат игрока (в rbxdserver)

```
   ┌──────────┐ push JOIN от rbxd   ┌──────────┐ data-transfer от rbxd ┌─────────┐
   │ UNKNOWN  │ ──────────────────► │ JOINED   │ ────────────────────► │ IN_GAME │
   └──────────┘ (init_player)      └────┬─────┘                       └────┬────┘
                                     │ 5 c, а игрок не загрузился         │
                                     ▼  (клиент крашнулся на старте)      │
                                ┌────────┐  push LEAVE от player-обёртки │
                                │ STALE  │ ◄──────────────────────────────┘
                                └────────┘  (proc умер / окно закрыто)
                                     │ трекер активности: нет запросов
                                     │ с этим userId ≥ 30 c (страховка)
                                     ▼
                                ┌──────────┐  список IN_GAME пуст → grace 15 c → стоп
                                │   GONE   │
                                └──────────┘
```

STALE — новое состояние: push пришёл, а игрок до игры так и не дошёл. Сейчас клиент мог
открыть WS, упасть на загрузке — и сервер будет считать его онлайн всю сессию.

### Протокол — следим изнутри (канал 0) + фолбэки

Главная находка: **RCC сам знает правду об игроках** — а rbxd уже умеет с ним общаться.
В `routines/rcc/startup_scripts.py` генерируется Lua-скрипт `RFDStarterScript.lua`, который
крутится **внутри игрового сервера**. Он уже:

```lua
-- бесконечно шлёт POST на вебсервер (RPC-канал в обе стороны):
local Url = "rfd/data-transfer"
while true do
    CallsJson = HttpRbxApiService:PostAsync(Url, ResultsJson, ...)
    -- ...и вебсервер может дёргать Lua-функции через transferer.call()
end

-- и уже реагирует на заход игрока:
game.Players.PlayerAdded:connect(function(Player)
    local Url = "rfd/is-player-allowed?userId=" .. Player.UserId
    ...
end)
```

Значит, добавить сигнал выхода — **пять строк Lua**, и они идут изнутри игры:

```lua
game.Players.PlayerRemoving:connect(function(Player)
    HttpRbxApiService:PostAsync("rfd/player-left?userId=" .. Player.UserId, "")
end)
```

**Почему это идеальный источник.** Единственный, кто точно знает, что игрок отключился —
это сам игровой движок. Не «процесс-обёртка умер» (не всегда эквивалентно — см. ниже),
не «тишина 30 c» (игрок мог просто стоять), а событие от движка.

**И главное — его можно протестировать СЕЙЧАС, не трогая rbxd.** В твоём
`~/.boblox/Places/f3x/GameConfig.toml` уже есть `startup_script` — любой Lua туда
вписывается и выполняется при старте сервера (`game_config.structure.py:49`). То есть
прототип leave-сигнала — это добавить пару строк в TOML и перезапустить плейс.

**Что нужно в rbxd (потом):**
- новый эндпойнт `POST /rfd/player-left?userId=<id>` (loopback-only, как `/rfd/data-transfer`)
  — и можно сразу дёргать `presence_url` push;
- встроить `PlayerRemoving` в `BASE_SCRIPT_FORMAT` рядом с `PlayerAdded`.

### Канал 0 ломает красивую теорию — и это нормально

Мой предыдущий план опирался на то, что **python-обёртка `player` живёт столько же, сколько
сессия**. Проверка на живом стенде это **опровергла**: убийство python-обёртки в btop
оставляет RobloxPlayerBeta.exe живым и в игре. Причина: `python` — родитель `umu-run`, а
SIGKILL родителя детей не убивает (они переподчиняются init). И наоборот — `p.wait()` в
`popen_entry.wait()` стоит на `umu-run`, а не на самой игре, а под Proton окно закрывается
раньше, чем помирают wine-процессы. Вывод: **обёртка ≠ сессия**, и пусть она и остаётся
полезным фолбэком, авторитетным сигналом быть не может.

### Сводка каналов (пересмотренная)

| # | Сигнал | Откуда | Тип | Надёжность |
|---|---|---|---|---|
| **0** | **join + leave** | **RCC (Lua: PlayerAdded / PlayerRemoving)** | push | 🟢🟢 **авторитет — сам движок** |
| 1 | join | rbxd `server` (`init_player`) | push | 🟢 авторитет |
| 2 | leave | rbxd `player` (обёртка) | push | 🟡 фолбэк — обёртка ≠ сессия (доказано) |
| 3 | presence | rbxd `server` | pull `GET /rfd/presence` | 🟡 reconciliation |
| 4 | inactive | rbxd `server` | push по таймауту `last_seen` | 🟡 эвристика |

Канал 0 покрывает и join, и leave — и обе половины теперь приходят **из одного источника**
(RCC), а не из двух разных, как в прошлом варианте. Каналы 1–4 остаются фолбэками на случай,
когда RCC умер сам (тогда `PlayerRemoving` не стрельнёт никогда) — но основная логика
ездит на канале 0.

**Сборка в rbxdserver:** push'и от rbxd/RCC принимаются новым POST-эндпойнтом, дедуплицируются
по `user_code` + таймстемпу, и состояние игрока едет по автомату
`UNKNOWN → JOINED → IN_GAME → GONE` (с `STALE` для «не дошёл до игры»). WS-канал из
rbxdclient превращается в тонкий heartbeat для случаев «сеть отвалилась, но клиент жив».

### Что это чинит (из rbxdserver/ISSUES.md)

- сессии по нику → по `user_code` из push'а rbxd (два «Player» — две записи);
- «чёрная дыра» WS → WS больше не единственный канал, есть явный LEAVE;
- клиент упал на старте → STALE, а не «онлайн весь день»;
- `onEmpty` → grace считается только по ушедшим из IN_GAME, а не по обрывам сокета;
- веб-панель rbxdserver → видит реальных игроков (`/rfd/presence`), а не только свои WS.

## 10. Wishlist: веб-часть 24/7, RCC по требованию

Цель: вебсервер каждого плейса крутится **постоянно и дёшево** (чистый Python, без Wine),
а тяжёлый `RCCService.exe` поднимается только когда в плейс реально играют.

**Это возможно уже сейчас**, проверено по коду:

- rbxd умеет раздельный запуск: `--skip_rcc` (только вебсервер) и `--skip_web` (только RCC)
  — `server.py:196,217`.
- Вебсервер — **не** `bin_entry` (`routines/web.py:16`): не качает бинарники, не трогает
  Wine/Proton/cage. Один Python-процесс на плейс стоит копейки ресурсов.
- RCC плевать, в одном он процессе с вебсервером или нет: он ходит по HTTPS на
  `web_host:web_port` (зашито в `GameServer.json` → `MachineAddress`).

```
       24/7, дёшево                                    по требованию, дорого
┌─────────────────────────────────────┐         ┌──────────────────────────────┐
│ плейс A: python3 _main.py server    │         │ python3 _main.py server      │
│          --config A --skip_rcc      │         │   --config A --skip_web      │
│          --web_port 2101            │◄────────┤   --web_port 2101            │
│ плейс B: … --web_port 2102          │  HTTPS  │   --port <rcc> --backend wine│
│ плейс C: … --web_port 2103          │         └──────────────────────────────┘
└─────────────────────────────────────┘
```

**Что починить в rbxd, чтобы это стало удобным (wishlist):**

1. **`save_place_file()` / `save_thumbnail()` переехали из `rcc.bootstrap` в web-часть.**
   Сейчас веб-only с холодного старта не раздаёт плейс — AssetCache пустой. Lucky case:
   `AssetCache` персистентный на диске (`clear_on_start = false`), так что после одного
   запуска с RCC веб-only раздаёт плейс с диска и так. Но для честного холодного старта
   парсинг плейса должен делать вебсервер.
2. **Стабильные `web_port` на плейс.** RCC подключается к уже работающему вебсерверу по
   порту — значит порт плейса не должен плавать между запусками (сейчас наружный код
   берёт свободный порт на каждый старт). Вариант: фиксить в `GameConfig.toml` или
   выделитель портов в rbxdserver с сохранением в `info.json`.
3. **Перечитка `GameConfig.toml` без рестарта.** `get_cached_config` и `read_file_data`
   под `functools.cache` — 24/7 вебсервер не увидит изменений конфига. Wishlist:
   эндпойнт `/rfd/reload` или mtime-проверка.
4. **(следствие ограничения Roblox)** больше одного живого RCC на версию нельзя — см. §7,
   но это не мешает: веб 24/7 для всех плейсов, RCC для того, во что играют.

**Что не нужно делать:** веб-only процесс (`--skip_rcc`) не требует `WINEPREFIX`, `cage`
и бинарников — он может жить даже на машине без Wine вообще (например, фронт в контейнере),
если `AssetCache` уже warm. Это, кстати, делает ассеты/аватар/маркетплейс доступными 24/7
без единого виндового процесса. А вот `--skip_web` (RCC-only) — наоборот, требует Wine.

## 12. Wishlist: каталог ассетов (AssetTypeId + поиск)

**Проблема.** AssetCache плоский: `AssetCache/000012221720` — без расширения, без типа,
без имени. Собрать скин или найти «тот самый звук» можно только вслепую, по ID.

**Тип узнать можно**, и бесплатно. Эндпойнт публичный, кука не нужно (проверено на реальных
ассетах из живого кэша):

```
GET https://economy.roblox.com/v2/assets/<id>/details
  → {"AssetTypeId": 4, "Name": "sword.mesh", "Creator": {...}, ...}
```

Маппинг `AssetTypeId` → имя: 1 Image, 2 TShirt, 3 Audio, 4 Mesh, 9 Model, 11 Shirt,
12 Pants, 13 ShirtGraphic, 15 Face, 16 Animation, 17 Gear, 19 HairAccessory, …
(полный список — `AssetType` в документации Roblox). Тот же вызов rbxd **уже делает**
внутри `assets/extractor.py:get_creator_place_idens` — просто выбрасывает тип.

**Предлагаемая схема (идея, не реализовано):**

```
┌─ один раз: индексация ────────────────────────────────────────┐
│  обойти AssetCache → для каждого ID: GET /v2/assets/<id>/details │
│  → sqlite: (id, type, name, creator)                            │
│  → потом только свежескачанные (diff по списку файлов)          │
└────────────────────────────────────────────────────────────────┘
        ↓
rbxd:  GET /rfd/assets?type=Pants&q=blue   ← поиск по имени/типу
        ↓
rbxdserver: проксирует в rbxd
        ↓
rbxdclient: UI «надеть скин» / мини-Toolbox:
  рубашки ← type 11   штаны ← 12   волосы ← 19   лицо ← 15
```

**Зачем это rbxdserver/rbxdclient:** сейчас «скин» — это ручная сборка `items: [id…]` в
`skins/<user_code>.json`. С каталогом клиент сможет показать «вот вся одежда, которая
есть в кэше», и надеть выбранное одной кнопкой. Мини-Toolbox в клиенте — тот же механизм:
поиск по имени вместо слепого перебора ID.

**Глобальный AssetCache.** Сейчас кэш отдельный на каждый плейс (`<place>/AssetCache`),
а одни и те же ассеты качаются заново для каждого. Общий кэш + общий индекс = одна
индексация на всё, и скин, собранный в одном плейсе, виден в другом.

## 13. Toolbox в Studio: что он просит, и как это увидеть

**Studio работает** (wine/proton, `python3 _main.py studio`), Toolbox-плагин грузится —
в FLog это видно: `Plugin load time 'builtin_Toolbox.rbxm': 123.7`.

**Сниффать процессы НЕ нужно.** BaseURL переведён на локальный вебсервер
(`save_app_settings` в `AppSettings.xml`), поэтому **любой** HTTP-запрос Studio —
включая Toolbox — приходит в твой собственный rbxd-вебсервер. Маршрутизатор
(`web_server/_logic.py:handle_request`) пишет в лог вообще каждый запрос
(`log_message`: `{ GET } https://localhost:<port>/path`), а нереализованные
отдают 404 — ровно они и есть TODO-список для Toolbox.

**Рецепт, как собрать, чего не хватает:**

```sh
# 1. запустить Studio-режим (вебсервер + Studio в одном процессе)
python3 Source/_main.py studio --config <place>/GameConfig.toml --backend proton

# 2. открыть Toolbox, покликать категории/поиск

# 3. собрать все URL, которые просил клиент
grep -o 'https\?://[^ ]*' <лог вебсервера> | sed 's|https\?://[^/]*||; s|?.*||' | sort -u > /tmp/req.txt

# 4. вычесть то, что уже реализовано
grep -rh '@server_path' Source/web_server/endpoints/*.py \
  | grep -oE "'/[^']+'" | tr -d "'" | sort -u > /tmp/impl.txt
comm -23 /tmp/req.txt /tmp/impl.txt      # ← недостающие эндпойнты
```

**Что уже точно нужно** (из реального лога, 404-промахи мимо роутов):
`/Analytics/Measurement.ashx`, `/Persistence/GetBlobUrl.ashx`,
`/userblock/getblockedusers`, `/users/<id>/canmanage/<place>`. Catalog-API Toolbox
(`catalog.roblox.com/…`) в логах ещё не появлялся — **надо открыть Toolbox в Studio и
походить по нему**, тогда его запросы всплывут. `/Setting/QuietGet/StudioAppSettings/`
(`fvars.py`) отдаёт Toolbox-флаги с `"Enabled": true` (`misc.py`), так что плагин не
отключён — просто его API никто не обслуживает.

**Альтернатива для запросов вне BaseURL.** Часть трафика может идти мимо вебсервера
(Analytics на реальные хосты). Тогда — `ss -tunp` на pid Studio или mitmproxy, но для
самого Toolbox хватает логов вебсервера.
