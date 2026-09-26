# IDEAS — идеи на будущее (не в активной разработке)

> Копилка отложенных фич. Сюда складываем то, что интересно, но пока не делается.
> Когда берёшь идею в работу — перенеси её в ISSUES.md или сразу в код.

## Рендер скинов — ОТКАЗАЛИСЬ, отдаём плейсхолдеры

Идею рендерить аватар из скина пробовали тремя способами, все оказались
оверхедом для одного игрока:

1. **2D-силуэт** (`skin_render.py`, удалён) — грубая картинка, одежды нет;
2. **3D через v463 RCC** — RCC не открывает SOAP-порт (`Service started on
   port 0`), путь тупиковый;
3. **3D через Mercury-RCC 2013** — рабочий (см. `tests/render_mercury_test.py`:
   настоящий движок, Shirt/Pants натягиваются через `ShirtTemplate`), но
   ради иконки держать отдельный wine+RCC — неоправданный оверхед.

**Сейчас:** `/headshot-thumbnail/*`, `/avatar-thumbnail/*`, `/v1/batch` и
`/v1/users/avatar*` отдают **случайный статичный плейсхолдер** из папок
`web_server/static/img/placeholder/avatar_placeholder/` и
`.../headshot_placeholder/`. В папку можно положить сколько угодно картинок
с любыми именами (`image.png`, `anapa2007.png`, …) — поддерживаются
png/jpg/gif/webp/bmp/tiff. При каждом запросе из папки случайно выбирается
одна картинка, без привязки к userId (перезаход даёт новую).

Папка сканируется заново при каждом запросе, поэтому картинки можно
добавлять, удалять и заменять на лету — без рестарта сервера.

Fallback-цепочка: плейсхолдер → `ContentDeleted.png` (лежит в
`web_server/static/ContentDeleted.png`, это не плейсхолдер юзера, а
отдельная заглушка «контент удалён») → 404 (сервер не падает в любом
случае).

**Никакого кэширования плейсхолдеров:** ответ всегда читается с диска
(никакого `functools.cache` — раньше удалённые/перемещённые файлы
продолжали отдаваться до рестарта процесса), а клиенту шлётся
`Cache-Control: no-store`.

Если когда-нибудь захочется вернуть настоящий рендер — стартовая точка
`tests/render_mercury_test.py`, там записаны все грабли (SOAP namespace,
OpenJobEx vs BatchJobEx, `wait()` ломает ответ, LoadAsset виснет,
`LIBGL_ALWAYS_SOFTWARE=1`).

## 3D-превью ассетов из Roblox (важная находка!)

Рабочий endpoint (проверено 2026-09):

```
GET https://thumbnails.roblox.com/v1/assets?assetIds=<id1>,<id2>,<id3>&size=420x420&format=Png&isCircular=false
→ {"data":[{"targetId":301809497,"state":"Completed",
           "imageUrl":"https://tr.rbxcdn.com/180DAY-…/420/420/Shirt/Png/noFilter",…}]}
```

Это **готовые отрендеренные Roblox'ом 3D-картинки вещей** (Shirt/Hat/Face/…),
которые можно скачать и наклеить поверх нашего силуэта. URL в ответе — CDN
(`tr.rbxcdn.com`), живёт 180 дней.

**Важные отличия от других (мёртвых) endpoints:**
- `/v1/assets` — РАБОТАЕТ (именно так, без суффикса)
- `/v1/assets/asset-thumbnails`, `/v1/avatar/asset-thumbnails`,
  `/v1/avatar/resize`, `assetgame.roblox.com/asset-thumbnail/image`,
  `www.roblox.com/thumbs/asset.ashx` — все отдают 404 (прикрыты)
- `/v1/users/avatar` и `/v1/users/avatar-headshot` работают, но **только по
  userId Roblox** — бесполезно для наших юзеров (compose-API по произвольному
  набору ассетов убран)

