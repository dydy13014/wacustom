import json
import re
import unicodedata
from base64 import b64encode, b64decode, urlsafe_b64encode, urlsafe_b64decode
from typing import Optional, Dict, Any, List
from urllib.parse import quote, quote_plus, urlparse, parse_qs, unquote

from wastream.utils.languages import normalize_language, LANGUAGE_MAPPING
from wastream.utils.quality import normalize_quality


# ===========================
# Text Normalization
# ===========================
def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = text.lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    text = "".join(c if c.isalnum() or c.isspace() else " " for c in text)
    text = " ".join(text.split())

    return text.strip()


# ===========================
# Configuration Encoding
# ===========================
def encode_config_to_base64(config: Dict[str, Any]) -> str:
    return b64encode(json.dumps(config).encode()).decode()


def encode_playback_token(data: Dict[str, Any]) -> str:
    return urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_playback_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        return json.loads(urlsafe_b64decode(token).decode())
    except Exception:
        return None


# ===========================
# Cache Key Creation
# ===========================
def create_cache_key(cache_type: str, title: str, year: Optional[str] = None) -> str:
    cache_key = f"{cache_type}:{quote_plus(title.lower())}"
    if year:
        cache_key += f":{year}"
    return cache_key


# ===========================
# URL Formatting
# ===========================
def format_url(url: str, base_url: str) -> str:
    if not url:
        return ""

    if url.startswith("http://") or url.startswith("https://"):
        return url

    if url.startswith("/"):
        return f"{base_url}{url}"

    return f"{base_url}/{url}"


# ===========================
# URL Parameter Encoding
# ===========================
def quote_url_param(param: str) -> str:
    return quote_plus(param)


def quote_path_segment(param: str) -> str:
    return quote(param, safe="")


# ===========================
# Filename Extraction and Decoding
# ===========================
def extract_and_decode_filename(url: str) -> Optional[str]:
    try:
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        filename_encoded = query_params.get('fn', [None])[0]

        if filename_encoded:
            filename_unquoted = unquote(filename_encoded)
            decoded_filename = b64decode(filename_unquoted).decode('utf-8')
            return decoded_filename
    except Exception:
        pass
    return None


# ===========================
# Movie Info Parsing
# ===========================
def parse_movie_info(decoded_filename: str) -> Dict[str, str]:
    quality = "Unknown"
    raw_language = "Unknown"

    if "[" in decoded_filename and "]" in decoded_filename:
        start = decoded_filename.find("[")
        end = decoded_filename.find("]", start)
        if start != -1 and end != -1:
            quality = decoded_filename[start + 1:end].strip()

    if " - " in decoded_filename:
        parts = decoded_filename.split(" - ")
        if len(parts) > 1:
            raw_language = parts[1].strip()

    return {
        "quality": normalize_quality(quality),
        "language": normalize_language(raw_language),
        "raw_language": raw_language
    }


# ===========================
# Series Info Parsing
# ===========================
def parse_series_info(decoded_filename: str) -> Dict[str, str]:
    season = "1"
    episode = "1"
    quality = "Unknown"
    raw_language = "Unknown"

    season_match = re.search(r"Saison (\d+)", decoded_filename)
    if season_match:
        season = season_match.group(1)

    episode_match = re.search(r"Épisode (\d+)", decoded_filename)
    if episode_match:
        episode = episode_match.group(1)

    if "[" in decoded_filename and "]" in decoded_filename:
        start = decoded_filename.find("[")
        end = decoded_filename.find("]", start)
        if start != -1 and end != -1:
            bracket_content = decoded_filename[start + 1:end].strip()

            parts = bracket_content.split()

            if len(parts) >= 1:
                raw_language = parts[0]

                if len(parts) > 1:
                    quality_parts = parts[1:]
                    quality = " ".join(quality_parts)

    return {
        "season": season,
        "episode": episode,
        "quality": normalize_quality(quality),
        "language": normalize_language(raw_language),
        "raw_language": raw_language
    }


# ===========================
# Filename Tokenization
# ===========================
def tokenize_filename(filename: str) -> List[str]:
    name_no_ext = re.sub(r"\.\w{2,4}$", "", filename)
    tokens = re.split(r"[\.\s\-_\(\)\[\]]+", name_no_ext)
    return [t.lower() for t in tokens if t]


