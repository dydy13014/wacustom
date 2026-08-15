import hashlib
import hmac
import json
import re
import unicodedata
from base64 import b64encode, b64decode, urlsafe_b64encode, urlsafe_b64decode
from typing import Optional, Dict, Any, List
from urllib.parse import quote, quote_plus, urlparse, parse_qs, unquote

from wastream.utils.languages import normalize_language, LANGUAGE_MAPPING
from wastream.utils.quality import normalize_quality
from wastream.utils.urls import canonicalize_url


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


# Longueur de la signature tronquée, en caractères hexadécimaux. 128 bits :
# largement hors de portée d'une falsification, et bien plus court que les 64
# caractères d'un SHA-256 complet — le jeton finit dans une URL, sa taille est
# déjà plafonnée par RESILIENT_TOKEN_MAX_BYTES.
_SIGNATURE_LEN = 32


def _signer(charge: str) -> str:
    from wastream.config.settings import settings   # import tardif : évite un cycle
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), charge.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:_SIGNATURE_LEN]


def encode_playback_token(data: Dict[str, Any]) -> str:
    """Jeton signé, au format `<charge base64>.<signature>`.

    Sans signature, le contenu du jeton — le lien à débrider, et jusqu'à une
    configuration complète avec sa clé debrid — était entièrement forgeable par
    quiconque savait construire du base64.
    """
    charge = urlsafe_b64encode(
        json.dumps(data, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return f"{charge}.{_signer(charge)}"


def decode_playback_token(token: str) -> Optional[Dict[str, Any]]:
    """Renvoie None si la signature est absente, invalide, ou si le contenu est
    illisible. Un jeton non signé (ancien format) est donc rejeté."""
    try:
        charge, _, signature = token.rpartition(".")
        if not charge or not signature:
            return None
        if not hmac.compare_digest(signature, _signer(charge)):
            return None

        padding = 4 - len(charge) % 4
        if padding != 4:
            charge += "=" * padding
        return json.loads(urlsafe_b64decode(charge).decode())
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
# `LANGUAGE_MAPPING` couvre ~130 langues via des codes ISO à 2-3 lettres.
# Un token à 2 lettres a une chance élevée de coïncider avec un mot FR/EN
# courant OU une syllabe romanisée japonaise ("no", "de", "la", "en", "it",
# "na", "wa", "ka", "ta"...) sans le moindre rapport avec une langue. Confirmé
# en conditions réelles (2026-08-01) : "Kimi No Na Wa" matche successivement
# "no" (Norwegian) PUIS "wa" (Walloon) une fois "no" écarté — whack-a-mole
# caractéristique des titres romanisés japonais, qui contiennent presque
# toujours une syllabe de 2 lettres qui coïncide avec un code ISO quelque part
# dans une liste de 130 langues. Un token à 3 lettres est bien moins exposé,
# mais pas totalement ("cat", "fin", "ben" sont aussi des mots/prénoms usuels).
#
# Stratégie : plutôt qu'une liste noire qui s'allonge indéfiniment à chaque
# nouvelle collision découverte, un token à 2 lettres n'est accepté que s'il
# fait partie d'un petit vocabulaire scène explicitement utilisé ici (VF/VO) ;
# les langues à code 2 lettres restent détectables via leur forme longue
# ("norwegian", "german"...) ou leur code 3 lettres, non ambigus.
_SAFE_SHORT_CODES = {"vf", "vo"}
_AMBIGUOUS_3LETTER_CODES = {"cat", "fin", "ben"}


def _is_plausible_language_token(token: str) -> bool:
    if len(token) == 2:
        return token in _SAFE_SHORT_CODES
    if token in _AMBIGUOUS_3LETTER_CODES:
        return False
    return True


def extract_language_from_tokens(tokens: List[str]) -> str:
    for token in tokens:
        if not _is_plausible_language_token(token):
            continue
        mapped = LANGUAGE_MAPPING.get(token)
        if mapped and mapped != "Unknown":
            return mapped
    return "Unknown"


def extract_raw_language_from_tokens(tokens: List[str]) -> str:
    for token in tokens:
        if not _is_plausible_language_token(token):
            continue
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
        link_key = canonicalize_url(result.get("link", ""))
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
# Numéro d'épisode « nu » des releases d'animé : « Titre - 05 », « EP05 », « #12 ».
# Ce format domine sur Nyaa et n'était couvert par aucun motif ci-dessus, si bien
# que episode_matches() répondait « rien détecté » et que l'appelant conservait
# TOUTES les releases — d'où des épisodes de la mauvaise saison dans les résultats.
#
# Deux garde-fous, indispensables pour ne pas capturer les chiffres du titre
# (« 86 EIGHTY-SIX », « Mob Psycho 100 », « Gundam 00 ») :
#   - un séparateur fort AVANT le numéro : tiret, tilde, ou préfixe EP/#
#   - une assertion de suite APRÈS : tag, résolution, codec, extension, ou fin
_ANIME_BARE_EP_RE = re.compile(
    r'(?:'
    r'(?:^|[\s\-_~.]+)(?:EP?\.?\s*|#)(\d{1,4})(?:v\d+)?'   # EP05, E05, #12
    r'|'
    r'[\s_]*[-~][\s_]*(\d{1,4})(?:v\d+)?'                   # « - 05 », « ~ 12 »
    r')'
    r'(?=[\s\-_~.]*'
    r'(?:\(|\[|1080p|720p|480p|2160p|x264|x265|h264|h265|hevc|avc'
    r'|multi|vostfr|sub|dub|end|fin|batch|complete|\d+bit|\.mkv|\.mp4)'
    r'|$)',
    re.IGNORECASE
)

# Marqueur de saison explicite dans le nom : interdit alors de comparer au
# numéro absolu (« S02 - 13 » veut dire l'épisode 13 DE la saison 2, pas le 13e
# de la série).
_EXPLICIT_SEASON_RE = re.compile(
    r'\b(?:S\d{1,2}|Season\s*\d|Saison\s*\d|\d+(?:st|nd|rd|th)\s*Season)\b',
    re.IGNORECASE
)

_SAMPLE_RE = re.compile(r'(?:^|[^a-z])sample(?:[^a-z]|$)', re.IGNORECASE)


def is_sample_file(filename: str) -> bool:
    """Détecte les fichiers 'sample' inclus dans certaines releases."""
    return bool(_SAMPLE_RE.search(filename or ""))


def episode_matches(release_name: str, season, episode,
                    absolute_episode=None, strict: bool = False) -> Optional[bool]:
    """Vérifie si un nom de release/fichier correspond à S{season}E{episode}.

    Retourne :
      True  → épisode exact ou season pack de la bonne saison
      False → saison ou épisode différent
      None  → aucune info d'épisode détectée (à l'appelant de décider)

    Paramètres optionnels, sans effet s'ils ne sont pas fournis — le
    comportement par défaut est donc STRICTEMENT identique à l'existant,
    pour ne rien changer aux autres sources ni aux instances tierces :

      absolute_episode → numérotation continue toutes saisons confondues.
          Les releases d'animé numérotent souvent ainsi (S02E01 = « 13 »).
          Comparé en plus du numéro relatif, mais seulement quand le nom ne
          porte aucun marqueur de saison explicite.
      strict → quand aucune information d'épisode n'est détectée, renvoyer
          False (écarter) au lieu de None (laisser l'appelant décider).
          Réservé aux sources dont la recherche est laxiste, comme Nyaa.
    """
    if not release_name or season is None or episode is None:
        return None
    try:
        req_s, req_e = int(season), int(episode)
        req_abs = int(absolute_episode) if absolute_episode is not None else None
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

    # 5. Numéro « nu » des releases d'animé (« Titre - 05 », « EP05 », « #12 »).
    #    Ne s'applique qu'aux noms sans aucun marqueur de saison — ceux-ci sont
    #    déjà traités au point 4 (et un « S02 » interdirait la comparaison au
    #    numéro absolu).
    bare = []
    for m in _ANIME_BARE_EP_RE.finditer(release_name):
        g = next((x for x in m.groups() if x is not None), None)
        if g is not None:
            bare.append(int(g))
    if bare:
        attendus = {req_e}
        if req_abs is not None and not _EXPLICIT_SEASON_RE.search(release_name):
            attendus.add(req_abs)
        if attendus & set(bare):
            return True
        return False  # un numéro est bien présent, mais ce n'est pas le nôtre

    # 6. Aucune info détectée
    return False if strict else None


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
