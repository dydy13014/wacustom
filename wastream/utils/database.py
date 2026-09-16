import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from databases import Database

from wastream.config.settings import settings
from wastream.utils.helpers import build_cache_key
from wastream.utils.logger import database_logger
from wastream.utils.urls import DOMAIN_ALIASES, canonicalize_url

# ===========================
# Database Instance
# ===========================
_database_options = {}
if settings.DATABASE_TYPE == "sqlite":
    _database_options["timeout"] = max(
        1, int(settings.DATABASE_BUSY_TIMEOUT)
    )
database = Database(settings.get_database_url(), **_database_options)

T = TypeVar("T")
_cache_stats_lock = asyncio.Lock()
_sqlite_transaction_lock = asyncio.Lock()
_remote_dead_link_store_lock = asyncio.Lock()
_cache_stats_suppression_depth = 0

DEAD_LINK_STATUS_ALIVE = 0
DEAD_LINK_STATUS_RECHECKABLE = 1
DEAD_LINK_STATUS_CONFIRMED = 2
_QUERY_BATCH_SIZE = 500
_HEALTH_HISTORY_CURRENT_INDEX = "idx_health_status_history_current"


# ===========================
# Database Retry Helpers
# ===========================
def _is_retryable_database_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in (
        "database is locked",
        "database table is locked",
        "database is busy",
        "deadlock detected",
        "could not serialize access",
        "serialization failure",
        "lock not available",
        "lock timeout",
        "could not obtain lock",
    ))


async def run_database_operation(
        operation: Callable[[], Awaitable[T]], operation_name: str) -> T:
    max_attempts = max(1, int(settings.DATABASE_RETRY_MAX_ATTEMPTS))
    base_delay = max(0.05, float(settings.DATABASE_RETRY_DELAY))

    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except Exception as error:
            if (
                    not _is_retryable_database_error(error)
                    or attempt >= max_attempts
            ):
                raise

            delay = min(5.0, base_delay * (2 ** (attempt - 1)))
            database_logger.warning(
                f"[Database] Transient conflict during {operation_name}; "
                f"retry {attempt}/{max_attempts - 1} in {delay:.2f}s"
            )
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"Database operation did not complete: {operation_name}"
    )


async def run_database_transaction(
        operation: Callable[[], Awaitable[T]], operation_name: str) -> T:
    async def transaction_operation() -> T:
        async with database.transaction():
            return await operation()

    if settings.DATABASE_TYPE == "sqlite":
        async with _sqlite_transaction_lock:
            return await run_database_operation(
                transaction_operation,
                operation_name,
            )
    return await run_database_operation(transaction_operation, operation_name)


def cache_stats_updates_suppressed() -> bool:
    return _cache_stats_suppression_depth > 0


@asynccontextmanager
async def suppress_cache_stats_updates():
    global _cache_stats_suppression_depth

    async with _cache_stats_lock:
        _cache_stats_suppression_depth += 1
    try:
        yield
    finally:
        async with _cache_stats_lock:
            _cache_stats_suppression_depth = max(
                0, _cache_stats_suppression_depth - 1
            )


# ===========================
# Database Setup
# ===========================
async def _table_columns(table_name: str) -> set:
    if settings.DATABASE_TYPE == "sqlite":
        return {
            row["name"]
            for row in await database.fetch_all(
                f"PRAGMA table_info({table_name})"
            )
        }
    rows = await database.fetch_all(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = :table_name",
        {"table_name": table_name},
    )
    return {row["column_name"] for row in rows}


def _is_health_history_current_index(index_name: str) -> bool:
    if index_name == _HEALTH_HISTORY_CURRENT_INDEX:
        return True

    # pgloader prefixes indexes copied from SQLite with idx_<table OID>_.
    prefix = "idx_"
    suffix = f"_{_HEALTH_HISTORY_CURRENT_INDEX}"
    if not index_name.startswith(prefix) or not index_name.endswith(suffix):
        return False
    return index_name[len(prefix):-len(suffix)].isdigit()


