import asyncio
import base64
import html
import json
import re
import time
import zlib
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import PBKDF2

from selectolax.parser import HTMLParser

from wastream.config.settings import settings
from wastream.services.scraper_importer import (
    ALLDEBRID_DEFAULT_BASE_URL,
    process_content_entries,
    resolve_tmdb_id,
    size_to_bytes,
)
from wastream.utils.http_client import http_client
from wastream.utils.helpers import normalize_text
from wastream.utils.languages import RELEASE_LANGUAGE_MARKERS
from wastream.utils.logger import scraper_logger
from wastream.utils.urls import canonicalize_url


# ===========================
# Constants
# ===========================
IDRIX_USER_AGENT = "Mozilla/5.0 (compatible; WAStream/1.0)"

RENTRY_HOSTS = {"rentry.co", "www.rentry.co"}
TEXTUP_HOSTS = {"textup.fr", "www.textup.fr"}
PRIVATEBIN_HOSTS = {"bin.idrix.fr", "www.bin.idrix.fr"}

ONEFICHIER_URL_RE = re.compile(
    r"https?://(?:[a-z0-9.-]+\.)?1fichier\.com/[^\s\"<>]+",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
EPISODE_RE = re.compile(r"\bS(\d{1,2})\s*E(\d{1,3})\b", re.IGNORECASE)
SIZE_RE = re.compile(
    r"~?\s*([\d.,]+)\s*(Ko|Mo|Go|To|KB|MB|GB|TB)"
    r"(?:\s+par\s+[eé]pisode)?",
    re.IGNORECASE,
)
FILE_EXTENSION_RE = re.compile(
    r"\.(?:mkv|mka|mp4|avi|m4v|mov|ts|webm)\b",
    re.IGNORECASE,
)
LANGUAGE_MARKERS = tuple(dict.fromkeys((*RELEASE_LANGUAGE_MARKERS, "VFO", "VFB", "VOST")))
LANGUAGE_MARKER_PATTERN = "|".join(re.escape(marker) for marker in LANGUAGE_MARKERS)
MARKER_RE = re.compile(
    rf"\b(?:{LANGUAGE_MARKER_PATTERN}|2160p|1080p|720p|480p|"
    r"4K(?:Light)?|UHD|Blu[-. ]?Ray|BDRip|BRRip|WEB[-. ]?DL|WEBRip|"
    r"WEB|HDLight|HDRip|HDTV|DVDRip|TVRip|x264|x265|H264|H265|HEVC|10bit)\b",
    re.IGNORECASE,
)
LANGUAGE_ALIASES = {marker: marker for marker in RELEASE_LANGUAGE_MARKERS}
LANGUAGE_ALIASES.update({
    "VFO": "VOF",
    "VFB": "VF",
    "VOST": "VO",
})
NOISE_RE = re.compile(
    r"\b(?:FINAL|FIN|COMPLET(?:E|ES)?|INTEGRALE?|E\d{1,3}|EP\d{1,3})\b",
    re.IGNORECASE,
)
BLOCKED_LABEL_RE = re.compile(
    r"\b(?:streaming|musique|music|filmographies?|signaler|rapport|"
    r"telegram|vpn|concerts?|clips?)\b",
    re.IGNORECASE,
)
CLOUDFLARE_CHALLENGE_RE = re.compile(
    r"(?:<title>\s*just\s+a\s+moment|cf-chl-|challenge-platform|"
    r"cf-turnstile|checking\s+your\s+browser\s+before\s+accessing)",
    re.IGNORECASE,
)
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
# URL Helpers
# ===========================
def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.fragment:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, "***"))
    return url


def _normalize_url(url: str, base_url: Optional[str] = None) -> Optional[str]:
    candidate = html.unescape((url or "").strip())
    if not candidate:
        return None
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    elif base_url:
        candidate = urljoin(base_url, candidate)

    normalized = canonicalize_url(candidate) or candidate
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    host = _host(normalized)
    if host in RENTRY_HOSTS:
        normalized = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, "", "")
        )
        parsed = urlsplit(normalized)
    if host not in PRIVATEBIN_HOSTS and parsed.fragment:
        normalized = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
        )
    return normalized


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def _rate_limit_host(host: str) -> str:
    if host in RENTRY_HOSTS:
        return "rentry.co"
    if host in TEXTUP_HOSTS:
        return "textup.fr"
    if host in PRIVATEBIN_HOSTS:
        return "bin.idrix.fr"
    return host


