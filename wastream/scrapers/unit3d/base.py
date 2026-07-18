from typing import List, Dict, Optional

from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger
from wastream.utils.helpers import (
    tokenize_filename, extract_quality_from_tokens,
    extract_language_from_tokens, extract_raw_language_from_tokens,
    build_display_name, normalize_size, episode_matches
)
from wastream.utils.quality import quality_sort_key


class BaseUnit3d:
    """Scraper pour les trackers UNIT3D (Gemini, Génération-Free, …) via leur
    API JSON native /api/torrents/filter — contrairement aux trackers Torznab
    (XML), UNIT3D expose du JSON et supporte le filtrage saison/épisode côté
    serveur."""

    def __init__(self, name: str, url: str, api_token: str):
        self.name = name
        self.url = url.rstrip("/").removesuffix("/api")
        self.api_token = api_token

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        if not self.api_token or not self.url:
            scraper_logger.debug(f"[{self.name}] URL or API token not configured, skipping")
            return []

        params = {
            "api_token": self.api_token,
            "name": title,
            "perPage": 50,
        }
        if season and episode:
            try:
                params["seasonNumber"] = int(season)
                params["episodeNumber"] = int(episode)
            except (ValueError, TypeError):
                pass
        elif year:
            params["name"] = f"{title} {year}"

        scraper_logger.debug(f"[{self.name}] Querying UNIT3D API: {params['name']}")

        try:
            response = await http_client.get(
                f"{self.url}/api/torrents/filter",
                params=params,
                headers={"User-Agent": "WAStream/1.0", "Accept": "application/json"}
            )
            if response.status_code != 200:
                scraper_logger.error(f"[{self.name}] UNIT3D search failed: HTTP {response.status_code}")
                return []

            data = response.json()
            results = []

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                release_name = attrs.get("name")
                infohash = (attrs.get("info_hash") or "").lower()
                if not release_name or not infohash:
                    continue

                # Validation d'épisode côté client (le filtre serveur peut être laxiste)
                if season and episode:
                    if episode_matches(release_name, season, episode) is False:
                        scraper_logger.debug(
                            f"[{self.name}] Skip (episode mismatch S{season}E{episode}): {release_name}"
                        )
                        continue

                size_bytes = attrs.get("size", 0) or 0
                if size_bytes > 0:
                    size_str = normalize_size(f"{size_bytes / (1024 ** 3):.2f} GB")
                else:
                    size_str = "Unknown"

                tokens = tokenize_filename(release_name)
                quality = extract_quality_from_tokens(tokens)
                language = extract_language_from_tokens(tokens)
                raw_language = extract_raw_language_from_tokens(tokens)

                display_name = build_display_name(
                    title=title,
                    year=year,
                    language=language,
                    quality=quality,
                    season=season,
                    episode=episode,
                    raw_language=raw_language
                )
                if not display_name:
                    display_name = release_name

                result = {
                    "link": f"magnet:?xt=urn:btih:{infohash}",
                    "infohash": infohash,
                    "quality": quality,
                    "language": language,
                    "raw_language": raw_language,
                    "source": self.name,
                    "hoster": "Torrent",
                    "size": size_str,
                    "display_name": display_name,
                    "model_type": "torrent"
                }
                if season:
                    result["season"] = str(season)
                if episode:
                    result["episode"] = str(episode)

                results.append(result)

            results.sort(key=quality_sort_key)
            scraper_logger.debug(f"[{self.name}] Found {len(results)} torrents")
            return results

        except Exception as e:
            scraper_logger.error(f"[{self.name}] Error searching UNIT3D: {e}")
            return []
