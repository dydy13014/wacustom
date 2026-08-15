import re
from typing import Dict

from wastream.utils.languages import extract_release_language, normalize_language
from wastream.utils.quality import extract_quality_from_text, normalize_quality

# ===========================
# Structured Release Parsing
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
        "raw_language": raw_language,
    }


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

            if parts:
                raw_language = parts[0]
                if len(parts) > 1:
                    quality = " ".join(parts[1:])

    return {
        "season": season,
        "episode": episode,
        "quality": normalize_quality(quality),
        "language": normalize_language(raw_language),
        "raw_language": raw_language,
    }


# ===========================
# Tokenized Release Parsing
# ===========================
def tokenize_filename(filename: str) -> list:
    name_no_ext = re.sub(r"\.\w{2,4}$", "", filename)
    tokens = re.split(r"[\.\s\-_\(\)\[\]]+", name_no_ext)
    return [token.lower() for token in tokens if token]


# ===========================
# Free-Text Release Parsing
# ===========================
def parse_release_info(text: str, fallback: str = "") -> Dict[str, str]:
    language_info = extract_release_language(text, fallback)
    return {
        "quality": extract_quality_from_text(text, fallback),
        "language": language_info["language"],
        "raw_language": language_info["raw_language"],
    }
