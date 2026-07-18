import asyncio
import json
import time
from typing import List, Dict, Any, Optional

from wastream.config.settings import settings
from wastream.utils.database import database
from wastream.utils.logger import database_logger

MAX_WASOURCE_LOCKS = 10000

# ===========================
# Lock Management for WASource
# ===========================
_wasource_locks: Dict[str, asyncio.Lock] = {}
_locks_lock = asyncio.Lock()


async def _get_wasource_lock(imdb_id: str, season: Optional[int], episode: Optional[int]) -> asyncio.Lock:
    key = f"{imdb_id}_{season if season is not None else 'None'}_{episode if episode is not None else 'None'}"
    async with _locks_lock:
        if len(_wasource_locks) >= MAX_WASOURCE_LOCKS:
            unlocked = [k for k, v in _wasource_locks.items() if not v.locked()]
            for k in unlocked:
                del _wasource_locks[k]
        if key not in _wasource_locks:
            _wasource_locks[key] = asyncio.Lock()
        return _wasource_locks[key]


async def _increment_wasource_link_count(count: int):
    try:
        from wastream.utils.database import get_cache_stats, save_cache_stats
        stats = await get_cache_stats()
        if stats:
            stats["wasource_total_links"] = stats.get("wasource_total_links", 0) + count
            await save_cache_stats(stats)
    except Exception:
        pass


async def _decrement_wasource_link_count(count: int):
    try:
        from wastream.utils.database import get_cache_stats, save_cache_stats
        stats = await get_cache_stats()
        if stats:
            stats["wasource_total_links"] = max(0, stats.get("wasource_total_links", 0) - count)
            await save_cache_stats(stats)
    except Exception:
        pass


async def _reset_wasource_link_count():
    try:
        from wastream.utils.database import get_cache_stats, save_cache_stats
        stats = await get_cache_stats()
        if stats:
            stats["wasource_total_links"] = 0
            await save_cache_stats(stats)
    except Exception:
        pass


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ===========================
# Get All WASource Content
# ===========================
async def get_wasource_links(
    limit: int = 100,
    offset: int = 0,
    imdb_id: Optional[str] = None,
    title: Optional[str] = None,
    release_name: Optional[str] = None,
    url: Optional[str] = None
) -> Dict[str, Any]:
    try:
        conditions = []
        filter_params = {}

        if imdb_id:
            conditions.append("imdb_id = :imdb_id")
            filter_params["imdb_id"] = imdb_id

        if title:
            conditions.append("title LIKE :title ESCAPE '\\'")
            filter_params["title"] = f"%{_escape_like(title)}%"

        if release_name:
            conditions.append("data LIKE :release_name ESCAPE '\\'")
            filter_params["release_name"] = f"%{_escape_like(release_name)}%"

        if url:
            conditions.append("data LIKE :url ESCAPE '\\'")
            filter_params["url"] = f"%{_escape_like(url)}%"

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = await database.fetch_val(
            f"SELECT COUNT(*) FROM wasource {where_clause}",
            filter_params if filter_params else None
        ) or 0

        query_params = {**filter_params, "limit": limit, "offset": offset}
        rows = await database.fetch_all(
            f"SELECT * FROM wasource {where_clause} ORDER BY updated_at DESC LIMIT :limit OFFSET :offset",
            query_params
        )

        contents = []
        for row in rows:
            data = json.loads(row["data"])
            releases = data.get("releases", [])

            if not releases and data.get("urls"):
                releases = [{
                    "quality": data.get("quality"),
                    "language": data.get("language"),
                    "release_name": data.get("release_name"),
                    "size": data.get("size"),
                    "urls": data.get("urls", [])
                }]

            if release_name or url:
                filtered_releases = []
                for rel in releases:
                    match = True
                    if release_name:
                        rel_name = rel.get("release_name", "") or ""
                        if release_name.lower() not in rel_name.lower():
                            match = False
                    if url and match:
                        urls_list = rel.get("urls", [])
                        url_found = any(url.lower() in (u.get("url", "") or "").lower() for u in urls_list)
                        if not url_found:
                            match = False
                    if match:
                        filtered_releases.append(rel)
                releases = filtered_releases

                if not releases:
                    continue

            contents.append({
                "id": row["id"],
                "imdb_id": row["imdb_id"],
                "tmdb_id": row["tmdb_id"],
                "title": row["title"],
                "year": row["year"],
                "season": row["season"],
                "episode": row["episode"],
                "releases": releases,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            })

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "contents": contents
        }

    except Exception as e:
        database_logger.error(f"[WASource] Failed to get contents: {type(e).__name__}: {e}")
        return {"total": 0, "limit": limit, "offset": offset, "contents": []}