**Как это меняет план рендера:** вместо 3D-движка (которого у нас нет и
которому нужны меши, лежащие на клиенте) — берём готовые рендеры вещей у
Roblox и компонуем поверх силуэта в цветах скина. План:
1. из `items`/`bundles` достаём id ассетов (уже умеем через
   `resolve_asset_entry`/`resolve_bundle`, заодно узнаём `assetType`);
2. по id'шкам батчами (до 100 за раз) спрашиваем `/v1/assets`;
3. качаем PNG, кэшируем в `ImageCache/asset-thumbs/<id>_<size>.png`
   (инвалидация по 180-дневному TTL или по mtime);
4. клеим на холст по типу: Head/Face → на голову, Shirt/Torso → на торс,
   Hat/Hair → сверху, Pants → на ноги. Пропорции — из `scales`.

Так аватар будет выглядеть как настоящий, а не как цветной силуэт.
Зависимости те же: Pillow (уже в `shell.nix`) + urllib (есть).

**Почему лучше, чем сырая развёртка текстуры:** развёртка — это плоская карта
UV, её нельзя «надеть» на 2D-силуэт без 3D-модели. Готовый рендер Roblox'а —
это уже картинка вещи «как она выглядит», её можно просто вклеить.

## Mercury (tp-link-extender/MercuryCore) — как у них устроен 3D-рендер

Mercury — это «build-your-own-Roblox» на TypeScript (SvelteKit) + SurrealDB +
свой RCCService. Клон лежит в `../Mercury/` (рядом с rbxd).

**Главный инсайт: 3D-ренер делает сам движок Roblox, а не какой-то внешний
рендерер.** Цепочка:

```
запрос рендера (type: Avatar/Clothing/Model/Mesh)
  → очередь в SurrealDB (requestRender.ts)
  → Proxy на Go шлёт SOAP BatchJobEx в RCCService (порт 64989)
     шаблон soap.xml: <id>_<expirationInSeconds>30<category>1<cores>1
                       <script>…Lua…</script>
  → RCCService исполняет Lua в настоящем движке Roblox
  → Lua: ThumbnailGenerator:Click("PNG", 1680, 1680, true)  ← вот рендер!
  → base64 PNG возвращается в Proxy, пишется в ../data/avatars|thumbnails
  → Proxy ресайзит через imaging (Go) и кладёт на диск
```

**Lua-скрипт рендера** (у Mercury лежит в `RCCService/RCCService/gameserver.txt`
— несмотря на имя файла, это скрипт рендера, не конфиг сервера!). Ключевое:

```lua
local ThumbnailGenerator = game:GetService "ThumbnailGenerator"
local Player = game.Players:CreateLocalPlayer(0)
-- Avatar: натягиваем аватар на персонажа и снимаем
Player.CharacterAppearance = BaseUrl .. "/Asset/CharacterFetch.ashx?userId=" .. Id
Player:LoadCharacter(false)
wait(3)
return ThumbnailGenerator:Click("PNG", 1680, 1680, true)
-- Clothing: тот же трюк, но через /api/render/characterasset?id=
-- Model:    InsertService:LoadAsset(Id) в workspace, снимаем
-- Mesh:     SpecialMesh + MeshId = /asset?id=…, снимаем
-- Head:     как Avatar, потом удаляем все Part кроме Head
```

`ThumbnailGenerator` — это **настоящий сервис Roblox**, доступный в RCC/Studio.
То, что Roblox использует для своих иконок каталога.

### Можно ли прикрутить к rbxd?

**Да, и это проще, чем кажется** — потому что у rbxd УЖЕ есть всё нужное:

| Что нужно Mercury | Что есть в rbxd |
|---|---|
| RCCService.exe | ✅ есть (`Source/Roblox/<ver>/Server/`), уже бегает под wine/cage |
| SOAP-прокси на Go | ❌ нет, но это ~100 строк (soap.xml + http.Post) |
| Очередь в SurrealDB | ✅ не нужна — можно прямо в sqlite (storage) или in-memory |
| `CharacterFetch.ashx` | ✅ близко: `avatar.py` уже отдаёт аватар по userId (`/v1.1/avatar-fetch/` для v347, `/v1/avatar` для v463) |
| ThumbnailGenerator в Lua | ✅ есть в Studio/RCC-бинаре v347/v463 (это старый API, он жил до 2018) |

**Подводные камни:**
1. **ThumbnailGenerator мёртв в новых клиентах.** Mercury использует старый
   RCC (формат скрипта — R6, `CharacterAppearance`), где сервис ещё жив. У rbxd
   v347 (2018M) есть шанс, v463 (2021E) — вряд ли (надо проверять `game:GetService("ThumbnailGenerator")` на nil).
2. **RCC rbxd уже занят игрой.** У Mercury RCC простаивает и только рендерит.
   Варианты: второй RCC-инстанс на другом порту (rbxd это умеет —
   `--rcc_port`), либо рендерить через Studio-бинарь (`routines/studio`).
3. **Wine + cage.** RCC rbxd запускается в cage (headless). ThumbnailGenerator
   пишет в кадр — ему нужен GPU/soft-рендер; Mercury запускает RCC под wine
   с OSMesa32.dll/OPENGL32.dll (видны в их копии RCCService) — soft-render.
   Для rbxd это значит: нельзя рендерить на основном RCC во время игры
   (падение FPS/креш), нужен отдельный процесс.

### ПРОВЕРЕНО: ThumbnailGenerator жив в v347 И v463 (2018 и 2021)

Бинари лежат в `rbxd/data/Roblox/<ver>/Server/RCCService.exe`:

```
v347: ThumbnailGenerator::click() success + clickTexture() success  (4 вхождения)
v463: ThumbnailGenerator::click() success + clickTexture() success  (6 вхождений)
```

Значит **отдельный RCC 2013 не нужен** — рендерит тот же движок, что и игра.
Экономия ресурсов: один экземпляр RCC на плейс, как и сейчас.

Отдельный RCC 2013 отпадает — сервис встроен в оба наших бинаря.

### РЕАЛИЗОВАНО: 3D-рендер через RCC (один экземпляр)

Код написан, пока **не протестирован на живом сервере**. Что сделано:

| Файл | Что |
|---|---|
| `web_server/render_queue.py` | SOAP-клиент (BatchJobEx), шаблон Lua-скрипта рендера, постановка в очередь, дедупликация, декодирование base64-результата |
| `web_server/endpoints/render_result.py` | приёмник `/rfd/render-result` — RCC шлёт сюда PNG, пишем в кэш |
| `routines/rcc/__init__.py` | флаг `-Console` при запуске RCC — открывает SOAP-порт `127.0.0.1:8001` |
| `web_server/endpoints/image.py` | если 2D-рендер не получился и кэша нет → ставим задачу в RCC, отдаём placeholder; потом клиент попадает в кэш |

Поток:
```
клиент просит headshot → кэша нет
  → image.py: request_user_render() → SOAP BatchJobEx в свой же RCC (:8001)
  → отдаём placeholder
  → RCC исполняет Lua: LoadCharacter + ThumbnailGenerator:Click("PNG",1680,1680)
  → Lua шлёт base64 на /rfd/render-result
  → пишем в ImageCache/originals/<task_id>
  → следующий запрос клиента → попадание в кэш → настоящий 3D headshot
```

Task_id: `user-<id>-head` / `user-<id>-body`. Дедупликация in-memory на процесс.

**ПРОВЕРЕНО НА ЖИВОМ СЕРВЕРЕ (2026-09-22): SOAP-подход не работает.**

Лог показал:
- `curl http://127.0.0.1:8001` → `000` — RCC **не открыл SOAP-порт**, хотя
  флаг `-Console` передаётся. В логе RCC: `Service started on port 0`.
