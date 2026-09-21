# IDEAS — идеи на будущее (не в активной разработке)

> Копилка отложенных фич. Сюда складываем то, что интересно, но пока не делается.
> Когда берёшь идею в работу — перенеси её в ISSUES.md или сразу в код.

## Рендер скинов в headshot-картинки — СДЕЛАНО

`/v1/users/avatar-headshot`, `/headshot-thumbnail/image` и `/avatar-thumbnail/image`
теперь отдают картинку, отрисованную из скина игрока (`data/skins/<user_code>.json`)
модулем `web_server/endpoints/skin_render.py`: силуэт тела в цветах скина
(BrickColor → HEX, палитра совпадает с настоящим Roblox) + пропорции из `scales`.

Ограничения (куда можно улучшить):
- аксессуары (шляпы/волосы/рубашки) пока не отрисовываются — тело есть, одежды
  нет. `_collect_asset_names` уже умеет доставать имена ассетов из catalog_cache;
  осталось нарисовать их поверх тела;
- нет 3D-рендера (для него нужен движок) — это 2D-композиция в стиле превью;
- кэш по sha256 от скина лежит в `ImageCache/skins/`, смена скина инвалидит
  его автоматически.

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
