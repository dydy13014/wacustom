import time
from typing import Dict, Any, Optional

from wastream.config.settings import settings
from wastream.utils.database import database
from wastream.utils.logger import database_logger


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

        total = await database.fetch_val(
            f"SELECT COUNT(*) FROM dead_links {where_clause}",
            filter_params if filter_params else None
        ) or 0

        query_params = {**filter_params, "limit": limit, "offset": offset}
        rows = await database.fetch_all(
            f"SELECT url, expires_at FROM dead_links {where_clause} ORDER BY expires_at DESC LIMIT :limit OFFSET :offset",
            query_params
        )

        links = []
        for row in rows:
            links.append({
                "url": row["url"],
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
        if not urls:
            return 0

        deleted = 0
        for url in urls:
            existing = await database.fetch_val(
                "SELECT 1 FROM dead_links WHERE url = :url",
                {"url": url}
            )
            if not existing:
                continue
            await database.execute(
                "DELETE FROM dead_links WHERE url = :url",
                {"url": url}
            )
            deleted += 1

        return deleted
    except Exception as e:
        database_logger.error(f"[Dead-Links] Failed to delete dead links: {type(e).__name__}: {e}")
        return 0


async def delete_all_dead_links() -> int:
    try:
        total = await database.fetch_val("SELECT COUNT(*) FROM dead_links") or 0
        await database.execute("DELETE FROM dead_links")
        return total
    except Exception as e:
        database_logger.error(f"[Dead-Links] Failed to delete all dead links: {type(e).__name__}: {e}")
        return 0


# ===========================
# Add Dead Links
# ===========================
async def add_dead_links(urls: list) -> int:
    try:
        if not urls:
            return 0

        added = 0
        for url in urls:
            url = url.strip()
            if not url:
                continue

            existing = await database.fetch_val(
                "SELECT 1 FROM dead_links WHERE url = :url",
                {"url": url}
            )
            if existing:
                continue

            if settings.DEAD_LINK_TTL == -1:
                expires_at = -1
            else:
                expires_at = int(time.time()) + settings.DEAD_LINK_TTL

            await database.execute(
                "INSERT INTO dead_links (url, expires_at) VALUES (:url, :expires_at)",
                {"url": url, "expires_at": expires_at}
            )
            added += 1

        return added
    except Exception as e:
        database_logger.error(f"[Dead-Links] Failed to add dead links: {type(e).__name__}: {e}")
        return 0
