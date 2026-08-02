import json
import re
from typing import Dict, Any, Optional
from urllib.parse import quote_plus, unquote_plus

from wastream.utils.database import database, rebuild_cache_stats
from wastream.utils.logger import database_logger


# ===========================
# Cache Key Parsing
# ===========================
def _parse_cache_key(cache_key: str) -> Dict[str, Any]:
    cache_type, _, rest = cache_key.partition(":")
    title_enc, _, year = rest.partition(":")

    if "_movie" in cache_type:
        content_type = "movie"
    elif "_series" in cache_type:
        content_type = "series"
    elif "_anime" in cache_type:
        content_type = "anime"
    else:
        content_type = "unknown"

    source = cache_type
    for marker in ("_movie", "_series", "_anime"):
        idx = cache_type.find(marker)
        if idx != -1:
            source = cache_type[:idx]
            break

    season = episode = None
    match = re.search(r"_s(\d+)e(\d+)", cache_type)
    if match:
        season, episode = int(match.group(1)), int(match.group(2))

    return {
        "title": unquote_plus(title_enc) or "(unknown)",
        "year": year or None,
        "content_type": content_type,
        "source": source,
        "season": season,
        "episode": episode,
    }


# ===========================
# Content Cache Browser
# ===========================
async def get_content_cache_list(limit: int = 100, offset: int = 0, search: Optional[str] = None) -> Dict[str, Any]:
    try:
        filter_params = {}
        where_clause = ""
        if search:
            filter_params["search"] = f"%{quote_plus(search.lower())}%"
            where_clause = "WHERE cache_key LIKE :search"

        total = await database.fetch_val(
            f"SELECT COUNT(*) FROM content_cache {where_clause}",
            filter_params if filter_params else None
        ) or 0

        query_params = {**filter_params, "limit": limit, "offset": offset}
        rows = await database.fetch_all(
            f"SELECT cache_key, content, expires_at FROM content_cache {where_clause} ORDER BY expires_at DESC LIMIT :limit OFFSET :offset",
            query_params
        )

        entries = []
        for row in rows:
            try:
                content = json.loads(row["content"])
                stream_count = len(content) if isinstance(content, list) else 0
            except (json.JSONDecodeError, TypeError):
                stream_count = 0

            entry = _parse_cache_key(row["cache_key"])
            entry.update({
                "cache_key": row["cache_key"],
                "stream_count": stream_count,
                "expires_at": row["expires_at"],
                "permanent": row["expires_at"] == -1
            })
            entries.append(entry)

        return {"total": total, "limit": limit, "offset": offset, "entries": entries}

    except Exception as e:
        database_logger.error(f"[Content-Cache] Failed to get content cache: {type(e).__name__}: {e}")
        return {"total": 0, "limit": limit, "offset": offset, "entries": []}


async def get_content_cache_entry(cache_key: str) -> Optional[Dict[str, Any]]:
    try:
        row = await database.fetch_one(
            "SELECT cache_key, content, expires_at FROM content_cache WHERE cache_key = :key",
            {"key": cache_key}
        )
        if not row:
            return None

        try:
            content = json.loads(row["content"])
            if not isinstance(content, list):
                content = []
        except (json.JSONDecodeError, TypeError):
            content = []

        streams = []
        for item in content:
            if not isinstance(item, dict):
                continue
            streams.append({
                "hoster": item.get("hoster"),
                "quality": item.get("quality"),
                "language": item.get("language"),
                "size": item.get("size"),
                "link": item.get("link"),
            })

        entry = _parse_cache_key(row["cache_key"])
        entry.update({
            "cache_key": row["cache_key"],
            "stream_count": len(streams),
            "expires_at": row["expires_at"],
            "permanent": row["expires_at"] == -1,
            "streams": streams
        })
        return entry

    except Exception as e:
        database_logger.error(f"[Content-Cache] Failed to get cache entry: {type(e).__name__}: {e}")
        return None


# ===========================
# Delete Content Cache
# ===========================
async def delete_content_cache(cache_keys: list) -> int:
    try:
        if not cache_keys:
            return 0

        deleted = 0
        for key in cache_keys:
            existing = await database.fetch_val(
                "SELECT 1 FROM content_cache WHERE cache_key = :key",
                {"key": key}
            )
            if not existing:
                continue
            await database.execute(
                "DELETE FROM content_cache WHERE cache_key = :key",
                {"key": key}
            )
            deleted += 1

        if deleted:
            await rebuild_cache_stats()
        return deleted
    except Exception as e:
        database_logger.error(f"[Content-Cache] Failed to delete content cache: {type(e).__name__}: {e}")
        return 0


async def delete_all_content_cache() -> int:
    try:
        total = await database.fetch_val("SELECT COUNT(*) FROM content_cache") or 0
        await database.execute("DELETE FROM content_cache")
        if total > 0:
            await rebuild_cache_stats()
        return total
    except Exception as e:
        database_logger.error(f"[Content-Cache] Failed to delete all content cache: {type(e).__name__}: {e}")
        return 0
