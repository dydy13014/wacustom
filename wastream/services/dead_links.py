import time
from typing import Dict, Any, Optional

from wastream.config.settings import settings
from wastream.utils.database import (
    database,
    mark_dead_links,
    run_database_transaction,
)
from wastream.utils.logger import database_logger
from wastream.utils.urls import canonicalize_url


# ===========================
# Dead Link Query Helpers
# ===========================
def _normalize_urls(urls: list) -> list[str]:
    normalized = []
    seen = set()
    for value in urls:
        if not isinstance(value, str) or not value.strip():
            continue
        url = canonicalize_url(value.strip()) or value.strip()
        if url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized


def _active_dead_links_query() -> str:
    return (
        "SELECT url, failure_count, last_failure_at, expires_at "
        "FROM dead_links "
        f"WHERE (expires_at = -1 OR expires_at > {int(time.time())})"
    )


async def get_dead_links_count() -> int:
    return int(await database.fetch_val(
        f"SELECT COUNT(*) FROM ({_active_dead_links_query()}) "
        "AS active_dead_links"
    ) or 0)


# ===========================
# Get Dead Links List
# ===========================
async def get_dead_links_list(limit: int = 100, offset: int = 0, url_filter: Optional[str] = None) -> Dict[str, Any]:
    try:
        filter_params = {}
        where_clause = ""
        if url_filter:
            filter_params["url_filter"] = f"%{url_filter}%"
            where_clause = "WHERE url LIKE :url_filter"

        active_query = _active_dead_links_query()
        total = await database.fetch_val(
            f"SELECT COUNT(*) FROM ({active_query}) AS active_dead_links "
            f"{where_clause}",
            filter_params if filter_params else None
        ) or 0

        query_params = {**filter_params, "limit": limit, "offset": offset}
        rows = await database.fetch_all(
            f"SELECT url, failure_count, expires_at FROM ({active_query}) "
            f"AS active_dead_links {where_clause} "
            "ORDER BY failure_count ASC, last_failure_at DESC, url ASC "
            "LIMIT :limit OFFSET :offset",
            query_params
        )

        links = []
        for row in rows:
            links.append({
                "url": row["url"],
                "failure_count": row["failure_count"],
                "expires_at": row["expires_at"],
                "permanent": row["expires_at"] == -1
            })

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "links": links
        }

    except Exception as e:
        database_logger.error(f"[Dead-Links] Failed to get dead links: {type(e).__name__}: {e}")
        return {"total": 0, "limit": limit, "offset": offset, "links": []}


# ===========================
# Delete Dead Links
# ===========================
async def delete_dead_links(urls: list) -> int:
    try:
        normalized_urls = _normalize_urls(urls)
        if not normalized_urls:
            return 0

        async def delete_rows() -> int:
            deleted = 0
            for url in normalized_urls:
                existing = await database.fetch_val(
                    "SELECT 1 FROM dead_links WHERE url = :url",
                    {"url": url}
                )
                if existing:
                    await database.execute(
                        "DELETE FROM dead_links WHERE url = :url",
                        {"url": url}
                    )
                    deleted += 1
            return deleted

        return await run_database_transaction(
            delete_rows,
            "delete dead links",
        )
    except Exception as e:
        database_logger.error(f"[Dead-Links] Failed to delete dead links: {type(e).__name__}: {e}")
        return 0


async def delete_all_dead_links() -> int:
    try:
        async def delete_rows() -> int:
            total = await get_dead_links_count()
            await database.execute("DELETE FROM dead_links")
            return total

        return await run_database_transaction(
            delete_rows,
            "delete all dead links",
        )
    except Exception as e:
        database_logger.error(f"[Dead-Links] Failed to delete all dead links: {type(e).__name__}: {e}")
        return 0


# ===========================
# Add Dead Links
# ===========================
async def add_dead_links(urls: list) -> int:
    try:
        normalized_urls = _normalize_urls(urls)
        if not normalized_urls:
            return 0
        return await mark_dead_links(
            normalized_urls,
            settings.DEAD_LINK_TTL,
            reason="ADMIN",
        )
    except Exception as e:
        database_logger.error(f"[Dead-Links] Failed to add dead links: {type(e).__name__}: {e}")
        return 0
