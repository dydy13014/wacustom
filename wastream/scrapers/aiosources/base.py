import re
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
    build_display_name, normalize_size, episode_matches
)
from wastream.utils.quality import quality_sort_key

_MAGNET_HASH_RE = re.compile(r"urn:btih:([a-fA-F0-9]{32,40})")


class AIOSourcesScraper:
    """Scraper pour AIOSources — addon Stremio tiers (maintenu par Théo [TB],
    cf. memoire projet) qui agrege plusieurs trackers (C411, Tr4ker, TsukiHime,
    Nostradamus, TheOldSchool) derriere une base pre-matchee TMDB/IMDb. Contrairement
    aux trackers Torznab (C411Scraper, Tr4kerScraper...), AIOSources n'a pas de
    notion de cle par utilisateur : c'est une instance partagee, reglee une seule
    fois par l'hebergeur (comme Zilean/Nyaa), interrogee via son propre protocole
    d'addon Stremio standard (GET /stream/{type}/{id}.json) plutot que Torznab.

    Recherche par ID uniquement (pas de texte libre comme les autres sources) :
    AIOSources n'expose que /stream/{type}/{imdb_id}[:season:episode].json, donc
    sans imdb_id dans les metadonnees la source est silencieusement ignoree.
    Anime non gere ici (AIOSources route l'anime par ID MAL, absent de nos
    metadonnees Kitsu actuelles) -- a etudier separement si besoin un jour.
    """

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        if not settings.AIOSOURCES_URL:
            scraper_logger.debug("[AIOSources] URL not configured, skipping")
            return []

        imdb_id = (metadata or {}).get("imdb_id")
        if not imdb_id:
            scraper_logger.debug("[AIOSources] No IMDB id in metadata, skipping")
            return []

        # AIOSources donne a copier l'URL complete du manifeste
        # (".../manifest.json", meme convention que toute installation
        # Stremio standard) -- tolere ce format en plus d'une URL de base nue,
        # sinon le premier reflexe naturel de l'utilisateur casse la source
        # en silence (404 sur chaque recherche, aucune erreur visible cote UI).
        base_url = settings.AIOSOURCES_URL.rstrip("/")
        if base_url.lower().endswith("/manifest.json"):
            base_url = base_url[: -len("/manifest.json")]

        is_series = bool(season and episode)
        stremio_type = "series" if is_series else "movie"
        stream_id = f"{imdb_id}:{int(season)}:{int(episode)}" if is_series else imdb_id

        try:
            response = await http_client.get(
                f"{base_url}/stream/{stremio_type}/{stream_id}.json",
                headers={"Accept": "application/json"}
            )

            if response.status_code != 200:
                scraper_logger.error(f"[AIOSources] Search failed: HTTP {response.status_code}")
                return []

            streams = response.json().get("streams", []) or []
            results = []

            for stream in streams:
                infohash = (stream.get("infoHash") or "").lower() or None
                stream_url = stream.get("url")

                if not infohash and stream_url and stream_url.startswith("magnet:"):
                    match = _MAGNET_HASH_RE.search(stream_url)
                    if match:
                        infohash = match.group(1).lower()

                # AllDebrid/TorBox necessitent un infohash pour verifier le cache --
                # un lien direct (url http/https) sans infohash n'est pas exploitable
                # par le pipeline debrid commun, contrairement a WASource/Wawacity qui
                # passent par leur propre chemin de resolution d'hebergeur.
                if not infohash:
                    continue

                behavior_hints = stream.get("behaviorHints") or {}
                raw_title = behavior_hints.get("filename") or stream.get("title") or stream.get("name") or ""
                if not raw_title:
                    continue

                if is_series and episode_matches(raw_title, season, episode) is False:
                    scraper_logger.debug(f"[AIOSources] Skip (episode mismatch S{season}E{episode}): {raw_title}")
                    continue

                size_bytes = behavior_hints.get("videoSize") or 0
                try:
                    size_bytes = int(size_bytes)
                except (ValueError, TypeError):
                    size_bytes = 0
                size_str = normalize_size(f"{size_bytes / (1024 ** 3):.2f} GB") if size_bytes > 0 else "Unknown"

                tokens = tokenize_filename(raw_title)
                quality = extract_quality_from_tokens(tokens)
                language = extract_language_from_tokens(tokens)
                raw_language = extract_raw_language_from_tokens(tokens)

                display_name = build_display_name(
                    title=title, year=year, language=language, quality=quality,
                    season=season, episode=episode, raw_language=raw_language
                )
                if not display_name:
                    display_name = raw_title

                result = {
                    "link": f"magnet:?xt=urn:btih:{infohash}",
                    "infohash": infohash,
                    "quality": quality,
                    "language": language,
                    "raw_language": raw_language,
                    "source": "AIOSources",
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
            scraper_logger.debug(f"[AIOSources] Found {len(results)} torrents")
            return results

        except Exception as e:
            scraper_logger.error(f"[AIOSources] Error searching: {e}")
            return []


aiosources_scraper = AIOSourcesScraper()
