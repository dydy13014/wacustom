import asyncio
import json
import time
import typing
from typing import Any, Dict, List

from wastream.config.settings import settings, Settings
from wastream.utils.crypto import encrypt_secret, decrypt_secret
from wastream.utils.database import database
from wastream.utils.http_client import http_client
from wastream.utils.logger import setup_logger, database_logger


# ===========================
# Editable settings registry
# ===========================
# Startup/security-critical keys: never editable at runtime.
EXCLUDED_SETTINGS = frozenset({
    "SECRET_KEY",
    "DATABASE_TYPE", "DATABASE_PATH", "DATABASE_URL", "DATABASE_VERSION",
    "DATABASE_BUSY_TIMEOUT_SECONDS",
    "PORT",
    "ADDON_ID",
    "ADMIN_PASSWORD", "ADDON_PASSWORD",
})

# Shown masked, write-only from the UI (PROXY_URL can embed user:pass credentials).
SENSITIVE_SETTINGS = frozenset({
    "DARKI_API_KEY", "TMDB_API_KEY", "PROXY_URL",
    # Clés des trackers propres au fork : même traitement que les
    # autres secrets (affichées masquées, écriture seule, chiffrées).
    "YGGREBORN_API_KEY", "TR4KER_API_KEY", "TORR9_API_KEY", "C411_API_KEY",
    "GEMINI_API_KEY", "GENERATIONFREE_API_KEY", "V3X_API_KEY", "LUMIO_MANIFEST_ID",
})

# Excluded from export/import: raw HTML -> importing from an untrusted file would be stored XSS.
UNSHAREABLE_SETTINGS = frozenset({"CUSTOM_HTML"})

MULTILINE_SETTINGS = frozenset({"CUSTOM_HTML"})

ADVANCED_SETTINGS = frozenset({
    "DATABASE_RETRY_MAX_ATTEMPTS", "DATABASE_RETRY_DELAY_SECONDS",
    "DARKIBOX_LINK_TIMEOUT",
    "ZONE_TELECHARGEMENT_MAX_CONCURRENCY",
    "SCRAPE_LOCK_TTL", "SCRAPE_WAIT_TIMEOUT",
    "HTTP_TIMEOUT", "METADATA_TIMEOUT", "HEALTH_CHECK_TIMEOUT",
    "STREAM_REQUEST_TIMEOUT", "DEBRID_MAX_RETRIES", "DEBRID_RETRY_DELAY_SECONDS", "DEBRID_CACHE_CHECK_HTTP_TIMEOUT",
    "DEBRID_HTTP_ERROR_MAX_RETRIES", "DEBRID_HTTP_ERROR_RETRY_DELAY",
    "HTTP_CACHE_STREAMS_TTL", "HTTP_CACHE_MANIFEST_TTL", "HTTP_CACHE_CONFIGURE_TTL", "HTTP_CACHE_STALE_WHILE_REVALIDATE",
    "HEALTH_CHECK_INTERVAL", "PASTEBIN_SCRAPER_INTERVAL",
    "PASTEBIN_SCRAPER_MAX_DEPTH", "PASTEBIN_SCRAPER_MAX_PAGES",
    "IDRIX_SCRAPER_INTERVAL", "IDRIX_SCRAPER_MAX_DEPTH", "IDRIX_SCRAPER_MAX_PAGES",
    "IDRIX_SCRAPER_RETRY_MAX_ATTEMPTS", "IDRIX_SCRAPER_RETRY_DELAY_SECONDS",
    "IDRIX_SCRAPER_REQUEST_DELAY_SECONDS",
    "IDRIX_SCRAPER_MAX_RETRY_DELAY_SECONDS",
    "DOMAIN_SYNC_INTERVAL", "DOMAIN_SYNC_HEALTH_ERROR_RECHECK_DELAY_SECONDS",
    "LUMIO_RATE_LIMIT_PAUSE",
    "PROXY_URL",
})

HOOKS = {
    "PROXY_URL": "http_client",
    "HTTP_TIMEOUT": "http_client",
    "LOG_LEVEL": "logger",
}

