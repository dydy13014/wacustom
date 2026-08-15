import ast
import asyncio
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from wastream.config.settings import settings
from wastream.services.wasource import add_wasource_links_bulk
from wastream.utils.database import database
from wastream.utils.http_client import http_client
from wastream.utils.helpers import normalize_text
from wastream.utils.languages import normalize_language
from wastream.utils.logger import scraper_logger
from wastream.utils.quality import extract_resolution
from wastream.utils.urls import canonicalize_url


# ===========================
# Shared Import Constants
# ===========================
ALLDEBRID_DEFAULT_BASE_URL = "https://alldebrid.com/f/"

HOST_DOMAINS = {
    "1fichier": ("1fichier.com",),
    "turbobit": ("turbobit.net",),
    "rapidgator": ("rapidgator.net", "rg.to"),
    "sendcm": ("send.cm",),
    "darkibox": ("darkibox.com",),
    "alldebrid": ("alldebrid.com",),
}


# ===========================
# Release Information
# ===========================
def _clean_name(text: str) -> str:
    cleaned = re.sub(r"[^\w]+", ".", text)
    cleaned = cleaned.replace("_", ".")
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    return cleaned.strip(".")


def _clean_release_name_suffix(res_name: Optional[str]) -> str:
    value = (res_name or "").strip()
    if not value or value.casefold() == "unknown":
        return ""

    parts = [part.strip() for part in value.split(" - ", 1)]
    if len(parts) == 2:
        parts = [part for part in parts if part and part.casefold() != "unknown"]
        return " - ".join(parts)

    return value


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


def build_release_name(
    title: str,
    year: Optional[int],
    season: Optional[int],
    episode: Optional[int],
    res_name: Optional[str],
) -> str:
    name = _clean_name(title)

    if year:
        name += f".{year}"

    if season is not None:
        name += f".S{str(season).zfill(2)}"
        if episode is not None:
            name += f"E{str(episode).zfill(2)}"

    release_suffix = _clean_release_name_suffix(res_name)
    if release_suffix:
        name += f".{_clean_name(release_suffix)}"

    return name


# ===========================
# URL Host Resolution
# ===========================
def detect_known_host(value: str) -> Optional[str]:
    candidate = value.strip()
    if not candidate:
        return None

    try:
        parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
        hostname = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return None

    for host in settings.WASOURCE_SUPPORTED_HOSTS:
        for domain in HOST_DOMAINS.get(host, (host,)):
            domain = domain.lower().rstrip(".")
            if hostname == domain or hostname.endswith(f".{domain}"):
                return host
    return None


def resolve_source_url(
    value: str,
    alldebrid_base_url: str = ALLDEBRID_DEFAULT_BASE_URL,
    logger_name: str = "SourceImporter",
) -> Optional[Dict[str, str]]:
    value = canonicalize_url((value or "").strip()) or ""
    if not value:
        return None

    host = detect_known_host(value)
    if host:
        url = value if "://" in value else f"https://{value}"
        return {"host": host, "url": url}

    if "://" in value or ("." in value and "/" in value):
        scraper_logger.debug(f"[{logger_name}] Ignored unsupported-host URL: {value[:80]}")
        return None

    return {"host": "alldebrid", "url": alldebrid_base_url + value}


# ===========================
# TMDB / IMDb Resolution
# ===========================
async def get_imdb_id_from_wasource(tmdb_id: str) -> Optional[str]:
    try:
        row = await database.fetch_one(
            "SELECT imdb_id FROM wasource WHERE tmdb_id = :tmdb_id LIMIT 1",
            {"tmdb_id": tmdb_id},
        )
        if row:
            return row["imdb_id"]
    except Exception:
        pass
    return None


async def resolve_imdb_id(
    tmdb_id: str,
    content_type: str,
    tmdb_api_token: str,
    logger_name: str = "SourceImporter",
) -> Optional[str]:
    imdb_id = await get_imdb_id_from_wasource(tmdb_id)
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
            timeout=settings.METADATA_TIMEOUT,
        )

        if response.status_code == 429:
            await asyncio.sleep(2)
            response = await http_client.get(
                url,
                params={"api_key": tmdb_api_token},
                timeout=settings.METADATA_TIMEOUT,
            )

        if response.status_code != 200:
            return None

        imdb_id = response.json().get("imdb_id")
        await asyncio.sleep(0.15)
        return imdb_id
    except Exception as e:
        scraper_logger.error(
            f"[{logger_name}] TMDB lookup failed for {tmdb_id}: {type(e).__name__}: {e}"
        )
        return None


