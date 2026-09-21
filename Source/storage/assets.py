from typing import override
from . import _logic
import enum
import time


class database(_logic.sqlite_connector_base):
    '''
    Кэш типов одиночных ассетов из `items` скина: `asset_id` ->
    (asset_type, name). Заполняется из
    `catalog.roblox.com/v1/catalog/items/<id>/details?itemType=Asset`.

    Нужен, чтобы «голый» id из скина не превращался вслепую в аксессуар:
    вебсервер знает, что перед ним — анимация, часть тела или аксессуар.
    '''
    TABLE_NAME = "assets"

    class field(enum.Enum):
        ASSET_ID = '"asset_id"'
        ASSET_TYPE = '"asset_type"'
        NAME = '"name"'
        FETCHED_AT = '"fetched_at"'

    @override
    def first_time_setup(self) -> None:
        self.sqlite.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{self.TABLE_NAME}" (
                {self.field.ASSET_ID.value} INTEGER NOT NULL,
                {self.field.ASSET_TYPE.value} INTEGER NOT NULL,
                {self.field.NAME.value} TEXT,
                {self.field.FETCHED_AT.value} DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(
                    {self.field.ASSET_ID.value}
                ) ON CONFLICT REPLACE
            );
            """,
        )

    def get(
        self,
        asset_id: int,
        max_age: float | None = None,
    ) -> tuple[int, str] | None:
        '''
        Возвращает (asset_type, name) или `None`, если записи нет (или она
        старше `max_age` секунд; `None` в `max_age` — без проверки возраста).
        '''
        result = self.sqlite.execute_and_fetch(
            query=f"""
            SELECT
            {self.field.ASSET_TYPE.value},
            {self.field.NAME.value},
            CAST(strftime('%s', {self.field.FETCHED_AT.value}) AS INTEGER)

            FROM "{self.TABLE_NAME}"
            WHERE {self.field.ASSET_ID.value} = ?
            """,
            values=(asset_id,),
        )
        if result is None or isinstance(result, Exception) or len(result) == 0:
            return None

        (asset_type, name, fetched_epoch) = result[0]
        if max_age is not None:
            if fetched_epoch is None or fetched_epoch < 0:
                return None
            if time.time() - fetched_epoch > max_age:
                return None
        return (
            int(asset_type),
            '' if name is None else str(name),
        )

    def put(
        self,
        asset_id: int,
        asset_type: int,
        name: str,
    ) -> None:
        self.sqlite.execute(
            f"""
            INSERT INTO "{self.TABLE_NAME}"
            (
                {self.field.ASSET_ID.value},
                {self.field.ASSET_TYPE.value},
                {self.field.NAME.value},
                {self.field.FETCHED_AT.value}
            )
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT({self.field.ASSET_ID.value})
            DO UPDATE SET
                {self.field.ASSET_TYPE.value} = ?,
                {self.field.NAME.value} = ?,
                {self.field.FETCHED_AT.value} = CURRENT_TIMESTAMP
            """,
            (
                asset_id,
                asset_type,
                name,
                asset_type,
                name,
            ),
        )