CHOICES = {
    "CONTENT_CACHE_MODE": ["background", "live"],
    "LOG_LEVEL": ["DEBUG", "INFO", "WARNING", "ERROR"],
}

NEXT_CYCLE_SETTINGS = frozenset({
    "PASTEBIN_SCRAPER_URLS", "PASTEBIN_SCRAPER_INTERVAL", "HEALTH_CHECK_INTERVAL", "CLEANUP_INTERVAL",
    "PASTEBIN_SCRAPER_MAX_DEPTH", "PASTEBIN_SCRAPER_MAX_PAGES",
    "IDRIX_SCRAPER_URLS", "IDRIX_SCRAPER_INTERVAL", "IDRIX_SCRAPER_MAX_DEPTH", "IDRIX_SCRAPER_MAX_PAGES",
    "IDRIX_SCRAPER_RETRY_MAX_ATTEMPTS", "IDRIX_SCRAPER_RETRY_DELAY_SECONDS",
    "IDRIX_SCRAPER_REQUEST_DELAY_SECONDS",
    "IDRIX_SCRAPER_MAX_RETRY_DELAY_SECONDS",
    "DOMAIN_SYNC_ENABLED", "DOMAIN_SYNC_INTERVAL", "DOMAIN_SYNC_RECHECK_ON_HEALTH_ERROR",
    "DOMAIN_SYNC_HEALTH_ERROR_RECHECK_DELAY_SECONDS", "DOMAIN_SYNC_ZONE_TELECHARGEMENT_TELEGRAM_URL",
    "DOMAIN_SYNC_WAWACITY_TELEGRAM_URL", "DOMAIN_SYNC_MOVIX_TELEGRAM_URL",
    "DOMAIN_SYNC_FREE_TELECHARGER_TELEGRAM_URL",
})

SETTINGS_FORMAT_VERSION = 1

EDITORS = {
    "DARKIMOVIX_KITSU_TMDB_MAPPING": "imdb-list",
    "KITSU_IMDB_OVERRIDE": "kitsu-override",
    "PASTEBIN_SCRAPER_URLS": "url-list",
    "IDRIX_SCRAPER_URLS": "url-list",
}

# Opt-in suggested values (not forced as defaults).
RECOMMENDED_SETTINGS = {
    "DARKIMOVIX_KITSU_TMDB_MAPPING": ["tt0388629"],
    "KITSU_IMDB_OVERRIDE": ["6589=1,8174=2,13893=3,42213=4-1,42927=4-2:tt2250192"],
}

