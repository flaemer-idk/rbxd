# ASSET_TYPES.md — типы ассетов Roblox и эндпойнты asset_cache

Справочник для `asset_cache.py` и будущего аватар-эдитора.
Скрипт раскладывает скачанное по подпапкам имени типа из таблицы ниже
(неизвестному id соответствует папка `Type<N>`; словарь-копия таблицы
живёт в `asset_cache.py:ASSET_TYPE_NAMES`).

## Формат файлов (как у Revival Kanvas)

```
<out>/Hat/12345.info.json            {"Name","AssetTypeID","Description","Creator","Created","Updated"}
<out>/Hat/12345.thumb.png            обложка 420x420
<out>/Hat/12345.rbxm                 сам ассет (только с --rbxm)
<out>/Bundles/108.package.json       {"Name","Assets":[{"ID","AssetTypeID"},...]}
<out>/Bundles/108.package.thumb.png
<out>/GamePass/275254.info.json      + PriceInRobux, IconImageAssetId
```

Полный сырой JSON ответа details хранится в колонке `raw` sqlite-базы.

## AssetTypeId -> тип

| ID | AssetType                  | Расширение     | Что содержит / отдаёт клиенту                                    |
|----|----------------------------|----------------|------------------------------------------------------------------|
| 1  | Image                      | .png / .jpg    | Картинка, текстура                                               |
| 2  | TShirt                     | .rbxm          | Объект ShirtGraphic (ссылается на Image)                          |
| 3  | Audio                      | .mp3 / .ogg    | Звуковой файл                                                    |
| 4  | Mesh                       | .mesh          | Бинарная 3D-геометрия (Roblox Mesh v1/v2/etc)                    |
| 5  | Lua                        | .lua / .luac   | Исходный или байткод скрипта                                     |
| 8  | Hat                        | .rbxm          | Объект Hat (классический головной убор)                          |
| 9  | Place                      | .rbxl / .rbxlx | Файл плейса / карты                                              |
| 10 | Model                      | .rbxm / .rbxmx | Модель / набор объектов                                          |
| 11 | Shirt                      | .rbxm          | Объект Shirt (ссылается на текстуру Image)                       |
| 12 | Pants                      | .rbxm          | Объект Pants (ссылается на текстуру Image)                       |
| 13 | Decal                      | .rbxm          | Объект Decal (ссылается на Image)                                |
| 17 | Head                       | .mesh / .rbxm  | Геометрия SpecialMesh / деталь головы                            |
| 18 | Face                       | .rbxm          | Объект Decal лица (ссылается на Image)                           |
| 19 | Gear                       | .rbxm          | Объект Tool со скриптами, мешами и звуками                       |
| 24 | Animation                  | .rbxm          | Объект KeyframeSequence (анимация)                               |
| 27 | Torso                      | .rbxm / .mesh  | CharacterMesh торса                                              |
| 28 | RightArm                   | .rbxm / .mesh  | CharacterMesh правой руки                                        |
| 29 | LeftArm                    | .rbxm / .mesh  | CharacterMesh левой руки                                         |
| 30 | LeftLeg                    | .rbxm / .mesh  | CharacterMesh левой ноги                                         |
| 31 | RightLeg                   | .rbxm / .mesh  | CharacterMesh правой ноги                                        |
| 32 | Package                    | .rbxm          | Пакет частей тела                                                |
| 38 | Plugin                     | .rbxm          | Плагин Studio                                                    |
| 39 | SolidModel                 | .csg / .bin    | Бинарные CSG-данные UnionOperation                               |
| 40 | MeshPart                   | .rbxm / .mesh  | Деталь MeshPart с геометрией                                     |
| 41 | HairAccessory              | .rbxm          | Объект Accessory (волосы)                                        |
| 42 | FaceAccessory              | .rbxm          | Объект Accessory (на лицо)                                       |
| 43 | NeckAccessory              | .rbxm          | Объект Accessory (на шею)                                        |
| 44 | ShoulderAccessory          | .rbxm          | Объект Accessory (на плечи)                                      |
| 45 | FrontAccessory             | .rbxm          | Объект Accessory (спереди)                                       |
| 46 | BackAccessory              | .rbxm          | Объект Accessory (на спину)                                      |
| 47 | WaistAccessory             | .rbxm          | Объект Accessory (на пояс)                                       |
| 48 | ClimbAnimation             | .rbxm          | Анимация карабканья (KeyframeSequence)                           |
| 49 | DeathAnimation             | .rbxm          | Анимация смерти                                                  |
| 50 | FallAnimation              | .rbxm          | Анимация падения                                                 |
| 51 | IdleAnimation              | .rbxm          | Анимация стойки                                                  |
| 52 | JumpAnimation              | .rbxm          | Анимация прыжка                                                  |
| 53 | RunAnimation               | .rbxm          | Анимация бега                                                    |
| 54 | SwimAnimation              | .rbxm          | Анимация плавания                                                |
| 55 | WalkAnimation              | .rbxm          | Анимация ходьбы                                                  |
| 56 | PoseAnimation              | .rbxm          | Анимация позы                                                    |
| 57 | EarAccessory               | .rbxm          | Объект Accessory (на уши)                                        |
| 58 | EyeAccessory               | .rbxm          | Объект Accessory (для глаз)                                      |
| 61 | EmoteAnimation             | .rbxm          | Анимация эмоции (эмоут)                                          |
| 62 | Video                      | .mp4 / .webm   | Видеофайл (для VideoFrame)                                       |
| 64 | TShirtAccessory            | .rbxm          | 3D-одежда: футболка                                              |
| 65 | ShirtAccessory             | .rbxm          | 3D-одежда: рубашка                                               |
| 66 | PantsAccessory             | .rbxm          | 3D-одежда: штаны                                                 |
| 67 | JacketAccessory            | .rbxm          | 3D-одежда: куртка                                                |
| 68 | SweaterAccessory           | .rbxm          | 3D-одежда: свитер                                                |
| 69 | ShortsAccessory            | .rbxm          | 3D-одежда: шорты                                                 |
| 70 | LeftShoeAccessory          | .rbxm          | 3D-обувь: левый ботинок                                          |
| 71 | RightShoeAccessory         | .rbxm          | 3D-обувь: правый ботинок                                         |
| 72 | DressSkirtAccessory        | .rbxm          | 3D-одежда: платье / юбка                                         |
| 73 | FontFamily                 | .json / .rbxm  | Манифест семейства шрифтов                                       |
| 74 | FontFace                   | .ttf / .otf    | Файл шрифта                                                      |
| 75 | MeshHiddenSurfaceRemoval   | .bin           | Бинарные данные HSR (скрытие полигонов)                          |
| 76 | EyebrowAccessory           | .rbxm          | Объект Accessory: брови                                          |
| 77 | EyelashAccessory           | .rbxm          | Объект Accessory: ресницы                                        |
| 78 | MoodAnimation              | .rbxm          | Анимация мимики лица                                             |
| 79 | DynamicHead                | .rbxm          | Динамическая анимированная голова                                |
| 88 | FaceMakeup                 | .png / .rbxm   | Текстура макияжа лица                                            |
| 89 | LipMakeup                  | .png / .rbxm   | Текстура макияжа губ                                             |
| 90 | EyeMakeup                  | .png / .rbxm   | Текстура макияжа глаз                                            |
| 92 | AvatarBackground           | .png           | Изображение фона профиля                                         |
| 93 | TextDocument               | .txt           | Текстовый документ                                               |