def _tmdb_result_titles(result: Dict[str, Any], content_type: str) -> List[str]:
    keys = ("title", "original_title") if content_type == "film" else ("name", "original_name")
    return list(dict.fromkeys(
        str(result.get(key) or "").strip()
        for key in keys
        if str(result.get(key) or "").strip()
    ))


def _tmdb_result_title(result: Dict[str, Any], content_type: str) -> str:
    titles = _tmdb_result_titles(result, content_type)
    return titles[0] if titles else ""


def _tmdb_result_year(result: Dict[str, Any], content_type: str) -> Optional[int]:
    key = "release_date" if content_type == "film" else "first_air_date"
    value = str(result.get(key) or "")
    match = re.match(r"(19\d{2}|20\d{2})", value)
    return int(match.group(1)) if match else None


def _tmdb_title_variants(
    title: str,
    content_type: str,
    allow_film_number_variants: bool = True,
) -> List[str]:
    normalized = normalize_text(title)
    if not normalized:
        return []

    variants = [normalized]
    if content_type == "film" and allow_film_number_variants:
        without_franchise_number = re.sub(r"^\d{1,3}\s+", "", normalized)
        without_sequel_number = re.sub(r"\b[1-9]\b(?:\s+|$)", "", normalized, count=1).strip()
        for variant in (without_franchise_number, without_sequel_number):
            if variant and variant not in variants:
                variants.append(variant)
    return variants


def _tmdb_title_match_score(
    query: str,
    candidate: str,
    content_type: str,
    allow_film_number_variants: bool = True,
) -> int:
    query_variants = _tmdb_title_variants(
        query, content_type, allow_film_number_variants
    )
    candidate_variants = _tmdb_title_variants(
        candidate, content_type, allow_film_number_variants
    )
    if not query_variants or not candidate_variants:
        return 0
    if query_variants[0] == candidate_variants[0]:
        return 2
    return 1 if set(query_variants) & set(candidate_variants) else 0


def _tmdb_title_matches(query: str, candidate: str, content_type: str = "film") -> bool:
    return _tmdb_title_match_score(query, candidate, content_type) > 0


async def resolve_tmdb_id(
    title: str,
    year: Optional[int],
    content_type: str,
    tmdb_api_token: str,
    logger_name: str = "SourceImporter",
) -> Optional[str]:
    if not title or not tmdb_api_token:
        return None

    try:
        endpoint = "movie" if content_type == "film" else "tv"
        allow_film_number_variants = not (
            content_type == "film" and year is None
        )
        query_variants = _tmdb_title_variants(
            title, content_type, allow_film_number_variants
        )
        for query in query_variants:
            params: Dict[str, str] = {
                "api_key": tmdb_api_token,
                "language": "fr-FR",
                "query": query,
            }
            if year:
                params["year" if content_type == "film" else "first_air_date_year"] = str(year)

            response = await http_client.get(
                f"{settings.TMDB_API_URL}/search/{endpoint}",
                params=params,
                timeout=settings.METADATA_TIMEOUT,
            )
            if response.status_code == 429:
                await asyncio.sleep(2)
                response = await http_client.get(
                    f"{settings.TMDB_API_URL}/search/{endpoint}",
                    params=params,
                    timeout=settings.METADATA_TIMEOUT,
                )
            if response.status_code != 200:
                continue

            matches = []
            for result in response.json().get("results", []):
                candidate_year = _tmdb_result_year(result, content_type)
                if year and candidate_year != year:
                    continue
                score = max(
                    (
                        _tmdb_title_match_score(
                            title,
                            candidate,
                            content_type,
                            allow_film_number_variants,
                        )
                        for candidate in _tmdb_result_titles(result, content_type)
                    ),
                    default=0,
                )
                if score:
                    matches.append((score, result))

            if matches:
                _, selected = max(matches, key=lambda item: item[0])
                tmdb_id = selected.get("id")
                if tmdb_id:
                    await asyncio.sleep(0.05)
                    return str(tmdb_id)
        return None
    except Exception as e:
        scraper_logger.error(
            f"[{logger_name}] TMDB title lookup failed for {title!r}: "
            f"{type(e).__name__}: {e}"
        )
        return None