DESCRIPTIONS = {
    "ADDON_NAME": "Display name shown in Stremio.",
    "WAWACITY_URL": "Wawacity base URL.",
    "FREE_TELECHARGER_URL": "Free-Telecharger base URL.",
    "DARKI_API_URL": "Darki-API base URL.",
    "DARKI_API_KEY": "Optional API key for Darki-API.",
    "MOVIX_URL": "Movix base URL (the API URL is auto-generated as api.{domain}).",
    "WEBSHARE_URL": "Webshare base URL.",
    "ZONE_TELECHARGEMENT_URL": "Zone-Telechargement base URL.",
    "DATABASE_RETRY_MAX_ATTEMPTS": "Max attempts including the first try for transient database lock or serialization errors (default: 8).",
    "DATABASE_RETRY_DELAY_SECONDS": "Initial delay between transient database retries in seconds (default: 0.25).",
    "DARKIMOVIX_KITSU_TMDB_MAPPING": "IMDB IDs whose Kitsu anime should use TMDB mapping on Darki-API / Movix.",
    "KITSU_IMDB_OVERRIDE": "Map Kitsu IDs to an IMDB season. Add a part only when Kitsu splits that IMDB season into several cours — episode numbering then continues across parts.",
    "WAWACITY_MAX_SEARCH_PAGES": "Max search result pages (default: 3).",
    "FREE_TELECHARGER_MAX_SEARCH_PAGES": "Max search result pages for Free-Telecharger (default: 3).",
    "WEBSHARE_MAX_SEARCH_PAGES": "Max search result pages for Webshare (default: 3).",
    "ZONE_TELECHARGEMENT_MAX_SEARCH_PAGES": "Max search result pages for Zone-Telechargement (default: 3).",
    "ZONE_TELECHARGEMENT_MAX_CONCURRENCY": "Max simultaneous Zoneurs link resolutions for Zone-Telechargement (default: 16).",
    "DARKI_API_MAX_LINK_PAGES": "Max link pages to fetch (default: 5).",
    "DARKIBOX_LINK_TIMEOUT": "Soft timeout in seconds for Darkibox links (default: 2).",
    "CONTENT_CACHE_TTL": "Content cache duration: -1 = permanent, or seconds (default: 3600 = 1 hour).",
    "CONTENT_CACHE_MODE": "background = wait if expired; live = serve instantly and refresh in background.",
    "DEAD_LINK_TTL": "Dead-link cache duration: -1 = permanent, or seconds (e.g. 2592000 = 30 days).",
    "SCRAPE_LOCK_TTL": "Distributed scrape-lock expiration in seconds (default: 300 = 5 min).",
    "SCRAPE_WAIT_TIMEOUT": "Max time a request waits for another worker's scrape lock (default: 30).",
    "HTTP_TIMEOUT": "General HTTP request timeout in seconds (default: 15).",
    "METADATA_TIMEOUT": "TMDB / Kitsu API timeout in seconds (default: 10).",
    "HEALTH_CHECK_TIMEOUT": "Health endpoint timeout in seconds (default: 5).",
    "DEBRID_MAX_RETRIES": "Max retry attempts for debrid operations (default: 5).",
    "DEBRID_RETRY_DELAY_SECONDS": "Delay between retries in seconds (default: 4).",
    "STREAM_REQUEST_TIMEOUT": "Timeout for stream requests in seconds (default: 20).",
    "DEBRID_CACHE_CHECK_HTTP_TIMEOUT": "Cache-check HTTP timeout in seconds (default: 3).",
    "DEBRID_HTTP_ERROR_MAX_RETRIES": "Max retries for HTTP errors 429/500/502/503/504 (default: 5).",
    "DEBRID_HTTP_ERROR_RETRY_DELAY": "Delay for HTTP-error retries in seconds (default: 1).",
    "PROXY_URL": "Optional HTTP proxy URL, empty if unused.",
    "LOG_LEVEL": "DEBUG = everything, INFO = workflow + warnings + errors, WARNING = warnings + errors, ERROR = errors only.",
    "HTTP_CACHE_ENABLED": "Enable HTTP response caching (adds ETag + Cache-Control headers). Default: off.",
    "HTTP_CACHE_STREAMS_TTL": "Stream-results cache in seconds (default: 300 = 5 min).",
    "HTTP_CACHE_MANIFEST_TTL": "Manifest cache in seconds (default: 86400 = 1 day).",
    "HTTP_CACHE_CONFIGURE_TTL": "Configure-page cache in seconds (default: 86400 = 1 day).",
    "HTTP_CACHE_STALE_WHILE_REVALIDATE": "Stale-while-revalidate window in seconds (default: 60).",
    "HEALTH_CHECK_INTERVAL": "Health-check interval in seconds (default: 60).",
    "TMDB_API_KEY": "TMDB API key used to auto-fill metadata in WASource.",
    "PASTEBIN_SCRAPER_URLS": "Pastebin URLs scraped into WASource (AllDebrid links only).",
    "PASTEBIN_SCRAPER_INTERVAL": "Scrape interval in seconds (default: 86400 = 24h).",
    "PASTEBIN_SCRAPER_MAX_DEPTH": "Max recursion depth when a page has only codes and its links are followed to reach content pages (default: 5).",
    "PASTEBIN_SCRAPER_MAX_PAGES": "Max pages fetched per starting URL when following index pages (default: 1000, logged if reached).",
    "IDRIX_SCRAPER_URLS": "Rentry/Textup catalog URLs or Idrix PrivateBin URLs (keep the #key fragment for PrivateBin).",
    "IDRIX_SCRAPER_INTERVAL": "Scrape interval in seconds (default: 86400 = 24h).",
    "IDRIX_SCRAPER_MAX_DEPTH": "Max recursion depth when an Idrix page links to other catalog pages (default: 5).",
    "IDRIX_SCRAPER_MAX_PAGES": "Max pages fetched per starting URL when following Idrix catalog pages (default: 1000, logged if reached).",
    "IDRIX_SCRAPER_RETRY_MAX_ATTEMPTS": "Max attempts for transient Idrix page errors such as HTTP 429/5xx (default: 3).",
    "IDRIX_SCRAPER_RETRY_DELAY_SECONDS": "Base delay between Idrix retries in seconds; Retry-After is respected when supplied (default: 2).",
    "IDRIX_SCRAPER_REQUEST_DELAY_SECONDS": (
        "Minimum delay between Idrix requests to the same host in seconds "
        "(default: 0.15)."
    ),
    "IDRIX_SCRAPER_MAX_RETRY_DELAY_SECONDS": (
        "Maximum delay for one Idrix retry; a longer Retry-After blocks "
        "that host for the current run (default: 30)."
    ),
    "DOMAIN_SYNC_ENABLED": "Enable automatic source-domain updates from the configured Telegram channels (default: false).",
    "DOMAIN_SYNC_INTERVAL": "Full Telegram domain synchronization interval in seconds (default: 7200 = 2h).",
    "DOMAIN_SYNC_RECHECK_ON_HEALTH_ERROR": "Request a source-specific Telegram recheck when a configured source is offline (default: true).",
    "DOMAIN_SYNC_HEALTH_ERROR_RECHECK_DELAY_SECONDS": "Minimum delay between health-triggered domain rechecks for one source (default: 900 = 15 min).",
    "DOMAIN_SYNC_WAWACITY_TELEGRAM_URL": "Public Telegram channel URL used to discover Wawacity domains.",
    "DOMAIN_SYNC_FREE_TELECHARGER_TELEGRAM_URL": "Public Telegram channel URL used to discover Free-Telecharger domains.",
    "DOMAIN_SYNC_MOVIX_TELEGRAM_URL": "Public Telegram channel URL used to discover Movix domains.",
    "DOMAIN_SYNC_ZONE_TELECHARGEMENT_TELEGRAM_URL": "Public Telegram channel URL used to discover Zone-Telechargement domains.",
    "CUSTOM_HTML": "Custom HTML shown on the web interface.",
}