async def _repair_health_history_current_index() -> None:
    if settings.DATABASE_TYPE == "sqlite":
        indexes = await database.fetch_all(
            "PRAGMA index_list(health_status_history)"
        )
    else:
        indexes = await database.fetch_all(
            "SELECT index_class.relname AS name, "
            "index_meta.indisunique AS is_unique, "
            "index_meta.indpred IS NOT NULL AS is_partial, "
            "pg_get_indexdef(index_meta.indexrelid, 1, TRUE) AS first_column, "
            "pg_get_indexdef(index_meta.indexrelid, 2, TRUE) AS second_column "
            "FROM pg_index AS index_meta "
            "JOIN pg_class AS table_class "
            "ON table_class.oid = index_meta.indrelid "
            "JOIN pg_class AS index_class "
            "ON index_class.oid = index_meta.indexrelid "
            "JOIN pg_namespace AS table_schema "
            "ON table_schema.oid = table_class.relnamespace "
            "WHERE table_schema.nspname = current_schema() "
            "AND table_class.relname = :table_name",
            {"table_name": "health_status_history"},
        )

    for index in indexes:
        index_name = str(index["name"] or "")
        if not _is_health_history_current_index(index_name):
            continue

        if settings.DATABASE_TYPE == "sqlite":
            columns = await database.fetch_all(
                f'PRAGMA index_info("{index_name}")'
            )
            column_names = [str(column["name"]) for column in columns]
            is_unique = bool(index["unique"])
            is_partial = bool(index["partial"])
        else:
            column_names = [
                str(index["first_column"] or ""),
                str(index["second_column"] or ""),
            ]
            is_unique = bool(index["is_unique"])
            is_partial = bool(index["is_partial"])

        if not is_unique or is_partial or column_names != ["category", "name"]:
            continue
        await database.execute(f'DROP INDEX IF EXISTS "{index_name}"')
        database_logger.info(
            "[Migration] Repaired malformed health-status current index"
        )


def _dead_link_expiry_value(first: int, second: int) -> int:
    if first == -1 or second == -1:
        return -1
    return max(first, second)


def _integer_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _merge_dead_link_records(
    current: Dict[str, Any], incoming: Dict[str, Any]
) -> Dict[str, Any]:
    current_count = _integer_value(current.get("failure_count"))
    incoming_count = _integer_value(incoming.get("failure_count"))
    failure_count = max(current_count, incoming_count)

    first_failures = [
        value
        for value in (
            _integer_value(current.get("first_failure_at")),
            _integer_value(incoming.get("first_failure_at")),
        )
        if value > 0
    ]
    current_last = _integer_value(current.get("last_failure_at"))
    incoming_last = _integer_value(incoming.get("last_failure_at"))

    if current_count > incoming_count:
        expires_at = _integer_value(current.get("expires_at"), -1)
    elif incoming_count > current_count:
        expires_at = _integer_value(incoming.get("expires_at"), -1)
    elif failure_count == DEAD_LINK_STATUS_RECHECKABLE:
        expires_at = -1
    else:
        expires_at = _dead_link_expiry_value(
            _integer_value(current.get("expires_at"), -1),
            _integer_value(incoming.get("expires_at"), -1),
        )

    current_reason = str(current.get("reason") or "")
    incoming_reason = str(incoming.get("reason") or "")
    reason = (
        incoming_reason
        if incoming_reason and incoming_last >= current_last
        else current_reason
    )

    return {
        "url": incoming.get("url") or current.get("url"),
        "failure_count": failure_count,
        "first_failure_at": min(first_failures) if first_failures else 0,
        "last_failure_at": max(current_last, incoming_last),
        "expires_at": expires_at,
        "reason": reason[:200],
    }


async def _write_dead_link_record(values: Dict[str, Any]) -> None:
    await database.execute(
        """INSERT INTO dead_links (
               url, failure_count, first_failure_at,
               last_failure_at, expires_at, reason
           ) VALUES (
               :url, :failure_count, :first_failure_at,
               :last_failure_at, :expires_at, :reason
           )
           ON CONFLICT (url) DO UPDATE SET
               failure_count = :failure_count,
               first_failure_at = :first_failure_at,
               last_failure_at = :last_failure_at,
               expires_at = :expires_at,
               reason = :reason""",
        values,
    )