def _is_privatebin_url(url: str) -> bool:
    return _host(url) in PRIVATEBIN_HOSTS and bool(urlsplit(url).fragment)


def _is_blocked_rentry_path(path: str) -> bool:
    normalized_path = path.lower().rstrip("/") or "/"
    return any(
        (
            normalized_path == "/",
            normalized_path == "/edit",
            normalized_path.endswith("/edit"),
            normalized_path.startswith("/cdn-cgi/"),
            normalized_path.endswith("/raw"),
            "/export-page" in normalized_path,
            normalized_path.endswith("-edit"),
            normalized_path.endswith("-repost"),
        )
    )


def _is_supported_page_url(url: str) -> bool:
    host = _host(url)
    if host not in RENTRY_HOSTS | TEXTUP_HOSTS | PRIVATEBIN_HOSTS:
        return False
    if host in RENTRY_HOSTS:
        return not _is_blocked_rentry_path(urlsplit(url).path)
    return True


def _is_cloudflare_challenge(content: str) -> bool:
    return bool(CLOUDFLARE_CHALLENGE_RE.search(content[:200_000]))


# ===========================
# HTML and Text Extraction
# ===========================
def _anchor_to_text(match: re.Match) -> str:
    href = html.unescape(match.group(2))
    inner = re.sub(r"<[^>]+>", " ", match.group(3))
    inner = html.unescape(re.sub(r"\s+", " ", inner)).strip()
    if "1fichier.com" in href.lower():
        return f" {href} "
    return f" {inner} "


