#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asset_cache_clean.py — чистка текстур классической одежды из кэша asset_cache.

Текстуры классической одежды — это ассеты типа 1 (Image) с Description ровно
«Shirt Image» / «Pants Image» / «TShirt Image». Сам ассет тут лишь обложка
настоящей одежды (Shirt=11 / Pants=12 / TShirt=2), так что asset_cache теперь
их скипает. Этот скрипт за один проход удаляет такие дубли из старых прогонов:
файлы (<id>.info.json, <id>.thumb.png, <id>.rbxm) и записи в sqlite-базе.

Ищет дубли по двум источникам:
  1) записи в базе (asset_type_id=1 + clothing-Description);
  2) файлы *.info.json в подпапках out-каталога — ловит и осиротевшие файлы,
     которых в базе уже нет.

Использование:
    python3 asset_cache_clean.py --dry-run    # показать, что удалится
    python3 asset_cache_clean.py              # удалить
Опции --out / --db те же, что у asset_cache.py.
"""

import argparse
import glob
import json
import os
import sqlite3
import sys

from asset_cache import CLOTHING_TEXTURE_DESC, DEFAULT_OUT

DESC_LIST = tuple(CLOTHING_TEXTURE_DESC)


def find_ids(db_path: str, out_dir: str) -> set:
    ids = set()

    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT id FROM objects WHERE asset_type_id=1 AND status<>'skipped' "
            "AND TRIM(COALESCE(description,'')) IN (?,?,?)",
            DESC_LIST,
        ).fetchall()
        ids |= {r[0] for r in rows}
        conn.close()

    # файлы — источник истины про то, что реально лежит на диске
    for info in glob.glob(os.path.join(out_dir, "*", "*.info.json")):
        try:
            with open(info, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get("AssetTypeID") == 1 and (d.get("Description") or "").strip() in CLOTHING_TEXTURE_DESC:
            stem = os.path.basename(info).split(".")[0]
            if stem.isdigit():
                ids.add(int(stem))

    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description="Удалить текстуры классической одежды из кэша asset_cache.")
    ap.add_argument("--out", default=DEFAULT_OUT, help="каталог кэша")
    ap.add_argument("--db", help="путь к sqlite (по умолчанию <out>/assetcache.db)")
    ap.add_argument("--dry-run", action="store_true", help="только показать, ничего не удалять")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out)
    db_path = os.path.abspath(args.db) if args.db else os.path.join(out_dir, "assetcache.db")
    ids = find_ids(db_path, out_dir)

    if not ids:
        print("Текстур классической одежды не найдено — чистить нечего.")
        return 0

    print(f"Найдено {len(ids)} текстур одежды" + (" (dry-run, ничего не удаляем):" if args.dry_run else ":"))
    deleted_files = deleted_rows = 0
    conn = sqlite3.connect(db_path) if os.path.exists(db_path) else None

    for oid in sorted(ids):
        matches = glob.glob(os.path.join(out_dir, "*", f"{oid}.*"))
        rel = ", ".join(os.path.relpath(m, out_dir) for m in matches) or "(файлов нет, только запись в базе)"
        print(f"  {oid:>15}  {rel}")

        if args.dry_run:
            continue
        for m in matches:
            os.remove(m)
            deleted_files += 1
        if conn:
            cur = conn.execute(
                "DELETE FROM objects WHERE id=? AND asset_type_id=1 AND status<>'skipped' "
                "AND TRIM(COALESCE(description,'')) IN (?,?,?)",
                (oid,) + DESC_LIST,
            )
            deleted_rows += cur.rowcount

    if conn:
        conn.commit()
        conn.close()
    if not args.dry_run:
        # подчищаем подпапки типов, опустевшие после удаления
        for entry in sorted(os.listdir(out_dir)):
            sub = os.path.join(out_dir, entry)
            if os.path.isdir(sub) and not os.listdir(sub):
                os.rmdir(sub)
        print(f"Удалено: файлов {deleted_files}, записей из базы {deleted_rows}.")
        print("Важно: asset_cache при следующих прогонах снова увидит эти id, узнает про них и")
        print("запишет как skipped — повторно файлы качаться не будут.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