# UI layout AND editable-keys allowlist. Order = display order.
SETTINGS_LAYOUT = [
    ("Addon", ["ADDON_NAME"]),
    ("Sources", ["WAWACITY_URL", "FREE_TELECHARGER_URL", "DARKI_API_URL", "DARKI_API_KEY", "MOVIX_URL", "WEBSHARE_URL", "ZONE_TELECHARGEMENT_URL"]),
    ("TMDB", ["TMDB_API_KEY"]),
    # Sources propres au fork, absentes d'upstream : configurables ici plutôt
    # que par le seul fichier .env.
    ("Trackers Torznab", ["YGGREBORN_URL", "YGGREBORN_API_KEY",
                          "TR4KER_URL", "TR4KER_API_KEY",
                          "TORR9_URL", "TORR9_API_KEY",
                          "C411_URL", "C411_API_KEY",
                          "V3X_URL", "V3X_API_KEY"]),
    ("Trackers UNIT3D", ["GEMINI_URL", "GEMINI_API_KEY",
                         "GENERATIONFREE_URL", "GENERATIONFREE_API_KEY"]),
    ("Zilean / Nyaa", ["ZILEAN_URL", "NYAA_URL"]),
    ("Lumio", ["LUMIO_MANIFEST_ID", "LUMIO_RATE_LIMIT_PAUSE"]),
    ("Database resilience", ["DATABASE_RETRY_MAX_ATTEMPTS", "DATABASE_RETRY_DELAY_SECONDS"]),
    ("Kitsu / Anime", ["DARKIMOVIX_KITSU_TMDB_MAPPING", "KITSU_IMDB_OVERRIDE"]),
    ("Pagination", ["WAWACITY_MAX_SEARCH_PAGES", "FREE_TELECHARGER_MAX_SEARCH_PAGES", "WEBSHARE_MAX_SEARCH_PAGES",
                    "ZONE_TELECHARGEMENT_MAX_SEARCH_PAGES", "DARKI_API_MAX_LINK_PAGES", "DARKIBOX_LINK_TIMEOUT"]),
    ("Scraper concurrency", ["ZONE_TELECHARGEMENT_MAX_CONCURRENCY"]),
    ("Pastebin", ["PASTEBIN_SCRAPER_URLS", "PASTEBIN_SCRAPER_INTERVAL",
                  "PASTEBIN_SCRAPER_MAX_DEPTH", "PASTEBIN_SCRAPER_MAX_PAGES"]),
    ("Idrix", ["IDRIX_SCRAPER_URLS", "IDRIX_SCRAPER_INTERVAL",
               "IDRIX_SCRAPER_MAX_DEPTH", "IDRIX_SCRAPER_MAX_PAGES",
               "IDRIX_SCRAPER_RETRY_MAX_ATTEMPTS",
               "IDRIX_SCRAPER_RETRY_DELAY_SECONDS",
               "IDRIX_SCRAPER_REQUEST_DELAY_SECONDS",
               "IDRIX_SCRAPER_MAX_RETRY_DELAY_SECONDS"]),
    ("Domain synchronization", [
        "DOMAIN_SYNC_ENABLED", "DOMAIN_SYNC_INTERVAL",
        "DOMAIN_SYNC_RECHECK_ON_HEALTH_ERROR",
        "DOMAIN_SYNC_HEALTH_ERROR_RECHECK_DELAY_SECONDS",
        "DOMAIN_SYNC_WAWACITY_TELEGRAM_URL", "DOMAIN_SYNC_FREE_TELECHARGER_TELEGRAM_URL",
        "DOMAIN_SYNC_MOVIX_TELEGRAM_URL", "DOMAIN_SYNC_ZONE_TELECHARGEMENT_TELEGRAM_URL",
    ]),
    ("Cache", ["CONTENT_CACHE_TTL", "CONTENT_CACHE_MODE", "DEAD_LINK_TTL"]),
    ("Debrid", ["STREAM_REQUEST_TIMEOUT", "DEBRID_MAX_RETRIES", "DEBRID_RETRY_DELAY_SECONDS",
                "DEBRID_CACHE_CHECK_HTTP_TIMEOUT", "DEBRID_HTTP_ERROR_MAX_RETRIES", "DEBRID_HTTP_ERROR_RETRY_DELAY"]),
    ("Locks", ["SCRAPE_LOCK_TTL", "SCRAPE_WAIT_TIMEOUT"]),
    ("HTTP timeouts", ["HTTP_TIMEOUT", "METADATA_TIMEOUT", "HEALTH_CHECK_TIMEOUT"]),
    ("HTTP cache", ["HTTP_CACHE_ENABLED", "HTTP_CACHE_STREAMS_TTL", "HTTP_CACHE_MANIFEST_TTL",
                    "HTTP_CACHE_CONFIGURE_TTL", "HTTP_CACHE_STALE_WHILE_REVALIDATE"]),
    ("Proxy", ["PROXY_URL"]),
    ("Health check", ["HEALTH_CHECK_INTERVAL"]),
    ("Logging", ["LOG_LEVEL"]),
    ("Interface", ["CUSTOM_HTML"]),
]