def _html_to_text(content: str) -> str:
    if not re.search(r"<[^>]+>", content):
        return html.unescape(content)

    preserved_blocks = {}

    def preserve_block(match: re.Match) -> str:
        key = f"__IDRIX_TEXT_BLOCK_{len(preserved_blocks)}__"
        block = match.group(1)
        block = re.sub(r"<br\s*/?>", "\n", block, flags=re.IGNORECASE)
        block = re.sub(r"<[^>]+>", " ", block)
        preserved_blocks[key] = html.unescape(block)
        return f"\n{key}\n"

    content = re.sub(
        r"<(?:textarea|pre)\b[^>]*>(.*?)</(?:textarea|pre)>",
        preserve_block,
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    content = re.sub(
        r"<a\b[^>]*\bhref\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>",
        _anchor_to_text,
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    content = re.sub(r"[\r\n]+", " ", content)
    content = re.sub(r"<script\b[^>]*>.*?</script>", " ", content, flags=re.I | re.S)
    content = re.sub(r"<style\b[^>]*>.*?</style>", " ", content, flags=re.I | re.S)
    content = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"</(?:p|div|li|tr|article|section|h[1-6])\s*>", "\n", content, flags=re.I)
    content = re.sub(r"<[^>]+>", " ", content)
    content = html.unescape(content)
    for key, block in preserved_blocks.items():
        content = content.replace(key, block)

    lines = []
    for line in content.splitlines():
        line = re.sub(r"[ \t\f\r]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _extract_page_links(content: str, base_url: str) -> List[Tuple[str, str]]:
    if not re.search(r"<a\b", content, re.IGNORECASE):
        return []

    parser = HTMLParser(content)
    links = []
    seen = set()
    for anchor in parser.css("a"):
        href = anchor.attributes.get("href")
        if not href:
            continue
        url = _normalize_url(href, base_url)
        if not url or not _is_supported_page_url(url):
            continue
        label = re.sub(r"\s+", " ", anchor.text(separator=" ", strip=True)).strip()
        key = (url, label.lower())
        if key in seen:
            continue
        seen.add(key)
        links.append((url, label))
    return links


# ===========================
# PrivateBin Decryption
# ===========================
def _base64_decode(value: str) -> bytes:
    value = value.strip()
    value += "=" * (-len(value) % 4)
    return base64.b64decode(value)


def _base58_decode(value: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for char in value:
        try:
            number = number * 58 + alphabet.index(char)
        except ValueError as exc:
            raise ValueError("invalid PrivateBin key") from exc
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + raw


def decrypt_privatebin(payload: str, fragment_key: str) -> str:
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("response is not valid PrivateBin JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("response is not a PrivateBin object")

    adata = data.get("adata")
    ciphertext_value = data.get("ct")
    if not adata:
        raise ValueError("payload is missing adata")
    if not isinstance(ciphertext_value, str) or not ciphertext_value:
        raise ValueError("payload is missing ciphertext")

    spec = adata[0] if isinstance(adata, list) else adata
    if not isinstance(spec, (list, tuple)) or len(spec) < 5:
        raise ValueError("payload contains an invalid adata specification")
    iv = _base64_decode(spec[0])
    salt = _base64_decode(spec[1])
    iterations = int(spec[2])
    key_size = int(spec[3])
    tag_size = int(spec[4])
    ciphertext = _base64_decode(ciphertext_value)

    derived_key = PBKDF2(
        _base58_decode(fragment_key),
        salt,
        dkLen=key_size // 8,
        count=iterations,
        hmac_hash_module=SHA256,
    )
    cipher = AES.new(derived_key, AES.MODE_GCM, nonce=iv, mac_len=tag_size // 8)
    cipher.update(json.dumps(adata, separators=(",", ":")).encode())
    tag_length = tag_size // 8
    plaintext = cipher.decrypt_and_verify(
        ciphertext[:-tag_length], ciphertext[-tag_length:]
    )

    try:
        plaintext = zlib.decompress(plaintext, -15)
    except zlib.error:
        pass

    decoded = plaintext.decode("utf-8", "replace")
    if decoded.lstrip().startswith("{"):
        nested = json.loads(decoded)
        if isinstance(nested, dict) and isinstance(nested.get("paste"), str):
            decoded = nested["paste"]
    return decoded


# ===========================
# Release Parsing
# ===========================
def _clean_release_text(value: str) -> str:
    value = html.unescape(value)
    value = FILE_EXTENSION_RE.sub(" ", value)
    value = re.sub(r"@@[^@]*@@", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -.:")


def _build_source_release_name(tags: str) -> str:
    tags = _clean_release_text(tags)
    tags = NOISE_RE.sub(" ", tags)
    tokens = [token.strip(".,;:()[]") for token in tags.split()]
    tokens = [token for token in tokens if token]
    languages = []
    quality = []
    for token in tokens:
        alias = LANGUAGE_ALIASES.get(token.upper())
        if alias:
            if alias not in languages:
                languages.append(alias)
        else:
            quality.append(token)

    language_part = " ".join(languages) or "Unknown"
    quality_part = " ".join(quality) or "Unknown"
    return f"{language_part} - {quality_part}"


def _extract_size_and_name(value: str) -> Tuple[str, Optional[int]]:
    matches = list(SIZE_RE.finditer(value))
    if not matches:
        return value, None
    match = matches[-1]
    size = size_to_bytes(match.group(1), match.group(2))
    name = f"{value[:match.start()]} {value[match.end():]}"
    return name.strip(" -:~"), size


def _clean_title(value: str) -> str:
    value = _clean_release_text(value)
    value = re.sub(r"\s*\((?:19\d{2}|20\d{2})\)\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip(" -.:")


def _parse_film_line(line: str) -> Optional[Dict[str, Any]]:
    url_match = ONEFICHIER_URL_RE.search(line)
    if not url_match:
        return None

    url = html.unescape(url_match.group(0)).rstrip(".,;:)]}")
    name, size = _extract_size_and_name(line[:url_match.start()])
    name = _clean_release_text(name)
    if not name:
        return None

    year_matches = list(YEAR_RE.finditer(name))
    if year_matches:
        year_match = year_matches[-1]
        year = int(year_match.group(1))
        title = _clean_title(name[:year_match.start()])
        tags = name[year_match.end():]
    else:
        marker = MARKER_RE.search(name)
        if not marker:
            return None
        year = None
        title = _clean_title(name[:marker.start()])
        tags = name[marker.start():]

    if not title or len(title) < 2:
        return None
    if EPISODE_RE.search(title):
        return None

    return {
        "title": title,
        "year": year,
        "release_name": _build_source_release_name(tags),
        "size": size,
        "url": url,
    }


def _parse_series_line(line: str) -> Optional[Dict[str, Any]]:
    url_match = ONEFICHIER_URL_RE.search(line)
    episode_match = EPISODE_RE.search(line)
    if not url_match or not episode_match:
        return None

    url = html.unescape(url_match.group(0)).rstrip(".,;:)]}")
    name, size = _extract_size_and_name(line[:url_match.start()])
    name = _clean_release_text(name)
    title = _clean_title(name[:episode_match.start()])
    tags = name[episode_match.end():]
    if not title:
        return None

    return {
        "title": title,
        "year": None,
        "season": int(episode_match.group(1)),
        "episode": int(episode_match.group(2)),
        "release_name": _build_source_release_name(tags),
        "size": size,
        "url": url,
    }


def parse_idrix_content(content: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    text = _html_to_text(content)
    films = []
    series = []
    seen_urls = set()
    pending_line = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if pending_line:
            line = f"{pending_line} {line}".strip()
            pending_line = ""
        if "1fichier.com" not in line.lower():
            if line.endswith("-"):
                pending_line = line
            continue
        record = _parse_series_line(line)
        if record:
            if record["url"] not in seen_urls:
                seen_urls.add(record["url"])
                series.append(record)
            continue

        record = _parse_film_line(line)
        if record and record["url"] not in seen_urls:
            seen_urls.add(record["url"])
            films.append(record)

    return films, series


# ===========================
# Discovery
# ===========================
def _is_allowed_child(url: str, label: str) -> bool:
    if not _is_supported_page_url(url):
        return False
    if BLOCKED_LABEL_RE.search(label or ""):
        return False

    host = _host(url)
    if host in PRIVATEBIN_HOSTS:
        return bool(urlsplit(url).fragment)
    if host in TEXTUP_HOSTS:
        return bool(label.strip())

    return bool(label.strip())


def _extract_child_urls(content: str, base_url: str) -> List[str]:
    children = []
    seen = set()
    for url, label in _extract_page_links(content, base_url):
        if not _is_allowed_child(url, label):
            continue
        if url in seen:
            continue
        seen.add(url)
        children.append(url)
    return children


def _new_request_context() -> Dict[str, Any]:
    return {
        "blocked_hosts": set(),
        "last_request_at": {},
    }


def _block_host(request_context: Optional[Dict[str, Any]], host: str) -> None:
    if request_context is not None:
        request_context.setdefault("blocked_hosts", set()).add(host)


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (retry_at - datetime.now(timezone.utc)).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            return None


async def _sleep_interruptibly(delay: float) -> bool:
    deadline = time.monotonic() + max(0.0, delay)
    while True:
        if _scraper_state["stop_requested"]:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        await asyncio.sleep(min(1.0, remaining))


async def _wait_for_request_slot(
    host: str,
    request_context: Optional[Dict[str, Any]],
) -> bool:
    if request_context is None:
        return not _scraper_state["stop_requested"]
    if host in request_context.setdefault("blocked_hosts", set()):
        return False

    interval = max(0.0, float(settings.IDRIX_SCRAPER_REQUEST_DELAY_SECONDS))
    last_request_at = request_context.setdefault("last_request_at", {})
    previous_request = last_request_at.get(host)
    if previous_request is not None:
        if not await _sleep_interruptibly(
            interval - (time.monotonic() - previous_request)
        ):
            return False
    last_request_at[host] = time.monotonic()
    return True


async def _fetch_page(
    url: str,
    request_context: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, bool]]:
    parsed = urlsplit(url)
    host = _rate_limit_host(_host(url))
    fetch_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    headers = {"User-Agent": IDRIX_USER_AGENT}
    if _is_privatebin_url(url):
        headers["X-Requested-With"] = "JSONHttpRequest"

    try:
        max_attempts = max(1, int(settings.IDRIX_SCRAPER_RETRY_MAX_ATTEMPTS))
        base_delay = max(0.0, float(settings.IDRIX_SCRAPER_RETRY_DELAY_SECONDS))
        max_retry_delay = max(
            0.0,
            float(settings.IDRIX_SCRAPER_MAX_RETRY_DELAY_SECONDS),
        )
        retryable_statuses = {429, 500, 502, 503, 504}
        for attempt in range(max_attempts):
            if not await _wait_for_request_slot(host, request_context):
                return None

            response = await http_client.get(
                fetch_url,
                headers=headers,
                timeout=30,
            )
            if response.status_code == 200:
                if _is_cloudflare_challenge(response.text):
                    _block_host(request_context, host)
                    scraper_logger.warning(
                        f"[IdrixScraper] Cloudflare challenge detected for "
                        f"{_safe_url(url)}; host skipped for the current run"
                    )
                    return None
                if _is_privatebin_url(url):
                    fragment = parsed.fragment
                    try:
                        return (
                            decrypt_privatebin(response.text, fragment),
                            False,
                        )
                    except (TypeError, ValueError) as exc:
                        scraper_logger.warning(
                            f"[IdrixScraper] Invalid PrivateBin payload for "
                            f"{_safe_url(url)}: {exc}"
                        )
                        return None
                return response.text, True

            retry_after = _parse_retry_after(
                response.headers.get("Retry-After")
            )
            if (
                response.status_code in retryable_statuses
                and retry_after is not None
                and retry_after > max_retry_delay
            ):
                _block_host(request_context, host)
                scraper_logger.warning(
                    f"[IdrixScraper] HTTP {response.status_code} for "
                    f"{_safe_url(url)} requested {retry_after:.0f}s; "
                    f"host skipped for the current run"
                )
                return None

            if (
                response.status_code not in retryable_statuses
                or attempt >= max_attempts - 1
            ):
                if response.status_code == 429:
                    _block_host(request_context, host)
                    scraper_logger.warning(
                        f"[IdrixScraper] HTTP 429 persisted for "
                        f"{_safe_url(url)}; host skipped for the current run"
                    )
                    return None
                scraper_logger.error(
                    f"[IdrixScraper] Failed to fetch {_safe_url(url)}: "
                    f"HTTP {response.status_code}"
                )
                return None

            delay = base_delay * (2 ** attempt)
            if retry_after is not None:
                delay = max(delay, retry_after)
            delay = min(delay, max_retry_delay)

            scraper_logger.warning(
                f"[IdrixScraper] HTTP {response.status_code} for {_safe_url(url)}; "
                f"retry {attempt + 1}/{max_attempts - 1} in {delay:.1f}s"
            )
            if not await _sleep_interruptibly(delay):
                return None
    except Exception as e:
        scraper_logger.error(
            f"[IdrixScraper] Fetch error for {_safe_url(url)}: {type(e).__name__}: {e}"
        )
        return None


def _merge_records(ctx: Dict[str, Any], films: Iterable[Dict[str, Any]], series: Iterable[Dict[str, Any]]) -> None:
    for record in films:
        ctx["film_records"].append(record)
    for record in series:
        ctx["series_records"].append(record)


async def _discover_and_scrape(url: str, ctx: Dict[str, Any], depth: int) -> None:
    if _scraper_state["stop_requested"]:
        return
    if url in ctx["visited"]:
        return
    if len(ctx["visited"]) >= settings.IDRIX_SCRAPER_MAX_PAGES:
        if not ctx["cap_logged"]:
            scraper_logger.warning(
                f"[IdrixScraper] Page cap ({settings.IDRIX_SCRAPER_MAX_PAGES}) reached"
            )
            ctx["cap_logged"] = True
        return
    if depth > settings.IDRIX_SCRAPER_MAX_DEPTH:
        scraper_logger.debug(f"[IdrixScraper] Max depth reached at {_safe_url(url)}")
        return

    ctx["visited"].add(url)
    fetched = await _fetch_page(url, ctx["request_context"])
    if not fetched:
        return
    content, is_html = fetched

    films, series = parse_idrix_content(content)
    if films or series:
        ctx["content_pages"] += 1
        _merge_records(ctx, films, series)
        scraper_logger.debug(
            f"[IdrixScraper] Content page ({len(films)} film, {len(series)} series release(s)): "
            f"{_safe_url(url)}"
        )

    if is_html:
        children = _extract_child_urls(content, url)
        if children:
            scraper_logger.debug(
                f"[IdrixScraper] Index page ({len(children)} child page(s)): {_safe_url(url)}"
            )
        for child in children:
            await _discover_and_scrape(child, ctx, depth + 1)


# ===========================
# Entry Grouping
# ===========================
def _group_entries(
    film_records: List[Dict[str, Any]],
    series_records: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    films: OrderedDict = OrderedDict()
    for record in film_records:
        key = (normalize_text(record["title"]), record.get("year"))
        group = films.setdefault(
            key,
            {
                "cat": "film",
                "title": record["title"],
                "year": record.get("year"),
                "releases": [],
            },
        )
        if not any(release[1] == record["url"] for release in group["releases"]):
            group["releases"].append(
                (record["release_name"], record["url"], record.get("size"))
            )

    series: OrderedDict = OrderedDict()
    for record in series_records:
        key = (
            normalize_text(record["title"]),
            record["season"],
            record["release_name"].lower(),
        )
        group = series.setdefault(
            key,
            {
                "cat": "serie",
                "title": record["title"],
                "year": record.get("year"),
                "season": record["season"],
                "release_name": record["release_name"],
                "episodes": {},
                "episode_sizes": {},
            },
        )
        episode = record["episode"]
        group["episodes"].setdefault(episode, [])
        if record["url"] not in group["episodes"][episode]:
            group["episodes"][episode].append(record["url"])
        if episode not in group["episode_sizes"]:
            group["episode_sizes"][episode] = record.get("size")

    return list(films.values()), list(series.values())


async def _resolve_entries(
    film_entries: List[Dict[str, Any]],
    series_entries: List[Dict[str, Any]],
    tmdb_api_token: str,
    stats: Dict[str, int],
) -> List[Dict[str, Any]]:
    resolved = []
    cache: Dict[Tuple[str, str, Optional[int]], Optional[str]] = {}

    for entry in film_entries:
        if _scraper_state["stop_requested"]:
            return resolved
        key = ("film", normalize_text(entry["title"]), entry.get("year"))
        if key not in cache:
            cache[key] = await resolve_tmdb_id(
                entry["title"], entry.get("year"), "film", tmdb_api_token, "IdrixScraper"
            )
        tmdb_id = cache[key]
        if not tmdb_id:
            scraper_logger.warning(
                f"[IdrixScraper] Film not found on TMDB: {entry['title']} ({entry.get('year') or '?'})"
            )
            stats["errors"] += 1
            continue
        entry["tmdb_id"] = tmdb_id
        resolved.append(entry)

    for entry in series_entries:
        if _scraper_state["stop_requested"]:
            return resolved
        key = ("serie", normalize_text(entry["title"]), entry.get("year"))
        if key not in cache:
            cache[key] = await resolve_tmdb_id(
                entry["title"], entry.get("year"), "serie", tmdb_api_token, "IdrixScraper"
            )
        tmdb_id = cache[key]
        if not tmdb_id:
            scraper_logger.warning(f"[IdrixScraper] Series not found on TMDB: {entry['title']}")
            stats["errors"] += 1
            continue
        entry["tmdb_id"] = tmdb_id
        resolved.append(entry)

    return resolved


# ===========================
# Scrape One Starting URL
# ===========================
async def scrape_idrix_url(
    url: str,
    tmdb_api_token: str,
    request_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    stats: Dict[str, int] = {"added": 0, "skipped": 0, "errors": 0, "total": 0}
    normalized = _normalize_url(url)
    if not normalized or not _is_supported_page_url(normalized):
        scraper_logger.error(f"[IdrixScraper] Unsupported starting URL: {_safe_url(url)}")
        stats["errors"] += 1
        return stats

    if request_context is None:
        request_context = _new_request_context()
    ctx: Dict[str, Any] = {
        "visited": set(),
        "cap_logged": False,
        "content_pages": 0,
        "film_records": [],
        "series_records": [],
        "request_context": request_context,
    }
    try:
        await _discover_and_scrape(normalized, ctx, 0)
        film_entries, series_entries = _group_entries(
            ctx["film_records"], ctx["series_records"]
        )
        stats["total"] = len(film_entries) + len(series_entries)
        if not stats["total"]:
            scraper_logger.warning(
                f"[IdrixScraper] No valid release found for {_safe_url(normalized)}"
            )
            return stats

        entries = await _resolve_entries(
            film_entries, series_entries, tmdb_api_token, stats
        )
        await process_content_entries(
            entries=entries,
            alldebrid_base_url=ALLDEBRID_DEFAULT_BASE_URL,
            tmdb_api_token=tmdb_api_token,
            stats=stats,
            stop_requested=lambda: _scraper_state["stop_requested"],
            logger_name="IdrixScraper",
        )
        scraper_logger.info(
            f"[IdrixScraper] {_safe_url(normalized)}: {len(ctx['visited'])} page(s) visited, "
            f"{ctx['content_pages']} content page(s), {stats['added']} link(s) added"
        )
    except Exception as e:
        scraper_logger.error(
            f"[IdrixScraper] Scrape error for {_safe_url(normalized)}: "
            f"{type(e).__name__}: {e}"
        )
        stats["errors"] += 1
    return stats


# ===========================
# Run Idrix Scraper
# ===========================
async def run_idrix_scraper() -> None:
    if _scraper_state["running"] or not settings.IDRIX_SCRAPER_URLS:
        return
    if not settings.TMDB_API_KEY:
        scraper_logger.error("[IdrixScraper] TMDB_API_KEY required")
        return

    _scraper_state["running"] = True
    _scraper_state["stop_requested"] = False
    _scraper_state["progress"] = 0
    _scraper_state["progress_total"] = len(settings.IDRIX_SCRAPER_URLS)
    scraper_logger.info(
        f"[IdrixScraper] Starting scrape of {len(settings.IDRIX_SCRAPER_URLS)} URL(s)"
    )

    total_stats = {"added": 0, "skipped": 0, "errors": 0, "total": 0}
    request_context = _new_request_context()
    stopped = False
    try:
        for index, url in enumerate(settings.IDRIX_SCRAPER_URLS):
            if _scraper_state["stop_requested"]:
                stopped = True
                break
            _scraper_state["current_url"] = _safe_url(url)
            _scraper_state["progress"] = index + 1
            stats = await scrape_idrix_url(
                url,
                settings.TMDB_API_KEY,
                request_context,
            )
            for key in total_stats:
                total_stats[key] += stats[key]
            scraper_logger.info(
                f"[IdrixScraper] {stats['added']} added, {stats['skipped']} skipped, "
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
        f"[IdrixScraper] {'Stopped' if stopped else 'Done'}: "
        f"{total_stats['added']} added, {total_stats['skipped']} skipped, "
        f"{total_stats['errors']} errors / {total_stats['total']} entries"
    )


# ===========================
# Manual Control
# ===========================
def get_idrix_scraper_status() -> Dict[str, Any]:
    return dict(_scraper_state)


def _log_manual_task_result(task) -> None:
    try:
        exception = task.exception()
    except asyncio.CancelledError:
        return
    if exception:
        scraper_logger.error(
            f"[IdrixScraper] Manual run crashed: {type(exception).__name__}: {exception}"
        )


async def trigger_idrix_scraper() -> str:
    if _scraper_state["running"]:
        return "already_running"
    if not settings.IDRIX_SCRAPER_URLS:
        return "no_urls"
    if not settings.TMDB_API_KEY:
        return "no_tmdb_key"

    global _manual_scrape_task
    _manual_scrape_task = asyncio.create_task(run_idrix_scraper())
    _manual_scrape_task.add_done_callback(_log_manual_task_result)
    return "started"


def request_stop_idrix_scraper() -> str:
    if not _scraper_state["running"]:
        return "not_running"
    _scraper_state["stop_requested"] = True
    return "stopping"


# ===========================
# Background Loop
# ===========================
async def start_idrix_scraper_loop() -> None:
    await asyncio.sleep(15)
    while True:
        try:
            await run_idrix_scraper()
        except Exception as e:
            scraper_logger.error(
                f"[IdrixScraper] Loop error: {type(e).__name__}: {e}"
            )
        await asyncio.sleep(max(1, settings.IDRIX_SCRAPER_INTERVAL))