# ===========================
# Quality Extraction from Tokens
# ===========================
def extract_quality_from_tokens(tokens: List[str]) -> str:
    resolution = ""
    release_type = ""

    for token in tokens:
        if not resolution:
            if "2160p" in token or "4k" == token or "uhd" == token or "ultra" == token:
                resolution = "2160p"
            elif "1080p" in token or "1080" == token or "hd" == token:
                resolution = "1080p"
            elif "720p" in token or "720" == token:
                resolution = "720p"
            elif "480p" in token or "480" == token:
                resolution = "480p"

        if not release_type:
            token_upper = token.upper()
            if token_upper == "REMUX":
                release_type = "REMUX"
            elif token_upper in ("BLURAY", "BDRIP", "BRRIP"):
                release_type = "BluRay"
            elif token_upper == "WEBDL":
                release_type = "WEB-DL"
            elif token_upper == "WEBRIP":
                release_type = "WEBRip"
            elif token_upper == "HDLIGHT":
                release_type = "HDLight"
            elif token_upper == "HDRIP":
                release_type = "HDRip"
            elif token_upper == "HDTV":
                release_type = "HDTV"
            elif token_upper == "DVDRIP":
                release_type = "DVDRip"
            elif token_upper == "TVRIP":
                release_type = "TVRip"

    if resolution and release_type:
        raw_quality = f"{resolution} {release_type}"
    elif resolution:
        raw_quality = resolution
    elif release_type:
        raw_quality = release_type
    else:
        raw_quality = "Unknown"

    return normalize_quality(raw_quality)


# ===========================
# Language Extraction from Tokens
# ===========================
def extract_language_from_tokens(tokens: List[str]) -> str:
    for token in tokens:
        mapped = LANGUAGE_MAPPING.get(token)
        if mapped and mapped != "Unknown":
            return mapped
    return "Unknown"


def extract_raw_language_from_tokens(tokens: List[str]) -> str:
    for token in tokens:
        mapped = LANGUAGE_MAPPING.get(token)
        if mapped and mapped != "Unknown":
            return token.upper()
    return "Unknown"


# ===========================
# Size Normalization
# ===========================
def normalize_size(raw_size: str) -> str:
    if not raw_size:
        return "Unknown"

    normalized = str(raw_size).strip()

    if normalized.upper() in ["N/A", "NULL", "UNKNOWN", "INCONNU", ""]:
        return "Unknown"

    normalized_upper = normalized.upper()
    normalized_upper = normalized_upper.replace(",", ".")
    normalized_upper = normalized_upper.replace(" GO", " GB")
    normalized_upper = normalized_upper.replace(" MO", " MB")
    normalized_upper = normalized_upper.replace(" KO", " KB")

    return normalized_upper


# ===========================
# Size Parsing to GB
# ===========================
def parse_size_to_gb(size_str: str) -> Optional[float]:
    if not size_str or size_str == "Unknown":
        return None

    size_upper = size_str.upper().strip()

    match = re.match(r"([\d.]+)\s*(GB|MB|KB)", size_upper)
    if not match:
        return None

    try:
        value = float(match.group(1))
        unit = match.group(2)

        if unit == "GB":
            return value
        elif unit == "MB":
            return value / 1024.0
        elif unit == "KB":
            return value / (1024.0 * 1024.0)
        else:
            return None

    except (ValueError, AttributeError):
        return None


# ===========================
# Size Parsing
# ===========================
def parse_size_to_bytes(size_str: str) -> int:
    if not size_str or size_str == "Unknown":
        return 0
    try:
        parts = size_str.strip().split()
        if len(parts) != 2:
            return 0
        value = float(parts[0])
        unit = parts[1].upper()
        if unit == "GB":
            return int(value * 1024 * 1024 * 1024)
        elif unit == "MB":
            return int(value * 1024 * 1024)
        elif unit == "KB":
            return int(value * 1024)
        return 0
    except (ValueError, IndexError):
        return 0