EDITABLE_KEYS = frozenset(k for _, keys in SETTINGS_LAYOUT for k in keys)

# Env-provided fields, snapshotted at import (before any setattr mutates model_fields_set). Env always wins.
_ENV_LOCKED = frozenset(settings.model_fields_set)
LEGACY_SETTING_ALIASES = {
    "DOMAIN_SYNC_ZT_TELEGRAM_URL": "DOMAIN_SYNC_ZONE_TELECHARGEMENT_TELEGRAM_URL",
}

_settings_write_lock = asyncio.Lock()


def get_registry_issues() -> List[str]:
    fields = set(Settings.model_fields.keys())
    issues = []
    unknown = EDITABLE_KEYS - fields
    if unknown:
        issues.append(f"unknown keys in layout: {sorted(unknown)}")
    leaked = EDITABLE_KEYS & EXCLUDED_SETTINGS
    if leaked:
        issues.append(f"excluded keys exposed: {sorted(leaked)}")
    bad_reco = set(RECOMMENDED_SETTINGS) - EDITABLE_KEYS
    if bad_reco:
        issues.append(f"recommended keys not editable: {sorted(bad_reco)}")
    return issues


# ===========================
# Field introspection
# ===========================
def _field_kind(key: str) -> str:
    ann = Settings.model_fields[key].annotation
    if ann is bool:
        return "bool"
    if ann is int:
        return "int"
    origin = typing.get_origin(ann)
    if origin in (list, List):
        return "list"
    if origin is typing.Union:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        if args and args[0] is bool:
            return "bool"
        if args and args[0] is int:
            return "int"
    return "str"


