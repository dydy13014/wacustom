import asyncio
import json
import os
import time
import uuid
from typing import Optional, List, Dict

from databases import Database

from wastream.config.settings import settings
from wastream.utils.helpers import create_cache_key
from wastream.utils.logger import database_logger

# ===========================
# Database Instance
# ===========================
database = Database(settings.get_database_url())


# ===========================
# Database Setup
# ===========================
async def setup_database():
    try:
        database_logger.info(f"Setup {settings.DATABASE_TYPE} database")
        if settings.DATABASE_TYPE == "sqlite":
            os.makedirs(os.path.dirname(settings.DATABASE_PATH), exist_ok=True)
            if not os.path.exists(settings.DATABASE_PATH):
                open(settings.DATABASE_PATH, "a").close()

        await database.connect()
        database_logger.info("Connected")

        await database.execute("CREATE TABLE IF NOT EXISTS db_version (id INTEGER PRIMARY KEY CHECK (id = 1), version TEXT)")
        current_version = await database.fetch_val("SELECT version FROM db_version WHERE id = 1")

        if current_version != settings.DATABASE_VERSION:
            if settings.DATABASE_TYPE == "sqlite":
                await database.execute("DROP TABLE IF EXISTS scrape_lock")
                await database.execute("DROP TABLE IF EXISTS content_cache")
                await database.execute("INSERT OR REPLACE INTO db_version VALUES (1, :version)", {"version": settings.DATABASE_VERSION})
            else:
                await database.execute("DROP TABLE IF EXISTS scrape_lock CASCADE")
                await database.execute("DROP TABLE IF EXISTS content_cache CASCADE")
                await database.execute(
                    "INSERT INTO db_version VALUES (1, :version) ON CONFLICT (id) DO UPDATE SET version = :version",
                    {"version": settings.DATABASE_VERSION}
                )

        await database.execute("CREATE TABLE IF NOT EXISTS dead_links (url TEXT PRIMARY KEY, expires_at INTEGER)")
        await database.execute("CREATE TABLE IF NOT EXISTS scrape_lock (lock_key TEXT PRIMARY KEY, instance_id TEXT, expires_at INTEGER)")
        await database.execute("CREATE TABLE IF NOT EXISTS content_cache (cache_key TEXT PRIMARY KEY, content TEXT NOT NULL, expires_at INTEGER)")
        await database.execute("""CREATE TABLE IF NOT EXISTS users (
            uuid TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            encrypted_config TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            accessed_at INTEGER NOT NULL
        )""")

        if settings.DATABASE_TYPE == "sqlite":
            await database.execute("""CREATE TABLE IF NOT EXISTS wasource (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                imdb_id TEXT NOT NULL,
                tmdb_id TEXT,
                title TEXT,
                year INTEGER,
                season INTEGER,
                episode INTEGER,
                data TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )""")
        else:
            await database.execute("""CREATE TABLE IF NOT EXISTS wasource (
                id SERIAL PRIMARY KEY,
                imdb_id TEXT NOT NULL,
                tmdb_id TEXT,
                title TEXT,
                year INTEGER,
                season INTEGER,
                episode INTEGER,
                data TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )""")

        await database.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_wasource_unique ON wasource(imdb_id, COALESCE(season, -1), COALESCE(episode, -1))"
        )
        await database.execute("CREATE INDEX IF NOT EXISTS idx_wasource_imdb ON wasource(imdb_id)")
        await database.execute("CREATE INDEX IF NOT EXISTS idx_wasource_tmdb ON wasource(tmdb_id)")
        await database.execute("CREATE INDEX IF NOT EXISTS idx_wasource_title ON wasource(title)")
        await database.execute("CREATE TABLE IF NOT EXISTS cache_stats (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)")

        await database.execute("CREATE INDEX IF NOT EXISTS idx_dead_links_expires ON dead_links(expires_at)")
        await database.execute("CREATE INDEX IF NOT EXISTS idx_scrape_lock_expires ON scrape_lock(expires_at)")
        await database.execute("CREATE INDEX IF NOT EXISTS idx_content_cache_expires ON content_cache(expires_at)")
        await database.execute("CREATE INDEX IF NOT EXISTS idx_users_accessed ON users(accessed_at)")

        if settings.DATABASE_TYPE == "sqlite":
            await database.execute("""CREATE TABLE IF NOT EXISTS remote_api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                key_encrypted TEXT NOT NULL,
                permissions TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL
            )""")

            await database.execute("""CREATE TABLE IF NOT EXISTS remote_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                api_key_encrypted TEXT,
                enabled INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL,
                last_check_at INTEGER,
                last_success_at INTEGER,
                is_online INTEGER DEFAULT 0,
                permissions TEXT,
                fetch_preferences TEXT,
                store_preferences TEXT
            )""")
        else:
            await database.execute("""CREATE TABLE IF NOT EXISTS remote_api_keys (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                key_encrypted TEXT NOT NULL,
                permissions TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL
            )""")

            await database.execute("""CREATE TABLE IF NOT EXISTS remote_instances (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                api_key_encrypted TEXT,
                enabled INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL,
                last_check_at INTEGER,
                last_success_at INTEGER,
                is_online INTEGER DEFAULT 0,
                permissions TEXT,
                fetch_preferences TEXT,
                store_preferences TEXT
            )""")

        await database.execute("CREATE INDEX IF NOT EXISTS idx_remote_api_keys_enabled ON remote_api_keys(enabled)")
        await database.execute("CREATE INDEX IF NOT EXISTS idx_remote_api_keys_hash ON remote_api_keys(key_hash)")
        await database.execute("CREATE INDEX IF NOT EXISTS idx_remote_instances_enabled ON remote_instances(enabled)")

        try:
            await database.execute("ALTER TABLE remote_instances ADD COLUMN store_preferences TEXT")
        except Exception:
            pass

        try:
            if settings.DATABASE_TYPE == "sqlite":
                columns = await database.fetch_all("PRAGMA table_info(remote_api_keys)")
                has_last_used = any(col["name"] == "last_used_at" for col in columns)

                if has_last_used:
                    await database.execute("""CREATE TABLE IF NOT EXISTS remote_api_keys_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        key_hash TEXT NOT NULL,
                        key_encrypted TEXT NOT NULL,
                        permissions TEXT NOT NULL,
                        enabled INTEGER DEFAULT 1,
                        created_at INTEGER NOT NULL
                    )""")
                    await database.execute("""INSERT INTO remote_api_keys_new (id, name, key_hash, key_encrypted, permissions, enabled, created_at)
                        SELECT id, name, key_hash, key_encrypted, permissions, enabled, created_at FROM remote_api_keys""")
                    await database.execute("DROP TABLE remote_api_keys")
                    await database.execute("ALTER TABLE remote_api_keys_new RENAME TO remote_api_keys")
                    await database.execute("CREATE INDEX IF NOT EXISTS idx_remote_api_keys_enabled ON remote_api_keys(enabled)")
                    await database.execute("CREATE INDEX IF NOT EXISTS idx_remote_api_keys_hash ON remote_api_keys(key_hash)")
                    database_logger.info("Migration: Removed last_used_at column from remote_api_keys")
            else:
                column_exists = await database.fetch_one(
                    """SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'remote_api_keys' AND column_name = 'last_used_at'"""
                )
                if column_exists:
                    await database.execute("ALTER TABLE remote_api_keys DROP COLUMN last_used_at")
                    database_logger.info("Migration: Removed last_used_at column from remote_api_keys")
        except Exception:
            pass

        if settings.DATABASE_TYPE == "sqlite":
            await database.execute("PRAGMA busy_timeout=30000")
            await database.execute("PRAGMA journal_mode=WAL")
            await database.execute("PRAGMA synchronous=NORMAL")
            await database.execute("PRAGMA temp_store=MEMORY")
            await database.execute("PRAGMA cache_size=-2000")

        # Create admin_settings table
        await database.execute("CREATE TABLE IF NOT EXISTS admin_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

        # Load persistent admin settings and apply to global settings instance
        try:
            rows = await database.fetch_all("SELECT key, value FROM admin_settings")
            for row in rows:
                key = row["key"]
                value = row["value"]
                if hasattr(settings, key):
                    # Empty string means None
                    val_to_apply = value if value.strip() else None
                    setattr(settings, key, val_to_apply)
                    database_logger.info(f"Loaded persistent setting: {key} = {val_to_apply}")
        except Exception as e:
            database_logger.error(f"Failed to load persistent admin settings: {e}")

        database_logger.info("Setup completed")

    except Exception as e:
        database_logger.error(f"Setup failed: {type(e).__name__}: {e}")
        raise


# ===========================
# Cleanup Expired Data
# ===========================
async def cleanup_expired_data():
    while True:
        try:
            current_time = int(time.time())

            deleted_locks = await database.execute(
                "DELETE FROM scrape_lock WHERE expires_at < :current_time",
                {"current_time": current_time}
            )

            deleted_links = await database.execute(
                "DELETE FROM dead_links WHERE expires_at > 0 AND expires_at < :current_time",
                {"current_time": current_time}
            )

            deleted_cache = await database.execute(
                "DELETE FROM content_cache WHERE expires_at > 0 AND expires_at < :current_time",
                {"current_time": current_time}
            )

            if deleted_locks or deleted_links or deleted_cache:
                database_logger.debug(f"Cleanup: {deleted_locks} locks, {deleted_links} links, {deleted_cache} cache")

        except Exception as e:
            database_logger.error(f"Cleanup error: {type(e).__name__}: {e}")

        await asyncio.sleep(settings.CLEANUP_INTERVAL)


# ===========================
# Cache Stats Management
# ===========================
async def get_cache_stats() -> dict:
    try:
        row = await database.fetch_one("SELECT data FROM cache_stats WHERE id = 1")
        if row:
            return json.loads(row["data"])
    except Exception:
        pass
    return {}


async def save_cache_stats(stats: dict):
    try:
        data = json.dumps(stats, separators=(",", ":"))
        if settings.DATABASE_TYPE == "sqlite":
            await database.execute(
                "INSERT OR REPLACE INTO cache_stats (id, data) VALUES (1, :data)",
                {"data": data}
            )
        else:
            await database.execute(
                "INSERT INTO cache_stats (id, data) VALUES (1, :data) ON CONFLICT (id) DO UPDATE SET data = :data",
                {"data": data}
            )
    except Exception as e:
        database_logger.error(f"[CacheStats] Save failed: {type(e).__name__}: {e}")


async def rebuild_cache_stats():
    database_logger.info("[CacheStats] Rebuilding stats...")
    unique_titles = set()
    streams_total = 0
    by_source: dict = {}
    by_content_type: dict = {}
    searches_cached = 0

    try:
        batch_size = 500
        offset = 0
        while True:
            rows = await database.fetch_all(
                "SELECT cache_key, content FROM content_cache LIMIT :limit OFFSET :offset",
                {"limit": batch_size, "offset": offset}
            )
            if not rows:
                break

            for row in rows:
                cache_key = row["cache_key"]
                parts = cache_key.split(":", 1)
                if len(parts) > 1:
                    unique_titles.add(parts[1])

                try:
                    content = json.loads(row["content"])
                    if isinstance(content, list):
                        streams_total += len(content)
                        for item in content:
                            if isinstance(item, dict):
                                src = item.get("source", "Unknown")
                                by_source[src] = by_source.get(src, 0) + 1

                                if "_movie" in cache_key:
                                    by_content_type["movie"] = by_content_type.get("movie", 0) + 1
                                elif "_series" in cache_key:
                                    by_content_type["series"] = by_content_type.get("series", 0) + 1
                                elif "_anime" in cache_key:
                                    by_content_type["anime"] = by_content_type.get("anime", 0) + 1
                except (json.JSONDecodeError, TypeError):
                    pass

            offset += batch_size

        searches_cached = len(unique_titles)

        wasource_total_links = 0
        ws_offset = 0
        while True:
            ws_rows = await database.fetch_all(
                "SELECT data FROM wasource LIMIT :limit OFFSET :offset",
                {"limit": batch_size, "offset": ws_offset}
            )
            if not ws_rows:
                break
            for ws_row in ws_rows:
                try:
                    ws_data = json.loads(ws_row["data"])
                    ws_releases = ws_data.get("releases", [])
                    if not ws_releases and ws_data.get("urls"):
                        wasource_total_links += len(ws_data.get("urls", []))
                    else:
                        for ws_release in ws_releases:
                            wasource_total_links += len(ws_release.get("urls", []))
                except (json.JSONDecodeError, TypeError):
                    pass
            ws_offset += batch_size

        stats = {
            "searches_cached": searches_cached,
            "streams_total": streams_total,
            "by_source_total": by_source,
            "by_content_type_total": by_content_type,
            "wasource_total_links": wasource_total_links
        }
        await save_cache_stats(stats)
        database_logger.info(f"[CacheStats] Done: {searches_cached} searches, {streams_total} streams, {wasource_total_links} wasource links")
        return stats

    except Exception as e:
        database_logger.error(f"[CacheStats] Rebuild failed: {type(e).__name__}: {e}")
        return {"searches_cached": 0, "streams_total": 0, "by_source_total": {}, "by_content_type_total": {}, "wasource_total_links": 0}


async def update_cache_stats_on_set(cache_key: str, new_results: list, old_results: list = None):
    try:
        stats = await get_cache_stats()
        if not stats:
            return

        if old_results:
            streams_total = stats.get("streams_total", 0) - len(old_results)
            by_source = stats.get("by_source_total", {})
            by_content_type = stats.get("by_content_type_total", {})
            for item in old_results:
                if isinstance(item, dict):
                    src = item.get("source", "Unknown")
                    if src in by_source:
                        by_source[src] = max(0, by_source[src] - 1)

                    if "_movie" in cache_key:
                        by_content_type["movie"] = max(0, by_content_type.get("movie", 0) - 1)
                    elif "_series" in cache_key:
                        by_content_type["series"] = max(0, by_content_type.get("series", 0) - 1)
                    elif "_anime" in cache_key:
                        by_content_type["anime"] = max(0, by_content_type.get("anime", 0) - 1)
            stats["streams_total"] = max(0, streams_total)
            stats["by_source_total"] = by_source
            stats["by_content_type_total"] = by_content_type

        streams_total = stats.get("streams_total", 0) + len(new_results)
        by_source = stats.get("by_source_total", {})
        by_content_type = stats.get("by_content_type_total", {})
        for item in new_results:
            if isinstance(item, dict):
                src = item.get("source", "Unknown")
                by_source[src] = by_source.get(src, 0) + 1

                if "_movie" in cache_key:
                    by_content_type["movie"] = by_content_type.get("movie", 0) + 1
                elif "_series" in cache_key:
                    by_content_type["series"] = by_content_type.get("series", 0) + 1
                elif "_anime" in cache_key:
                    by_content_type["anime"] = by_content_type.get("anime", 0) + 1

        stats["streams_total"] = streams_total
        stats["by_source_total"] = by_source
        stats["by_content_type_total"] = by_content_type

        parts = cache_key.split(":", 1)
        if len(parts) > 1:
            title_key = parts[1]
            existing_count = await database.fetch_val(
                "SELECT COUNT(*) FROM content_cache WHERE cache_key LIKE :pattern",
                {"pattern": f"%:{title_key}"}
            )
            if existing_count and existing_count <= 1:
                stats["searches_cached"] = stats.get("searches_cached", 0) + 1

        await save_cache_stats(stats)

    except Exception as e:
        database_logger.error(f"[CacheStats] Update failed: {type(e).__name__}: {e}")


# ===========================
# Dead Link Checking
# ===========================
async def is_dead_link(url: str) -> bool:
    try:
        current_time = int(time.time())
        result = await database.fetch_one(
            "SELECT expires_at FROM dead_links WHERE url = :url",
            {"url": url}
        )
        if result is None:
            return False

        expires_at = result[0]
        if expires_at == -1:
            return True
        return expires_at > current_time
    except Exception as e:
        database_logger.error(f"Dead link check failed: {type(e).__name__}: {e}")
        return False


async def check_dead_links_batch(urls: List[str]) -> Dict[str, bool]:
    if not urls:
        return {}

    try:
        import asyncio
        from wastream.services.remote import fetch_remote_dead_links

        async def check_local():
            current_time = int(time.time())
            local_results = {}
            placeholders = ", ".join([f":url{i}" for i in range(len(urls))])
            params = {f"url{i}": url for i, url in enumerate(urls)}
            rows = await database.fetch_all(
                f"SELECT url, expires_at FROM dead_links WHERE url IN ({placeholders})",
                params
            )
            for row in rows:
                url = row["url"]
                expires_at = row["expires_at"]
                if expires_at == -1 or expires_at > current_time:
                    local_results[url] = True
            return local_results

        local_result, remote_result = await asyncio.gather(
            check_local(),
            fetch_remote_dead_links(urls),
            return_exceptions=True
        )

        results = {}

        if isinstance(local_result, dict):
            results.update(local_result)

        if isinstance(remote_result, tuple):
            remote_dead, should_store = remote_result
            for url, is_dead in remote_dead.items():
                if is_dead and url not in results:
                    results[url] = True
                    if should_store:
                        asyncio.create_task(_store_remote_dead_link(url))

        return results

    except Exception as e:
        database_logger.error(f"Batch dead link check failed: {type(e).__name__}: {e}")
        return {}


async def _store_remote_dead_link(url: str):
    try:
        existing = await database.fetch_one(
            "SELECT url FROM dead_links WHERE url = :url",
            {"url": url}
        )
        if not existing:
            await mark_dead_link(url, settings.DEAD_LINK_TTL)
            database_logger.debug("Stored remote dead link locally")
    except Exception:
        pass


# ===========================
# Dead Link Marking
# ===========================
async def mark_dead_link(url: str, ttl: int):
    try:
        if ttl == -1:
            expires_at = -1
        else:
            current_time = int(time.time())
            expires_at = current_time + ttl

        if settings.DATABASE_TYPE == "sqlite":
            query = "INSERT OR REPLACE INTO dead_links (url, expires_at) VALUES (:url, :expires_at)"
        else:
            query = """INSERT INTO dead_links (url, expires_at) VALUES (:url, :expires_at)
                       ON CONFLICT (url) DO UPDATE SET expires_at = :expires_at"""

        await database.execute(query, {"url": url, "expires_at": expires_at})
    except Exception as e:
        database_logger.error(f"Mark dead link failed: {type(e).__name__}: {e}")


# ===========================
# Lock Acquisition
# ===========================
async def acquire_lock(lock_key: str, instance_id: str, duration: int = settings.SCRAPE_LOCK_TTL) -> bool:
    try:
        current_time = int(time.time())
        expires_at = current_time + duration

        await database.execute(
            "DELETE FROM scrape_lock WHERE expires_at < :current_time",
            {"current_time": current_time}
        )

        if settings.DATABASE_TYPE == "sqlite":
            query = "INSERT OR IGNORE INTO scrape_lock (lock_key, instance_id, expires_at) VALUES (:lock_key, :instance_id, :expires_at)"
        else:
            query = """INSERT INTO scrape_lock (lock_key, instance_id, expires_at)
                       VALUES (:lock_key, :instance_id, :expires_at) ON CONFLICT (lock_key) DO NOTHING"""

        await database.execute(query, {
            "lock_key": lock_key,
            "instance_id": instance_id,
            "expires_at": expires_at
        })

        existing_lock = await database.fetch_one(
            "SELECT instance_id FROM scrape_lock WHERE lock_key = :lock_key",
            {"lock_key": lock_key}
        )

        return existing_lock and existing_lock["instance_id"] == instance_id

    except Exception as e:
        database_logger.error(f"Lock attempt failed: {type(e).__name__}: {e}")
        return False


# ===========================
# Lock Release
# ===========================
async def release_lock(lock_key: str, instance_id: str):
    try:
        await database.execute(
            "DELETE FROM scrape_lock WHERE lock_key = :lock_key AND instance_id = :instance_id",
            {"lock_key": lock_key, "instance_id": instance_id}
        )
    except Exception as e:
        database_logger.error(f"Failed to release lock: {type(e).__name__}: {e}")


# ===========================
# Search Lock Context Manager
# ===========================
class SearchLock:
    def __init__(self, content_type: str, title: str, year: Optional[str] = None,
                 timeout: Optional[int] = None, retry_interval: float = 1.0):
        lock_key = create_cache_key(content_type, title, year)
        self.lock_key = lock_key
        self.instance_id = f"{uuid.uuid4()}_{os.getpid()}"
        self.duration = settings.SCRAPE_LOCK_TTL
        self.timeout = timeout if timeout is not None else settings.SCRAPE_WAIT_TIMEOUT
        self.retry_interval = retry_interval
        self.acquired = False

    async def __aenter__(self):
        start_time = time.time()
        attempt = 0

        while time.time() - start_time < self.timeout:
            attempt += 1
            self.acquired = await acquire_lock(self.lock_key, self.instance_id, self.duration)

            if self.acquired:
                elapsed_ms = int((time.time() - start_time) * 1000)
                database_logger.debug(
                    f"Lock acquired: {self.lock_key[:30]}... "
                    f"({elapsed_ms}ms, attempt {attempt})"
                )
                return self

            database_logger.debug(f"Lock busy: {self.lock_key[:30]}... (retry in {self.retry_interval}s)")
            await asyncio.sleep(self.retry_interval)

        elapsed_ms = int((time.time() - start_time) * 1000)
        database_logger.warning(
            f"Lock timeout: {self.lock_key[:30]}... "
            f"({elapsed_ms}ms, {attempt} attempts)"
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            await release_lock(self.lock_key, self.instance_id)
            database_logger.debug(f"Lock released: {self.lock_key[:30]}...")


# ===========================
# Database Teardown
# ===========================
async def teardown_database():
    try:
        await database.disconnect()
        database_logger.info("Disconnected")
    except Exception as e:
        database_logger.error(f"Failed to disconnect: {type(e).__name__}: {e}")
