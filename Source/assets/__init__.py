# Standard library imports
from typing import Callable
import dataclasses
import functools
import shutil
import os

# Internal or local application imports
import util.const
from . import material, queue, returns, serialisers, extractor, thumbnail, toolbox


@dataclasses.dataclass
class asset_redirect:
    def __post_init__(self) -> None:
        if sum([
            self.cmd_line is not None,
            self.raw_data is not None,
            self.forward_url is not None,
        ]) > 1:
            raise Exception(
                'Entries for `asset_redirects` should not have ' +
                'more than one of a `forward_url`, a pipeable `cmd_line`, or a `raw_data` chunk.'
            )
    forward_url: str | None = None
    raw_data: bytes | None = None
    cmd_line: str | None = None


class asseter:
    def __init__(
        self,
        dir_path: str,
        redirect_func: Callable[[int | str], asset_redirect | None],
        asset_name_func: Callable[[int | str], str],
        clear_on_start: bool,
        shared_dir_path: str | None = None,
        place_iden: int = util.const.PLACE_IDEN_CONST,
    ) -> None:
        super().__init__()
        # Локальный кэш плейса: файл плейса, иконка, legacy-наследство.
        self.dir_path = dir_path
        # Общий пул на все плейсы (`data/Assets`): всё скачанное с roblox.com
        # копится там один раз. `None` — пул отключён.
        self.shared_dir_path = shared_dir_path
        # Идентификатор плейса из конфига: под ним лежит карта и по нему
        # работает 403-защита.
        self.place_iden = place_iden
        self.redirect_func = redirect_func
        self.asset_name_func = asset_name_func
        self.queuer = queue.queuer()

        if os.path.isdir(dir_path):
            if clear_on_start:
                # Чистится ТОЛЬКО локальный кэш: общий пул переживает плейсы.
                shutil.rmtree(dir_path)
                os.makedirs(dir_path)
        else:
            os.makedirs(dir_path)

        if shared_dir_path is not None and not os.path.isdir(shared_dir_path):
            os.makedirs(shared_dir_path)

    def _is_local_only(self, asset_id: int | str) -> bool:
        '''
        Файл плейса и иконка плейса живут только в локальном кэше: в общем
        пуле они затирали бы друг друга между плейсами, а карта ещё и стала
        бы скачиваемой с любого другого сервера.
        '''
        return (
            asset_id == self.place_iden or
            asset_id == util.const.THUMBNAIL_ID_CONST
        )

    @functools.cache
    def _build_asset_path(self, base_dir: str, asset_id: int | str) -> str:
        # Build the candidate path then normalise and convert to an absolute path.
        candidate_abs = os.path.abspath(os.path.join(
            base_dir, self.asset_name_func(asset_id),
        ))
        base_abs = os.path.abspath(base_dir)

        # Ensure the resolved candidate path is inside the asset cache directory.
        # This defends against path traversal (e.g. id="..\GameConfig.toml") and
        # absolute paths supplied as asset ids.
        if not (candidate_abs == base_abs or candidate_abs.startswith(base_abs + os.sep)):
            # Construct a safe filename fallback derived from the asset iden.
            # Keep only alphanumerics, dash and underscore; replace others with underscore.
            safe_name = ''.join(
                (c if (c.isalnum() or c in ('-', '_')) else '_') for c in str(asset_id)
            )
            candidate_abs = os.path.join(base_abs, safe_name)

        return candidate_abs

    def get_asset_path(self, asset_id: int | str) -> str:
        '''Путь в локальном кэше плейса.'''
        return self._build_asset_path(self.dir_path, asset_id)

    @functools.cache
    def get_shared_asset_path(self, asset_id: int | str) -> str:
        '''Путь в общем пуле (`data/Assets`).'''
        assert self.shared_dir_path is not None
        return self._build_asset_path(self.shared_dir_path, asset_id)

    def _load_file(self, path: str) -> bytes | None:
        if not os.path.isfile(path):
            return None

        with open(path, 'rb') as f:
            return f.read()

    def _save_file(self, path: str, data: bytes) -> None:
        try:
            with open(path, 'wb') as f:
                f.write(data)
        except OSError:
            pass

    def _load_online_asset(self, asset_id: int) -> bytes | None:
        data = self.queuer.get(asset_id, extractor.download_rōblox_asset)
        if data is None:
            return None

        data, _changed = serialisers.parse(data)
        return data

    def resolve_asset_id(self, id_str: str | None) -> int | None:
        if id_str is None:
            return None
        try:
            return int(id_str)
        except ValueError:
            return None

    def resolve_asset_version_id(self, id_str: str | None) -> int | None:
        # Don't assume this is true for Rōblox.com:
        # RFD treats 'asset version idens' the same way as just plain 'version idens'.
        return self.resolve_asset_id(id_str)

    def resolve_asset_query(self, query: dict[str, str]) -> int | str | None:
        candidate_funcs = [
            (query.get('id'), self.resolve_asset_id),
            (query.get('ID'), self.resolve_asset_id),
            (query.get('aid'), self.resolve_asset_id),
            (query.get('AssetID'), self.resolve_asset_id),
            (query.get('assetid'), self.resolve_asset_id),
            (query.get('assetId'), self.resolve_asset_id),
            (query.get('assetversionid'), self.resolve_asset_version_id),
        ]

        for (prop_val, func) in candidate_funcs:
            if prop_val is None:
                continue
            result = func(prop_val)
            if result is not None:
                return result
        for (prop_val, func) in candidate_funcs:
            if prop_val is not None:
                return prop_val
        return None

    def add_asset(self, asset_id: int | str, data: bytes) -> None:
        path = self.get_asset_path(asset_id)
        if self._load_file(path) == data:
            return
        self._save_file(path, data)

    @functools.cache
    def is_blocklisted(self, asset_id: int | str) -> bool:
        '''
        This is to make sure that unauthorised clients can't get private (i.e., place map) files.
        '''
        asset_path = self.get_asset_path(asset_id)
        place_path = self.get_asset_path(self.place_iden)
        if asset_path == place_path:
            return True
        return False

    def _load_asset_num(self, asset_id: int) -> bytes | None:
        return self._load_online_asset(asset_id)

    def _load_asset_str(self, asset_id: str) -> bytes | None:
        if material.check(asset_id):
            return material.load_asset(asset_id)
        if thumbnail.check(asset_id):
            return thumbnail.load_asset(asset_id)
        return None

    def _load_redir_asset(self, asset_id: int | str, redirect: asset_redirect) -> returns.base_type | None:
        asset_path = self.get_asset_path(asset_id)
        local_data = self._load_file(asset_path)
        if local_data is not None:
            return returns.construct(data=local_data)

        if redirect.forward_url is not None:
            return returns.construct(
                redirect_url=redirect.forward_url,
            )
        elif redirect.cmd_line is not None:
            # NOTE: redirect asset ids which share the same command line may also share the same output dump.
            # This happens when both assets are loaded near the same time.
            return returns.construct(
                data=self.queuer.get(
                    redirect.cmd_line,
                    extractor.process_command_line,
                ),
            )
        elif redirect.raw_data is not None:
            return returns.construct(
                data=redirect.raw_data,
            )
        else:
            return returns.construct()

    def _fetch_asset(self, asset_id: int | str) -> returns.base_type:
        redirect_info = self.redirect_func(asset_id)
        if redirect_info is not None:
            redir = self._load_redir_asset(
                asset_id=asset_id, redirect=redirect_info,
            )
            if redir is not None:
                return redir

        if isinstance(asset_id, str):
            remote_data = self._load_asset_str(asset_id)
        else:
            remote_data = self._load_asset_num(asset_id)
        return returns.construct(data=remote_data)

    def _cache_asset(self, asset_id: int | str, data: bytes) -> None:
        if self._is_local_only(asset_id):
            self._save_file(self.get_asset_path(asset_id), data)
        elif self.shared_dir_path is not None:
            self._save_file(self.get_shared_asset_path(asset_id), data)
        else:
            self._save_file(self.get_asset_path(asset_id), data)

    def get_asset(
        self,
        asset_id: int | str,
        bypass_blocklist: bool = False
    ) -> returns.base_type:
        if not bypass_blocklist and self.is_blocklisted(asset_id):
            return returns.construct(error='Asset is blocklisted.')

        if toolbox.is_toolbox_id(asset_id):
            # Ассеты локального тулбокса живут только в data/Toolbox: мимо
            # кэша плейса, общего пула и интернета. Читаем с диска каждый
            # раз — файлы могут меняться на лету.
            data = toolbox.load_asset_bytes(asset_id)
            if data is not None:
                return returns.construct(data=data)
            return returns.construct(error='Toolbox asset not found.')

        asset_path = self.get_asset_path(asset_id)
        local_data = self._load_file(asset_path)
        if local_data is not None:
            return returns.construct(data=local_data)

        if self.shared_dir_path is not None:
            shared_data = self._load_file(self.get_shared_asset_path(asset_id))
            if shared_data is not None:
                return returns.construct(data=shared_data)

        result_data = self._fetch_asset(asset_id)
        if isinstance(result_data, returns.ret_data):
            self._cache_asset(asset_id, result_data.data)

        return result_data
