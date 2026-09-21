import functools
import os.path
import threading

import util.resource
from vendored import sqlite_worker
from storage import bundles as bundles_table
from storage import assets as assets_table


class catalog_cache:
    '''
    Глобальный кэш каталога Roblox (бандлы + типы ассетов), общий для всех
    плейсов и процессов. Живёт отдельным файлом `catalog.sqlite` в корне
    данных (`<rbxd>/data`, см. util.resource.get_rfd_top_dir) — не в
    пер-плейсовом `_.sqlite`, потому что скины и бандлы глобальны, а не
    принадлежат плейсу.

    Файл — обычный sqlite: набежавший за месяц кэш остаётся на диске, и при
    отсутствии интернета avatar-fetch отвечает из него (см. фолбэки в
    `web_server/endpoints/avatar.py`).
    '''

    def __init__(self, path: str) -> None:
        self.sqlite = sqlite_worker.SqliteWorker(path)
        self.bundles = bundles_table.database(self.sqlite, is_first_time=False)
        self.assets = assets_table.database(self.sqlite, is_first_time=False)

    def close(self) -> None:
        self.sqlite.close()


_catalog_cache_lock = threading.Lock()


@functools.cache
def get_catalog_cache() -> catalog_cache:
    base_dir = util.resource.get_rfd_top_dir()
    os.makedirs(base_dir, exist_ok=True)
    # `functools.cache` сам по себе не гарантирует, что функция выполнится
    # один раз: два запроса аватара могут прийти одновременно до первого
    # кэширования, и тогда породятся два `SqliteWorker` на одном sqlite-файле
    # (один из них утечёт как висящий демон-поток с открытым соединением).
    # Лок внутри функции это исключает.
    with _catalog_cache_lock:
        return catalog_cache(os.path.join(base_dir, 'catalog.sqlite'))
