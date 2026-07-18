import asyncio
import json
import time
from typing import Optional, List, Dict, Tuple

from wastream.config.settings import settings
from wastream.utils.helpers import create_cache_key
from wastream.utils.logger import cache_logger
from wastream.utils.database import update_cache_stats_on_set


# ===========================
# Cache Retrieval
# ===========================
async def get_cache(database, cache_type: str, title: str, year: Optional[str] = None) -> Optional[List[Dict]]:
    cache_key = create_cache_key(cache_type, title, year)

    try:
        current_time = int(time.time())
        result = await database.fetch_one(
            "SELECT content FROM content_cache WHERE cache_key = :cache_key AND (expires_at = -1 OR expires_at > :current_time)",
            {"cache_key": cache_key, "current_time": current_time}
        )

        if not result:
            cache_logger.debug(f"Miss: {cache_type} {title} ({year})")
            return None

        cached_data = json.loads(result["content"])
        cache_logger.debug(f"Hit: {cache_type} {title} ({year}) - {len(cached_data)} results")
        return cached_data
    except json.JSONDecodeError as e:
        cache_logger.error(f"Corrupted cache: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        cache_logger.error(f"Cache read failed: {type(e).__name__}: {e}")
        return None


async def get_cache_with_status(database, cache_type: str, title: str, year: Optional[str] = None) -> Tuple[Optional[List[Dict]], bool]:
    cache_key = create_cache_key(cache_type, title, year)

    try:
        current_time = int(time.time())
        result = await database.fetch_one(
            "SELECT content, expires_at FROM content_cache WHERE cache_key = :cache_key",
            {"cache_key": cache_key}
        )

        if not result:
            cache_logger.debug(f"Miss: {cache_type} {title} ({year})")
            return None, False

        cached_data = json.loads(result["content"])
        is_valid = result["expires_at"] == -1 or result["expires_at"] > current_time

        if is_valid:
            cache_logger.debug(f"Hit (valid): {cache_type} {title} ({year}) - {len(cached_data)} results")
        else:
            cache_logger.debug(f"Hit (expired): {cache_type} {title} ({year}) - {len(cached_data)} results")

        return cached_data, is_valid
    except json.JSONDecodeError as e:
        cache_logger.error(f"Corrupted cache: {type(e).__name__}: {e}")
        return None, False
    except Exception as e:
        cache_logger.error(f"Cache read failed: {type(e).__name__}: {e}")
        return None, False


# ===========================
# Cache Storage
# ===========================
async def set_cache(database, cache_type: str, title: str, year: Optional[str] = None,
                    results: Optional[List] = None, ttl: int = 3600):
    cache_key = create_cache_key(cache_type, title, year)

    try:
        old_row = await database.fetch_one(
            "SELECT content FROM content_cache WHERE cache_key = :cache_key",
            {"cache_key": cache_key}
        )
        old_results = None
        if old_row:
            try:
                old_results = json.loads(old_row["content"])
            except (json.JSONDecodeError, TypeError):
                pass

        if ttl == -1:
            expires_at = -1
        else:
            current_time = int(time.time())
            expires_at = current_time + ttl
        content = json.dumps(results or [])

        if settings.DATABASE_TYPE == "sqlite":
            query = """INSERT OR REPLACE INTO content_cache (cache_key, content, expires_at)
                       VALUES (:cache_key, :content, :expires_at)"""
        else:
            query = """INSERT INTO content_cache (cache_key, content, expires_at)
                       VALUES (:cache_key, :content, :expires_at)
                       ON CONFLICT (cache_key) DO UPDATE
                       SET content = :content, expires_at = :expires_at"""

        await database.execute(query, {
            "cache_key": cache_key,
            "content": content,
            "expires_at": expires_at
        })

        asyncio.create_task(update_cache_stats_on_set(cache_key, results or [], old_results))

        ttl_str = "permanent" if ttl == -1 else f"{ttl}s"
        cache_logger.debug(f"Saved: {cache_type} {title} ({year}) - {len(results or [])} results ({ttl_str})")
    except Exception as e:
        cache_logger.error(f"Cache save failed: {type(e).__name__}: {e}")


async def set_cache_if_not_exists(
        database, cache_type: str, title: str, year: Optional[str] = None,
        results: Optional[List] = None, ttl: int = 3600) -> bool:
    cache_key = create_cache_key(cache_type, title, year)

    try:
        existing = await database.fetch_one(
            "SELECT cache_key FROM content_cache WHERE cache_key = :cache_key",
            {"cache_key": cache_key}
        )

        if existing:
            cache_logger.debug(f"Skip (exists): {cache_type} {title} ({year})")
            return False

        if ttl == -1:
            expires_at = -1
        else:
            current_time = int(time.time())
            expires_at = current_time + ttl
        content = json.dumps(results or [])

        await database.execute(
            "INSERT INTO content_cache (cache_key, content, expires_at) VALUES (:cache_key, :content, :expires_at)",
            {"cache_key": cache_key, "content": content, "expires_at": expires_at}
        )

        asyncio.create_task(update_cache_stats_on_set(cache_key, results or []))

        ttl_str = "permanent" if ttl == -1 else f"{ttl}s"
        cache_logger.debug(f"Saved (new): {cache_type} {title} ({year}) - {len(results or [])} results ({ttl_str})")
        return True
    except Exception as e:
        cache_logger.error(f"Cache save failed: {type(e).__name__}: {e}")
        return False


# ===========================
# Parallel Cache Lookup (Local + Remote)
# ===========================
async def get_cache_parallel(database, cache_type: str, title: str, year: Optional[str] = None) -> Tuple[Optional[List[Dict]], bool]:
    """
    Check local and remote cache in parallel.
    Returns: (cached_data, should_store_from_remote)
    - If local found: returns (data, False)
    - If remote found: returns (data, should_store)
    - If nothing found: returns (None, False)
    """
    from wastream.services.remote import fetch_remote_cache

    async def check_local():
        return await get_cache(database, cache_type, title, year)

    async def check_remote():
        return await fetch_remote_cache(cache_type, title, year)

    try:
        local_result, remote_result = await asyncio.gather(
            check_local(),
            check_remote(),
            return_exceptions=True
        )

        if isinstance(local_result, list) and local_result is not None:
            return local_result, False

        if local_result is not None and not isinstance(local_result, Exception):
            return local_result, False

        if isinstance(remote_result, tuple):
            remote_data, should_store = remote_result
            if remote_data:
                cache_logger.debug(f"Remote hit: {cache_type} {title} ({year}) - {len(remote_data)} results")
                return remote_data, should_store

        cache_logger.debug(f"Remote miss: {cache_type} {title} ({year})")
        return None, False

    except Exception as e:
        cache_logger.error(f"Parallel cache lookup failed: {type(e).__name__}: {e}")
        return None, False
