#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asset_cache.py — офлайн-кэш информации и обложек ассетов Roblox (формат Revival Kanvas).

Скачивает details + thumbnail для одиночных ассетов (шляпы, штаны, меши...),
бандлов и геймпассов и складывает файлы так, как их ждёт Kanvas, раскладывая
по подпапкам имени типа ассета (AssetTypeId -> имя см. ASSET_TYPES.md):

    <out>/Hat/12345.info.json       {"Name","AssetTypeID","Description","Creator",...}
    <out>/Hat/12345.thumb.png       обложка ассета (420x420)
    <out>/Bundles/108.package.json  {"Name","Assets":[{"ID","AssetTypeID"},...]}
    <out>/Bundles/108.package.thumb.png
    <out>/GamePass/275254.info.json

Неизвестному AssetTypeId соответствует папка Type<N>.

Ничего не сканирует по папкам: факт «ассет уже скачан» хранится в sqlite-базе.
Если id есть в базе со статусом ok — он пропускается. Ошибочные записи
(status=failed) при следующем запуске дозакачиваются.

Использование:
    python3 asset_cache.py 108 311450081 1028713     # тип определяется сам
    python3 asset_cache.py --kind bundle 108
    python3 asset_cache.py --kind pass 275254
    python3 asset_cache.py --from-file ids.txt       # по одному id на строку
    python3 asset_cache.py --from-dir DIR            # файлы с числовыми именами = id
    python3 asset_cache.py --rbxm 311450081          # ещё и сам ассет (.rbxm)
    python3 asset_cache.py --force 108               # перекачать, игнорируя базу
    python3 asset_cache.py --list                    # что уже в базе

Опции:
    --out DIR    каталог для файлов (по умолчанию ./assetcache рядом со скриптом)
    --db FILE    путь к sqlite-базе (по умолчанию <out>/assetcache.db)
