import os
import json
import shutil
import re
from web_server._logic import web_server_handler, server_path
from config_type.types import structs, wrappers
import util.versions as versions
from game_config import obj_type

# Стандартный шаблон скина, если папка или default.json отсутствуют
DEFAULT_AVATAR = {
    "type": "R6",
    "items": [],
    "scales": {
        "height": 1.0,
        "width": 1.0,
        "head": 1.0,
        "depth": 1.0,
        "proportion": 0.0,
        "body_type": 0.0
    },
    "colors": {
        "head": 1,
        "left_arm": 1,
        "left_leg": 1,
        "right_arm": 1,
        "right_leg": 1,
        "torso": 1
    }
}

def get_avatar(id_num: int, game_config: obj_type) -> structs.avatar_data:
    user_code = get_user_code(id_num, game_config)
    
    # Если код пользователя не найден в базе данных, используем временное имя
    if user_code is None:
        user_code = f"Player_{id_num}"

    # Создаем папку 'skins' в текущей рабочей директории, если её нет
    skins_dir = os.path.join(os.getcwd(), 'skins')
    os.makedirs(skins_dir, exist_ok=True)

    default_json_path = os.path.join(skins_dir, 'default.json')
    
    # Если default.json не существует, создаем его с базовым шаблоном
    if not os.path.exists(default_json_path):
        try:
            with open(default_json_path, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_AVATAR, f, indent=4)
        except Exception as e:
            print(f"Error creating default.json: {e}")

    # Очищаем имя пользователя от недопустимых в путях символов
    safe_user_code = re.sub(r'[^a-zA-Z0-9_\-]', '_', user_code)
    user_json_path = os.path.join(skins_dir, f'{safe_user_code}.json')

    # Если файла скина для конкретного игрока нет, копируем default.json под его именем
    if not os.path.exists(user_json_path) and os.path.exists(default_json_path):
        try:
            shutil.copy(default_json_path, user_json_path)
        except Exception as e:
            print(f"Error copying default.json to {safe_user_code}.json: {e}")

    # Пытаемся прочитать JSON-файл скина игрока
    try:
        target_path = user_json_path if os.path.exists(user_json_path) else default_json_path
        with open(target_path, 'r', encoding='utf-8') as f:
            avatar_raw = json.load(f)
    except Exception as e:
        print(f"Error reading skin JSON for {user_code}: {e}. Using hardcoded default.")
        avatar_raw = DEFAULT_AVATAR

    # Безопасно парсим значения и приводим их к типам данных, которые ожидает движок RFD
    try:
        raw_type = avatar_raw.get("type", "R15")
        # Приводим к enum-типу (R6 или R15)
        parsed_type = structs.avatar_type(raw_type)
        
        # Получаем массивы ID надетых ассетов
        parsed_items = avatar_raw.get("items", [])
        
        # Парсим масштабы тела
        raw_scales = avatar_raw.get("scales", DEFAULT_AVATAR["scales"])
        parsed_scales = structs.avatar_scales(
            height=float(raw_scales.get("height", 1.0)),
            width=float(raw_scales.get("width", 1.0)),
            head=float(raw_scales.get("head", 1.0)),
            depth=float(raw_scales.get("depth", 1.0)),
            proportion=float(raw_scales.get("proportion", 0.0)),
            body_type=float(raw_scales.get("body_type", 0.0))
        )
        
        # Парсим цвета частей тела (BrickColor ID)
        raw_colors = avatar_raw.get("colors", DEFAULT_AVATAR["colors"])
        parsed_colors = structs.avatar_colors(
            head=int(raw_colors.get("head", 1)),
            left_arm=int(raw_colors.get("left_arm", 1)),
            left_leg=int(raw_colors.get("left_leg", 1)),
            right_arm=int(raw_colors.get("right_arm", 1)),
            right_leg=int(raw_colors.get("right_leg", 1)),
            torso=int(raw_colors.get("torso", 1))
        )

        return structs.avatar_data(
            type=parsed_type,
            items=parsed_items,
            scales=parsed_scales,
            colors=parsed_colors
        )
    except Exception as e:
        print(f"Error parsing skin structure for {user_code}: {e}")
        # Защитный фоллбек, чтобы Студия или сервер не упали при ошибке в JSON
        return structs.avatar_data(
            type=structs.avatar_type.R15,
            items=[],
            scales=structs.avatar_scales(**DEFAULT_AVATAR["scales"]),
            colors=structs.avatar_colors(**DEFAULT_AVATAR["colors"])
        )

def get_user_code(id_num: int, game_config: obj_type) -> str | None:
    database = game_config.storage.players
    user_code = database.get_player_field_from_index(
        database.player_field.IDEN_NUM,
        id_num,
        database.player_field.USERCODE,
    )
    if user_code is None:
        return None
    return user_code[0]


