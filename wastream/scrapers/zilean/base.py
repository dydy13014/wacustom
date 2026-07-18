from typing import List, Dict, Optional

from wastream.config.settings import settings
from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger
from wastream.utils.helpers import (
    tokenize_filename, extract_quality_from_tokens,
    extract_language_from_tokens, extract_raw_language_from_tokens,
    build_display_name, normalize_size, episode_matches
)
from wastream.utils.quality import quality_sort_key

MAX_RESULTS = 100


class ZileanScraper:
    """Scraper pour Zilean — index des hashlists DMM (Debrid Media Manager).
    API JSON sans authentification :
      - films  : POST /dmm/search   {"queryText": "..."}
      - séries : GET  /dmm/filtered?Query=...&Season=...&Episode=...
    Chaque entrée contient info_hash + raw_title + size (octets) + flag trash."""

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        if not settings.ZILEAN_URL:
            scraper_logger.debug("[Zilean] URL not configured, skipping")
            return []

        base_url = settings.ZILEAN_URL.rstrip("/")

        try:
            if season and episode:
                response = await http_client.get(
                    f"{base_url}/dmm/filtered",
                    params={"Query": title, "Season": int(season), "Episode": int(episode)},
                    headers={"Accept": "application/json"}
                )
            else:
                response = await http_client.post(
                    f"{base_url}/dmm/search",
                    json={"queryText": f"{title} {year}" if year else title},
                    headers={"Accept": "application/json"}
                )

            if response.status_code != 200:
                scraper_logger.error(f"[Zilean] Search failed: HTTP {response.status_code}")
                return []

            entries = response.json()
            results = []

            for entry in entries[:MAX_RESULTS]:
                raw_title = entry.get("raw_title")
                infohash = (entry.get("info_hash") or "").lower()
                if not raw_title or not infohash:
                    continue

                # Écarter ce que Zilean marque lui-même comme déchet
                if entry.get("trash"):
                    continue

                # Films : filtrer par année quand Zilean la connaît (recherche
                # full-text laxiste — "Dune" remonte aussi le film de 1984)
                if year and not (season and episode):
                    entry_year = entry.get("year")
                    if entry_year:
                        try:
                            if abs(int(entry_year) - int(year)) > 1:
                                continue
                        except (ValueError, TypeError):
                            pass

                # Séries : re-validation locale (multi-formats, packs, plages)
                if season and episode:
                    if episode_matches(raw_title, season, episode) is False:
                        continue

                try:
                    size_bytes = int(entry.get("size", 0) or 0)
                except (ValueError, TypeError):
                    size_bytes = 0
                if size_bytes > 0:
                    size_str = normalize_size(f"{size_bytes / (1024 ** 3):.2f} GB")
                else:
                    size_str = "Unknown"

                tokens = tokenize_filename(raw_title)
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
                    display_name = raw_title

                result = {
                    "link": f"magnet:?xt=urn:btih:{infohash}",
                    "infohash": infohash,
                    "quality": quality,
                    "language": language,
                    "raw_language": raw_language,
                    "source": "Zilean",
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
            scraper_logger.debug(f"[Zilean] Found {len(results)} torrents")
            return results

        except Exception as e:
            scraper_logger.error(f"[Zilean] Error searching: {e}")
            return []


zilean_scraper = ZileanScraper()