"""

import argparse
import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "assetcache")

ASSET_DETAILS_URL = "https://economy.roblox.com/v2/assets/{id}/details"
BUNDLE_DETAILS_URL = "https://catalog.roblox.com/v1/bundles/{id}/details"
PASS_DETAILS_URL = "https://apis.roblox.com/game-passes/v1/game-passes/{id}/product-info"
ASSET_RBXM_URL = "https://assetdelivery.roblox.com/v1/asset/?id={id}"

# thumbs-эндпойнты батчат id через запятую — гоняем пачками
ASSET_THUMBS_URL = "https://thumbnails.roblox.com/v1/assets?assetIds={ids}&size=420x420&format=Png"
BUNDLE_THUMBS_URL = "https://thumbnails.roblox.com/v1/bundles/thumbnails?bundleIds={ids}&size=420x420&format=Png"
PASS_THUMBS_URL = "https://thumbnails.roblox.com/v1/game-passes?gamePassIds={ids}&size=150x150&format=Png"

THUMB_BATCH = 50          # id за один запрос к thumbnails
REQUEST_PAUSE = 0.4       # пауза между запросами к Roblox, сек
TIMEOUT = 20

# AssetTypeId -> имя типа (папка для файлов). Полная таблица: ASSET_TYPES.md
ASSET_TYPE_NAMES = {
    1: "Image", 2: "TShirt", 3: "Audio", 4: "Mesh", 5: "Lua", 8: "Hat",
    9: "Place", 10: "Model", 11: "Shirt", 12: "Pants", 13: "Decal",
    17: "Head", 18: "Face", 19: "Gear", 24: "Animation",
    27: "Torso", 28: "RightArm", 29: "LeftArm", 30: "LeftLeg", 31: "RightLeg",
    32: "Package", 38: "Plugin", 39: "SolidModel", 40: "MeshPart",
    41: "HairAccessory", 42: "FaceAccessory", 43: "NeckAccessory",
    44: "ShoulderAccessory", 45: "FrontAccessory", 46: "BackAccessory",
    47: "WaistAccessory", 48: "ClimbAnimation", 49: "DeathAnimation",
    50: "FallAnimation", 51: "IdleAnimation", 52: "JumpAnimation",
    53: "RunAnimation", 54: "SwimAnimation", 55: "WalkAnimation",
    56: "PoseAnimation", 57: "EarAccessory", 58: "EyeAccessory",
    61: "EmoteAnimation", 62: "Video", 64: "TShirtAccessory",
    65: "ShirtAccessory", 66: "PantsAccessory", 67: "JacketAccessory",
    68: "SweaterAccessory", 69: "ShortsAccessory", 70: "LeftShoeAccessory",
    71: "RightShoeAccessory", 72: "DressSkirtAccessory", 73: "FontFamily",
    74: "FontFace", 75: "MeshHiddenSurfaceRemoval", 76: "EyebrowAccessory",
    77: "EyelashAccessory", 78: "MoodAnimation", 79: "DynamicHead",
    88: "FaceMakeup", 89: "LipMakeup", 90: "EyeMakeup",
    92: "AvatarBackground", 93: "TextDocument",
}

# Текстуры классической одежды лежат как Image (AssetTypeId 1) с таким
# Description. Сам ассет — лишь обложка настоящей одежды (Shirt=11/Pants=12/
# TShirt=2), поэтому такие не скачиваем вовсе, а пишем в базу как skipped.
CLOTHING_TEXTURE_DESC = {"Shirt Image": 11, "Pants Image": 12, "TShirt Image": 2}


# --------------------------------------------------------------------------- #
#  HTTP
# --------------------------------------------------------------------------- #

_SSL_CTX = ssl.create_default_context()


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "rbxd-asset-cache/1.0",
        "Accept": "application/json, image/png, application/octet-stream",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
        return resp.read()


def http_get_json(url: str):
    return json.loads(http_get(url).decode("utf-8"))


class FetchError(Exception):
    """Ошибка сети/эндпойнта, попадает в базу как failed."""


def fetch_json(url: str):
    try:
        return http_get_json(url)
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}: {url}") from e
    except Exception as e:
        raise FetchError(f"{type(e).__name__}: {url} ({e})") from e


# --------------------------------------------------------------------------- #
#  База
# --------------------------------------------------------------------------- #

def db_open(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS objects (
            id            INTEGER PRIMARY KEY,   -- assetId / bundleId / gamePassId
            kind          TEXT NOT NULL,         -- asset | bundle | pass
            name          TEXT,
            asset_type_id INTEGER,
            creator       TEXT,
            description   TEXT,
            thumb_state   TEXT,                  -- Completed / Blocked / ...
            status        TEXT NOT NULL,         -- ok | failed
            error         TEXT,
            fetched_at    TEXT,
            raw           TEXT                   -- полный JSON ответа details
        )
    """)
    conn.commit()
    return conn


def db_get(conn, obj_id):
    row = conn.execute("SELECT kind, status FROM objects WHERE id=?", (obj_id,)).fetchone()
    return row


def db_save(conn, rec: dict):
    rec = dict(rec)
    if isinstance(rec.get("raw"), (dict, list)):
        rec["raw"] = json.dumps(rec["raw"], ensure_ascii=False)
    conn.execute("""
        INSERT INTO objects (id, kind, name, asset_type_id, creator, description,
                             thumb_state, status, error, fetched_at, raw)
        VALUES (:id, :kind, :name, :asset_type_id, :creator, :description,
                :thumb_state, :status, :error, :fetched_at, :raw)
        ON CONFLICT(id) DO UPDATE SET
            kind=excluded.kind, name=excluded.name, asset_type_id=excluded.asset_type_id,
            creator=excluded.creator, description=excluded.description,
            thumb_state=excluded.thumb_state, status=excluded.status,
            error=excluded.error, fetched_at=excluded.fetched_at, raw=excluded.raw
    """, rec)
    conn.commit()


