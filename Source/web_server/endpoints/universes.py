# Standard library imports
import datetime

# Local application imports
import util.auth
from web_server._logic import web_server_handler, server_path


'''
Универсумы для студийного Publish-диалога (PublishPlaceAs 2021E).

Контракты сняты с OpenPekora (`RobloxApi/Universe.cs`):
  * `GET /v1/search/universes?q=creator:User|Team` — список игр юзера
    (в диалоге это ScreenChooseGame -> LoadExistingGames);
  * `GET /v1/user/groups/canmanage` — группы с правом публикации
    (LoadGroups); у нас групп нет — пустой список;
  * `POST /universes/create` — «Create New Game». Публикуем всегда в
    ТЕКУЩИЙ плейс (модель пользователя: тот же .rbxl, тот же плейс),
    поэтому вселенная = текущий game_config. Настоящее создание
    отдельных плейсов — фаза 2 (нужен реестр плейсов).
'''


def _universe_entry(
    self: web_server_handler,
    creator_id: int,
) -> dict:
    config = self.game_config
    place_iden = config.game_setup.place_iden
    metadata = config.server_core.metadata
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        'id': place_iden,
        'name': metadata.title,
        'description': metadata.description,
        'isArchived': False,
        'rootPlaceId': place_iden,
        'isActive': True,
        'privacyType': 'Public',
        'creatorType': 'User',
        'creatorTargetId': creator_id,
        'creatorName': metadata.creator_name,
        'created': now,
        'updated': now,
    }


@server_path('/v1/user/groups/canmanage')
def _(self: web_server_handler) -> bool:
    self.send_json({'data': []})
    return True


@server_path('/v1/search/universes')
def _(self: web_server_handler) -> bool:
    creator_id = 0
    if util.auth.is_studio_mode(self):
        identity = util.auth.get_studio_player_identity(self)
        if identity is not None:
            creator_id = identity[0]

    self.send_json({
        'previousPageCursor': None,
        'nextPageCursor': None,
        'data': [_universe_entry(self, creator_id)],
    })
    return True


@server_path('/universes/create', commands={'POST', 'GET'})
def _(self: web_server_handler) -> bool:
    place_iden = self.game_config.game_setup.place_iden
    self.send_json({'placeId': place_iden, 'universeId': place_iden})
    return True
