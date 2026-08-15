import ast
import asyncio
import re
import time
from typing import Any, List, Dict, Optional, Tuple

from wastream.config.settings import settings
from wastream.services.scraper_importer import (
    ALLDEBRID_DEFAULT_BASE_URL,
    build_release_name,
    parse_release_info as _parse_release_info,
    parse_size_list,
    process_content_entries,
    resolve_imdb_id as _resolve_imdb_id,
    resolve_source_url,
)
from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger


# ===========================
# Constants
# ===========================
COL_CAT = 0
COL_TMDB = 1
COL_TITLE = 2
COL_SAISON = 3
COL_YEAR = 8
COL_RES = 10
COL_SIZE = 11

SERIES_URL_PATTERN = re.compile(r"(\d+)\s*:\s*'([^']*)'")

# Auto-discovery: a paste "code" is a bare alphanumeric token on its own line (a paste id
# to append to the base URL). Used to tell an index page (only codes) from a content page.
PASTE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

_scraper_state: Dict[str, Any] = {
    "running": False,
    "stop_requested": False,
    "last_run": None,
    "last_stats": None,
    "current_url": None,
    "progress": 0,
    "progress_total": 0,
}

_manual_scrape_task = None


# ===========================
# Scraper State
# ===========================
def get_pastebin_scraper_status() -> Dict[str, Any]:
    return dict(_scraper_state)


build_pastebin_release_name = build_release_name
parse_release_info = _parse_release_info
resolve_imdb_id = _resolve_imdb_id


# ===========================
# Series URL Parsing
# ===========================
def parse_series_urls(urls_raw: str) -> List[Tuple[int, str]]:
    matches = SERIES_URL_PATTERN.findall(urls_raw)
    return [(int(ep), suffix) for ep, suffix in matches]


resolve_pastebin_url = resolve_source_url


# ===========================
# TMDB → IMDB Resolution
# ===========================
# ===========================
# Size Parsing
# ===========================
# ===========================
# Parse Pastebin Content
# ===========================
def parse_pastebin_content(content: str) -> tuple:
    lines = content.strip().split("\n")
    if not lines:
        return [], ALLDEBRID_DEFAULT_BASE_URL

    header = lines[0]
    alldebrid_base_url = ALLDEBRID_DEFAULT_BASE_URL
    header_parts = header.split(";")
    if header_parts:
        urls_header = header_parts[-1].strip()
        if "=" in urls_header:
            alldebrid_base_url = urls_header.split("=", 1)[1].strip()

    entries = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue

        parts = line.split(";")
        if len(parts) < 12:
            continue

        try:
            cat = parts[COL_CAT].strip()
            tmdb_id = parts[COL_TMDB].strip()
            title = parts[COL_TITLE].strip()
            season_str = parts[COL_SAISON].strip()
            year_str = parts[COL_YEAR].strip()
            res_raw = parts[COL_RES].strip()
            urls_raw = parts[-1].strip()
            sizes = parse_size_list(parts[COL_SIZE].strip()) if len(parts) >= 13 else []

            if not tmdb_id or not title:
                continue

            season = int(season_str) if season_str and season_str.isdigit() else None
            year = int(year_str) if year_str and year_str.isdigit() else None

            if cat == "serie":
                release_name = res_raw if res_raw else None
                episode_urls = parse_series_urls(urls_raw)

                if not episode_urls:
                    continue

                episodes: Dict[int, List[str]] = {}
                episode_sizes: Dict[int, Optional[int]] = {}
                for ep_num, suffix in episode_urls:
                    episodes.setdefault(ep_num, []).append(suffix)
                    if ep_num not in episode_sizes:
                        k = len(episode_sizes)
                        episode_sizes[ep_num] = sizes[k] if k < len(sizes) else None

                entries.append({
                    "cat": "serie",
                    "tmdb_id": tmdb_id,
                    "title": title,
                    "season": season,
                    "year": year,
                    "release_name": release_name,
                    "episodes": episodes,
                    "episode_sizes": episode_sizes,
                })

            else:
                res_list = ast.literal_eval(res_raw) if res_raw else []
                urls_list = ast.literal_eval(urls_raw) if urls_raw else []

                if not isinstance(res_list, (list, tuple)) or not isinstance(urls_list, (list, tuple)):
                    continue

                if not res_list or not urls_list or len(res_list) != len(urls_list):
                    continue

                releases = [
                    (res, url, sizes[i] if i < len(sizes) else None)
                    for i, (res, url) in enumerate(zip(res_list, urls_list))
                ]

                entries.append({
                    "cat": "film",
                    "tmdb_id": tmdb_id,
                    "title": title,
                    "season": None,
                    "year": year,
                    "releases": releases,
                })

        except (ValueError, SyntaxError):
            continue

    return entries, alldebrid_base_url