# --------------------------------------------------------------------------- #
#  Загрузка details
# --------------------------------------------------------------------------- #

def _clean_str(v):
    return (v or "").strip()


def fetch_asset_details(obj_id: int) -> dict:
    raw = fetch_json(ASSET_DETAILS_URL.format(id=obj_id))
    if not raw.get("Name"):
        raise FetchError(f"пустой ответ details (TargetId={raw.get('TargetId')})")
    return {
        "id": obj_id,
        "kind": "asset",
        "name": _clean_str(raw.get("Name")),
        "asset_type_id": raw.get("AssetTypeId") or 0,
        "creator": _clean_str((raw.get("Creator") or {}).get("Name")),
        "description": _clean_str(raw.get("Description")),
        "raw": raw,
    }


def fetch_bundle_details(obj_id: int) -> dict:
    raw = fetch_json(BUNDLE_DETAILS_URL.format(id=obj_id))
    if raw.get("name") is None:
        raise FetchError(f"бандл {obj_id} не найден")
    return {
        "id": obj_id,
        "kind": "bundle",
        "name": _clean_str(raw.get("name")),
        "asset_type_id": None,
        "creator": _clean_str((raw.get("creator") or {}).get("name")),
        "description": _clean_str(raw.get("description")),
        "raw": raw,
    }


def fetch_pass_details(obj_id: int) -> dict:
    raw = fetch_json(PASS_DETAILS_URL.format(id=obj_id))
    if not raw.get("Name"):
        raise FetchError(f"геймпасс {obj_id} не найден")
    return {
        "id": obj_id,
        "kind": "pass",
        "name": _clean_str(raw.get("Name")),
        "asset_type_id": raw.get("AssetTypeId") or 0,
        "creator": _clean_str((raw.get("Creator") or {}).get("Name")),
        "description": _clean_str(raw.get("Description")),
        "raw": raw,
    }


DETAIL_FETCHERS = {
    "asset": fetch_asset_details,
    "bundle": fetch_bundle_details,
    "pass": fetch_pass_details,
}


def detect_kind(obj_id: int) -> dict:
    """Пробует asset, потом bundle. Отдельные id-пространства, поэтому
    однозначность гарантируется только явным --kind."""
    try:
        return fetch_asset_details(obj_id)
    except FetchError:
        pass
    time.sleep(REQUEST_PAUSE)
    return fetch_bundle_details(obj_id)  # если и тут не выйдет — FetchError наверх


# --------------------------------------------------------------------------- #
#  Обложки
# --------------------------------------------------------------------------- #

def fetch_thumb(kind: str, obj_id: int) -> tuple:
    """Возвращает (state, image_url)."""
    url = {
        "asset": ASSET_THUMBS_URL,
        "bundle": BUNDLE_THUMBS_URL,
        "pass": PASS_THUMBS_URL,
    }[kind].format(ids=obj_id)
    try:
        data = fetch_json(url)
    except FetchError as e:
        return (f"request-failed: {e}", None)
    items = data.get("data", [])
    if not items:
        return ("missing-from-response", None)
    return (items[0].get("state"), items[0].get("imageUrl"))


def download_thumb(image_url: str, dest_path: str) -> None:
    blob = http_get(image_url)
    with open(dest_path, "wb") as f:
        f.write(blob)


# --------------------------------------------------------------------------- #
#  Запись файлов (формат Kanvas)
# --------------------------------------------------------------------------- #