# ===========================
# Results Deduplication and Sorting
# ===========================
def deduplicate_and_sort_results(results: list, quality_sort_key_func) -> list:
    seen_links = set()
    seen_infohashes = set()
    deduplicated = []

    for result in results:
        link_key = result.get("link", "")
        infohash = result.get("infohash", "")

        # For torrents: deduplicate by infohash so the same release from
        # multiple private trackers is only checked once against the debrid cache.
        if infohash:
            norm_hash = infohash.lower()
            if norm_hash in seen_infohashes:
                continue
            seen_infohashes.add(norm_hash)

        if link_key and link_key not in seen_links:
            seen_links.add(link_key)
            deduplicated.append(result)

    deduplicated.sort(key=quality_sort_key_func)
    return deduplicated


# ===========================
# Display Name Builder
# ===========================
def _clean_display_name(text: str) -> str:
    cleaned = re.sub(r'[^\w]+', '.', text)
    cleaned = cleaned.replace('_', '.')
    cleaned = re.sub(r'\.{2,}', '.', cleaned)
    return cleaned.strip('.')


def build_display_name(title: str, year: Optional[str] = None, language: str = "Unknown",
                       quality: str = "Unknown", season: Optional[str] = None,
                       episode: Optional[str] = None, raw_language: Optional[str] = None) -> str:
    display_name = _clean_display_name(title)

    if year and str(year) not in title:
        display_name += f".{year}"

    if season:
        if episode:
            season_padded = str(season).zfill(2)
            episode_padded = str(episode).zfill(2)
            display_name += f".S{season_padded}E{episode_padded}"
        else:
            season_padded = str(season).zfill(2)
            display_name += f".S{season_padded}"

    lang_for_display = raw_language if raw_language and raw_language != "Unknown" else language
    if lang_for_display and lang_for_display != "Unknown":
        if lang_for_display.startswith("Multi (") and lang_for_display.endswith(")"):
            langs_str = lang_for_display[7:-1]
            langs = [lang.strip() for lang in langs_str.split(",")]
            display_name += ".MULTi." + ".".join(langs)
        else:
            display_name += f".{_clean_display_name(lang_for_display)}"

    if quality and quality != "Unknown":
        display_name += f".{_clean_display_name(quality)}"

    return display_name


# ===========================
# Debrid API Key Retrieval
# ===========================
def get_debrid_api_key(config: Dict[str, Any], service_name: str) -> str:
    debrid_services = config.get("debrid_services", [])
    for entry in debrid_services:
        if entry.get("service") == service_name:
            return entry.get("api_key", "")
    return ""


# ===========================
# Debrid Services Retrieval
# ===========================
def get_debrid_services(config: Dict[str, Any]) -> list:
    return config.get("debrid_services", [])


def should_enable_full_season(config: Dict[str, Any]) -> bool:
    debrid_services = config.get("debrid_services", [])
    for service_entry in debrid_services:
        service = service_entry.get("service", "")
        if service == "torbox":
            if service_entry.get("enable_nzb", False) and service_entry.get("enable_full_season", False):
                return True
        elif service == "nzbdav":
            if service_entry.get("enable_full_season", False):
                return True
    return False


# ===========================
# Tracker URL Normalization
# ===========================
def normalize_tracker_url(name: str, url: str) -> str:
    if not url:
        return url
    url = url.strip().rstrip("/")
    if name == "YggReborn":
        if not url.endswith("/api"):
            url = f"{url}/api"
    elif name == "Tr4ker":
        if not url.endswith("/api"):
            url = f"{url}/api"
    elif name == "Torr9":
        if "api/v1/torznab" not in url:
            if url.endswith("/api"):
                url = f"{url}/v1/torznab"
            else:
                url = f"{url}/api/v1/torznab"
    elif name == "C411":
        if "api/torznab" not in url:
            if url.endswith("/api"):
                url = f"{url}/torznab"
            else:
                url = f"{url}/api/torznab"
    return url