- Меркуриевский SOAP-порт 64989 к нашим бинарям отношения не имеет.

Зато в бинаре (v347 и v463) нашлась **нативная подсистема RCCThumbnail** —
RCC сам умеет рендерить аватары и присылать результат по HTTP:

```
VThumbnailGenerator / ThumbnailGenerator / getAssetThumbnailAsync
BatchThumbnailFetcher / ThumbnailFetchJob / BatchThumbnailResult
GetUserThumbnailAsync (внутриигровой API — его дёргает и клиент)
RCCThumbnailJobFailureInfluxHundrethsPercentage / RCCFailedThumbnailRequest
```

И, главное, — RCC сам обращается на эти эндпойнты вебсервера:

```
/thumbnail/avatar-headshot     ← сюда RCC присылает отрендеренный headshot
/v1.0/avatar-fetch-thumbnail/  ← второй путь для thumbnail'ов
/asset-thumbnail/json
```

**Новый механизм (без SOAP вообще):**

```
клиент: Players:GetUserThumbnailAsync(HeadShot)
  → RCC рендерит аватар сам (ThumbnailGenerator)
  → POST /thumbnail/avatar-headshot?userId=<id>  ← картиночка прилетает к нам
  → мы пишем её в ImageCache/originals/user-<id>-head
  → следующий запрос — попадание в кэш
```

RCC выступает и рендерером, и отправителем. Никакого `-Console`, никакого
порта 8001, никакого Lua — движок делает всё сам.

Добавленные эндпойнты (`web_server/endpoints/image.py`):
- `/thumbnail/avatar-headshot` (GET/POST) — приём headshot'а от RCC;
- `/v1.0/avatar-fetch-thumbnail/` (GET/POST) — то же, второй путь.

Принимаем и сырой PNG, и JSON `{"imageUrl": "..."}` (тогда качаем по ссылке).
Гатятся `is_privileged` (loopback only), как и остальные `/rfd/*`.

**Почему placeholders всё ещё показывались:** старый код сначала пытался
2D-рендер через `skin_image_cache_key`, и тот возвращал хэш даже когда
Pillow не установлена — поэтому 3D-фолбэк никогда не вызывался. Теперь
рендер происходит реально, а не «ключ есть, картинки нет».

**`Service started on port 0`: SOAP-порт не открывается — подробности**

Проверено экспериментально (writable wine-префикс `.wine-rfd`):

1. `Roblox.Thumbnails.Relay` в реестре **НЕ управляет портом**.
   Wine видит ключ идеально (`reg query` -> `REG_DWORD 0xfdd9`), но
   RCC всё равно пишет `port 0`. Удалён.
2. **64989 захардкожен в бинаре v463 (x2)** как `mov [ebp-8], 0xFDD9` —
   это дефолт, а `port 0` = "не удалось стартовать SOAP-сервис".
3. RCC вообще **не пытается** открыть TCP-порт: `ss -tlnp` во время
   работы показывает только Python на 2005. RCC слушает UDP 2005 и всё.
4. **`~/.wine` смонтирован read-only** — из-за этого ключ не записывался.
   Решение: префикс в workspace (`rbxd/.wine-rfd`, `--wine-prefix`).
5. RCC крашится на `LoadClientSettingsFailure (ConnectFail)` без
   вебсервера (`--skip_web`). С вебсервером работает, SOAP мёртв всё равно.

**Вывод:** SOAP-путь (Mercury-стиль) в v463 тупиковый. Рендер делаем свой.

**АССЕТЫ ДЛЯ РЕНДЕРА — всё уже в кэше `rbxd/data/Assets/`** (имя = %011d):
```
aya.json items:
  6598847320 → Shirt      (ShirtTemplate rbxassetid://6598847316)
  9399214725 → Pants      (texture 9399214718 — PNG 585x559)
  63690008   → Face       (модель "<roblox!...")
  376526673  → Accessory  (mesh 116436358 + texture 376188793 PNG 256x256)
  253151806  → Accessory  (mesh 253147987 + texture 253143666 PNG 256x256)
```