def write_info_file(out_dir: str, rec: dict) -> None:
    info = {
        "Name": rec["name"],
        "AssetTypeID": rec["asset_type_id"],
        "Description": rec["description"],
        "Creator": rec["creator"],
    }
    raw = rec["raw"]
    if rec["kind"] == "pass":
        info["PriceInRobux"] = raw.get("PriceInRobux")
        info["IconImageAssetId"] = raw.get("IconImageAssetId")
    else:
        info["Created"] = raw.get("Created") or raw.get("created")
        info["Updated"] = raw.get("Updated") or raw.get("updated")
    path = os.path.join(out_dir, f"{rec['id']}.info.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)


def write_package_file(out_dir: str, rec: dict) -> None:
    items = [it for it in rec["raw"].get("items", []) if it.get("type") == "Asset"]
    package = {
        "Name": rec["name"],
        "Assets": [{"ID": it["id"], "AssetTypeID": it.get("assetType")} for it in items],
    }
    path = os.path.join(out_dir, f"{rec['id']}.package.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(package, f, ensure_ascii=False, indent=2)


def write_rbxm(out_dir: str, obj_id: int) -> bool:
    try:
        blob = http_get(ASSET_RBXM_URL.format(id=obj_id))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"    ! rbxm недоступен: {e}")
        return False
    path = os.path.join(out_dir, f"{obj_id}.rbxm")
    with open(path, "wb") as f:
        f.write(blob)
    return True


def type_folder(rec: dict) -> str:
    """Подпапка внутри out_dir: по AssetTypeId для ассетов,
    отдельные папки для бандлов и геймпассов."""
    if rec["kind"] == "bundle":
        return "Bundles"
    if rec["kind"] == "pass":
        return "GamePass"
    t = rec["asset_type_id"] or 0
    return ASSET_TYPE_NAMES.get(t, f"Type{t}")


# --------------------------------------------------------------------------- #
#  Основной цикл
# --------------------------------------------------------------------------- #

def parse_ids(args) -> list:
    ids = [int(x) for x in args.ids]
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    ids.append(int(line.split()[0]))
    if args.from_dir:
        # файлы, чьё имя целиком состоит из цифр (в т.ч. с ведущими нулями),
        # считаем id ассета; всё остальное (расширения, мусор) игнорируем
        for name in os.listdir(args.from_dir):
            stem = os.path.splitext(name)[0]
            if stem.isdigit():
                ids.append(int(stem))
    seen, uniq = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser(description="Кэш инфы и обложек ассетов Roblox (Kanvas-формат, sqlite-учёт).")
    ap.add_argument("ids", nargs="*", help="id ассетов/бандлов/геймпассов")
    ap.add_argument("--from-file", help="файл со списком id (по одному на строку, # — комментарий)")
    ap.add_argument("--from-dir", help="сканировать каталог: файлы с числовыми именами = id ассетов")
    ap.add_argument("--kind", choices=["asset", "bundle", "pass", "auto"], default="auto",
                    help="тип (по умолчанию определяется сам: asset -> bundle; для pass указывать явно)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="каталог для файлов")
    ap.add_argument("--db", help="путь к sqlite (по умолчанию <out>/assetcache.db)")
    ap.add_argument("--rbxm", action="store_true", help="дополнительно скачать сам ассет как .rbxm")
    ap.add_argument("--force", action="store_true", help="перекачать даже если id уже в базе со статусом ok")
    ap.add_argument("--no-retry", action="store_true", help="не ретраить прошлые failed-записи")
    ap.add_argument("--list", action="store_true", help="показать содержимое базы и выйти")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out)
    db_path = os.path.abspath(args.db) if args.db else os.path.join(out_dir, "assetcache.db")
    os.makedirs(out_dir, exist_ok=True)
    conn = db_open(db_path)

    if args.list:
        rows = conn.execute(
            "SELECT id, kind, status, name, thumb_state, asset_type_id FROM objects ORDER BY kind, id"
        ).fetchall()
        print(f"База: {db_path} ({len(rows)} записей)")
        for oid, kind, status, name, thumb, atype in rows:
            folder = type_folder({"kind": kind, "asset_type_id": atype})
            print(f"  {folder:<24} {oid:>15}  {status:6}  thumb={thumb}  {name}")
        return 0

    ids = parse_ids(args)
    if not ids:
        ap.error("укажите id (аргументами или --from-file)")

    # что вообще качаем: в базе ok/skipped -> скип, failed -> ретрай
    todo = []
    skipped = 0
    for oid in ids:
        row = db_get(conn, oid)
        if row and row[1] in ("ok", "skipped") and not args.force:
            skipped += 1
            continue
        if row and row[1] == "failed" and args.no_retry and not args.force:
            skipped += 1
            continue
        todo.append(oid)
    print(f"Задач: {len(todo)}, пропущено по базе: {skipped}")

    downloaded = failed = skipped_textures = 0
    for oid in todo:
        kind = args.kind if args.kind != "auto" else None
        print(f"[{oid}] ...", flush=True)
        try:
            fetcher = DETAIL_FETCHERS[kind] if kind else detect_kind
            rec = fetcher(oid)
        except FetchError as e:
            print(f"    x {e}")
            db_save(conn, {"id": oid, "kind": kind or "unknown", "name": None,
                           "asset_type_id": None, "creator": None, "description": None,
                           "thumb_state": None, "status": "failed", "error": str(e),
                           "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "raw": None})
            failed += 1
            continue

        rec.update({
            "thumb_state": None,
            "status": "ok",
            "error": None,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

        # текстуры классической одежды (Shirt/Pants/TShirt Image): красивого
        # рендера у них нет (thumb — плоская текстура), поэтому в обычные
        # папки типов их не кладём — info + png идут в подпапку-сиротник
        if rec["kind"] == "asset" and (rec["asset_type_id"] or 0) == 1 \
                and (rec["description"] or "").strip() in CLOTHING_TEXTURE_DESC:
            desc = (rec["description"] or "").strip()
            parent = ASSET_TYPE_NAMES[CLOTHING_TEXTURE_DESC[desc]]
            orphan_dir = os.path.join(out_dir, parent, "NoRender")
            os.makedirs(orphan_dir, exist_ok=True)
            state, img_url = fetch_thumb("asset", oid)
            rec["thumb_state"] = state
            rec["status"] = "skipped"
            db_save(conn, rec)
            write_info_file(orphan_dir, rec)
            if img_url and state == "Completed":
                try:
                    download_thumb(img_url, os.path.join(orphan_dir, f"{oid}.thumb.png"))
                except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
                    print(f"    ! png текстуры не скачался: {e}")
            else:
                print(f"    ! превью текстуры недоступно: state={state}")
            skipped_textures += 1
            print(f"    s текстура одежды -> {parent}/NoRender (info + png)")
            continue

        state, img_url = fetch_thumb(rec["kind"], oid)
        rec["thumb_state"] = state

        target_dir = os.path.join(out_dir, type_folder(rec))
        os.makedirs(target_dir, exist_ok=True)
        write_info_file(target_dir, rec)
        if rec["kind"] == "bundle":
            write_package_file(target_dir, rec)
        if img_url and state == "Completed":
            ext = "package.thumb.png" if rec["kind"] == "bundle" else "thumb.png"
            try:
                download_thumb(img_url, os.path.join(target_dir, f"{oid}.{ext}"))
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
                print(f"    ! обложка не скачалась: {e}")
        else:
            print(f"    ! обложка недоступна: state={state}")
        if args.rbxm and rec["kind"] == "asset" and rec["asset_type_id"]:
            write_rbxm(target_dir, oid)

        db_save(conn, rec)
        print(f"    + {rec['kind']}/{type_folder(rec)}: {rec['name'] or '(без имени)'} "
              f"(type={rec['asset_type_id']}, creator={rec['creator'] or '—'})")
        downloaded += 1
        time.sleep(REQUEST_PAUSE)

    conn.close()
    print(f"Готово: скачано {downloaded}, скипов текстур {skipped_textures}, ошибок {failed}. Файлы: {out_dir}")
    print(f"База:   {db_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