def _default_value(key: str) -> Any:
    return Settings.model_fields[key].default


def _is_env_locked(key: str) -> bool:
    return key in _ENV_LOCKED


def _effect(key: str) -> str:
    return "next_cycle" if key in NEXT_CYCLE_SETTINGS else "immediate"


# ===========================
# Persistence
# ===========================
# Marks an AES-encrypted (SECRET_KEY) value at rest in the DB.
_ENC_MARKER = "__enc__"


def _encode_override(key: str, value: Any) -> str:
    payload = json.dumps(value)
    if key in SENSITIVE_SETTINGS:
        return json.dumps({_ENC_MARKER: encrypt_secret(payload)})
    return payload


def _decode_override(raw: str) -> Any:
    parsed = json.loads(raw)
    if isinstance(parsed, dict) and _ENC_MARKER in parsed:
        plain = decrypt_secret(parsed[_ENC_MARKER])
        if plain is None:
            raise ValueError("decrypt failed")
        return json.loads(plain)
    return parsed


async def _load_overrides() -> Dict[str, Any]:
    rows = await database.fetch_all("SELECT setting_key, setting_value FROM settings_overrides")
    out: Dict[str, Any] = {}
    for r in rows:
        raw_key = r["setting_key"]
        key = LEGACY_SETTING_ALIASES.get(raw_key, raw_key)
        try:
            value = _decode_override(r["setting_value"])
            if key not in out or raw_key == key:
                out[key] = value
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return out


async def _upsert_override(key: str, value_json: str):
    if settings.DATABASE_TYPE == "sqlite":
        query = "INSERT OR REPLACE INTO settings_overrides (setting_key, setting_value) VALUES (:k, :v)"
    else:
        query = """INSERT INTO settings_overrides (setting_key, setting_value) VALUES (:k, :v)
                   ON CONFLICT (setting_key) DO UPDATE SET setting_value = :v"""
    await database.execute(query, {"k": key, "v": value_json})


# ===========================
# Hooks (singleton re-apply)
# ===========================
async def _run_hook(key: str):
    hook = HOOKS.get(key)
    if hook == "http_client":
        await http_client.reload()
    elif hook == "logger":
        setup_logger(settings.LOG_LEVEL)


# ===========================
# Public API
# ===========================
async def get_settings_view() -> List[Dict[str, Any]]:
    overrides = await _load_overrides()
    view: List[Dict[str, Any]] = []
    for category, keys in SETTINGS_LAYOUT:
        fields = []
        for key in keys:
            env_locked = _is_env_locked(key)
            sensitive = key in SENSITIVE_SETTINGS
            current = getattr(settings, key)
            source = "env" if env_locked else ("override" if key in overrides else "default")
            fields.append({
                "key": key,
                "kind": _field_kind(key),
                "value": None if sensitive else current,
                "is_set": bool(current) if sensitive else None,
                "sensitive": sensitive,
                "multiline": key in MULTILINE_SETTINGS,
                "advanced": key in ADVANCED_SETTINGS,
                "editor": EDITORS.get(key),
                "description": DESCRIPTIONS.get(key),
                "source": source,
                "editable": not env_locked,
                "choices": CHOICES.get(key),
                "effect": _effect(key),
                "default": None if sensitive else _default_value(key),
                "recommended": None if sensitive else RECOMMENDED_SETTINGS.get(key),
            })
        view.append({"category": category, "fields": fields})
    return view


