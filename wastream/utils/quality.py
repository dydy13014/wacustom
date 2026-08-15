import re
from typing import Dict, Any, List


# ===========================
# Quality Sort Constants
# ===========================
QUALITY_SORT_KEY_UNKNOWN = 999


# ===========================
# Available Resolutions
# ===========================
AVAILABLE_RESOLUTIONS = [
    "2160p",
    "1080p",
    "720p",
    "480p",
    "Unknown"
]


# ===========================
# Release Format Patterns
# ===========================
RELEASE_FORMAT_PATTERNS = (
    (r"\bremux\b", "REMUX"),
    (r"\bblu[-. _]?ray\b", "BluRay"),
    (r"\bbr[-. _]?rip\b", "BRRip"),
    (r"\bbd[-. _]?rip\b", "BDRip"),
    (r"\bweb[-. _]?dl\b", "WEB-DL"),
    (r"\bhdlight\b", "HDLight"),
    (r"\bweb[-. _]?rip\b", "WEBRip"),
    (r"\bhd[-. _]?rip\b", "HDRip"),
    (r"\bhd[-. _]?tv\b", "HDTV"),
    (r"\bdvd[-. _]?rip\b", "DVDRip"),
    (r"\btv[-. _]?rip\b", "TVRip"),
    (r"(?<![a-z])web(?![a-z])", "WEB-DL"),
)


# ===========================
# Resolution Extraction
# ===========================
def extract_resolution(quality: str) -> str:
    if not quality or quality == "Unknown":
        return "Unknown"

    quality_upper = str(quality).strip().upper()

    if "2160" in quality_upper or "4K" in quality_upper or "UHD" in quality_upper or "ULTRA" in quality_upper:
        return "2160p"

    elif "1080" in quality_upper or (quality_upper == "HD" and "720" not in quality_upper):
        return "1080p"

    elif "720" in quality_upper:
        return "720p"

    elif "480" in quality_upper:
        return "480p"

    else:
        return "Unknown"


# ===========================
# Quality Normalization
# ===========================
def normalize_quality(raw_quality: str) -> str:
    if not raw_quality:
        return "Unknown"

    normalized = str(raw_quality).strip()

    if normalized.upper() in ["N/A", "NULL", "UNKNOWN", "INCONNU", ""]:
        return "Unknown"

    if "ULTRA" in normalized.upper() and "HDLIGHT" in normalized.upper():
        normalized = "HDLight 2160p"

    return normalized


# ===========================
# Free-Text Quality Extraction
# ===========================
def extract_quality_from_text(text: str, fallback: str = "") -> str:
    resolution = ""
    release_type = ""

    for value in (text or "", fallback or ""):
        value_lower = value.lower()
        if not resolution:
            if ("2160" in value_lower or "4k" in value_lower or "uhd" in value_lower
                    or re.search(r"\bultra[-. _]+hd(?:light)?\b", value_lower)):
                resolution = "2160p"
            elif "1080" in value_lower:
                resolution = "1080p"
            elif "720" in value_lower:
                resolution = "720p"
            elif "480" in value_lower:
                resolution = "480p"

        if not release_type:
            for pattern, label in RELEASE_FORMAT_PATTERNS:
                if re.search(pattern, value_lower):
                    release_type = label
                    break

    if resolution and release_type:
        raw_quality = f"{release_type} {resolution}"
    else:
        raw_quality = release_type or resolution or "Unknown"

    return normalize_quality(raw_quality)


# ===========================
# Tokenized Quality Extraction
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
# Quality Sort Key
# ===========================
def quality_sort_key(item: Dict[str, Any]) -> tuple:
    quality_raw = item.get("quality", "")

    if not quality_raw or str(quality_raw).strip().upper() in ["N/A", "NULL", "UNKNOWN", "INCONNU", ""]:
        return (QUALITY_SORT_KEY_UNKNOWN, QUALITY_SORT_KEY_UNKNOWN)

    quality_upper = str(quality_raw).strip().upper()

    is_2160p = "2160" in quality_upper or "4K" in quality_upper or "UHD" in quality_upper or "ULTRA" in quality_upper
    is_1080p = "1080" in quality_upper or (quality_upper == "HD" and "720" not in quality_upper)
    is_720p = "720" in quality_upper

    if "REMUX" in quality_upper:
        release_type = 0

    elif "BLURAY" in quality_upper or "BLU-RAY" in quality_upper or "BDRIP" in quality_upper or "BRRIP" in quality_upper or "BD-RIP" in quality_upper or "BR-RIP" in quality_upper:
        release_type = 1

    # Le repli generique sur "WEB" doit exclure les DEUX ecritures de WEBRip :
    # sans tiret ET avec tiret. Sans "WEB-RIP", une release "...WEB-RIP..."
    # tombait ici et etait classee WEB-DL (type 2, premium) au lieu de type 4,
    # la branche WEBRip plus bas n'etant jamais atteinte -> un rip remontait en
    # tete de liste devant de vraies sources WEB-DL.
    elif "WEB-DL" in quality_upper or "WEBDL" in quality_upper or (
        "WEB" in quality_upper and "WEBRIP" not in quality_upper and "WEB-RIP" not in quality_upper
    ):
        release_type = 2

    elif "HDLIGHT" in quality_upper or "LIGHT" in quality_upper:
        release_type = 3

    elif "WEBRIP" in quality_upper or "WEB-RIP" in quality_upper:
        release_type = 4

    elif "HDRIP" in quality_upper or "HD-RIP" in quality_upper:
        release_type = 5

    elif "HDTV" in quality_upper or "HD-TV" in quality_upper:
        release_type = 6

    elif "DVDRIP" in quality_upper or "DVD-RIP" in quality_upper:
        release_type = 7

    elif "TVRIP" in quality_upper or "TV-RIP" in quality_upper:
        release_type = 8

    else:
        release_type = 99

    if is_2160p:
        return (0, release_type)
    elif is_1080p:
        return (1, release_type)
    elif is_720p:
        return (2, release_type)
    else:
        return (99, release_type)
