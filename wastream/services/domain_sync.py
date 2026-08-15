import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

from wastream.config.settings import settings
from wastream.services.settings_manager import set_override
from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger


# ===========================
# Domain Synchronization Sources
# ===========================
URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
DOMAIN_RE = re.compile(r"(?:www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/[^\s<>'\"]*)?", re.IGNORECASE)
NEW_ADDRESS_RE = re.compile(r"\b(?:nouvelle|new)\s+adresse\b", re.IGNORECASE)
BLOCKED_PAGE_RE = re.compile(
    r"(?:just\s+a\s+moment|verify\s+you\s+are\s+human|cf[-_ ]?challenge|access\s+denied)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DomainSource:
    key: str
    name: str
    source_setting: str
    channel_setting: str
    domain_fragment: str
    extractor: Callable[[str], Optional[str]]
    uses_messages: bool = False


DOMAIN_SOURCES = (
    DomainSource(
        key="wawacity",
        name="Wawacity",
        source_setting="WAWACITY_URL",
        channel_setting="DOMAIN_SYNC_WAWACITY_TELEGRAM_URL",
        domain_fragment="wawacity",
        extractor=lambda html: _extract_meta_domain(html, "og:title", "wawacity"),
    ),
    DomainSource(
        key="free_telecharger",
        name="Free-Telecharger",
        source_setting="FREE_TELECHARGER_URL",
        channel_setting="DOMAIN_SYNC_FREE_TELECHARGER_TELEGRAM_URL",
        domain_fragment="free-telecharger",
        extractor=lambda html: _extract_latest_message_domain(html, "free-telecharger"),
        uses_messages=True,
    ),
    DomainSource(
        key="movix",
        name="Movix",
        source_setting="MOVIX_URL",
        channel_setting="DOMAIN_SYNC_MOVIX_TELEGRAM_URL",
        domain_fragment="movix",
        extractor=lambda html: _extract_latest_message_domain(html, "movix"),
        uses_messages=True,
    ),
    DomainSource(
        key="zone_telechargement",
        name="Zone-Telechargement",
        source_setting="ZONE_TELECHARGEMENT_URL",
        channel_setting="DOMAIN_SYNC_ZONE_TELECHARGEMENT_TELEGRAM_URL",
        domain_fragment="zone-telechargement",
        extractor=lambda html: _extract_meta_domain(html, "og:description", "zone-telechargement"),
    ),
)

SOURCES_BY_NAME = {source.name: source for source in DOMAIN_SOURCES}


@dataclass
class DomainSyncState:
    running: bool = False
    stop_requested: bool = False
    last_run: Optional[float] = None
    last_full_sync: Optional[float] = None
    last_results: Dict[str, Dict[str, object]] = field(default_factory=dict)
    last_recheck_at: Dict[str, float] = field(default_factory=dict)


_sync_state = DomainSyncState()
_pending_rechecks: Set[str] = set()
_recheck_event = asyncio.Event()
_manual_sync_task: Optional[asyncio.Task] = None
_sync_lock = asyncio.Lock()


# ===========================
# Parsing Helpers
# ===========================
def _node_text(node) -> str:
    try:
        return node.text(separator=" ")
    except TypeError:
        return node.text()


def _clean_candidate(value: str) -> str:
    return value.strip().strip("()[]{}<>\"'.,;!?")


def _with_scheme(value: str) -> str:
    value = _clean_candidate(value)
    if value.lower().startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def _domain_candidates(text: str) -> List[Tuple[int, str]]:
    matches = []
    for pattern in (URL_RE, DOMAIN_RE):
        for match in pattern.finditer(text or ""):
            matches.append((match.start(), match.end(), match.group(0)))

    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected = []
    for start, end, value in matches:
        if any(start < selected_end and end > selected_start
               for selected_start, selected_end, _ in selected):
            continue
        candidate = _clean_candidate(value)
        if candidate:
            selected.append((start, end, candidate))

    return [(start, value) for start, _, value in selected]


def _matching_domain_candidates(
    text: str,
    expected_fragment: str,
) -> List[Tuple[int, str]]:
    matches = []
    seen_hosts = set()
    expected = expected_fragment.lower()
    for position, value in _domain_candidates(text):
        candidate = _with_scheme(value)
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError:
            continue
        host_key = hostname if not port else f"{hostname}:{port}"
        if expected in hostname and host_key not in seen_hosts:
            matches.append((position, candidate))
            seen_hosts.add(host_key)
    return matches


def _extract_domain_value(text: str, expected_fragment: str) -> Optional[str]:
    candidates = _matching_domain_candidates(text, expected_fragment)
    if candidates:
        return candidates[-1][1]
    return None


def _extract_domain_after_new_address(
    text: str,
    expected_fragment: str,
) -> Optional[str]:
    marker = NEW_ADDRESS_RE.search(text or "")
    if not marker:
        return None
    for position, candidate in _matching_domain_candidates(
        text,
        expected_fragment,
    ):
        if position >= marker.end():
            return candidate
    return None


def _extract_unambiguous_domain(
    text: str,
    expected_fragment: str,
) -> Optional[str]:
    candidates = _matching_domain_candidates(text, expected_fragment)
    return candidates[0][1] if len(candidates) == 1 else None


def _extract_meta_domain(
    html: str,
    property_name: str,
    expected_fragment: str,
) -> Optional[str]:
    document = HTMLParser(html)
    meta = document.css_first(f'meta[property="{property_name}"]')
    if not meta:
        meta = document.css_first(f'meta[name="{property_name}"]')
    if not meta:
        return None
    return _extract_domain_value(
        meta.attributes.get("content", ""),
        expected_fragment,
    )


def _extract_latest_message_domain(
    html: str,
    expected_fragment: str,
) -> Optional[str]:
    document = HTMLParser(html)
    blocks = document.css("div.tgme_widget_message")
    candidate = None
    matched_domain_block = False

    for block in blocks:
        block_html = block.html or ""
        block_text = _node_text(block)
        search_text = block_text
        candidates = _matching_domain_candidates(
            search_text,
            expected_fragment,
        )
        if not candidates:
            search_text = block_html
            candidates = _matching_domain_candidates(
                search_text,
                expected_fragment,
            )
        if not candidates:
            continue
        matched_domain_block = True

        if NEW_ADDRESS_RE.search(block_text):
            candidate = _extract_domain_after_new_address(
                block_text,
                expected_fragment,
            )
            if candidate is None:
                candidate = _extract_domain_after_new_address(
                    block_html,
                    expected_fragment,
                )
        elif NEW_ADDRESS_RE.search(search_text):
            candidate = _extract_domain_after_new_address(
                search_text,
                expected_fragment,
            )
        else:
            candidate = _extract_unambiguous_domain(
                search_text,
                expected_fragment,
            )

    if matched_domain_block:
        return candidate

    page_text = html
    if NEW_ADDRESS_RE.search(page_text):
        return _extract_domain_after_new_address(
            page_text,
            expected_fragment,
        )
    return _extract_unambiguous_domain(page_text, expected_fragment)


# ===========================
# URL and Telegram Helpers
# ===========================
def _root_url(value: str) -> Optional[str]:
    parsed = urlparse(_with_scheme(value))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    if port:
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def _masked_url(value: Optional[str]) -> Optional[str]:
    return _root_url(value) if value else None


def _telegram_urls(value: str) -> List[str]:
    configured = _with_scheme(value).rstrip("/")
    parsed = urlparse(configured)
    if not parsed.hostname or parsed.hostname.lower() not in {"t.me", "telegram.me", "www.t.me"}:
        return [configured]

    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return [configured]

    username = segments[1] if segments[0].lower() == "s" and len(segments) > 1 else segments[0]
    public_messages_url = f"https://t.me/s/{username}"
    if "/s/" in parsed.path.lower():
        return [configured]
    return [configured, public_messages_url]


async def _fetch_telegram_page(channel_url: str, uses_messages: bool) -> str:
    last_error: Optional[Exception] = None
    headers = {"User-Agent": "WAStream domain synchronizer"}
    urls = _telegram_urls(channel_url)
    if uses_messages:
        urls.reverse()

    for url in urls:
        try:
            response = await http_client.get(
                url,
                headers=headers,
                timeout=settings.HTTP_TIMEOUT,
            )
            if response.status_code < 400 and response.text:
                return response.text
            last_error = RuntimeError(f"HTTP {response.status_code}")
        except Exception as error:
            last_error = error

    if last_error:
        raise last_error
    raise RuntimeError("Telegram page is empty")


# ===========================
# Candidate Validation
# ===========================
def _host_matches(url: str, expected_fragment: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return expected_fragment in hostname


def _movix_source_url_from_api(url: str) -> Optional[str]:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    if not hostname.startswith("api.") or len(hostname) <= len("api."):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    source_host = hostname.removeprefix("api.")
    netloc = source_host if not port else f"{source_host}:{port}"
    return f"{parsed.scheme}://{netloc}"


def _is_rejected_page(url: str, body: str) -> bool:
    path = (urlparse(url).path or "").lower()
    if any(marker in path for marker in ("/login", "/signin", "/challenge", "/cdn-cgi/")):
        return True
    return bool(BLOCKED_PAGE_RE.search(body[:20000]))


def _source_marker_present(source: DomainSource, response: httpx.Response) -> bool:
    body = response.text[:50000].lower()
    return source.domain_fragment in body or source.domain_fragment.replace("-", " ") in body


async def _validate_regular_source(source: DomainSource, candidate: str) -> Tuple[bool, Optional[str], str]:
    candidate_url = _with_scheme(candidate)
    if _is_rejected_page(candidate_url, ""):
        return False, None, "blocked_or_login_page"
    candidate_root = _root_url(candidate)
    if not candidate_root:
        return False, None, "invalid_candidate_url"

    client_args = {
        "timeout": httpx.Timeout(float(settings.HTTP_TIMEOUT)),
        "follow_redirects": True,
        "verify": False,
        "headers": {"User-Agent": "WAStream domain synchronizer"},
    }
    if settings.PROXY_URL:
        client_args["proxy"] = settings.PROXY_URL

    async with httpx.AsyncClient(**client_args) as client:
        response = await client.get(candidate_root)
        final_url = _root_url(str(response.url))
        if not final_url or not _host_matches(final_url, source.domain_fragment):
            return False, None, "redirected_to_unexpected_domain"
        if response.status_code >= 400:
            return False, None, f"HTTP_{response.status_code}"
        if _is_rejected_page(str(response.url), response.text):
            return False, None, "blocked_or_login_page"
        if not _source_marker_present(source, response):
            return False, None, "source_marker_not_found"
        return True, final_url, "valid"


async def _validate_movix_source(source: DomainSource, candidate: str) -> Tuple[bool, Optional[str], str]:
    candidate_root = _root_url(candidate)
    if not candidate_root:
        return False, None, "invalid_candidate_url"

    client_args = {
        "timeout": httpx.Timeout(float(settings.HTTP_TIMEOUT)),
        "follow_redirects": True,
        "verify": False,
        "headers": {
            "User-Agent": "WAStream domain synchronizer",
            "Origin": candidate_root,
            "Referer": f"{candidate_root}/",
        },
    }
    if settings.PROXY_URL:
        client_args["proxy"] = settings.PROXY_URL

    async with httpx.AsyncClient(**client_args) as client:
        site_response = await client.get(candidate_root)
        final_site_url = _root_url(str(site_response.url))
        if not final_site_url or not _host_matches(
            final_site_url,
            source.domain_fragment,
        ):
            return False, None, "redirected_to_unexpected_domain"
        if site_response.status_code >= 400:
            return False, None, f"HTTP_{site_response.status_code}"
        if _is_rejected_page(str(site_response.url), site_response.text):
            return False, None, "blocked_or_login_page"

        parsed = urlparse(final_site_url)
        api_host = f"api.{parsed.hostname.removeprefix('www.')}"
        api_url = f"{parsed.scheme}://{api_host}/api/search"
        api_headers = {
            "User-Agent": "WAStream domain synchronizer",
            "Origin": final_site_url,
            "Referer": f"{final_site_url}/",
        }
        response = await client.get(
            api_url,
            params={"title": "test"},
            headers=api_headers,
        )
        final_api_url = str(response.url)
        if response.status_code >= 400:
            return False, None, f"API_HTTP_{response.status_code}"
        if not _host_matches(final_api_url, source.domain_fragment):
            return False, None, "api_redirected_to_unexpected_domain"
        redirected_source_url = _movix_source_url_from_api(final_api_url)
        return True, redirected_source_url or final_site_url, "valid"


async def _validate_candidate(source: DomainSource, candidate: str) -> Tuple[bool, Optional[str], str]:
    if source.key == "movix":
        return await _validate_movix_source(source, candidate)
    return await _validate_regular_source(source, candidate)


# ===========================
# Synchronization Workflow
# ===========================
async def _sync_source(source: DomainSource) -> Dict[str, object]:
    current_url = getattr(settings, source.source_setting)
    channel_url = getattr(settings, source.channel_setting)
    result: Dict[str, object] = {
        "source": source.name,
        "status": "unknown",
        "current_url": _masked_url(current_url),
        "candidate_url": None,
        "reason": None,
        "timestamp": time.time(),
    }

    if not channel_url:
        result.update(status="unconfigured", reason="telegram_channel_not_configured")
        return result

    try:
        html = await _fetch_telegram_page(channel_url, source.uses_messages)
        candidate = source.extractor(html)
        result["candidate_url"] = _masked_url(candidate)
        if not candidate:
            result.update(status="no_candidate", reason="no_matching_address_found")
            scraper_logger.warning(f"[DomainSync] {source.name}: no matching domain found")
            return result

        valid, validated_url, reason = await _validate_candidate(source, candidate)
        if not valid or not validated_url:
            result.update(status="rejected", reason=reason)
            scraper_logger.warning(f"[DomainSync] {source.name}: candidate rejected ({reason})")
            return result

        result["candidate_url"] = _masked_url(validated_url)
        current_root = _root_url(current_url) if current_url else None
        if current_root == validated_url:
            result.update(status="unchanged", reason="domain_already_current")
            return result

        update_status = await set_override(source.source_setting, validated_url)
        if update_status == "ok":
            result.update(status="updated", reason="domain_updated")
            scraper_logger.info(f"[DomainSync] {source.name}: domain updated to {_masked_url(validated_url)}")
        elif update_status == "env_locked":
            result.update(status="env_locked", reason="source_url_defined_in_env")
            scraper_logger.warning(f"[DomainSync] {source.name}: new domain found but source URL is locked by .env")
        else:
            result.update(status="update_failed", reason=update_status)
            scraper_logger.error(f"[DomainSync] {source.name}: domain update failed ({update_status})")
        return result
    except Exception as error:
        result.update(status="error", reason=type(error).__name__)
        scraper_logger.error(f"[DomainSync] {source.name}: synchronization failed ({type(error).__name__}: {error})")
        return result


async def run_domain_sync(source_names: Optional[List[str]] = None) -> str:
    if _sync_state.running:
        return "already_running"

    selected = [SOURCES_BY_NAME[name] for name in source_names if name in SOURCES_BY_NAME] if source_names else list(DOMAIN_SOURCES)
    if not selected:
        return "no_sources"

    async with _sync_lock:
        if _sync_state.running:
            return "already_running"
        _sync_state.running = True
        _sync_state.stop_requested = False
        is_full_sync = source_names is None
        if is_full_sync:
            _pending_rechecks.clear()

        try:
            for source in selected:
                if _sync_state.stop_requested:
                    break
                result = await _sync_source(source)
                _sync_state.last_results[source.name] = result
            _sync_state.last_run = time.time()
            if is_full_sync:
                _sync_state.last_full_sync = _sync_state.last_run
            return "stopped" if _sync_state.stop_requested else "completed"
        finally:
            _sync_state.running = False
            _sync_state.stop_requested = False


# ===========================
# Health-triggered Rechecks
# ===========================
def request_source_recheck(source_name: str) -> str:
    if not settings.DOMAIN_SYNC_ENABLED or not settings.DOMAIN_SYNC_RECHECK_ON_HEALTH_ERROR:
        return "disabled"
    source = SOURCES_BY_NAME.get(source_name)
    if not source:
        return "unsupported_source"
    if not getattr(settings, source.channel_setting):
        return "channel_not_configured"

    now = time.time()
    last_attempt = _sync_state.last_recheck_at.get(source_name, 0)
    delay = max(1, settings.DOMAIN_SYNC_HEALTH_ERROR_RECHECK_DELAY_SECONDS)
    if now - last_attempt < delay:
        return "cooldown"

    _sync_state.last_recheck_at[source_name] = now
    _pending_rechecks.add(source_name)
    _recheck_event.set()
    scraper_logger.debug(f"[DomainSync] {source_name}: health-triggered recheck queued")
    return "queued"


# ===========================
# Manual Control and Status
# ===========================
def get_domain_sync_status() -> Dict[str, object]:
    sources = []
    for source in DOMAIN_SOURCES:
        result = _sync_state.last_results.get(source.name, {})
        sources.append({
            "name": source.name,
            "channel_configured": bool(getattr(settings, source.channel_setting)),
            "current_url": _masked_url(getattr(settings, source.source_setting)),
            "last_result": result or None,
            "last_recheck_at": _sync_state.last_recheck_at.get(source.name),
        })
    return {
        "enabled": settings.DOMAIN_SYNC_ENABLED,
        "running": _sync_state.running,
        "stop_requested": _sync_state.stop_requested,
        "interval": settings.DOMAIN_SYNC_INTERVAL,
        "health_recheck_enabled": settings.DOMAIN_SYNC_RECHECK_ON_HEALTH_ERROR,
        "health_recheck_delay_seconds": settings.DOMAIN_SYNC_HEALTH_ERROR_RECHECK_DELAY_SECONDS,
        "last_run": _sync_state.last_run,
        "last_full_sync": _sync_state.last_full_sync,
        "pending_rechecks": sorted(_pending_rechecks),
        "sources": sources,
    }


def _log_manual_task_result(task: asyncio.Task):
    try:
        error = task.exception()
    except asyncio.CancelledError:
        return
    if error:
        scraper_logger.error(f"[DomainSync] Manual run crashed: {type(error).__name__}: {error}")


async def trigger_domain_sync() -> str:
    if _sync_state.running:
        return "already_running"
    if not any(getattr(settings, source.channel_setting) for source in DOMAIN_SOURCES):
        return "no_channels"

    global _manual_sync_task
    _manual_sync_task = asyncio.create_task(run_domain_sync())
    _manual_sync_task.add_done_callback(_log_manual_task_result)
    return "started"


def request_stop_domain_sync() -> str:
    if not _sync_state.running:
        return "not_running"
    _sync_state.stop_requested = True
    return "stopping"


# ===========================
# Background Loop
# ===========================
async def start_background_domain_sync():
    scraper_logger.info(f"[DomainSync] Background loop started (interval: {settings.DOMAIN_SYNC_INTERVAL}s)")

    while True:
        try:
            if settings.DOMAIN_SYNC_ENABLED and not _sync_state.running:
                now = time.time()
                interval = max(1, settings.DOMAIN_SYNC_INTERVAL)
                if _sync_state.last_full_sync is None or now - _sync_state.last_full_sync >= interval:
                    await run_domain_sync()
                elif _pending_rechecks:
                    names = sorted(_pending_rechecks)
                    _pending_rechecks.clear()
                    await run_domain_sync(names)

            wait_timeout = max(1, min(60, settings.DOMAIN_SYNC_INTERVAL))
            try:
                await asyncio.wait_for(_recheck_event.wait(), timeout=wait_timeout)
            except asyncio.TimeoutError:
                pass
            _recheck_event.clear()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            scraper_logger.error(f"[DomainSync] Background loop error: {type(error).__name__}: {error}")