---

**

В бинаре v463 рядом с RCCThumbnail-подсистемой лежат схемы URL:

```
rbxtemp://
rbxhttp://
rbxthumb://      ← вот он
render/texture/local
```

И enum типов: `Avatar`, `AvatarHeadShot`, `GameThumbnail`, `GroupIcon`,
`BadgeIcon`, `Outfit`, `Asset`.

`rbxthumb://` — это **локальный протокол thumbnail'ов**: когда `imageUrl`
в ответе `/v1/batch` указывает на `rbxthumb://...`, движок понимает это как
команду «отрендерить самому», а не «скачать по HTTP». Вероятный формат:

```
rbxthumb://<type>/<id>/<size>x<size>
   например rbxthumb://AvatarHeadShot/7497908/420x420
```

**План:** в `batch_image_request` (image.py) для типов `Avatar` /
`AvatarHeadShot` отдавать `imageUrl` вида `rbxthumb://...` вместо нашего
HTTPS URL. RCC должен сам отрендерить аватар и, возможно, закэшировать.
Рендер идёт через тот же BatchThumbnailFetcher → ThumbnailGenerator.

**Что точно есть в бинаре (v463):**
- `BatchThumbnailFetcher`, `ThumbnailFetchJob`, `BatchThumbnailResult` —
  конвейер;
- `GetUserThumbnailAsync` — игровой API;
- `ThumbnailGenerator` (живой, `click() success`);
- эндпойнты, которые RCC дёргает сам: `/thumbnail/avatar-headshot`,
  `/v1.0/avatar-fetch-thumbnail/`, `/v1/batch`, `/asset-thumbnail/json`.

**СВОЙ 3D-РЕНДЕР БЕЗ RCC — proof of concept готов**

Главное открытие: **клиент ничего не рендерит**. В `RobloxPlayerBeta.exe`
(v463) нет ни `Thumbnail`, ни `headshot`, ни `rbxthumb://` — ноль
совпадений. Вся его работа с thumbnail'ами:

| Запрос клиента | Что он ждёт |
|---|---|
| `GET /headshot-thumbnail/json?userId=…&width=420&height=420` | `{"Final": true, "Url": "<картинка>"}` |
| `GET /avatar-thumbnail/json?userId=…&width=100&height=100` | `{"Final": true, "Url": "<картинка>"}` |
| `POST /v1/batch` | массив с `imageUrl` |

Клиент просто скачивает PNG по URL, который мы отдаём, и показывает.

**А меши тела уже есть в репо!**
```
data/Roblox/v463/Server/Content/Avatar/
├── meshes/{torso,leftarm,rightarm,leftleg,rightleg}.mesh
└── heads/head.mesh           (517 вершин, 846 граней)
```
И парсер: `Source/assets/serialisers/mesh/rbxmesh.py` (форматы до v7).
Меши — `version 2.00`, координаты в stud'ах:
```
torso    x[-1.0,1.0] y[-1.0,1.0] z[-0.5,0.5]
head     x[-0.6,0.6] y[-0.6,0.6] z[-0.6,0.6]
```

**Два тестовых скрипта** (standalone, без клиента/сервера):
```
tests/render_avatar_test.py aya              → render_aya_avatar.png
tests/render_avatar_test.py aya --headshot   → render_aya_headshot.png
tests/render_rcc_test.py aya                 → через RCCService (нужен запущенный сервер)
```
Первый — софтверный рендер: парсит меши, красит в BrickColor'ы скина,
рисует ортогональную проекцию с диффузным освещением (painter's
algorithm + backface cull). Работает для обеих версий.

**Рендерer: проверенный layout (420x420, aya):**
```
head       → y[51-138]   (вверху)
torso      → y[130-274]  (центр)
left_arm   → x[63-153]   (слева)
right_arm  → x[266-356]  (справа)
legs       → y[274-418]  (внизу)
```