def _find_release(releases: List[Dict], quality: Optional[str], language: Optional[str], release_name: Optional[str] = None) -> Optional[Dict]:
    for release in releases:
        if release.get("quality") == quality and release.get("language") == language:
            if release_name is None or release.get("release_name") == release_name:
                return release
    return None


# ===========================
# Add WASource Content (Bulk URLs)
# ===========================
async def add_wasource_links_bulk(
    imdb_id: str,
    title: str,
    release_name: Optional[str],
    quality: Optional[str],
    language: Optional[str],
    size: Optional[int],
    season: Optional[int],
    episode: Optional[int],
    urls: List[Dict[str, str]],
    tmdb_id: Optional[str] = None,
    year: Optional[int] = None
) -> Dict[str, Any]:
    added = 0
    skipped = 0
    errors = []

    lock = await _get_wasource_lock(imdb_id, season, episode)

    async with lock:
        try:
            existing = await database.fetch_one(
                """SELECT id, data FROM wasource
                   WHERE imdb_id = :imdb_id
                   AND COALESCE(season, -1) = COALESCE(:season, -1)
                   AND COALESCE(episode, -1) = COALESCE(:episode, -1)""",
                {"imdb_id": imdb_id, "season": season, "episode": episode}
            )

            current_time = int(time.time())

            if existing:
                data = json.loads(existing["data"])
                releases = data.get("releases", [])

                if not releases and data.get("urls"):
                    releases = [{
                        "quality": data.get("quality"),
                        "language": data.get("language"),
                        "release_name": data.get("release_name"),
                        "size": data.get("size"),
                        "urls": data.get("urls", [])
                    }]

                release = _find_release(releases, quality, language, release_name)
                if release:
                    release["size"] = size
                    existing_urls = {u.get("url") for u in release.get("urls", [])}
                else:
                    release = {
                        "quality": quality,
                        "language": language,
                        "release_name": release_name,
                        "size": size,
                        "urls": []
                    }
                    releases.append(release)
                    existing_urls = set()

                for url_entry in urls:
                    url = url_entry.get("url", "").strip()
                    host = url_entry.get("host", "").strip()

                    if not url or not url.startswith("http"):
                        errors.append(f"Invalid URL: {url[:50]}...")
                        continue
                    if not host:
                        errors.append(f"Host is required for URL: {url[:30]}...")
                        continue

                    if url in existing_urls:
                        skipped += 1
                        continue

                    release["urls"].append({"host": host, "url": url})
                    added += 1

                data["releases"] = releases
                data.pop("quality", None)
                data.pop("language", None)
                data.pop("release_name", None)
                data.pop("size", None)
                data.pop("urls", None)

                await database.execute(
                    "UPDATE wasource SET title = :title, tmdb_id = :tmdb_id, year = :year, data = :data, updated_at = :updated_at WHERE id = :id",
                    {"id": existing["id"], "title": title, "tmdb_id": tmdb_id, "year": year, "data": json.dumps(data), "updated_at": current_time}
                )
            else:
                release = {
                    "quality": quality,
                    "language": language,
                    "release_name": release_name,
                    "size": size,
                    "urls": []
                }

                for url_entry in urls:
                    url = url_entry.get("url", "").strip()
                    host = url_entry.get("host", "").strip()

                    if not url or not url.startswith("http"):
                        errors.append(f"Invalid URL: {url[:50]}...")
                        continue
                    if not host:
                        errors.append(f"Host is required for URL: {url[:30]}...")
                        continue

                    release["urls"].append({"host": host, "url": url})
                    added += 1

                data = {"releases": [release]}
                insert_params = {
                    "imdb_id": imdb_id,
                    "tmdb_id": tmdb_id,
                    "title": title,
                    "year": year,
                    "season": season,
                    "episode": episode,
                    "data": json.dumps(data),
                    "created_at": current_time,
                    "updated_at": current_time
                }

                if settings.DATABASE_TYPE == "sqlite":
                    await database.execute(
                        """INSERT OR IGNORE INTO wasource (imdb_id, tmdb_id, title, year, season, episode, data, created_at, updated_at)
                           VALUES (:imdb_id, :tmdb_id, :title, :year, :season, :episode, :data, :created_at, :updated_at)""",
                        insert_params
                    )
                else:
                    await database.execute(
                        """INSERT INTO wasource (imdb_id, tmdb_id, title, year, season, episode, data, created_at, updated_at)
                           VALUES (:imdb_id, :tmdb_id, :title, :year, :season, :episode, :data, :created_at, :updated_at)
                           ON CONFLICT (imdb_id, COALESCE(season, -1), COALESCE(episode, -1)) DO NOTHING""",
                        insert_params
                    )

                conflict = await database.fetch_one(
                    """SELECT id, data FROM wasource
                       WHERE imdb_id = :imdb_id
                       AND COALESCE(season, -1) = COALESCE(:season, -1)
                       AND COALESCE(episode, -1) = COALESCE(:episode, -1)""",
                    {"imdb_id": imdb_id, "season": season, "episode": episode}
                )
                if conflict and json.loads(conflict["data"]) != data:
                    ex_data = json.loads(conflict["data"])
                    ex_releases = ex_data.get("releases", [])
                    if not _find_release(ex_releases, quality, language, release_name):
                        ex_releases.append(release)
                        ex_data["releases"] = ex_releases
                        await database.execute(
                            "UPDATE wasource SET title = :title, tmdb_id = :tmdb_id, year = :year, data = :data, updated_at = :updated_at WHERE id = :id",
                            {"id": conflict["id"], "title": title, "tmdb_id": tmdb_id, "year": year, "data": json.dumps(ex_data), "updated_at": current_time}
                        )

        except Exception as e:
            database_logger.error(f"[WASource] Failed to add content: {type(e).__name__}: {e}")
            errors.append(f"Database error: {type(e).__name__}: {e}")

    if added > 0:
        asyncio.create_task(_increment_wasource_link_count(added))

    return {
        "added": added,
        "skipped": skipped,
        "errors": errors
    }


