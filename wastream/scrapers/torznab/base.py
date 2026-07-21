import xml.etree.ElementTree as ET
import urllib.parse
import re
from typing import List, Dict, Optional, Tuple

from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger
from wastream.utils.helpers import (
    tokenize_filename, extract_quality_from_tokens,
    extract_language_from_tokens, extract_raw_language_from_tokens,
    build_display_name, normalize_size, normalize_tracker_url,
    episode_matches
)
from wastream.utils.quality import quality_sort_key

# Mots trop courants pour être discriminants (FR + EN) — exclus du calcul de
# pertinence pour ne pas gonfler artificiellement le score de correspondance
# sur des titres qui les contiennent par coïncidence.
_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "a", "au", "aux",
    "en", "sur", "dans", "avec", "pour", "the", "a", "an", "of", "and",
}
_MIN_RELEVANCE_RATIO = 0.6


def _is_relevant(title: str, release_name: str) -> bool:
    """Certains trackers Torznab (constaté sur C411, 2026-07-20) retombent
    silencieusement sur des résultats "tendance" sans rapport quand la
    recherche ne matche rien en interne — jamais une liste vide, donc rien
    ne signale l'échec. Cas réel : la recherche "comme les grands S01E01"
    a renvoyé des animes isekai totalement étrangers. On revalide localement
    que le titre du résultat contient l'essentiel des mots du titre demandé."""
    title_tokens = {t for t in tokenize_filename(title) if len(t) > 1 and t not in _STOPWORDS}
    if not title_tokens:
        return True
    release_tokens = set(tokenize_filename(release_name)) - _STOPWORDS
    matched = len(title_tokens & release_tokens)
    return (matched / len(title_tokens)) >= _MIN_RELEVANCE_RATIO


class BaseTorznab:
    def __init__(self, name: str, url: str, api_key: str, auth_type: str = "query"):
        self.name = name
        self.url = normalize_tracker_url(name, url)
        self.api_key = api_key
        self.auth_type = auth_type  # "query" or "header"

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        if not self.api_key or not self.url:
            scraper_logger.debug(f"[{self.name}] URL or API key not configured, skipping")
            return []

        # 1. Formulate search query
        search_query = title
        if season and episode:
            # Saison seule dans la requête, pas l'épisode : certains trackers
            # (constaté sur C411, 2026-07-20) ne remontent JAMAIS un pack de
            # saison ("...S03.VFF...") si la requête contient "S03E01" — leur
            # moteur de recherche fait un matching textuel qui ne trouve pas
            # "E01" dans un nom de pack, donc le pack n'apparaît même pas dans
            # les résultats. Le filtrage client (episode_matches, plus bas)
            # sait déjà repérer un pack de la bonne saison ou l'épisode exact
            # dans un ensemble de résultats plus large — inutile de
            # sur-préciser la requête envoyée au tracker.
            try:
                search_query += f" S{int(season):02d}"
            except (ValueError, TypeError):
                search_query += f" S{season}"
        elif year:
            search_query += f" {year}"

        # 2. Build URL and headers
        headers = {
            "User-Agent": "WAStream/1.0"
        }
        if self.auth_type == "header":
            headers["Authorization"] = f"Bearer {self.api_key}"
            url = f"{self.url}?t=search&q={urllib.parse.quote(search_query)}"
        else:
            url = f"{self.url}?t=search&q={urllib.parse.quote(search_query)}&apikey={self.api_key}"

        scraper_logger.debug(f"[{self.name}] Querying: {search_query}")

        try:
            response = await http_client.get(url, headers=headers)
            if response.status_code != 200:
                scraper_logger.error(f"[{self.name}] Torznab search failed: HTTP {response.status_code}")
                return []

            # 3. Parse XML response
            root = ET.fromstring(response.content)
            results = []

            for item in root.findall(".//item"):
                title_node = item.find("title")
                if title_node is None or not title_node.text:
                    continue
                release_name = title_node.text

                if not _is_relevant(title, release_name):
                    scraper_logger.debug(
                        f"[{self.name}] Skip (hors sujet, tracker en repli tendance ?): {release_name}"
                    )
                    continue

                # Parse custom Torznab attributes
                size = 0
                infohash = None
                for attr in item.findall(".//{http://torznab.com/schemas/2015/feed}attr"):
                    attr_name = attr.attrib.get("name")
                    attr_value = attr.attrib.get("value")
                    if attr_name == "infohash":
                        infohash = attr_value.lower() if attr_value else None
                    elif attr_name == "size":
                        try:
                            size = int(attr_value)
                        except ValueError:
                            pass

                # Extract magnet or enclosure link
                enclosure = item.find("enclosure")
                torrent_url = None
                if enclosure is not None:
                    torrent_url = enclosure.attrib.get("url")
                if not torrent_url:
                    link_node = item.find("link")
                    if link_node is not None:
                        torrent_url = link_node.text

                # We need either an infohash or a download link
                if not infohash and not torrent_url:
                    continue

                # If we don't have infohash but have magnet URL, extract hash from magnet
                if not infohash and torrent_url and torrent_url.startswith("magnet:"):
                    match = re.search(r"urn:btih:([a-fA-F0-9]{32,40})", torrent_url)
                    if match:
                        infohash = match.group(1).lower()

                if not infohash:
                    # Some trackers might only provide torrent URL, but TorBox / debrid check cached requires infohash.
                    # In case infohash is missing, we try to see if name/link contains it or skip
                    continue

                # Normalize size
                if size > 0:
                    size_gb = size / (1024 ** 3)
                    size_str = f"{size_gb:.2f} GB"
                else:
                    size_str = "Unknown"
                size_str = normalize_size(size_str)

                # Tokenize and parse release name
                tokens = tokenize_filename(release_name)
                quality = extract_quality_from_tokens(tokens)
                language = extract_language_from_tokens(tokens)
                raw_language = extract_raw_language_from_tokens(tokens)

                # --- Episode validation ---
                # When we're searching for a specific episode, verify the release
                # name actually contains that episode (or is a season pack).
                # Trackers like C411/UNIT3D do fuzzy full-text search and may
                # return neighbouring episodes (E5, E6 …) alongside E4.
                if season and episode:
                    # episode_matches gère SxxExx, multi-ep, plages, 2x04,
                    # season packs (S02/Saison 2/Season 2). None = pas d'info → on garde.
                    if episode_matches(release_name, season, episode) is False:
                        scraper_logger.debug(
                            f"[{self.name}] Skip (episode mismatch S{season}E{episode}): {release_name}"
                        )
                        continue
                # --------------------------

                # Format display name
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

                # Construct magnet link if we have infohash
                magnet_link = f"magnet:?xt=urn:btih:{infohash}"

                result = {
                    "link": magnet_link,
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
            scraper_logger.error(f"[{self.name}] Error searching Torznab: {e}")
            return []
