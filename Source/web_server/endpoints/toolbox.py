# Standard library imports
import os
import struct
import zlib

# Internal or local application imports
import assets.toolbox
from web_server._logic import web_server_handler, server_path
import util.auth


DEFAULT_NUM = 30
MAX_NUM = 100


def _creator_info(self: web_server_handler) -> tuple[int, str]:
    '''
    Автор для элементов тулбокса: в studio-режиме — текущий пользователь
    (та же личность, что и в игре), иначе — создатель плейса из метаданных.
    '''
    identity = util.auth.get_studio_player_identity(self)
    if identity is not None:
        return identity
    metadata = self.game_config.server_core.metadata
    return (1, metadata.creator_name)


def _entry_result(
    self: web_server_handler,
    entry: assets.toolbox.toolbox_entry,
    creator_id: int,
    creator_name: str,
) -> dict:
    '''
    Формат элемента — как в старом веб-тулбоксе (контракт 2016-2019,
    снят с живого лога студии и с доноров rblxDOTLocal / Epic.VIP).
    '''
    thumbnail_url = f'{self.hostname}/model-thumbnails?assetId={entry.id_num}'
    return {
        'Asset': {
            'Id': entry.id_num,
            'Name': entry.name,
            'TypeId': assets.toolbox.CATEGORY_TYPE_IDS.get(entry.category, 10),
            'IsEndorsed': False,
        },
        'Creator': {
            'Id': creator_id,
            'Name': creator_name,
            'Type': 1,
        },
        'Thumbnail': {
            'Final': True,
            'Url': thumbnail_url,
            'RetryUrl': thumbnail_url,
            'UserId': creator_id,
            'EndpointType': 'Avatar',
        },
        'Voting': {
            'ShowVotes': False,
            'UpVotes': 0,
            'DownVotes': 0,
            'CanVote': False,
            'UserVote': False,
            'HasVoted': False,
            'ReasonForNotVoteable': None,
        },
    }


@server_path('/ide/toolbox/items')
@server_path('/IDE/Toolbox/Items')
@server_path('/Ide/Toolbox/Items')
def _(self: web_server_handler) -> bool:
    try:
        num = min(max(int(self.query.get('num', DEFAULT_NUM)), 1), MAX_NUM)
    except ValueError:
        num = DEFAULT_NUM
    try:
        page = max(int(self.query.get('page', 1)), 1)
    except ValueError:
        page = 1

    entries = assets.toolbox.filter_entries(
        assets.toolbox.scan(),
        self.query.get('category', ''),
        self.query.get('keyword', ''),
    )
    creator_id, creator_name = _creator_info(self)

    start = (page - 1) * num
    self.send_json({
        'TotalResults': len(entries),
        'Results': [
            _entry_result(self, entry, creator_id, creator_name)
            for entry in entries[start:start + num]
        ],
    })
    return True


def _build_placeholder_png(width: int = 100, height: int = 100) -> bytes:
    '''
    Серая заглушка превью, сгенерированная на месте: в репозитории не нужен
    бинарный файл, а знакомый вид «нет картинки» сохраняется.
    '''
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack('>I', len(data)) + tag + data +
            struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
        )

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    row = b'\x00' + bytes((0xD9, 0xD9, 0xD9)) * width
    raw = row * height
    return (
        b'\x89PNG\r\n\x1a\n' +
        chunk(b'IHDR', ihdr) +
        chunk(b'IDAT', zlib.compress(raw)) +
        chunk(b'IEND', b'')
    )


PLACEHOLDER_PNG = _build_placeholder_png()

PREVIEW_CONTENT_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
}


@server_path('/model-thumbnails')
def _(self: web_server_handler) -> bool:
    '''
    Превью элемента тулбокса: <имя>.png рядом с <имя>.rbxm, если есть;
    иначе — серая заглушка. no-store, чтобы замена картинки на диске
    не залипала в кэше браузера студии.
    '''
    try:
        asset_id = int(self.query.get('assetId', 0))
    except ValueError:
        self.send_error(404)
        return True

    entry = assets.toolbox.find_entry(asset_id)
    if entry is not None and entry.preview_path is not None:
        try:
            with open(entry.preview_path, 'rb') as f:
                data = f.read()
            content_type = PREVIEW_CONTENT_TYPES.get(
                os.path.splitext(entry.preview_path)[1].lower(),
                'image/png',
            )
        except OSError:
            data = None
    else:
        data = None

    if data is None:
        data = PLACEHOLDER_PNG
        content_type = 'image/png'

    self.send_data(data, headers={
        'Content-Type': content_type,
        'Cache-Control': 'no-store',
    })
    return True


CLIENT_TOOLBOX_PAGE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'static', 'toolbox', 'clienttoolbox.html',
)


@server_path('/ide/clienttoolbox')
@server_path('/Ide/ClientToolbox')
@server_path('/IDE/ClientToolbox.aspx')
def _(self: web_server_handler) -> bool:
    '''
    Веб-страница тулбокса: студия 2016-2021 показывает её во встроенном
    браузере панели Toolbox (в exe v347 зашита ссылка ide/clienttoolbox).
    Все ссылки внутри страницы относительные.
    '''
    try:
        with open(CLIENT_TOOLBOX_PAGE, 'rb') as f:
            content = f.read()
    except OSError:
        self.send_error(404)
        return True

    self.send_data(content, headers={
        'Content-Type': 'text/html',
        'Cache-Control': 'no-store',
    })
    return True