async def add_wasource_links_from_remote(
    imdb_id: str,
    title: str,
    release_name: Optional[str],
    quality: Optional[str],
    language: Optional[str],
    size: Optional[int],
    season: Optional[int],
    episode: Optional[int],
    urls: List[Dict[str, str]],
    tmdb_id: Optional[str] = None,
    year: Optional[int] = None
) -> Dict[str, Any]:
    added = 0
    skipped = 0
    errors = []

    lock = await _get_wasource_lock(imdb_id, season, episode)

    async with lock:
        try:
            existing = await database.fetch_one(
                """SELECT id, data FROM wasource
                   WHERE imdb_id = :imdb_id
                   AND COALESCE(season, -1) = COALESCE(:season, -1)
                   AND COALESCE(episode, -1) = COALESCE(:episode, -1)""",
                {"imdb_id": imdb_id, "season": season, "episode": episode}
            )

            current_time = int(time.time())

            if existing:
                data = json.loads(existing["data"])
                releases = data.get("releases", [])

                if not releases and data.get("urls"):
                    releases = [{
                        "quality": data.get("quality"),
                        "language": data.get("language"),
                        "release_name": data.get("release_name"),
                        "size": data.get("size"),
                        "urls": data.get("urls", [])
                    }]

                release = _find_release(releases, quality, language)

                if release:
                    existing_urls = {u.get("url") for u in release.get("urls", [])}

                    for url_entry in urls:
                        url = url_entry.get("url", "").strip()
                        host = url_entry.get("host", "").strip()

                        if not url or not url.startswith("http"):
                            errors.append(f"Invalid URL: {url[:50]}...")
                            continue
                        if not host:
                            errors.append(f"Host is required for URL: {url[:30]}...")
                            continue

                        if url in existing_urls:
                            skipped += 1
                            continue

                        release["urls"].append({"host": host, "url": url})
                        added += 1

                    if added > 0:
                        data["releases"] = releases
                        data.pop("quality", None)
                        data.pop("language", None)
                        data.pop("release_name", None)
                        data.pop("size", None)
                        data.pop("urls", None)

                        await database.execute(
                            "UPDATE wasource SET data = :data, updated_at = :updated_at WHERE id = :id",
                            {"id": existing["id"], "data": json.dumps(data), "updated_at": current_time}
                        )
                else:
                    new_release = {
                        "quality": quality,
                        "language": language,
                        "release_name": release_name,
                        "size": size,
                        "urls": []
                    }

                    for url_entry in urls:
                        url = url_entry.get("url", "").strip()
                        host = url_entry.get("host", "").strip()

                        if not url or not url.startswith("http"):
                            errors.append(f"Invalid URL: {url[:50]}...")
                            continue
                        if not host:
                            errors.append(f"Host is required for URL: {url[:30]}...")
                            continue

                        new_release["urls"].append({"host": host, "url": url})
                        added += 1

                    if added > 0:
                        releases.append(new_release)
                        data["releases"] = releases
                        data.pop("quality", None)
                        data.pop("language", None)
                        data.pop("release_name", None)
                        data.pop("size", None)
                        data.pop("urls", None)

                        await database.execute(
                            "UPDATE wasource SET data = :data, updated_at = :updated_at WHERE id = :id",
                            {"id": existing["id"], "data": json.dumps(data), "updated_at": current_time}
                        )

            else:
                release = {
                    "quality": quality,
                    "language": language,
                    "release_name": release_name,
                    "size": size,
                    "urls": []
                }

                for url_entry in urls:
                    url = url_entry.get("url", "").strip()
                    host = url_entry.get("host", "").strip()

                    if not url or not url.startswith("http"):
                        errors.append(f"Invalid URL: {url[:50]}...")
                        continue
                    if not host:
                        errors.append(f"Host is required for URL: {url[:30]}...")
                        continue

                    release["urls"].append({"host": host, "url": url})
                    added += 1

                if added > 0:
                    data = {"releases": [release]}

                    await database.execute(
                        """INSERT INTO wasource (imdb_id, tmdb_id, title, year, season, episode, data, created_at, updated_at)
                           VALUES (:imdb_id, :tmdb_id, :title, :year, :season, :episode, :data, :created_at, :updated_at)""",
                        {
                            "imdb_id": imdb_id,
                            "tmdb_id": tmdb_id,
                            "title": title,
                            "year": year,
                            "season": season,
                            "episode": episode,
                            "data": json.dumps(data),
                            "created_at": current_time,
                            "updated_at": current_time
                        }
                    )

        except Exception as e:
            database_logger.error(f"[WASource] Failed to add content from remote: {type(e).__name__}: {e}")
            errors.append(f"Database error: {type(e).__name__}: {e}")

    if added > 0:
        asyncio.create_task(_increment_wasource_link_count(added))

    return {
        "added": added,
        "skipped": skipped,
        "errors": errors
    }