async def set_override(key: str, value: Any) -> str:
    if key not in EDITABLE_KEYS:
        return "not_editable"
    if _is_env_locked(key):
        return "env_locked"
    if key in SENSITIVE_SETTINGS and (value is None or value == ""):
        return "skipped"

    async with _settings_write_lock:
        old = getattr(settings, key)
        try:
            setattr(settings, key, value)
        except Exception:
            return "invalid_value"

        if key in CHOICES and getattr(settings, key) not in CHOICES[key]:
            setattr(settings, key, old)
            return "invalid_value"

        try:
            await _upsert_override(key, _encode_override(key, getattr(settings, key)))
        except Exception as e:
            setattr(settings, key, old)
            database_logger.error(f"[Settings] Persist failed for {key}: {type(e).__name__}: {e}")
            return "error"

        await _run_hook(key)
        return "ok"


async def reset_override(key: str) -> str:
    if key not in EDITABLE_KEYS:
        return "not_editable"
    if _is_env_locked(key):
        return "env_locked"

    async with _settings_write_lock:
        await database.execute("DELETE FROM settings_overrides WHERE setting_key = :k", {"k": key})
        try:
            setattr(settings, key, _default_value(key))
        except Exception:
            pass
        await _run_hook(key)
        return "ok"


# ===========================
# Export / Import (share setup)
# ===========================
async def export_settings() -> Dict[str, Any]:
    overrides = await _load_overrides()
    exported = {k: v for k, v in overrides.items() if k in EDITABLE_KEYS and k not in SENSITIVE_SETTINGS and k not in UNSHAREABLE_SETTINGS}
    return {
        "format_version": SETTINGS_FORMAT_VERSION,
        "exported_at": int(time.time()),
        "db_version": settings.DATABASE_VERSION,
        "settings": exported,
    }


async def import_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("format_version") != SETTINGS_FORMAT_VERSION:
        return {"error": "invalid_format"}
    incoming = payload.get("settings")
    if not isinstance(incoming, dict):
        return {"error": "invalid_format"}

    imported = 0
    skipped = 0
    for key, value in incoming.items():
        if key in SENSITIVE_SETTINGS or key in UNSHAREABLE_SETTINGS:
            skipped += 1
            continue
        if await set_override(key, value) == "ok":
            imported += 1
        else:
            skipped += 1

    database_logger.debug(f"[Settings] Import: {imported} applied, {skipped} skipped")
    return {"success": True, "imported": imported, "skipped": skipped}


async def _purge_orphan_overrides(keys: List[str]):
    try:
        for key in keys:
            await database.execute("DELETE FROM settings_overrides WHERE setting_key = :k", {"k": key})
        database_logger.info(f"[Settings] Purged {len(keys)} orphan override(s): {', '.join(sorted(keys))}")
    except Exception as e:
        database_logger.error(f"[Settings] Orphan purge failed: {type(e).__name__}: {e}")


async def apply_startup_overrides():
    issues = get_registry_issues()
    if issues:
        database_logger.error(f"[Settings] Registry misconfiguration: {issues}")
    overrides = await _load_overrides()

    # A row whose setting no longer exists as a field can never be applied: purge it.
    # Keys still declared in Settings but absent from the layout keep their row.
    orphans = [key for key in overrides if key not in Settings.model_fields]
    if orphans:
        await _purge_orphan_overrides(orphans)
        for key in orphans:
            overrides.pop(key, None)

    hooks_needed = set()
    applied = 0
    for key, value in overrides.items():
        if key not in EDITABLE_KEYS or _is_env_locked(key):
            continue
        try:
            setattr(settings, key, value)
        except Exception as e:
            database_logger.error(f"[Settings] Skip invalid override {key}: {type(e).__name__}")
            continue
        if key in CHOICES and getattr(settings, key) not in CHOICES[key]:
            setattr(settings, key, _default_value(key))
            continue
        applied += 1
        if key in HOOKS:
            hooks_needed.add(HOOKS[key])

    if "http_client" in hooks_needed:
        await http_client.reload()
    if "logger" in hooks_needed:
        setup_logger(settings.LOG_LEVEL)
    if applied:
        database_logger.info(f"[Settings] Applied {applied} override(s) from DB")
