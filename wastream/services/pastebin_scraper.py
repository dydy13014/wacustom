import ast
import asyncio
import hashlib
import re
import time
from typing import Any, List, Dict, Optional, Tuple

from wastream.config.settings import settings
from wastream.services.wasource import add_wasource_links_bulk
from wastream.utils.database import database
from wastream.utils.http_client import http_client
from wastream.utils.languages import normalize_language
from wastream.utils.logger import scraper_logger
from wastream.utils.quality import extract_resolution


# ===========================
# Constants
# ===========================
ALLDEBRID_DEFAULT_BASE_URL = "https://alldebrid.com/f/"

COL_CAT = 0
COL_TMDB = 1
COL_TITLE = 2
COL_SAISON = 3
COL_YEAR = 8
COL_RES = 10
COL_URLS = 11

SERIES_URL_PATTERN = re.compile(r"(\d+)\s*:\s*'([^']*)'")

_pastebin_content_hashes: Dict[str, str] = {}

_scraper_state: Dict[str, Any] = {
    "running": False,
    "last_run": None,
    "last_stats": None,
    "current_url": None,
    "progress": 0,
    "progress_total": 0,
}


# ===========================
# Scraper State
# ===========================
def get_pastebin_scraper_status() -> Dict[str, Any]:
    return dict(_scraper_state)


# ===========================
# Release Info Extraction
# ===========================
def _clean_name(text: str) -> str:
    cleaned = re.sub(r'[^\w]+', '.', text)
    cleaned = cleaned.replace('_', '.')
    cleaned = re.sub(r'\.{2,}', '.', cleaned)
    return cleaned.strip('.')


def parse_release_info(release_name: str) -> Tuple[str, str]:
    if not release_name:
        return "Unknown", "Unknown"

    parts = release_name.split(" - ", 1)
    if len(parts) < 2:
        return "Unknown", release_name

    first_part = parts[0].strip()
    tokens = first_part.upper().split()

    if "MULTI" in tokens:
        return "Multi", parts[1].strip()

    for token in tokens:
        normalized = normalize_language(token.lower())
        if normalized != "Unknown":
            return normalized, parts[1].strip()

    return "Unknown", release_name


def build_pastebin_release_name(
    title: str, year: Optional[int], season: Optional[int],
    episode: Optional[int], res_name: Optional[str]
) -> str:
    name = _clean_name(title)

    if year:
        name += f".{year}"

    if season is not None:
        s = str(season).zfill(2)
        if episode is not None:
            e = str(episode).zfill(2)
            name += f".S{s}E{e}"
        else:
            name += f".S{s}"

    if res_name:
        name += f".{_clean_name(res_name)}"

    return name


# ===========================
# Series URL Parsing
# ===========================
def parse_series_urls(urls_raw: str) -> List[Tuple[int, str]]:
    matches = SERIES_URL_PATTERN.findall(urls_raw)
    return [(int(ep), suffix) for ep, suffix in matches]


# ===========================
# TMDB → IMDB Resolution
# ===========================
async def lookup_imdb_from_wasource(tmdb_id: str) -> Optional[str]:
    try:
        row = await database.fetch_one(
            "SELECT imdb_id FROM wasource WHERE tmdb_id = :tmdb_id LIMIT 1",
            {"tmdb_id": tmdb_id}
        )
        if row:
            return row["imdb_id"]
    except Exception:
        pass
    return None


async def fetch_imdb_id_from_tmdb(tmdb_id: str, content_type: str, tmdb_api_token: str) -> Optional[str]:
    imdb_id = await lookup_imdb_from_wasource(tmdb_id)
    if imdb_id:
        return imdb_id

    if not tmdb_api_token:
        return None

    try:
        endpoint = "movie" if content_type == "film" else "tv"
        url = f"{settings.TMDB_API_URL}/{endpoint}/{tmdb_id}/external_ids"

        response = await http_client.get(
            url,
            params={"api_key": tmdb_api_token},
            timeout=settings.METADATA_TIMEOUT
        )

        if response.status_code == 429:
            await asyncio.sleep(2)
            response = await http_client.get(
                url,
                params={"api_key": tmdb_api_token},
                timeout=settings.METADATA_TIMEOUT
            )

        if response.status_code != 200:
            return None

        data = response.json()
        imdb_id = data.get("imdb_id")

        await asyncio.sleep(0.15)
        return imdb_id

    except Exception as e:
        scraper_logger.error(f"[PastebinScraper] TMDB lookup failed for {tmdb_id}: {type(e).__name__}: {e}")
        return None


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
    if len(header_parts) > COL_URLS:
        urls_header = header_parts[COL_URLS].strip()
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
            urls_raw = parts[COL_URLS].strip()

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
                for ep_num, suffix in episode_urls:
                    episodes.setdefault(ep_num, []).append(suffix)

                entries.append({
                    "cat": "serie",
                    "tmdb_id": tmdb_id,
                    "title": title,
                    "season": season,
                    "year": year,
                    "release_name": release_name,
                    "episodes": episodes,
                })

            else:
                res_list = ast.literal_eval(res_raw) if res_raw else []
                urls_list = ast.literal_eval(urls_raw) if urls_raw else []

                if not res_list or not urls_list or len(res_list) != len(urls_list):
                    continue

                entries.append({
                    "cat": "film",
                    "tmdb_id": tmdb_id,
                    "title": title,
                    "season": None,
                    "year": year,
                    "releases": list(zip(res_list, urls_list)),
                })

        except (ValueError, SyntaxError):
            continue

    return entries, alldebrid_base_url