# ===========================
# Delete WASource Content
# ===========================
async def delete_wasource_links(release_ids: List[str]) -> int:
    try:
        if not release_ids:
            return 0

        releases_by_content: Dict[int, List[int]] = {}
        for release_id in release_ids:
            parts = str(release_id).split("_")
            if len(parts) == 2:
                try:
                    content_id = int(parts[0])
                    release_idx = int(parts[1])
                except ValueError:
                    continue
                if content_id not in releases_by_content:
                    releases_by_content[content_id] = []
                releases_by_content[content_id].append(release_idx)

        deleted = 0
        for content_id, release_indices in releases_by_content.items():
            row = await database.fetch_one(
                "SELECT data FROM wasource WHERE id = :id",
                {"id": content_id}
            )
            if not row:
                continue

            data = json.loads(row["data"])
            releases = data.get("releases", [])

            if not releases and data.get("urls"):
                releases = [{
                    "quality": data.get("quality"),
                    "language": data.get("language"),
                    "release_name": data.get("release_name"),
                    "size": data.get("size"),
                    "urls": data.get("urls", [])
                }]

            indices_to_delete = sorted(set(release_indices), reverse=True)
            for idx in indices_to_delete:
                if 0 <= idx < len(releases):
                    releases.pop(idx)
                    deleted += 1

            if not releases:
                await database.execute(
                    "DELETE FROM wasource WHERE id = :id",
                    {"id": content_id}
                )
            else:
                data["releases"] = releases
                await database.execute(
                    "UPDATE wasource SET data = :data, updated_at = :updated_at WHERE id = :id",
                    {"id": content_id, "data": json.dumps(data), "updated_at": int(time.time())}
                )

        return deleted
    except Exception as e:
        database_logger.error(f"[WASource] Failed to delete releases: {type(e).__name__}: {e}")
        return 0