**Pillow**: в nix store, не в системном python. Запуск тестов:
```bash
export PYTHONPATH=/nix/store/4v9j9wbzyhrlx9980ygbr812313mazy0-python3.13-pillow-12.3.0/lib/python3.13/site-packages
python3 tests/render_avatar_test.py aya
```

---

**В v347 (2018M) `rbxthumb://` НЕТ** — там другой механизм (там RCCThumbnail
тоже есть, но протокола нет). Так что фокус с `rbxthumb://` — только для v463.

**Флаги, уже выставлены** (`web_server/endpoints/fvars.py`, v463-блок):
`FFlagDebugDisableThumbnailBatchJob: False`, `FFlagWhitelistThumbnailsAPI: True`,
`FFlagThrottleGetUserThumbnailAsync: False` и др. — см. `thumbnail_flags`.

 и присылать? Он шлёт
результат, только когда сам решит отрендерить (например, когда клиент
дёргает GetUserThumbnailAsync, а ответ указывает на наш вебсервер).
Возможно, нужен FFlag `WhitelistThumbnailsAPI` (он есть в бинаре) или
правильный ответ `/v1/users/avatar-headshot`, который укажет RCC на себя.

**Что ещё есть у Mercury и стоит украсть (мелкое):**
- `Proxy/RCCService.go` — готовый SOAP-обёртка для RCC на Go (можно
  переписать на Python ~80 строк);
- они хранят рендеры как файлы `data/avatars/<userId>.png` + `data/thumbnails/`,
  инвалидация по времени — проще, чем наш sha256, но менее точно;
- `Economy/` — отдельный Go-сервис экономики (у нас этого нет, но есть funds);
- `Orbiter/` — авто-установка версий клиента (Setup), rbxd качает через
  `pretasks/download.py` — подходы разные, оба рабочие.

Зависимость: Pillow (добавлена в `shell.nix`). Если её нет — рендер отдаёт None,
и image.py откатывается на placeholder.

## Auth: регистрация и логин

**Сейчас.** `user_code` — строка из GameConfig/URL, без пароля. ID получается из имени через
конфиг-хук `retrieve_user_id`. Просто, удобно для LAN, и это сознательный выбор
(см. `AGENTS.md` — «user_code, не имя»).

**Идея.** Портировать core-auth из RFD-N (`util/auth.py` + таблицы `user`/`auth_session`/
`auth_ticket`), когда понадобится:
- мультипользовательский режим с разными правами (админ/игрок);
- чтобы Studio показывала реальный username вместо «Unknown» (`/studio-login/v1/login`);
- веб-морда для редактирования скинов в браузере.

**Что важно помнить при портировании:**
- fallback `get_legacy_query_user` (RFD-N `join_data.py:386`) — заход по `UserCode` должен
  остаться рабочим, auth не должен ломать текущий flow;
- выкинуть детерминированную соль `_GetArgonSalt` (sha256 от хардкод-SESSION_KEY) —
  argon2-cffi сам генерирует случайную соль;
- выкинуть `GLOBAL_COOKIE_DOMAIN = ".rbolock.tk"` и `player_cookie_store`
  (Windows-only, на NixOS — no-op);
- **не** коммитить `Source/ssl/` — в RFD-N там лежит приватный ключ.

## Persistence V2 (DataStore API)

Отказались. В upstream-версии `persistence.py` уже есть `set`/`get`/`query_sorted_data`.
RFD-N добавляет `remove()` + `list_entries()` (с пагинацией), но V2-эндпойнты у неё
WIP: отладочные `print("11111111111")` в продакшене и нет universe-scoping
(датасторы шарятся между всеми плейсами). Если когда-то понадобится — взять 2 метода
из `storage/persistence.py` RFD-N (они чистые), а эндпойнты написать самим.