async def _setup_dead_links_schema() -> None:
    await database.execute("""CREATE TABLE IF NOT EXISTS dead_links (
        url TEXT PRIMARY KEY,
        failure_count INTEGER NOT NULL DEFAULT 1,
        first_failure_at INTEGER NOT NULL DEFAULT 0,
        last_failure_at INTEGER NOT NULL DEFAULT 0,
        expires_at INTEGER NOT NULL,
        reason TEXT
    )""")

    columns = await _table_columns("dead_links")
    additions = {
        "failure_count": "INTEGER NOT NULL DEFAULT 1",
        "first_failure_at": "INTEGER NOT NULL DEFAULT 0",
        "last_failure_at": "INTEGER NOT NULL DEFAULT 0",
        "reason": "TEXT",
    }
    for column, definition in additions.items():
        if column in columns:
            continue
        if settings.DATABASE_TYPE == "sqlite":
            await database.execute(
                f"ALTER TABLE dead_links ADD COLUMN {column} {definition}"
            )
        else:
            await database.execute(
                "ALTER TABLE dead_links "
                f"ADD COLUMN IF NOT EXISTS {column} {definition}"
            )

    await database.execute(
        "UPDATE dead_links SET failure_count = :recheckable_status "
        "WHERE failure_count IS NULL "
        "OR failure_count NOT IN (:recheckable_status, :confirmed_status)",
        {
            "recheckable_status": DEAD_LINK_STATUS_RECHECKABLE,
            "confirmed_status": DEAD_LINK_STATUS_CONFIRMED,
        },
    )
    await database.execute(
        "UPDATE dead_links SET first_failure_at = 0 "
        "WHERE first_failure_at IS NULL"
    )
    await database.execute(
        "UPDATE dead_links SET last_failure_at = 0 "
        "WHERE last_failure_at IS NULL"
    )
    # Legacy rows have one known failure and remain eligible for one recheck.
    await database.execute(
        "UPDATE dead_links SET failure_count = :recheckable_status "
        "WHERE failure_count = :confirmed_status "
        "AND first_failure_at = 0 AND last_failure_at = 0",
        {
            "recheckable_status": DEAD_LINK_STATUS_RECHECKABLE,
            "confirmed_status": DEAD_LINK_STATUS_CONFIRMED,
        },
    )
    await database.execute(
        "UPDATE dead_links SET expires_at = -1 "
        "WHERE failure_count = :recheckable_status",
        {"recheckable_status": DEAD_LINK_STATUS_RECHECKABLE},
    )


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

        await _setup_dead_links_schema()
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
        await database.execute("""CREATE TABLE IF NOT EXISTS admin_sessions (
            token_hash TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )""")
        await database.execute("""CREATE TABLE IF NOT EXISTS health_status_history (
            event_id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            previous_status TEXT,
            status TEXT NOT NULL,
            changed_at INTEGER NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 1
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
        await database.execute("CREATE TABLE IF NOT EXISTS settings_overrides (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)")

        await database.execute("CREATE INDEX IF NOT EXISTS idx_dead_links_expires ON dead_links(expires_at)")
        await database.execute(
            "CREATE INDEX IF NOT EXISTS idx_dead_links_status_expires "
            "ON dead_links(failure_count, expires_at)"
        )
        await database.execute("CREATE INDEX IF NOT EXISTS idx_scrape_lock_expires ON scrape_lock(expires_at)")
        await database.execute("CREATE INDEX IF NOT EXISTS idx_content_cache_expires ON content_cache(expires_at)")
        await database.execute("CREATE INDEX IF NOT EXISTS idx_users_accessed ON users(accessed_at)")
        await database.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires "
            "ON admin_sessions(expires_at)"
        )
        await database.execute(
            "CREATE INDEX IF NOT EXISTS idx_health_status_history_changed "
            "ON health_status_history(changed_at)"
        )
        await _repair_health_history_current_index()
        await database.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            f"{_HEALTH_HISTORY_CURRENT_INDEX} "
            "ON health_status_history(category, name) WHERE is_current = 1"
        )

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
                    database_logger.info("[Migration] Removed last_used_at column from remote_api_keys")
            else:
                column_exists = await database.fetch_one(
                    """SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'remote_api_keys' AND column_name = 'last_used_at'"""
                )
                if column_exists:
                    await database.execute("ALTER TABLE remote_api_keys DROP COLUMN last_used_at")
                    database_logger.info("[Migration] Removed last_used_at column from remote_api_keys")
        except Exception:
            pass

        if settings.DATABASE_TYPE == "sqlite":
            busy_timeout_ms = max(
                1, int(settings.DATABASE_BUSY_TIMEOUT)
            ) * 1000
            await database.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            await database.execute("PRAGMA journal_mode=WAL")
            await database.execute("PRAGMA synchronous=NORMAL")
            await database.execute("PRAGMA temp_store=MEMORY")
            await database.execute("PRAGMA cache_size=-2000")

        await migrate_domain_aliases()

        # Notre table historique de reglages persistants, anterieure au
        # settings_manager d'upstream (qui utilise sa propre table
        # settings_overrides, cree plus haut). Les deux coexistent sans
        # collision ; celle-ci reste alimentee par notre endpoint d'admin.
        await database.execute("CREATE TABLE IF NOT EXISTS admin_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

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
# Domain Alias Migration
# ===========================
async def migrate_domain_aliases():
    try:
        for alias, canonical in DOMAIN_ALIASES.items():
            params = {"alias": alias, "canonical": canonical, "pattern": f"%{alias}%"}
            await database.execute(
                "UPDATE content_cache SET content = REPLACE(content, :alias, :canonical) "
                "WHERE content LIKE :pattern",
                params
            )
            await database.execute(
                "UPDATE wasource SET data = REPLACE(data, :alias, :canonical) "
                "WHERE data LIKE :pattern",
                params
            )

        like_params = {f"p{index}": f"%{alias}%" for index, alias in enumerate(DOMAIN_ALIASES)}
        where_clause = " OR ".join(f"url LIKE :{key}" for key in like_params)
        dead_link_rows = await database.fetch_all(
            "SELECT url, failure_count, first_failure_at, last_failure_at, "
            f"expires_at, reason FROM dead_links WHERE {where_clause}",
            like_params,
        )

        migrated_dead_links = 0
        for row in dead_link_rows:
            new_url = canonicalize_url(row["url"])
            if new_url == row["url"]:
                continue
            existing = await database.fetch_one(
                "SELECT url, failure_count, first_failure_at, "
                "last_failure_at, expires_at, reason FROM dead_links "
                "WHERE url = :url",
                {"url": new_url},
            )
            values = dict(row)
            values["url"] = new_url
            if existing:
                values = _merge_dead_link_records(dict(existing), values)
            await _write_dead_link_record(values)
            await database.execute("DELETE FROM dead_links WHERE url = :url", {"url": row["url"]})
            migrated_dead_links += 1

        if migrated_dead_links:
            database_logger.info(
                f"[Migration] Normalized {migrated_dead_links} dead links"
            )
    except Exception as e:
        database_logger.error(f"[Migration] Domain alias normalization failed: {type(e).__name__}: {e}")


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

            deleted_sessions = await database.execute(
                "DELETE FROM admin_sessions WHERE expires_at <= :current_time",
                {"current_time": current_time}
            )

            retention_seconds = max(
                1,
                int(settings.HEALTH_STATUS_HISTORY_RETENTION),
            )
            history_cutoff = current_time - retention_seconds
            deleted_history = await database.execute(
                "DELETE FROM health_status_history "
                "WHERE is_current = 0 AND changed_at < :history_cutoff",
                {"history_cutoff": history_cutoff},
            )

            if (deleted_locks or deleted_links
                    or deleted_cache or deleted_sessions or deleted_history):
                database_logger.debug(
                    f"Cleanup: {deleted_locks} locks, {deleted_links} links, "
                    f"{deleted_cache} cache, {deleted_sessions} admin sessions, "
                    f"{deleted_history} health events"
                )

        except Exception as e:
            database_logger.error(f"Cleanup error: {type(e).__name__}: {e}")

        await asyncio.sleep(max(1, settings.CLEANUP_INTERVAL))


# ===========================
# Cache Stats Management
# ===========================
async def get_cache_stats() -> dict:
    async def read() -> dict:
        row = await database.fetch_one("SELECT data FROM cache_stats WHERE id = 1")
        if row:
            return json.loads(row["data"])
        return {}

    try:
        return await run_database_operation(read, "cache stats read")
    except Exception:
        pass
    return {}


async def set_cache_stats(stats: dict):
    if cache_stats_updates_suppressed():
        return

    async with _cache_stats_lock:
        if cache_stats_updates_suppressed():
            return
        await _write_cache_stats(stats)


async def _write_cache_stats(stats: dict):
    data = json.dumps(stats, separators=(",", ":"))

    async def write() -> None:
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

    try:
        await run_database_operation(write, "cache stats save")
    except Exception as error:
        database_logger.error(
            f"[CacheStats] Save failed: {type(error).__name__}: {error}"
        )


async def rebuild_cache_stats():
    async with _cache_stats_lock:
        try:
            return await run_database_operation(_rebuild_cache_stats, "cache stats rebuild")
        except Exception as error:
            database_logger.error(f"[CacheStats] Rebuild failed: {type(error).__name__}: {error}")
            return {
                "searches_cached": 0, "streams_total": 0,
                "by_source_total": {}, "by_content_type_total": {},
                "wasource_total_links": 0
            }


async def _rebuild_cache_stats():
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
        await _write_cache_stats(stats)
        database_logger.info(f"[CacheStats] Done: {searches_cached} searches, {streams_total} streams, {wasource_total_links} wasource links")
        return stats

    except Exception as error:
        if _is_retryable_database_error(error):
            raise
        database_logger.error(f"[CacheStats] Rebuild failed: {type(error).__name__}: {error}")
        return {
            "searches_cached": 0, "streams_total": 0,
            "by_source_total": {}, "by_content_type_total": {},
            "wasource_total_links": 0
        }


async def update_cache_stats_on_set(
    cache_key: str,
    new_results: list,
    old_results: Optional[list] = None,
):
    async with _cache_stats_lock:
        if cache_stats_updates_suppressed():
            return
        try:
            await run_database_operation(
                lambda: _update_cache_stats_on_set(cache_key, new_results, old_results),
                "cache stats update"
            )
        except Exception as error:
            database_logger.error(
                f"[CacheStats] Update failed: {type(error).__name__}: {error}"
            )


async def _update_cache_stats_on_set(
    cache_key: str,
    new_results: list,
    old_results: Optional[list] = None,
):
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

        await _write_cache_stats(stats)

    except Exception as error:
        if _is_retryable_database_error(error):
            raise
        database_logger.error(
            f"[CacheStats] Update failed: {type(error).__name__}: {error}"
        )


# ===========================
# Dead Link Checking
# ===========================
async def is_dead_link(url: str) -> bool:
    try:
        url = canonicalize_url(url) or url
        current_time = int(time.time())
        result = await database.fetch_one(
            "SELECT failure_count, expires_at FROM dead_links "
            "WHERE url = :url",
            {"url": url}
        )
        if result is None:
            return False

        if int(result["failure_count"] or 0) < DEAD_LINK_STATUS_RECHECKABLE:
            return False
        expires_at = result["expires_at"]
        if expires_at == -1:
            return True
        return expires_at > current_time
    except Exception as e:
        database_logger.error(f"Dead link check failed: {type(e).__name__}: {e}")
        return False


async def _fetch_local_dead_link_statuses(
    urls: List[str],
) -> Dict[str, Dict[str, Any]]:
    current_time = int(time.time())
    results = {}
    for start in range(0, len(urls), _QUERY_BATCH_SIZE):
        batch = urls[start:start + _QUERY_BATCH_SIZE]
        placeholders = ", ".join(
            f":url{i}" for i in range(len(batch))
        )
        params: Dict[str, Any] = {
            **{f"url{i}": url for i, url in enumerate(batch)},
            "current_time": current_time,
        }
        rows = await database.fetch_all(
            "SELECT url, failure_count, first_failure_at, "
            "last_failure_at, expires_at, reason FROM dead_links "
            f"WHERE url IN ({placeholders}) "
            "AND (expires_at = -1 OR expires_at > :current_time)",
            params,
        )
        for row in rows:
            results[row["url"]] = dict(row)
    return results


async def _fetch_remote_dead_link_statuses(
    urls: List[str],
) -> tuple:
    from wastream.services.remote import fetch_remote_dead_links

    remote_statuses = {}
    stored_statuses = {}
    for start in range(0, len(urls), _QUERY_BATCH_SIZE):
        batch = urls[start:start + _QUERY_BATCH_SIZE]
        batch_statuses, batch_stored = await fetch_remote_dead_links(batch)
        for url, failure_count in batch_statuses.items():
            remote_statuses[url] = max(
                int(remote_statuses.get(url, DEAD_LINK_STATUS_ALIVE)),
                int(failure_count),
            )
        for url, failure_count in batch_stored.items():
            stored_statuses[url] = max(
                int(stored_statuses.get(url, DEAD_LINK_STATUS_ALIVE)),
                int(failure_count),
            )
    return remote_statuses, stored_statuses


def _merge_remote_dead_link_statuses(
    local_statuses: Dict[str, Dict[str, Any]],
    remote_result: Any,
) -> tuple:
    merged_statuses = {
        url: dict(state) for url, state in local_statuses.items()
    }
    if not isinstance(remote_result, tuple) or len(remote_result) != 2:
        return merged_statuses, {}

    remote_statuses, stored_statuses = remote_result
    if not isinstance(remote_statuses, dict):
        return merged_statuses, {}

    for url, failure_count in remote_statuses.items():
        if not isinstance(url, str) or not url.strip():
            continue
        failure_count = min(
            DEAD_LINK_STATUS_CONFIRMED,
            max(DEAD_LINK_STATUS_ALIVE, _integer_value(failure_count)),
        )
        if failure_count < DEAD_LINK_STATUS_RECHECKABLE:
            continue
        canonical_url = canonicalize_url(url.strip()) or url.strip()
        incoming = {
            "url": canonical_url,
            "failure_count": failure_count,
            "first_failure_at": 0,
            "last_failure_at": 0,
            "expires_at": -1,
            "reason": (
                "REMOTE_CONFIRMED"
                if failure_count >= DEAD_LINK_STATUS_CONFIRMED
                else "REMOTE_RECHECKABLE"
            ),
        }
        current = merged_statuses.get(canonical_url)
        merged_statuses[canonical_url] = (
            _merge_dead_link_records(current, incoming)
            if current
            else incoming
        )

    statuses_to_store = {}
    if isinstance(stored_statuses, dict):
        for url, failure_count in stored_statuses.items():
            if not isinstance(url, str) or not url.strip():
                continue
            failure_count = min(
                DEAD_LINK_STATUS_CONFIRMED,
                max(DEAD_LINK_STATUS_ALIVE, _integer_value(failure_count)),
            )
            if failure_count < DEAD_LINK_STATUS_RECHECKABLE:
                continue
            canonical_url = canonicalize_url(url.strip()) or url.strip()
            local_count = _integer_value(
                local_statuses.get(canonical_url, {}).get("failure_count")
            )
            if failure_count > local_count:
                statuses_to_store[canonical_url] = max(
                    statuses_to_store.get(
                        canonical_url,
                        DEAD_LINK_STATUS_ALIVE,
                    ),
                    failure_count,
                )

    return merged_statuses, statuses_to_store


async def _load_dead_link_statuses(
    canonical_urls: List[str],
    include_remote: bool,
) -> Dict[str, Dict[str, Any]]:
    if include_remote:
        local_result, remote_result = await asyncio.gather(
            _fetch_local_dead_link_statuses(canonical_urls),
            _fetch_remote_dead_link_statuses(canonical_urls),
            return_exceptions=True,
        )
    else:
        local_result = await _fetch_local_dead_link_statuses(canonical_urls)
        remote_result = ({}, {})

    local_statuses = local_result if isinstance(local_result, dict) else {}
    merged_statuses, statuses_to_store = _merge_remote_dead_link_statuses(
        local_statuses,
        remote_result,
    )
    if statuses_to_store:
        await _store_remote_dead_link_statuses(statuses_to_store)
    return merged_statuses


async def check_dead_links_batch(
    urls: List[str],
    include_remote: bool = True,
) -> Dict[str, bool]:
    original_to_canonical = {
        url: canonicalize_url(url) or url for url in urls if url
    }
    if not original_to_canonical:
        return {}

    try:
        canonical_urls = list(dict.fromkeys(
            original_to_canonical.values()
        ))
        statuses = await _load_dead_link_statuses(
            canonical_urls,
            include_remote,
        )
        return {
            original: True
            for original, canonical in original_to_canonical.items()
            if _integer_value(
                statuses.get(canonical, {}).get("failure_count")
            ) >= DEAD_LINK_STATUS_RECHECKABLE
        }
    except Exception as e:
        database_logger.error(
            f"Batch dead link check failed: {type(e).__name__}: {e}"
        )
        return {}


async def _store_remote_dead_link_statuses(
    statuses: Dict[str, int],
) -> None:
    try:
        normalized_statuses = {}
        for url, failure_count in statuses.items():
            if not isinstance(url, str) or not url.strip():
                continue
            failure_count = min(
                DEAD_LINK_STATUS_CONFIRMED,
                max(DEAD_LINK_STATUS_ALIVE, _integer_value(failure_count)),
            )
            if failure_count < DEAD_LINK_STATUS_RECHECKABLE:
                continue
            canonical_url = canonicalize_url(url.strip()) or url.strip()
            normalized_statuses[canonical_url] = max(
                normalized_statuses.get(
                    canonical_url,
                    DEAD_LINK_STATUS_ALIVE,
                ),
                failure_count,
            )

        if not normalized_statuses:
            return

        async def store_statuses() -> int:
            local_statuses = await _fetch_local_dead_link_statuses(
                list(normalized_statuses)
            )
            pending_statuses = [
                (url, failure_count)
                for url, failure_count in normalized_statuses.items()
                if failure_count > _integer_value(
                    local_statuses.get(url, {}).get("failure_count")
                )
            ]
            if not pending_statuses:
                return 0

            current_time = int(time.time())
            confirmed_expiry = _dead_link_expiry(
                settings.DEAD_LINK_TTL,
                current_time,
            )
            stored = 0
            for start in range(0, len(pending_statuses), _QUERY_BATCH_SIZE):
                batch = pending_statuses[start:start + _QUERY_BATCH_SIZE]

                async def write_batch() -> int:
                    written = 0
                    for url, failure_count in batch:
                        is_confirmed = (
                            failure_count >= DEAD_LINK_STATUS_CONFIRMED
                        )
                        changed = await _upsert_dead_link_state(
                            url=url,
                            failure_count=failure_count,
                            current_time=current_time,
                            expires_at=(
                                confirmed_expiry if is_confirmed else -1
                            ),
                            reason=(
                                "REMOTE_CONFIRMED"
                                if is_confirmed
                                else "REMOTE_RECHECKABLE"
                            ),
                        )
                        written += int(changed)
                    return written

                stored += await run_database_transaction(
                    write_batch,
                    "store remote dead-link states",
                )
            return stored

        if settings.DATABASE_TYPE == "sqlite":
            async with _remote_dead_link_store_lock:
                stored = await store_statuses()
        else:
            stored = await store_statuses()
        if stored:
            database_logger.debug(
                f"Stored {stored} remote dead-link states locally"
            )
    except Exception as error:
        database_logger.error(
            "Remote dead-link storage failed: "
            f"{type(error).__name__}: {error}"
        )


# ===========================
# Dead Link Marking
# ===========================
def _dead_link_expiry(ttl: int, current_time: int) -> int:
    return -1 if ttl == -1 else current_time + max(1, ttl)


async def _upsert_dead_link_state(
    url: str,
    failure_count: int,
    current_time: int,
    expires_at: int,
    reason: str,
) -> bool:
    row = await database.fetch_one(
        """INSERT INTO dead_links (
               url, failure_count, first_failure_at,
               last_failure_at, expires_at, reason
           ) VALUES (
               :url, :failure_count, :current_time,
               :current_time, :expires_at, :reason
           )
           ON CONFLICT (url) DO UPDATE SET
               failure_count = :failure_count,
               first_failure_at = CASE
                   WHEN dead_links.expires_at > 0
                        AND dead_links.expires_at <= :current_time
                   THEN :current_time
                   WHEN dead_links.first_failure_at > 0
                   THEN dead_links.first_failure_at
                   ELSE :current_time
               END,
               last_failure_at = :current_time,
               expires_at = :expires_at,
               reason = CASE
                   WHEN :reason != '' THEN :reason
                   ELSE dead_links.reason
               END
           WHERE (
               dead_links.expires_at > 0
               AND dead_links.expires_at <= :current_time
           ) OR dead_links.failure_count < :failure_count
           RETURNING url""",
        {
            "url": url,
            "failure_count": failure_count,
            "current_time": current_time,
            "expires_at": expires_at,
            "reason": (reason or "")[:200],
        },
    )
    return row is not None


async def _upsert_recheckable_dead_link(
    url: str,
    current_time: int,
    reason: str,
) -> Dict[str, Any]:
    await _upsert_dead_link_state(
        url=url,
        failure_count=DEAD_LINK_STATUS_RECHECKABLE,
        current_time=current_time,
        expires_at=-1,
        reason=reason,
    )
    row = await database.fetch_one(
        "SELECT url, failure_count, first_failure_at, "
        "last_failure_at, expires_at, reason FROM dead_links "
        "WHERE url = :url",
        {"url": url},
    )
    if row is None:
        raise RuntimeError("Dead-link state was not persisted")
    return dict(row)


async def _upsert_confirmed_dead_link(
    url: str,
    current_time: int,
    expires_at: int,
    reason: str,
) -> bool:
    return await _upsert_dead_link_state(
        url=url,
        failure_count=DEAD_LINK_STATUS_CONFIRMED,
        current_time=current_time,
        expires_at=expires_at,
        reason=reason,
    )


async def mark_dead_links(
    urls: List[str],
    ttl: int,
    reason: str = "",
) -> int:
    normalized_urls = list(dict.fromkeys(
        canonicalize_url(url.strip()) or url.strip()
        for url in urls
        if isinstance(url, str) and url.strip()
    ))
    if not normalized_urls:
        return 0

    current_time = int(time.time())
    expires_at = _dead_link_expiry(ttl, current_time)

    written = 0
    for start in range(0, len(normalized_urls), _QUERY_BATCH_SIZE):
        batch = normalized_urls[start:start + _QUERY_BATCH_SIZE]

        async def write_batch() -> int:
            batch_written = 0
            for url in batch:
                changed = await _upsert_confirmed_dead_link(
                    url,
                    current_time,
                    expires_at,
                    reason,
                )
                batch_written += int(changed)
            return batch_written

        written += await run_database_transaction(
            write_batch,
            "mark dead links",
        )
    return written


async def mark_dead_link(url: str, ttl: int, reason: str = "") -> None:
    try:
        await mark_dead_links([url], ttl, reason)
    except Exception as error:
        database_logger.error(
            f"Mark dead link failed: {type(error).__name__}: {error}"
        )


async def get_dead_link_statuses_batch(
    urls: List[str],
    include_remote: bool = True,
) -> Dict[str, Dict[str, Any]]:
    original_to_canonical = {
        url: canonicalize_url(url) or url for url in urls if url
    }
    if not original_to_canonical:
        return {}

    canonical_urls = list(dict.fromkeys(
        original_to_canonical.values()
    ))
    try:
        status_rows = await _load_dead_link_statuses(
            canonical_urls,
            include_remote,
        )
    except Exception as error:
        database_logger.error(
            f"Dead link state check failed: {type(error).__name__}: {error}"
        )
        status_rows = {}

    results = {}
    for original, canonical in original_to_canonical.items():
        state = status_rows.get(canonical)
        results[original] = state or {
            "url": canonical,
            "failure_count": DEAD_LINK_STATUS_ALIVE,
            "first_failure_at": 0,
            "last_failure_at": 0,
            "expires_at": 0,
            "reason": "",
        }

    return results


async def get_dead_link_status(url: str) -> Dict[str, Any]:
    statuses = await get_dead_link_statuses_batch(
        [url],
        include_remote=False,
    )
    return statuses.get(
        url,
        {
            "url": canonicalize_url(url) or url,
            "failure_count": DEAD_LINK_STATUS_ALIVE,
            "first_failure_at": 0,
            "last_failure_at": 0,
            "expires_at": 0,
            "reason": "",
        },
    )


async def record_dead_link_failure(
    url: str,
    reason: str = "",
) -> Dict[str, Any]:
    url = canonicalize_url(url) or url
    current_time = int(time.time())

    async def write_state() -> Dict[str, Any]:
        return await _upsert_recheckable_dead_link(
            url,
            current_time,
            reason,
        )

    return await run_database_transaction(
        write_state,
        "record dead-link failure",
    )


async def clear_recheckable_dead_link_state(
    url: str,
    first_failure_at: int,
) -> None:
    url = canonicalize_url(url) or url

    async def clear_state() -> None:
        await database.execute(
            "DELETE FROM dead_links "
            "WHERE url = :url "
            "AND failure_count = :recheckable_status "
            "AND first_failure_at = :first_failure_at",
            {
                "url": url,
                "recheckable_status": DEAD_LINK_STATUS_RECHECKABLE,
                "first_failure_at": first_failure_at,
            },
        )

    await run_database_transaction(
        clear_state,
        "clear recheckable dead-link state",
    )


async def confirm_recheckable_dead_link_state(
    url: str,
    first_failure_at: int,
    reason: str = "",
) -> Optional[Dict[str, Any]]:
    url = canonicalize_url(url) or url
    current_time = int(time.time())
    confirmed_expiry = _dead_link_expiry(
        settings.DEAD_LINK_TTL,
        current_time,
    )

    async def confirm_state() -> Optional[Dict[str, Any]]:
        params = {
            "url": url,
            "recheckable_status": DEAD_LINK_STATUS_RECHECKABLE,
            "confirmed_status": DEAD_LINK_STATUS_CONFIRMED,
            "first_failure_at": first_failure_at,
            "current_time": current_time,
            "confirmed_expiry": confirmed_expiry,
            "reason": (reason or "")[:200],
        }
        await database.execute(
            "UPDATE dead_links SET "
            "failure_count = :confirmed_status, "
            "last_failure_at = :current_time, "
            "expires_at = :confirmed_expiry, "
            "reason = :reason "
            "WHERE url = :url "
            "AND failure_count = :recheckable_status "
            "AND first_failure_at = :first_failure_at",
            params,
        )
        row = await database.fetch_one(
            "SELECT url, failure_count, first_failure_at, "
            "last_failure_at, expires_at, reason "
            "FROM dead_links "
            "WHERE url = :url "
            "AND failure_count = :confirmed_status "
            "AND first_failure_at = :first_failure_at",
            {
                "url": url,
                "confirmed_status": DEAD_LINK_STATUS_CONFIRMED,
                "first_failure_at": first_failure_at,
            },
        )
        if not row:
            return None
        return dict(row)

    return await run_database_transaction(
        confirm_state,
        "confirm recheckable dead-link state",
    )


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

        return bool(
            existing_lock
            and existing_lock["instance_id"] == instance_id
        )

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
                 timeout: Optional[int] = None, retry_interval: float = 1.0,
                 wait: bool = True):
        lock_key = build_cache_key(content_type, title, year)
        self.lock_key = lock_key
        self.instance_id = f"{uuid.uuid4()}_{os.getpid()}"
        self.duration = settings.SCRAPE_LOCK_TTL
        self.timeout = timeout if timeout is not None else settings.SCRAPE_WAIT_TIMEOUT
        self.retry_interval = retry_interval
        self.wait = wait
        self.acquired = False

    async def __aenter__(self):
        start_time = time.time()
        attempt = 0

        if not self.wait:
            self.acquired = await acquire_lock(
                self.lock_key,
                self.instance_id,
                self.duration,
            )
            if self.acquired:
                database_logger.debug(f"Lock acquired: {self.lock_key[:30]}... (no wait)")
            else:
                database_logger.debug(f"Lock busy: {self.lock_key[:30]}... (no wait)")
            return self

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