# ===========================
# Delete All WASource Content
# ===========================
async def delete_all_wasource_links() -> int:
    try:
        total = await database.fetch_val("SELECT COUNT(*) FROM wasource") or 0
        await database.execute("DELETE FROM wasource")
        if total > 0:
            asyncio.create_task(_reset_wasource_link_count())
        return total
    except Exception as e:
        database_logger.error(f"[WASource] Failed to delete all contents: {type(e).__name__}: {e}")
        return 0


# ===========================
# Update WASource Content
# ===========================
async def update_wasource_content(
    content_id: int,
    imdb_id: Optional[str] = None,
    tmdb_id: Optional[str] = None,
    title: Optional[str] = None,
    year: Optional[int] = None,
    release_name: Optional[str] = None,
    quality: Optional[str] = None,
    language: Optional[str] = None,
    size: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    urls: Optional[List[Dict[str, str]]] = None
) -> bool:
    try:
        existing = await database.fetch_one(
            "SELECT * FROM wasource WHERE id = :id",
            {"id": content_id}
        )
        if not existing:
            return False

        data = json.loads(existing["data"])
        releases = data.get("releases", [])

        if not releases and data.get("urls"):
            releases = [{
                "quality": data.get("quality"),
                "language": data.get("language"),
                "release_name": data.get("release_name"),
                "size": data.get("size"),
                "urls": data.get("urls", [])
            }]

        if releases:
            release = releases[0]
            if release_name is not None:
                release["release_name"] = release_name
            if quality is not None:
                release["quality"] = quality
            if language is not None:
                release["language"] = language
            if size is not None:
                release["size"] = size
            if urls is not None:
                release["urls"] = urls
        else:
            releases = [{
                "quality": quality,
                "language": language,
                "release_name": release_name,
                "size": size,
                "urls": urls or []
            }]

        data["releases"] = releases
        data.pop("quality", None)
        data.pop("language", None)
        data.pop("release_name", None)
        data.pop("size", None)
        data.pop("urls", None)

        new_imdb_id = imdb_id if imdb_id is not None else existing["imdb_id"]
        new_tmdb_id = tmdb_id if tmdb_id is not None else existing["tmdb_id"]
        new_title = title if title is not None else existing["title"]
        new_year = year if year is not None else existing["year"]
        new_season = season if season is not None else existing["season"]
        new_episode = episode if episode is not None else existing["episode"]

        await database.execute(
            """UPDATE wasource SET
               imdb_id = :imdb_id,
               tmdb_id = :tmdb_id,
               title = :title,
               year = :year,
               season = :season,
               episode = :episode,
               data = :data,
               updated_at = :updated_at
               WHERE id = :id""",
            {
                "id": content_id,
                "imdb_id": new_imdb_id,
                "tmdb_id": new_tmdb_id,
                "title": new_title,
                "year": new_year,
                "season": new_season,
                "episode": new_episode,
                "data": json.dumps(data),
                "updated_at": int(time.time())
            }
        )

        return True

    except Exception as e:
        database_logger.error(f"[WASource] Failed to update content: {type(e).__name__}: {e}")
        return False