## Эндпойнты, которые дёргает скрипт

Детали (открытые, без авторизации):

```
GET https://economy.roblox.com/v2/assets/{id}/details
    Любой одиночный ассет (шляпа, аудио, декаль, меш, модель...).
    Если ProductType=null и TargetId=id — ассет вне каталога (бесплатный/старый), это норм.

GET https://catalog.roblox.com/v1/bundles/{id}/details
    Бандл. items[] содержит и Asset-компоненты (id, assetType), и UserOutfit.

GET https://apis.roblox.com/game-passes/v1/game-passes/{id}/product-info
    Геймпасс.
```

Обложки (батчатся через запятую, до 100 id за раз):

```
GET https://thumbnails.roblox.com/v1/assets?assetIds={ids}&size=420x420&format=Png
GET https://thumbnails.roblox.com/v1/bundles/thumbnails?bundleIds={ids}&size=420x420&format=Png
GET https://thumbnails.roblox.com/v1/game-passes?gamePassIds={ids}&size=150x150&format=Png
    state=Completed -> imageUrl (cdn tr.rbxcdn.com, ссылка живёт ограниченное время).
    Превью генерятся для всех типов, включая меши и модели.
```

Сам ассет (для --rbxm):

```
GET https://assetdelivery.roblox.com/v1/asset/?id={id}
```

## Нюансы

- Текстуры классической одежды — это ассеты типа 1 (Image) с Description ровно
  «Shirt Image» / «Pants Image» / «TShirt Image». Сам ассет тут лишь текстура
  настоящей одежды (Shirt=11 / Pants=12 / TShirt=2), красивого рендера у неё
  нет (thumb — плоская развёртка). asset_cache не кладёт их в общую папку типа,
  а пишет info.json + <id>.thumb.png (плоская текстура-превью) в подпапку
  <Pants|Shirt|TShirt>/NoRender/ — «сироты без рендера». В базе такие
  со status=skipped, при повторных прогонах не перекачиваются.
- Связь «текстура -> родительская одежда» надёжного способа НЕ существует:
  ни один эндпойнт не даёт обратной ссылки, а эвристики (соседние id, автор)
  ненадёжны — ассеты появляются пачками за миллисекунды, автор у текстуры
  может быть группой или Roblox (id 1) вместо реального владельца.
  Надёжно только направление «одежда -> текстура»: rbxm ассета одежды
  содержит ShirtTemplate/PantsTemplate со ссылкой на текстуру; для новых
  ассетов assetdelivery требует .ROBLOSECURITY.
- asset_cache_clean.py удаляет такие текстуры из старых прогонов (файлы +
  записи в базе); skipped-записи не трогает. `--dry-run` показывает, что удалится.
- По той же схеме бывают «Decal Image» — текстуры декалей; пока качаются
  в Image/ как есть.
- id бандлов и id ассетов — разные пространства имён; автодетект скрипта
  сначала пробует ассет, для бандлов можно указывать `--kind bundle` явно.
- Аудио длиннее ~6 секунд отдаётся assetdelivery только с авторизацией —
  но details/обложка и без неё работают.
- game-passes может вернуть state=Blocked (модерируемая картинка) —
  в базу пишется thumb_state, файл не создаётся.