# ===========================
# Fetch
# ===========================
async def _fetch_pastebin(url: str) -> Optional[str]:
    try:
        response = await http_client.get(url, timeout=30)
        if response.status_code != 200:
            scraper_logger.error(f"[PastebinScraper] Failed to fetch {url}: HTTP {response.status_code}")
            return None
        return response.text
    except Exception as e:
        scraper_logger.error(f"[PastebinScraper] Fetch error for {url}: {type(e).__name__}: {e}")
        return None


# ===========================
# Content Processing
# ===========================
async def _process_content_entries(entries: List[Dict], alldebrid_base_url: str, tmdb_api_token: str, stats: Dict):
    await process_content_entries(
        entries=entries,
        alldebrid_base_url=alldebrid_base_url,
        tmdb_api_token=tmdb_api_token,
        stats=stats,
        stop_requested=lambda: _scraper_state["stop_requested"],
        logger_name="PastebinScraper",
    )


async def _process_content_page(url: str, content: str, entries: List[Dict], alldebrid_base_url: str, tmdb_api_token: str, stats: Dict):
    stats["total"] += len(entries)
    await _process_content_entries(entries, alldebrid_base_url, tmdb_api_token, stats)


# ===========================
# Auto-Discovery
# ===========================
def _extract_paste_codes(content: str) -> List[str]:
    codes = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if PASTE_CODE_PATTERN.match(line):
            codes.append(line)
        else:
            return []
    return codes