# ===========================
# Delete Single URL from Content
# ===========================
async def delete_wasource_url(content_id: int, url_to_delete: str) -> bool:
    try:
        existing = await database.fetch_one(
            "SELECT id, data FROM wasource WHERE id = :id",
            {"id": content_id}
        )
        if not existing:
            return False

        data = json.loads(existing["data"])
        releases = data.get("releases", [])

        if not releases and data.get("urls"):
            releases = [{
                "quality": data.get("quality"),
                "language": data.get("language"),
                "release_name": data.get("release_name"),
                "size": data.get("size"),
                "urls": data.get("urls", [])
            }]

        url_found = False

        for release in releases:
            original_count = len(release.get("urls", []))
            release["urls"] = [u for u in release.get("urls", []) if u.get("url") != url_to_delete]
            if len(release["urls"]) < original_count:
                url_found = True

        if not url_found:
            return False

        releases = [r for r in releases if r.get("urls")]

        if not releases:
            await database.execute(
                "DELETE FROM wasource WHERE id = :id",
                {"id": content_id}
            )
        else:
            data["releases"] = releases
            data.pop("quality", None)
            data.pop("language", None)
            data.pop("release_name", None)
            data.pop("size", None)
            data.pop("urls", None)

            await database.execute(
                "UPDATE wasource SET data = :data, updated_at = :updated_at WHERE id = :id",
                {"id": content_id, "data": json.dumps(data), "updated_at": int(time.time())}
            )

        asyncio.create_task(_decrement_wasource_link_count(1))
        return True

    except Exception as e:
        database_logger.error(f"[WASource] Failed to delete URL: {type(e).__name__}: {e}")
        return False


# ===========================
# Get WASource Stats
# ===========================
async def get_wasource_stats() -> Dict[str, Any]:
    try:
        from wastream.utils.database import get_cache_stats

        total_contents = await database.fetch_val("SELECT COUNT(*) FROM wasource") or 0
        unique_imdb = await database.fetch_val("SELECT COUNT(DISTINCT imdb_id) FROM wasource") or 0

        cache_stats = await get_cache_stats()
        total_urls = cache_stats.get("wasource_total_links", 0)

        return {
            "total_links": total_urls,
            "total_contents": total_contents,
            "unique_titles": unique_imdb
        }
    except Exception as e:
        database_logger.error(f"[WASource] Failed to get stats: {type(e).__name__}: {e}")
        return {"total_links": 0, "total_contents": 0, "unique_titles": 0}


# ===========================
# Get Links by IMDB (for scraper)
# ===========================
async def get_links_by_imdb(imdb_id: str, season: Optional[int] = None, episode: Optional[int] = None) -> List[Dict]:
    try:
        if season is not None and episode is not None:
            rows = await database.fetch_all(
                """SELECT * FROM wasource
                   WHERE imdb_id = :imdb_id AND season = :season AND episode = :episode""",
                {"imdb_id": imdb_id, "season": season, "episode": episode}
            )
            if not rows:
                rows = await database.fetch_all(
                    """SELECT * FROM wasource
                       WHERE imdb_id = :imdb_id AND season = :season AND episode IS NULL""",
                    {"imdb_id": imdb_id, "season": season}
                )
        elif season is not None:
            rows = await database.fetch_all(
                """SELECT * FROM wasource
                   WHERE imdb_id = :imdb_id AND season = :season""",
                {"imdb_id": imdb_id, "season": season}
            )
        else:
            rows = await database.fetch_all(
                """SELECT * FROM wasource
                   WHERE imdb_id = :imdb_id AND season IS NULL AND episode IS NULL""",
                {"imdb_id": imdb_id}
            )

        results = []
        for row in rows:
            data = json.loads(row["data"])
            releases = data.get("releases", [])

            if not releases and data.get("urls"):
                releases = [{
                    "quality": data.get("quality"),
                    "language": data.get("language"),
                    "release_name": data.get("release_name"),
                    "size": data.get("size"),
                    "urls": data.get("urls", [])
                }]

            for release in releases:
                for url_entry in release.get("urls", []):
                    results.append({
                        "id": row["id"],
                        "imdb_id": row["imdb_id"],
                        "title": row["title"],
                        "release_name": release.get("release_name"),
                        "quality": release.get("quality"),
                        "language": release.get("language"),
                        "size": release.get("size"),
                        "season": row["season"],
                        "episode": row["episode"],
                        "host": url_entry.get("host"),
                        "url": url_entry.get("url")
                    })

        return results

    except Exception as e:
        database_logger.error(f"[WASource] Failed to get links by IMDB: {type(e).__name__}: {e}")
        return []


