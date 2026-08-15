import asyncio
import json
import time
import zlib
from typing import Dict, Any, List, AsyncIterator

from wastream.config.settings import settings
from wastream.utils.database import (
    database,
    rebuild_cache_stats,
    run_database_transaction,
    suppress_cache_stats_updates,
)
from wastream.utils.logger import database_logger

# Streaming backup: gzip'd NDJSON (meta line + one tagged row per line) -> flat memory at any size.
NDJSON_FORMAT = "wastream-ndjson"
NDJSON_FORMAT_VERSION = 2
_STREAM_BATCH = 2000
_WASOURCE_MERGE_BATCH_SIZE = 200
_GZIP_WBITS = 16 + zlib.MAX_WBITS   # 16 = gzip container

# cap per INSERT to stay under the SQLite/PostgreSQL bind-param limits
_MAX_BIND_PARAMS = 4500
_WASOURCE_COLUMNS = ("imdb_id", "tmdb_id", "title", "year", "season", "episode", "data", "created_at", "updated_at")
_backup_import_lock = asyncio.Lock()


# ===========================
# WASource Row Helpers
# ===========================
def _normalize_releases(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    releases = data.get("releases", [])
    if not releases and data.get("urls"):
        releases = [{
            "quality": data.get("quality"),
            "language": data.get("language"),
            "release_name": data.get("release_name"),
            "size": data.get("size"),
            "urls": data.get("urls", []),
        }]
    return releases


def _parse_row_releases(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        data = json.loads(row.get("data") or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    return _normalize_releases(data)


def _build_wasource_values(row: Dict[str, Any], releases: List[Dict[str, Any]], now: int) -> Dict[str, Any]:
    return {
        "imdb_id": row.get("imdb_id"),
        "tmdb_id": row.get("tmdb_id"),
        "title": row.get("title"),
        "year": row.get("year"),
        "season": row.get("season"),
        "episode": row.get("episode"),
        "data": json.dumps({"releases": releases}),
        "created_at": row.get("created_at") or now,
        "updated_at": row.get("updated_at") or now,
    }


async def _insert_wasource_row(row: Dict[str, Any]) -> int:
    releases = _parse_row_releases(row)
    await database.execute(
        """INSERT INTO wasource (imdb_id, tmdb_id, title, year, season, episode, data, created_at, updated_at)
           VALUES (:imdb_id, :tmdb_id, :title, :year, :season, :episode, :data, :created_at, :updated_at)""",
        _build_wasource_values(row, releases, int(time.time()))
    )
    return sum(len(rel.get("urls", [])) for rel in releases)


async def _merge_wasource_row(row: Dict[str, Any]) -> int:
    imdb_id = row.get("imdb_id")
    if not imdb_id:
        return 0

    existing = await database.fetch_one(
        """SELECT id, data FROM wasource
           WHERE imdb_id = :imdb_id
           AND COALESCE(season, -1) = COALESCE(:season, -1)
           AND COALESCE(episode, -1) = COALESCE(:episode, -1)""",
        {"imdb_id": imdb_id, "season": row.get("season"), "episode": row.get("episode")}
    )

    if not existing:
        return await _insert_wasource_row(row)

    try:
        data = json.loads(existing["data"])
    except (json.JSONDecodeError, TypeError):
        data = {}
    releases = _normalize_releases(data)

    added = 0
    for imported in _parse_row_releases(row):
        key = (imported.get("quality"), imported.get("language"), imported.get("release_name"))
        match = next((r for r in releases if (r.get("quality"), r.get("language"), r.get("release_name")) == key), None)
        if match:
            existing_urls = {u.get("url") for u in match.get("urls", [])}
            for url_entry in imported.get("urls", []):
                if url_entry.get("url") and url_entry.get("url") not in existing_urls:
                    match.setdefault("urls", []).append(url_entry)
                    added += 1
        else:
            releases.append(imported)
            added += len(imported.get("urls", []))

    data["releases"] = releases
    data.pop("quality", None)
    data.pop("language", None)
    data.pop("release_name", None)
    data.pop("size", None)
    data.pop("urls", None)

    await database.execute(
        "UPDATE wasource SET data = :data, updated_at = :updated_at WHERE id = :id",
        {"data": json.dumps(data), "updated_at": int(time.time()), "id": existing["id"]}
    )
    return added


# ===========================
# Streaming Export (gzip NDJSON)
# ===========================
async def export_backup(include: Dict[str, bool]) -> AsyncIterator[bytes]:
    comp = zlib.compressobj(6, zlib.DEFLATED, _GZIP_WBITS)

    def push(obj: Dict[str, Any]) -> bytes:
        return comp.compress((json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"))

    head = push({"format": NDJSON_FORMAT, "format_version": NDJSON_FORMAT_VERSION,
                 "exported_at": int(time.time()), "db_version": settings.DATABASE_VERSION})
    if head:
        yield head

    queries = []
    if include.get("wasource"):
        queries.append(("SELECT imdb_id, tmdb_id, title, year, season, episode, data, created_at, updated_at FROM wasource", "wasource"))
    if include.get("dead_links"):
        queries.append(("SELECT url, expires_at FROM dead_links", "dead_links"))
    if include.get("content_cache"):
        queries.append(("SELECT cache_key, content, expires_at FROM content_cache", "content_cache"))

    for sql, tag in queries:
        async for row in database.iterate(sql):
            d = dict(row)
            d["t"] = tag
            chunk = push(d)
            if chunk:
                yield chunk

    tail = comp.flush()
    if tail:
        yield tail


# ===========================
# Streaming Import (gzip NDJSON)
# ===========================
async def _iter_ndjson(byte_iter: AsyncIterator[bytes]) -> AsyncIterator[str]:
    decomp = None
    started = False
    buf = b""
    async for chunk in byte_iter:
        if not chunk:
            continue
        if not started:
            buf += chunk
            if len(buf) < 2:
                continue
            if buf[:2] == b"\x1f\x8b":   # gzip magic
                decomp = zlib.decompressobj(_GZIP_WBITS)
            started = True
            chunk, buf = buf, b""
        buf += decomp.decompress(chunk) if decomp else chunk
        while b"\n" in buf:
            head, buf = buf.split(b"\n", 1)
            yield head.decode("utf-8")
    if decomp and buf:
        buf += decomp.flush()
    for tail in buf.split(b"\n"):
        if tail.strip():
            yield tail.decode("utf-8")


async def _batch_upsert(table: str, columns: tuple, conflict: str, rows: List[Dict[str, Any]], mode: str):
    cols_sql = ", ".join(columns)
    chunk_size = max(1, _MAX_BIND_PARAMS // len(columns))
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        groups, params = [], {}
        for i, values in enumerate(chunk):
            groups.append("(" + ", ".join(f":{c}_{i}" for c in columns) + ")")
            for c in columns:
                params[f"{c}_{i}"] = values.get(c)
        values_sql = ", ".join(groups)
        if settings.DATABASE_TYPE == "sqlite":
            verb = "INSERT OR REPLACE" if mode == "merge" else "INSERT OR IGNORE"
            sql = f"{verb} INTO {table} ({cols_sql}) VALUES {values_sql}"
        elif mode == "merge":
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != conflict)
            sql = f"INSERT INTO {table} ({cols_sql}) VALUES {values_sql} ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
        else:
            sql = f"INSERT INTO {table} ({cols_sql}) VALUES {values_sql} ON CONFLICT ({conflict}) DO NOTHING"
        await database.execute(sql, params)


async def _batch_wasource_replace(rows: List[Dict[str, Any]]) -> int:
    now = int(time.time())
    total = 0
    values = []
    for row in rows:
        releases = _parse_row_releases(row)
        total += sum(len(rel.get("urls", [])) for rel in releases)
        values.append(_build_wasource_values(row, releases, now))
    cols_sql = ", ".join(_WASOURCE_COLUMNS)
    chunk_size = max(1, _MAX_BIND_PARAMS // len(_WASOURCE_COLUMNS))
    for start in range(0, len(values), chunk_size):
        chunk = values[start:start + chunk_size]
        groups, params = [], {}
        for i, v in enumerate(chunk):
            groups.append("(" + ", ".join(f":{c}_{i}" for c in _WASOURCE_COLUMNS) + ")")
            for c in _WASOURCE_COLUMNS:
                params[f"{c}_{i}"] = v.get(c)
        values_sql = ", ".join(groups)
        if settings.DATABASE_TYPE == "sqlite":
            sql = f"INSERT OR IGNORE INTO wasource ({cols_sql}) VALUES {values_sql}"
        else:
            conflict_target = "(imdb_id, (COALESCE(season, -1)), (COALESCE(episode, -1)))"
            sql = (
                f"INSERT INTO wasource ({cols_sql}) VALUES {values_sql} "
                f"ON CONFLICT {conflict_target} DO NOTHING"
            )
        await database.execute(sql, params)
    return total


async def _merge_wasource_batch(rows: List[Dict[str, Any]]) -> int:
    async def merge_rows() -> int:
        total = 0
        for row in rows:
            total += await _merge_wasource_row(row)
        return total

    return await run_database_transaction(
        merge_rows,
        "backup WASource merge batch"
    )


async def _replace_wasource_batch(rows: List[Dict[str, Any]]) -> int:
    return await run_database_transaction(
        lambda: _batch_wasource_replace(rows),
        "backup WASource replace batch"
    )


async def _import_backup(
        byte_iter: AsyncIterator[bytes], mode: str, include: Dict[str, bool]) -> Dict[str, Any]:
    lines = _iter_ndjson(byte_iter)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        return {"error": "invalid_format"}

    try:
        meta = json.loads(first)
    except (json.JSONDecodeError, ValueError):
        meta = None

    if not (isinstance(meta, dict) and meta.get("format") == NDJSON_FORMAT):
        return {"error": "invalid_format"}

    backup_version = meta.get("db_version")
    cache_ok = backup_version is None or backup_version == settings.DATABASE_VERSION
    if include.get("content_cache") and not cache_ok:
        database_logger.warning(f"[Backup] Skipping content_cache: backup db_version {backup_version} != {settings.DATABASE_VERSION}")

    do_ws, do_dl = include.get("wasource"), include.get("dead_links")
    do_cc = include.get("content_cache") and cache_ok
    imported = {"wasource": 0, "dead_links": 0, "content_cache": 0}
    stats_touched = False

    try:
        async with suppress_cache_stats_updates():
            if mode == "replace":
                async def clear_tables() -> None:
                    if do_ws:
                        await database.execute("DELETE FROM wasource")
                    if do_dl:
                        await database.execute("DELETE FROM dead_links")
                    if do_cc:
                        await database.execute("DELETE FROM content_cache")

                await run_database_transaction(
                    clear_tables,
                    "backup replace cleanup"
                )

            dl_batch, cc_batch = [], []
            ws_replace_batch, ws_merge_batch = [], []

            async for raw in lines:
                if not raw.strip():
                    continue
                try:
                    o = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                t = o.get("t")

                if t == "dead_links" and do_dl and o.get("url"):
                    dl_batch.append({"url": o["url"], "expires_at": o.get("expires_at", -1)})
                    if len(dl_batch) >= _STREAM_BATCH:
                        batch = dl_batch
                        await run_database_transaction(
                            lambda: _batch_upsert(
                                "dead_links", ("url", "expires_at"), "url", batch, mode
                            ),
                            "backup dead-links batch"
                        )
                        imported["dead_links"] += len(batch)
                        dl_batch = []

                elif t == "content_cache" and do_cc and o.get("cache_key"):
                    stats_touched = True
                    cc_batch.append({"cache_key": o["cache_key"], "content": o.get("content", "[]"), "expires_at": o.get("expires_at", -1)})
                    if len(cc_batch) >= _STREAM_BATCH:
                        batch = cc_batch
                        await run_database_transaction(
                            lambda: _batch_upsert(
                                "content_cache", ("cache_key", "content", "expires_at"),
                                "cache_key", batch, mode
                            ),
                            "backup content-cache batch"
                        )
                        imported["content_cache"] += len(batch)
                        cc_batch = []

                elif t == "wasource" and do_ws and o.get("imdb_id"):
                    stats_touched = True
                    if mode == "replace":
                        ws_replace_batch.append(o)
                        if len(ws_replace_batch) >= _STREAM_BATCH:
                            batch = ws_replace_batch
                            imported["wasource"] += await _replace_wasource_batch(batch)
                            ws_replace_batch = []
                    else:
                        ws_merge_batch.append(o)
                        if len(ws_merge_batch) >= _WASOURCE_MERGE_BATCH_SIZE:
                            batch = ws_merge_batch
                            imported["wasource"] += await _merge_wasource_batch(batch)
                            ws_merge_batch = []

            if dl_batch:
                batch = dl_batch
                await run_database_transaction(
                    lambda: _batch_upsert(
                        "dead_links", ("url", "expires_at"), "url", batch, mode
                    ),
                    "backup dead-links final batch"
                )
                imported["dead_links"] += len(batch)
            if cc_batch:
                batch = cc_batch
                await run_database_transaction(
                    lambda: _batch_upsert(
                        "content_cache", ("cache_key", "content", "expires_at"),
                        "cache_key", batch, mode
                    ),
                    "backup content-cache final batch"
                )
                imported["content_cache"] += len(batch)
            if ws_replace_batch:
                imported["wasource"] += await _replace_wasource_batch(ws_replace_batch)
            if ws_merge_batch:
                imported["wasource"] += await _merge_wasource_batch(ws_merge_batch)

            if stats_touched:
                await rebuild_cache_stats()
        return {"success": True, "mode": mode, "imported": imported}

    except Exception as e:
        database_logger.error(f"[Backup] Stream import failed: {type(e).__name__}: {e}")
        return {"error": "import_failed"}


async def import_backup(
        byte_iter: AsyncIterator[bytes], mode: str, include: Dict[str, bool]) -> Dict[str, Any]:
    if _backup_import_lock.locked():
        return {"error": "import_in_progress"}

    async with _backup_import_lock:
        return await _import_backup(byte_iter, mode, include)
