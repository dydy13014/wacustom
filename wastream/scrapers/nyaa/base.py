import re
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional

from wastream.config.settings import settings
from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger
from wastream.utils.release_parser import tokenize_filename
from wastream.utils.quality import extract_quality_from_tokens
from wastream.utils.languages import (
    extract_language_from_tokens, extract_raw_language_from_tokens
)
from wastream.utils.helpers import (
    build_display_name, episode_matches
)
from wastream.utils.quality import quality_sort_key

MAX_RESULTS = 50
NYAA_NS = "https://nyaa.si/xmlns/nyaa"
_SIZE_RE = re.compile(r"([\d.]+)\s*(GiB|MiB|KiB|GB|MB|KB)", re.IGNORECASE)


class NyaaScraper:
    """Scraper pour Nyaa.si — tracker public anime (fansubs), sans clé API.
    Flux RSS structuré : chaque item fournit directement l'infoHash et la
    taille — pas besoin de parser du HTML ni de télécharger le .torrent."""

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None, category: str = "1_0",
                     absolute_episode: Optional[str] = None) -> List[Dict]:
        if not settings.NYAA_URL:
            scraper_logger.debug("[Nyaa] URL not configured, skipping")
            return []

        base_url = settings.NYAA_URL.rstrip("/")

        try:
            response = await http_client.get(
                f"{base_url}/",
                params={"page": "rss", "q": title, "c": category, "f": "0"},
                headers={"User-Agent": "WAStream/1.0"}
            )
            if response.status_code != 200:
                scraper_logger.error(f"[Nyaa] Search failed: HTTP {response.status_code}")
                return []

            root = ET.fromstring(response.content)
            results = []

            for item in root.findall(".//item")[:MAX_RESULTS]:
                title_node = item.find("title")
                if title_node is None or not title_node.text:
                    continue
                release_name = title_node.text

                infohash_node = item.find(f"{{{NYAA_NS}}}infoHash")
                infohash = (infohash_node.text or "").lower() if infohash_node is not None else None
                if not infohash:
                    continue

                size_node = item.find(f"{{{NYAA_NS}}}size")
                size_str = self._normalize_nyaa_size(size_node.text if size_node is not None else None)

                # Recherche full-text laxiste côté Nyaa : re-valider localement
                # que la release correspond bien à l'épisode demandé.
                #
                # strict=True : une release d'animé est quasi toujours numérotée,
                # donc un nom où l'on ne détecte AUCUN épisode est du bruit (autre
                # série, PV, bande-annonce). Sans ce mode, tout ce que les motifs
                # ne reconnaissaient pas passait — et le format dominant ici,
                # « Titre - 05 », n'était justement pas reconnu, d'où des épisodes
                # d'autres saisons dans les résultats.
                #
                # absolute_episode : Nyaa numérote souvent en continu (S02E01 se
                # nomme « 13 »), on accepte donc aussi ce numéro tant que le nom
                # ne porte pas de marqueur de saison explicite.
                if season and episode:
                    if episode_matches(release_name, season, episode,
                                       absolute_episode=absolute_episode,
                                       strict=True) is False:
                        continue

                tokens = tokenize_filename(release_name)
                quality = extract_quality_from_tokens(tokens)
                language = extract_language_from_tokens(tokens)
                raw_language = extract_raw_language_from_tokens(tokens)

                display_name = build_display_name(
                    title=title, year=year, language=language, quality=quality,
                    season=season, episode=episode, raw_language=raw_language
                )
                if not display_name:
                    display_name = release_name

                result = {
                    "link": f"magnet:?xt=urn:btih:{infohash}",
                    "infohash": infohash,
                    "quality": quality,
                    "language": language,
                    "raw_language": raw_language,
                    "source": "Nyaa",
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
            scraper_logger.debug(f"[Nyaa] Found {len(results)} torrents")
            return results

        except Exception as e:
            scraper_logger.error(f"[Nyaa] Error searching: {e}")
            return []

    @staticmethod
    def _normalize_nyaa_size(raw: Optional[str]) -> str:
        if not raw:
            return "Unknown"
        m = _SIZE_RE.match(raw.strip())
        if not m:
            return "Unknown"
        value, unit = m.group(1), m.group(2).upper().replace("IB", "B")
        return f"{value} {unit}"


nyaa_scraper = NyaaScraper()