# ===========================
# Get Links by Title (for Kitsu)
# ===========================
async def get_links_by_title(title: str, year: Optional[int] = None, season: Optional[int] = None, episode: Optional[int] = None) -> List[Dict]:
    try:
        search_pattern = f"%{_escape_like(title.lower())}%"

        if season is not None and episode is not None:
            rows = await database.fetch_all(
                """SELECT * FROM wasource
                   WHERE LOWER(title) LIKE :pattern ESCAPE '\\' AND year = :year AND season = :season AND episode = :episode""",
                {"pattern": search_pattern, "year": year, "season": season, "episode": episode}
            )
        elif season is not None:
            rows = await database.fetch_all(
                """SELECT * FROM wasource
                   WHERE LOWER(title) LIKE :pattern ESCAPE '\\' AND year = :year AND season = :season""",
                {"pattern": search_pattern, "year": year, "season": season}
            )
        else:
            rows = await database.fetch_all(
                """SELECT * FROM wasource
                   WHERE LOWER(title) LIKE :pattern ESCAPE '\\' AND year = :year AND season IS NULL AND episode IS NULL""",
                {"pattern": search_pattern, "year": year}
            )

        results = []
        for row in rows:
            data = json.loads(row["data"])
            releases = data.get("releases", [])

            if not releases and data.get("urls"):
                releases = [{
                    "quality": data.get("quality"),
                    "language": data.get("language"),
                    "release_name": data.get("release_name"),
                    "size": data.get("size"),
                    "urls": data.get("urls", [])
                }]

            for release in releases:
                for url_entry in release.get("urls", []):
                    results.append({
                        "id": row["id"],
                        "imdb_id": row["imdb_id"],
                        "title": row["title"],
                        "release_name": release.get("release_name"),
                        "quality": release.get("quality"),
                        "language": release.get("language"),
                        "size": release.get("size"),
                        "season": row["season"],
                        "episode": row["episode"],
                        "host": url_entry.get("host"),
                        "url": url_entry.get("url")
                    })

        return results

    except Exception as e:
        database_logger.error(f"[WASource] Failed to get links by title: {type(e).__name__}: {e}")
        return []


# ===========================
# Update Release Sizes in DB
# ===========================
async def update_wasource_release_sizes(row_ids: List[int], url_to_size: Dict[str, int]):
    if not url_to_size or not row_ids:
        return

    try:
        unique_ids = set(row_ids)
        updated = 0

        for row_id in unique_ids:
            row = await database.fetch_one(
                "SELECT id, imdb_id, season, episode, data FROM wasource WHERE id = :id",
                {"id": row_id}
            )
            if not row:
                continue

            lock = await _get_wasource_lock(row["imdb_id"], row["season"], row["episode"])

            async with lock:
                fresh_row = await database.fetch_one(
                    "SELECT id, data FROM wasource WHERE id = :id",
                    {"id": row_id}
                )
                if not fresh_row:
                    continue

                data = json.loads(fresh_row["data"])
                releases = data.get("releases", [])
                changed = False

                for release in releases:
                    if release.get("size") and release["size"] > 0:
                        continue
                    for url_entry in release.get("urls", []):
                        entry_url = url_entry.get("url")
                        if entry_url and entry_url in url_to_size and url_to_size[entry_url] > 0:
                            release["size"] = url_to_size[entry_url]
                            changed = True
                            break

                if changed:
                    data["releases"] = releases
                    await database.execute(
                        "UPDATE wasource SET data = :data, updated_at = :updated_at WHERE id = :id",
                        {"id": row_id, "data": json.dumps(data), "updated_at": int(time.time())}
                    )
                    updated += 1

        if updated:
            database_logger.debug(f"[WASource] Updated sizes for {updated} entries")

    except Exception as e:
        database_logger.error(f"[WASource] Failed to update sizes: {type(e).__name__}: {e}")