def _derive_paste_base(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/"


async def _discover_and_scrape(url: str, tmdb_api_token: str, ctx: Dict, depth: int, stats: Dict):
    if _scraper_state["stop_requested"]:
        return
    if url in ctx["visited"]:
        return
    if len(ctx["visited"]) >= settings.PASTEBIN_SCRAPER_MAX_PAGES:
        if not ctx["cap_logged"]:
            scraper_logger.warning(f"[PastebinScraper] Page cap ({settings.PASTEBIN_SCRAPER_MAX_PAGES}) reached, discovery truncated")
            ctx["cap_logged"] = True
        return
    if depth > settings.PASTEBIN_SCRAPER_MAX_DEPTH:
        scraper_logger.debug(f"[PastebinScraper] Max depth reached at {url}")
        return
    ctx["visited"].add(url)

    content = await _fetch_pastebin(url)
    if not content:
        return

    entries, alldebrid_base_url = parse_pastebin_content(content)
    if entries:
        ctx["content_pages"] += 1
        scraper_logger.debug(f"[PastebinScraper] Content page ({len(entries)} entries): {url}")
        await _process_content_page(url, content, entries, alldebrid_base_url, tmdb_api_token, stats)
        return

    codes = _extract_paste_codes(content)
    if not codes:
        return
    scraper_logger.debug(f"[PastebinScraper] Index page ({len(codes)} codes): {url}")
    base = _derive_paste_base(url)
    for code in codes:
        await _discover_and_scrape(base + code, tmdb_api_token, ctx, depth + 1, stats)


# ===========================
# Scrape Pastebin URL
# ===========================
async def scrape_pastebin_url(url: str, tmdb_api_token: str) -> Dict:
    stats = {"added": 0, "skipped": 0, "errors": 0, "total": 0}
    try:
        ctx = {"visited": set(), "cap_logged": False, "content_pages": 0}
        await _discover_and_scrape(url, tmdb_api_token, ctx, 0, stats)
        scraper_logger.info(
            f"[PastebinScraper] {url}: {len(ctx['visited'])} page(s) visited, "
            f"{ctx['content_pages']} content page(s), {stats['added']} link(s) added"
        )
    except Exception as e:
        scraper_logger.error(f"[PastebinScraper] Scrape error for {url}: {type(e).__name__}: {e}")
    return stats


# ===========================
# Run Pastebin Scraper
# ===========================
async def run_pastebin_scraper():
    if _scraper_state["running"]:
        return

    if not settings.PASTEBIN_SCRAPER_URLS:
        return

    if not settings.TMDB_API_KEY:
        scraper_logger.error("[PastebinScraper] TMDB_API_KEY required")
        return

    _scraper_state["running"] = True
    _scraper_state["stop_requested"] = False
    _scraper_state["progress"] = 0
    _scraper_state["progress_total"] = len(settings.PASTEBIN_SCRAPER_URLS)

    scraper_logger.info(f"[PastebinScraper] Starting scrape of {len(settings.PASTEBIN_SCRAPER_URLS)} URL(s)")

    total_stats = {"added": 0, "skipped": 0, "errors": 0, "total": 0}
    stopped = False

    try:
        for i, url in enumerate(settings.PASTEBIN_SCRAPER_URLS):
            if _scraper_state["stop_requested"]:
                stopped = True
                break

            _scraper_state["current_url"] = url
            _scraper_state["progress"] = i + 1

            stats = await scrape_pastebin_url(url, settings.TMDB_API_KEY)

            for key in total_stats:
                total_stats[key] += stats[key]

            scraper_logger.info(
                f"[PastebinScraper] {stats['added']} added, {stats['skipped']} skipped, "
                f"{stats['errors']} errors / {stats['total']} entries"
            )
        stopped = stopped or _scraper_state["stop_requested"]
    finally:
        _scraper_state["running"] = False
        _scraper_state["stop_requested"] = False
        _scraper_state["current_url"] = None
        _scraper_state["last_run"] = int(time.time())
        _scraper_state["last_stats"] = total_stats

    scraper_logger.info(
        f"[PastebinScraper] {'Stopped' if stopped else 'Done'}: {total_stats['added']} added, "
        f"{total_stats['skipped']} skipped, {total_stats['errors']} errors / {total_stats['total']} entries"
    )


# ===========================
# Manual Control
# ===========================
def _log_manual_task_result(task):
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        scraper_logger.error(f"[PastebinScraper] Manual run crashed: {type(exc).__name__}: {exc}")


async def trigger_pastebin_scraper() -> str:
    if _scraper_state["running"]:
        return "already_running"
    if not settings.PASTEBIN_SCRAPER_URLS:
        return "no_urls"
    if not settings.TMDB_API_KEY:
        return "no_tmdb_key"
    global _manual_scrape_task
    _manual_scrape_task = asyncio.create_task(run_pastebin_scraper())
    _manual_scrape_task.add_done_callback(_log_manual_task_result)
    return "started"


def request_stop_pastebin_scraper() -> str:
    if not _scraper_state["running"]:
        return "not_running"
    _scraper_state["stop_requested"] = True
    return "stopping"


# ===========================
# Background Loop
# ===========================
async def start_pastebin_scraper_loop():
    await asyncio.sleep(15)

    while True:
        try:
            await run_pastebin_scraper()
        except Exception as e:
            scraper_logger.error(f"[PastebinScraper] Loop error: {type(e).__name__}: {e}")

        await asyncio.sleep(max(1, settings.PASTEBIN_SCRAPER_INTERVAL))