# ===========================
# Scrape Single Pastebin URL
# ===========================
async def scrape_pastebin_url(url: str, tmdb_api_token: str) -> Dict:
    stats = {"added": 0, "skipped": 0, "errors": 0, "total": 0}

    try:
        response = await http_client.get(url, timeout=30)
        if response.status_code != 200:
            scraper_logger.error(f"[PastebinScraper] Failed to fetch {url}: HTTP {response.status_code}")
            return stats

        content = response.text

        content_hash = hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()
        if _pastebin_content_hashes.get(url) == content_hash:
            scraper_logger.info("[PastebinScraper] Unchanged, skipping")
            return stats

        entries, alldebrid_base_url = parse_pastebin_content(content)
        stats["total"] = len(entries)

        for entry in entries:
            try:
                tmdb_id = entry["tmdb_id"]
                cat = entry["cat"]

                imdb_id = await fetch_imdb_id_from_tmdb(tmdb_id, cat, tmdb_api_token)
                if not imdb_id:
                    stats["errors"] += 1
                    continue

                if cat == "serie":
                    res_name = entry.get("release_name")
                    language, raw_quality = parse_release_info(res_name)
                    quality = extract_resolution(raw_quality)

                    for ep_num, suffixes in entry["episodes"].items():
                        urls = [{"host": "alldebrid", "url": alldebrid_base_url + s} for s in suffixes]
                        release_name = build_pastebin_release_name(
                            entry["title"], entry["year"], entry["season"], ep_num, res_name
                        )

                        result = await add_wasource_links_bulk(
                            imdb_id=imdb_id,
                            title=entry["title"],
                            release_name=release_name,
                            quality=quality,
                            language=language,
                            size=None,
                            season=entry["season"],
                            episode=ep_num,
                            urls=urls,
                            tmdb_id=str(tmdb_id),
                            year=entry["year"]
                        )

                        stats["added"] += result.get("added", 0)
                        stats["skipped"] += result.get("skipped", 0)

                else:
                    for res_name, url_suffix in entry["releases"]:
                        full_url = alldebrid_base_url + url_suffix
                        language, raw_quality = parse_release_info(res_name)
                        quality = extract_resolution(raw_quality)
                        release_name = build_pastebin_release_name(
                            entry["title"], entry["year"], None, None, res_name
                        )

                        result = await add_wasource_links_bulk(
                            imdb_id=imdb_id,
                            title=entry["title"],
                            release_name=release_name,
                            quality=quality,
                            language=language,
                            size=None,
                            season=None,
                            episode=None,
                            urls=[{"host": "alldebrid", "url": full_url}],
                            tmdb_id=str(tmdb_id),
                            year=entry["year"]
                        )

                        stats["added"] += result.get("added", 0)
                        stats["skipped"] += result.get("skipped", 0)

            except Exception as e:
                scraper_logger.error(f"[PastebinScraper] Entry error ({entry.get('title', '?')}): {type(e).__name__}: {e}")
                stats["errors"] += 1

        _pastebin_content_hashes[url] = content_hash

    except Exception as e:
        scraper_logger.error(f"[PastebinScraper] Scrape error for {url}: {type(e).__name__}: {e}")

    return stats


# ===========================
# Run Pastebin Scraper
# ===========================
async def run_pastebin_scraper():
    if not settings.PASTEBIN_SCRAPER_URLS:
        return

    if not settings.TMDB_API_KEY:
        scraper_logger.error("[PastebinScraper] TMDB_API_KEY required")
        return

    _scraper_state["running"] = True
    _scraper_state["progress"] = 0
    _scraper_state["progress_total"] = len(settings.PASTEBIN_SCRAPER_URLS)

    scraper_logger.info(f"[PastebinScraper] Starting scrape of {len(settings.PASTEBIN_SCRAPER_URLS)} URL(s)")

    total_stats = {"added": 0, "skipped": 0, "errors": 0, "total": 0}

    for i, url in enumerate(settings.PASTEBIN_SCRAPER_URLS):
        _scraper_state["current_url"] = url
        _scraper_state["progress"] = i + 1

        stats = await scrape_pastebin_url(url, settings.TMDB_API_KEY)

        for key in total_stats:
            total_stats[key] += stats[key]

        scraper_logger.info(
            f"[PastebinScraper] {stats['added']} added, {stats['skipped']} skipped, "
            f"{stats['errors']} errors / {stats['total']} entries"
        )

    _scraper_state["running"] = False
    _scraper_state["current_url"] = None
    _scraper_state["last_run"] = int(time.time())
    _scraper_state["last_stats"] = total_stats

    scraper_logger.info(
        f"[PastebinScraper] Done: {total_stats['added']} added, {total_stats['skipped']} skipped, "
        f"{total_stats['errors']} errors / {total_stats['total']} entries"
    )


# ===========================
# Background Loop
# ===========================
async def start_pastebin_scraper_loop():
    if not settings.PASTEBIN_SCRAPER_URLS:
        return

    await asyncio.sleep(15)

    while True:
        try:
            await run_pastebin_scraper()
        except Exception as e:
            scraper_logger.error(f"[PastebinScraper] Loop error: {type(e).__name__}: {e}")

        await asyncio.sleep(settings.PASTEBIN_SCRAPER_INTERVAL)