# ===========================
# Episode Matching (partagé torznab / debrid)
# ===========================
# Tous les formats courants de noms de release :
#   S02E04, S2E4, S02.E04, s02 e04        → _EP_SXEX_RE
#   S02E01-E04, S02E01-04 (plages)        → _EP_RANGE_RE
#   2x04, 02x04                            → _EP_ALT_RE
#   S02 seul, Saison 2, Season 2 (packs)   → _EP_SEASON_RE
_EP_SXEX_RE = re.compile(r'[Ss](\d{1,2})((?:[.\s_-]?[Ee]\d{1,3})+)')
_EP_NUM_RE = re.compile(r'[Ee](\d{1,3})')
_EP_RANGE_RE = re.compile(r'[Ss](\d{1,2})[.\s_-]?[Ee](\d{1,3})\s?[-–]\s?[Ee]?(\d{1,3})')
_EP_ALT_RE = re.compile(r'(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)')
_EP_SEASON_RE = re.compile(
    r'[Ss](\d{1,2})(?![.\s_-]?[Ee]\d)|saison[\s._-]?(\d{1,2})|season[\s._-]?(\d{1,2})',
    re.IGNORECASE
)
_SAMPLE_RE = re.compile(r'(?:^|[^a-z])sample(?:[^a-z]|$)', re.IGNORECASE)


def is_sample_file(filename: str) -> bool:
    """Détecte les fichiers 'sample' inclus dans certaines releases."""
    return bool(_SAMPLE_RE.search(filename or ""))


def episode_matches(release_name: str, season, episode) -> Optional[bool]:
    """Vérifie si un nom de release/fichier correspond à S{season}E{episode}.

    Retourne :
      True  → épisode exact ou season pack de la bonne saison
      False → saison ou épisode différent
      None  → aucune info d'épisode détectée (à l'appelant de décider)
    """
    if not release_name or season is None or episode is None:
        return None
    try:
        req_s, req_e = int(season), int(episode)
    except (ValueError, TypeError):
        return None

    # 1. Plages d'épisodes : S02E01-E04 → OK si l'épisode demandé est dedans
    for m in _EP_RANGE_RE.finditer(release_name):
        s, e_start, e_end = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if e_start > e_end:  # plage aberrante → ignorer
            continue
        if s == req_s and e_start <= req_e <= e_end:
            return True

    # 2. Épisodes explicites SxxExx, y compris multi-épisodes S02E03E04
    sxex_pairs = []
    for m in _EP_SXEX_RE.finditer(release_name):
        s = int(m.group(1))
        for e_m in _EP_NUM_RE.finditer(m.group(2)):
            sxex_pairs.append((s, int(e_m.group(1))))
    if sxex_pairs:
        if (req_s, req_e) in sxex_pairs:
            return True
        return False  # des épisodes sont listés mais pas le nôtre

    # 3. Format alternatif 2x04
    alt_pairs = [(int(m.group(1)), int(m.group(2))) for m in _EP_ALT_RE.finditer(release_name)]
    if alt_pairs:
        if (req_s, req_e) in alt_pairs:
            return True
        return False

    # 4. Saison seule (S02 / Saison 2 / Season 2) → season pack
    seasons = []
    for m in _EP_SEASON_RE.finditer(release_name):
        g = next(g for g in m.groups() if g is not None)
        seasons.append(int(g))
    if seasons:
        return req_s in seasons

    # 5. Aucune info détectée
    return None


def select_episode_file(files: List[Dict], season, episode,
                        name_key: str = "filename", size_key: str = "size") -> Optional[Dict]:
    """Sélectionne le meilleur fichier vidéo d'une liste (season pack ou single).

    Stratégie : exclut les samples, privilégie le match d'épisode exact,
    sinon le plus gros fichier vidéo, sinon le plus gros fichier tout court.
    """
    video_extensions = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.webm')

    if not files:
        return None

    candidates = [f for f in files if not is_sample_file(f.get(name_key, ""))] or list(files)
    videos = [f for f in candidates if f.get(name_key, "").lower().endswith(video_extensions)] or candidates

    if season is not None and episode is not None and len(videos) > 1:
        matching = [f for f in videos if episode_matches(f.get(name_key, ""), season, episode) is True]
        if matching:
            return max(matching, key=lambda f: f.get(size_key, 0) or 0)

    return max(videos, key=lambda f: f.get(size_key, 0) or 0)