@server_path('/v1.1/avatar-fetch/', versions={versions.rōblox.v347})
def _(self: web_server_handler) -> bool:
    '''
    Character appearance for v347.
    '''
    id_num = int(self.query['userId'])
    avatar = get_avatar(id_num, self.game_config)

    self.send_json({
        "animations": {},
        "resolvedAvatarType": avatar.type.name,
        "accessoryVersionIds": avatar.items,
        "equippedGearVersionIds": [],
        "backpackGearVersionIds": [],
        "bodyColors": {
            "HeadColor": avatar.colors.head,
            "LeftArmColor": avatar.colors.left_arm,
            "LeftLegColor": avatar.colors.left_leg,
            "RightArmColor": avatar.colors.right_arm,
            "RightLegColor": avatar.colors.right_leg,
            "TorsoColor": avatar.colors.torso,
        },
        "scales": {
            "Height": avatar.scales.height,
            "Width": avatar.scales.width,
            "Head": avatar.scales.head,
            "Depth": avatar.scales.depth,
            "Proportion": avatar.scales.proportion,
            "BodyType": avatar.scales.body_type,
        },
    })
    return True


@server_path('/v1/avatar', versions={versions.rōblox.v463})
@server_path('/v1/avatar/', versions={versions.rōblox.v463})
@server_path('/v1/avatar-fetch', versions={versions.rōblox.v463})
@server_path('/v1/avatar-fetch/', versions={versions.rōblox.v463})
def _(self: web_server_handler) -> bool:
    '''
    Character appearance for v463.
    '''
    id_num = int(self.query['userId'])
    avatar = get_avatar(id_num, self.game_config)

    self.send_json({
        "resolvedAvatarType": avatar.type.name,
        "equippedGearVersionIds": [],
        "backpackGearVersionIds": [],
        "assetAndAssetTypeIds": [
            {
                "assetId": item,
                "assetTypeId": 8
            }
            for item in avatar.items
        ],
        "animationAssetIds": {},
        "bodyColors": {
            "headColorId": avatar.colors.head,
            "leftArmColorId": avatar.colors.left_arm,
            "leftLegColorId": avatar.colors.left_leg,
            "rightArmColorId": avatar.colors.right_arm,
            "rightLegColorId": avatar.colors.right_leg,
            "torsoColorId": avatar.colors.torso,
        },
        "scales": {
            "height": avatar.scales.height,
            "width": avatar.scales.width,
            "head": avatar.scales.head,
            "depth": avatar.scales.depth,
            "proportion": max(avatar.scales.proportion, 1e-2),
            "bodyType": max(avatar.scales.body_type, 1e-2),
        },
        "emotes": [
            {
                "assetId": 3696763549,
                "assetName": "Heisman Pose",
                "position": 1
            },
            {
                "assetId": 3360692915,
                "assetName": "Tilt",
                "position": 2
            },
            {
                "assetId": 3696761354,
                "assetName": "Air Guitar",
                "position": 3
            },
            {
                "assetId": 3576968026,
                "assetName": "Shrug",
                "position": 4
            },
            {
                "assetId": 3576686446,
                "assetName": "Hello",
                "position": 5
            },
            {
                "assetId": 3696759798,
                "assetName": "Superhero Reveal",
                "position": 6
            },
            {
                "assetId": 3360689775,
                "assetName": "Salute",
                "position": 7
            },
            {
                "assetId": 3360686498,
                "assetName": "Stadium",
                "position": 8
            }
        ]
    })
    return True


@server_path('/v1.1/game-start-info', versions={versions.rōblox.v463})
def _(self: web_server_handler) -> bool:
    '''
    https://github.com/Heliodex/Meteorite/blob/76d53e75dace3195c1068e0de66c137376a88bcf/Back/server.mjs#L3718
    '''
    self.send_json({
        "gameAvatarType": "PlayerChoice",
        "allowCustomAnimations": True,
        "universeAvatarCollisionType": "OuterBox",
        "universeAvatarBodyType": "Standard",
        "jointPositioningType": "ArtistIntent",
        "universeAvatarMinScales": {
            "height": -1e17,
            "width": -1e17,
            "head": -1e17,
            "depth": -1e17,
            "proportion": -1e17,
            "bodyType": -1e17,
        },
        "universeAvatarMaxScales": {
            "height": +1e17,
            "width": +1e17,
            "head": +1e17,
            "depth": +1e17,
            "proportion": +1e17,
            "bodyType": +1e17,
        },
        "universeAvatarAssetOverrides": [],
        "moderationStatus": None,
    })
    return True
