from typing import override
from . import _logic
import enum
import time


class database(_logic.sqlite_connector_base):
    '''
    Кэш развёрнутых бандлов: `bundle_id` -> список элементов
    (asset_id, asset_type, name), полученных из
    `catalog.roblox.com/v1/bundles/<id>/details`.

    Хранит не голые id, а вместе с `asset_type`, чтобы вебсервер всегда знал,
    что перед ним — анимация, часть тела или аксессуар, без повторных запросов
    к каталогу.
    '''
    TABLE_NAME = "bundles"

    class field(enum.Enum):
        BUNDLE_ID = '"bundle_id"'
        ITEM_INDEX = '"item_index"'
        ASSET_ID = '"asset_id"'
        ASSET_TYPE = '"asset_type"'
        NAME = '"name"'
        FETCHED_AT = '"fetched_at"'

    @override
    def first_time_setup(self) -> None:
        self.sqlite.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{self.TABLE_NAME}" (
                {self.field.BUNDLE_ID.value} INTEGER NOT NULL,
                {self.field.ITEM_INDEX.value} INTEGER NOT NULL,
                {self.field.ASSET_ID.value} INTEGER NOT NULL,
                {self.field.ASSET_TYPE.value} INTEGER NOT NULL,
                {self.field.NAME.value} TEXT,
                {self.field.FETCHED_AT.value} DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(
                    {self.field.BUNDLE_ID.value},
                    {self.field.ITEM_INDEX.value}
                ) ON CONFLICT REPLACE
            );
            """,
        )

    def get(
        self,
        bundle_id: int,
        max_age: float | None = None,
    ) -> list[tuple[int, int, str]] | None:
        '''
        Возвращает элементы бандла из кэша или `None`, если бандла нет в кэше
        (или он старше `max_age` секунд; `None` в `max_age` — без проверки
        возраста, используется как фолбэк при ошибке сети).
        '''
        result = self.sqlite.execute_and_fetch(
            query=f"""
            SELECT
            {self.field.ASSET_ID.value},
            {self.field.ASSET_TYPE.value},
            {self.field.NAME.value},
            CAST(strftime('%s', {self.field.FETCHED_AT.value}) AS INTEGER)

            FROM "{self.TABLE_NAME}"
            WHERE {self.field.BUNDLE_ID.value} = ?
            ORDER BY {self.field.ITEM_INDEX.value} ASC
            """,
            values=(bundle_id,),
        )
        if result is None or isinstance(result, Exception) or len(result) == 0:
            return None

        items: list[tuple[int, int, str]] = []
        for (asset_id, asset_type, name, fetched_epoch) in result:
            if max_age is not None:
                if fetched_epoch is None:
                    return None
                if fetched_epoch < 0:
                    return None
                if time.time() - fetched_epoch > max_age:
                    return None
            items.append((
                int(asset_id),
                int(asset_type),
                '' if name is None else str(name),
            ))
        return items

    def put(
        self,
        bundle_id: int,
        items: list[tuple[int, int, str]],
    ) -> None:
        '''
        Перезаписывает кэш бандла свежими данными из каталога.
        '''
        self.sqlite.execute(
            f"""
            DELETE FROM "{self.TABLE_NAME}"
            WHERE {self.field.BUNDLE_ID.value} = ?
            """,
            (bundle_id,),
        )
        for (index, (asset_id, asset_type, name)) in enumerate(items):
            self.sqlite.execute(
                f"""
                INSERT INTO "{self.TABLE_NAME}"
                (
                    {self.field.BUNDLE_ID.value},
                    {self.field.ITEM_INDEX.value},
                    {self.field.ASSET_ID.value},
                    {self.field.ASSET_TYPE.value},
                    {self.field.NAME.value},
                    {self.field.FETCHED_AT.value}
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    bundle_id,
                    index,
                    asset_id,
                    asset_type,
                    name,
                ),
            )
