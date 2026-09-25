import asyncio
import time
import xml.etree.ElementTree as ET
import re
from typing import List, Dict, Optional, Tuple

from wastream.config.settings import settings
from wastream.services.torrent_files import remember_torrent_url
from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger
from wastream.utils.release_parser import tokenize_filename
from wastream.utils.quality import extract_quality_from_tokens
from wastream.utils.languages import (
    extract_language_from_tokens, extract_raw_language_from_tokens
)
from wastream.utils.helpers import (
    build_display_name, normalize_size, normalize_tracker_url, episode_matches, normalize_text
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


def _relevance_tokens(text: str) -> set:
    """Tokens comparables pour le calcul de pertinence. Les apostrophes sont
    fusionnées (retirées sans espace), pas remplacées par un séparateur : la
    convention scène réelle pour un titre comme "Charlie's Angels" ou
    "Ocean's Eleven" est de coller la lettre restante ("Charlies.Angels",
    "Oceans.Eleven"), pas de la séparer. `normalize_text` gère ensuite accents
    et ponctuation restante."""
    return set(normalize_text(text.replace("'", "").replace("’", "")).split())


def _is_relevant(title: str, release_name: str) -> bool:
    """Certains trackers Torznab (constaté sur C411, 2026-07-20) retombent
    silencieusement sur des résultats "tendance" sans rapport quand la
    recherche ne matche rien en interne — jamais une liste vide, donc rien
    ne signale l'échec. Cas réel : la recherche "comme les grands S01E01"
    a renvoyé des animes isekai totalement étrangers. On revalide localement
    que le titre du résultat contient l'essentiel des mots du titre demandé.

    Comparaison via `_relevance_tokens` plutôt que `tokenize_filename` : ce
    dernier ne touche ni aux apostrophes ni aux accents, donc un titre FR
    comme "Charlie's Angels" ou "Amélie" ne matchait jamais un nom de release
    qui les écrit différemment — de vrais résultats étaient donc rejetés en
    silence par ce filtre anti-pollution, sur un projet francophone où ce cas
    est courant."""
    title_tokens = {t for t in _relevance_tokens(title) if len(t) > 1 and t not in _STOPWORDS}
    if not title_tokens:
        return True
    release_tokens = _relevance_tokens(release_name) - _STOPWORDS
    matched = len(title_tokens & release_tokens)
    return (matched / len(title_tokens)) >= _MIN_RELEVANCE_RATIO


_KNOWN_ID_PARAMS = ("imdbid", "tmdbid", "tvdbid")


def _parse_caps_id_params(caps_xml: bytes) -> Tuple[List[str], List[str]]:
    """Extrait les identifiants exacts (imdbid/tmdbid/tvdbid) qu'un tracker
    annonce supporter dans sa reponse ?t=caps, pour t=movie et t=tvsearch.
    Renvoie ([] , []) si le XML est invalide ou si le mode n'est pas
    `available="yes"` -- fail-open vers la recherche texte, jamais une
    exception qui casserait le scraper."""
    try:
        root = ET.fromstring(caps_xml)
    except ET.ParseError:
        return [], []

    def extract(tag: str) -> List[str]:
        node = root.find(f".//{tag}")
        if node is None or node.attrib.get("available") != "yes":
            return []
        supported = node.attrib.get("supportedParams", "")
        # Ordre de declaration du tracker preserve (sert d'ordre de preference).
        return [p for p in supported.split(",") if p in _KNOWN_ID_PARAMS]

    return extract("movie-search"), extract("tv-search")


class _CapsCache:
    """Cache en memoire des capacites Torznab par URL de tracker. Succes mis
    en cache longtemps (les capacites d'un tracker changent rarement) ; echec/
    vide mis en cache brievement pour reessayer bientot sans matraquer un
    tracker temporairement indisponible a chaque recherche."""
    _entries: Dict[str, Tuple[float, List[str], List[str]]] = {}
    _locks: Dict[str, asyncio.Lock] = {}
    _TTL_SUCCESS = 86400
    _TTL_EMPTY = 600

    @classmethod
    async def get(cls, name: str, url: str, api_key: str, auth_type: str) -> Tuple[List[str], List[str]]:
        now = time.monotonic()
        cached = cls._entries.get(url)
        if cached and now - cached[0] < (cls._TTL_SUCCESS if (cached[1] or cached[2]) else cls._TTL_EMPTY):
            return cached[1], cached[2]

        lock = cls._locks.setdefault(url, asyncio.Lock())
        async with lock:
            cached = cls._entries.get(url)
            if cached and now - cached[0] < (cls._TTL_SUCCESS if (cached[1] or cached[2]) else cls._TTL_EMPTY):
                return cached[1], cached[2]

            movie_params, tv_params = await cls._fetch(name, url, api_key, auth_type)
            cls._entries[url] = (now, movie_params, tv_params)
            return movie_params, tv_params

    @staticmethod
    async def _fetch(name: str, url: str, api_key: str, auth_type: str) -> Tuple[List[str], List[str]]:
        headers = {"User-Agent": "WAStream/1.0"}
        params = {"t": "caps"}
        if auth_type == "header":
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            params["apikey"] = api_key
        try:
            response = await http_client.get(url, headers=headers, params=params, timeout=10)
            if response.status_code != 200:
                scraper_logger.debug(f"[{name}] caps HTTP {response.status_code}, recherche texte par defaut")
                return [], []
            movie_params, tv_params = _parse_caps_id_params(response.content)
            if movie_params or tv_params:
                scraper_logger.debug(
                    f"[{name}] caps: movie={movie_params or '-'} tv={tv_params or '-'}"
                )
            return movie_params, tv_params
        except Exception as e:
            scraper_logger.debug(f"[{name}] caps fetch failed ({type(e).__name__}), recherche texte par defaut")
            return [], []


class BaseTorznab:
    def __init__(self, name: str, url: str, api_key: str, auth_type: str = "query",
                 movie_id_params: Optional[List[str]] = None,
                 tv_id_params: Optional[List[str]] = None):
        self.name = name
        self.url = normalize_tracker_url(name, url)
        self.api_key = api_key
        self.auth_type = auth_type  # "query" or "header"
        # Override manuel optionnel. Si non fourni (None), les identifiants
        # exacts supportes par ce tracker sont auto-decouverts et mis en cache
        # via son endpoint ?t=caps (cf. _CapsCache) -- pas besoin de les
        # maintenir a la main par tracker, ni de les deviner si le tracker
        # change un jour son implementation Torznab.
        self._movie_id_params_override = movie_id_params
        self._tv_id_params_override = tv_id_params

    @staticmethod
    def _metadata_value(metadata: Dict, id_param: str) -> Optional[str]:
        key = {"imdbid": "imdb_id", "tmdbid": "tmdb_id", "tvdbid": "tvdb_id"}.get(id_param)
        return metadata.get(key) if key else None

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        if not self.api_key or not self.url:
            scraper_logger.debug(f"[{self.name}] URL or API key not configured, skipping")
            return []

        is_tv = bool(season and episode)
        metadata = metadata or {}

        if self._movie_id_params_override is not None or self._tv_id_params_override is not None:
            movie_id_params = self._movie_id_params_override or []
            tv_id_params = self._tv_id_params_override or []
        else:
            movie_id_params, tv_id_params = await _CapsCache.get(
                self.name, self.url, self.api_key, self.auth_type
            )

        id_prefs = tv_id_params if is_tv else movie_id_params
        id_param = id_value = None
        for candidate in id_prefs:
            value = self._metadata_value(metadata, candidate)
            if value:
                id_param, id_value = candidate, value
                break

        # 1. Formulate search query
        search_query = title
        if id_param:
            # Recherche par ID exact (t=movie/t=tvsearch) : season/ep passent
            # en parametres dedies, q reste un simple filet de securite pour
            # les trackers qui font quand meme un matching texte en plus de l'ID.
            mode = "tvsearch" if is_tv else "movie"
        else:
            mode = "search"
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

        # 2. Build request
        # La cle passe par `params` plutot que par une URL assemblee a la main :
        # elle ne figure ainsi dans aucune chaine que ce code manipule, donc ni
        # dans un log applicatif, ni dans un message d'exception qui embarquerait
        # cette chaine (cas de HTTPStatusError, qui expose l'URL complete). httpx
        # se charge de l'encodage — d'ou le retrait du quote() manuel, qui
        # produirait sinon un double encodage.
        headers = {
            "User-Agent": "WAStream/1.0"
        }
        # limit proche du max habituel (100) : recupere davantage de resultats
        # en un seul appel plutot que de se limiter au defaut du tracker
        # (ex. 25 sur C411) -- pas de vraie pagination multi-pages, une
        # recherche par titre precis depasse rarement 100 releases.
        params = {"t": mode, "q": search_query, "limit": "100"}
        if id_param:
            params[id_param] = str(id_value)
            if is_tv:
                # Season seul, jamais "ep" : verifie en direct sur C411 le
                # 2026-09-25, le parametre "ep" fait systematiquement remonter
                # 0 resultat (meme torrent trouve sans lui) -- identique au
                # piège deja documente plus bas pour la recherche texte libre.
                # Le filtrage client (episode_matches) se charge de retrouver
                # le bon episode dans le pack de saison remonte.
                params["season"] = str(season)
        if self.auth_type == "header":
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            params["apikey"] = self.api_key

        if id_param:
            scraper_logger.debug(f"[{self.name}] Querying by {id_param}={id_value} (t={mode})")
        else:
            scraper_logger.debug(f"[{self.name}] Querying: {search_query}")

        try:
            response = await http_client.get(self.url, headers=headers, params=params)
            # Un 5xx isole est frequent sur ces trackers et coutait toute la
            # source pour la recherche en cours. Une seule seconde chance,
            # uniquement sur erreur serveur : un 4xx (cle invalide, quota)
            # ne se repare pas en reessayant et ne doit pas doubler la charge.
            if 500 <= response.status_code < 600:
                scraper_logger.debug(
                    f"[{self.name}] HTTP {response.status_code}, nouvelle tentative dans {settings.TORZNAB_RETRY_DELAY}s"
                )
                await asyncio.sleep(settings.TORZNAB_RETRY_DELAY)
                response = await http_client.get(self.url, headers=headers, params=params)

            if response.status_code != 200:
                scraper_logger.error(f"[{self.name}] Torznab search failed: HTTP {response.status_code}")
                return []

            # 3. Parse XML response
            root = ET.fromstring(response.content)

            # La spec Torznab autorise un HTTP 200 avec un <error> a la racine
            # au lieu du <channel> habituel (cle invalide, parametre incorrect,
            # rate limit...) -- sans ce controle, une clé API expirée remonte
            # silencieusement comme "0 resultat", indiscernable d'un titre
            # reellement absent du tracker.
            error_node = root if root.tag == "error" else root.find(".//error")
            if error_node is not None:
                scraper_logger.error(
                    f"[{self.name}] Torznab error {error_node.attrib.get('code', '?')}: "
                    f"{error_node.attrib.get('description', 'no description')}"
                )
                return []

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
                seeders = None
                peers = None
                freeleech = False
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
                    elif attr_name == "seeders":
                        try:
                            seeders = int(attr_value)
                        except (ValueError, TypeError):
                            pass
                    elif attr_name == "peers":
                        try:
                            peers = int(attr_value)
                        except (ValueError, TypeError):
                            pass
                    elif attr_name == "downloadvolumefactor":
                        # 0.0 = freeleech (le telechargement ne compte pas dans
                        # le ratio du tracker prive) -- utile pour prioriser un
                        # torrent equivalent sans risquer le ratio de l'utilisateur.
                        try:
                            freeleech = float(attr_value) == 0.0
                        except (ValueError, TypeError):
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

                # Le resultat ne porte qu'un magnet nu ; le vrai .torrent reste
                # cote serveur pour la lecture hors cache (cf. torrent_files).
                if torrent_url and not torrent_url.startswith("magnet:"):
                    remember_torrent_url(infohash, self.name, torrent_url)
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
                    "model_type": "torrent",
                    "freeleech": freeleech,
                }
                if seeders is not None:
                    result["seeders"] = seeders
                if peers is not None:
                    result["peers"] = peers

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