# ===========================
# Size Parsing
# ===========================
def size_to_bytes(value: Any, unit: str) -> Optional[int]:
    try:
        amount = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount <= 0:
        return None

    multipliers = {
        "ko": 1024,
        "kb": 1024,
        "mo": 1024 ** 2,
        "mb": 1024 ** 2,
        "go": 1024 ** 3,
        "gb": 1024 ** 3,
        "to": 1024 ** 4,
        "tb": 1024 ** 4,
    }
    multiplier = multipliers.get(unit.lower())
    return int(amount * multiplier) if multiplier else None


def size_gb_to_bytes(value: Any) -> Optional[int]:
    return size_to_bytes(value, "Go")


def parse_size_list(size_raw: str) -> List[Optional[int]]:
    if not size_raw:
        return []
    try:
        values = ast.literal_eval(size_raw)
    except (ValueError, SyntaxError):
        return []
    if not isinstance(values, (list, tuple)):
        return []
    return [size_gb_to_bytes(value) for value in values]


# ===========================
# Common Content Processing
# ===========================
async def process_content_entries(
    entries: List[Dict[str, Any]],
    alldebrid_base_url: str,
    tmdb_api_token: str,
    stats: Dict[str, int],
    stop_requested: Callable[[], bool],
    logger_name: str,
) -> None:
    for entry in entries:
        if stop_requested():
            break

        try:
            tmdb_id = entry["tmdb_id"]
            category = entry["cat"]
            content_type = "film" if category != "serie" else "serie"
            imdb_id = await resolve_imdb_id(
                tmdb_id, content_type, tmdb_api_token, logger_name
            )
            if not imdb_id:
                stats["errors"] += 1
                continue

            if category == "serie":
                res_name = entry.get("release_name") or "Unknown - Unknown"
                language, raw_quality = parse_release_info(res_name)
                quality = extract_resolution(raw_quality)
                episode_sizes = entry.get("episode_sizes", {})

                for episode, suffixes in entry.get("episodes", {}).items():
                    urls = [
                        link
                        for link in (
                            resolve_source_url(
                                suffix,
                                alldebrid_base_url,
                                logger_name,
                            )
                            for suffix in suffixes
                        )
                        if link
                    ]
                    if not urls:
                        continue

                    release_name = build_release_name(
                        entry["title"],
                        entry.get("year"),
                        entry.get("season"),
                        episode,
                        res_name,
                    )
                    result = await add_wasource_links_bulk(
                        imdb_id=imdb_id,
                        title=entry["title"],
                        release_name=release_name,
                        quality=quality,
                        language=language,
                        size=episode_sizes.get(episode),
                        season=entry.get("season"),
                        episode=episode,
                        urls=urls,
                        tmdb_id=str(tmdb_id),
                        year=entry.get("year"),
                    )
                    stats["added"] += result.get("added", 0)
                    stats["skipped"] += result.get("skipped", 0)
            else:
                for release in entry.get("releases", []):
                    if isinstance(release, dict):
                        res_name = release.get("release_name") or release.get("res")
                        suffix = release.get("url")
                        size = release.get("size")
                    else:
                        res_name, suffix, size = release

                    link = resolve_source_url(
                        suffix,
                        alldebrid_base_url,
                        logger_name,
                    )
                    if not link:
                        continue

                    language, raw_quality = parse_release_info(res_name)
                    quality = extract_resolution(raw_quality)
                    release_name = build_release_name(
                        entry["title"],
                        entry.get("year"),
                        None,
                        None,
                        res_name,
                    )
                    result = await add_wasource_links_bulk(
                        imdb_id=imdb_id,
                        title=entry["title"],
                        release_name=release_name,
                        quality=quality,
                        language=language,
                        size=size,
                        season=None,
                        episode=None,
                        urls=[link],
                        tmdb_id=str(tmdb_id),
                        year=entry.get("year"),
                    )
                    stats["added"] += result.get("added", 0)
                    stats["skipped"] += result.get("skipped", 0)
        except Exception as e:
            scraper_logger.error(
                f"[{logger_name}] Entry error ({entry.get('title', '?')}): "
                f"{type(e).__name__}: {e}"
            )
            stats["errors"] += 1
