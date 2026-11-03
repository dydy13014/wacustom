"""Source Viewio (addon.viewio.me) : flux deja verifies en cache debrid.

Meme principe que Lumio : on interroge l'endpoint Stremio de l'installation
Viewio de l'hebergeur, et on relaie tels quels les flux qu'il sait lire. Chaque
flux est un lien opaque `/playback/<jeton>` gere par Viewio, qui redirige
lui-meme vers le lien direct debrid. Il n'y a donc ni infohash a verifier ni
etat de cache a deviner : `behaviorHints.cached` dit deja que c'est jouable.

**Chacun apporte son propre lien de manifest** (`VIEWIO_MANIFEST_URL`, vide par
defaut = source desactivee). Ce lien contient un jeton secret propre a
l'installation Viewio de l'hebergeur : a traiter comme une cle de tracker.

Ce que Viewio renvoie pour un film (observe le 2026-11-02) : de 1 a quelques
dizaines de flux `[AllDebrid ⚡] Viewio <resolution>`, avec dans
`behaviorHints` : `cached`, `filename` (liens directs) ou `folderName`
(torrents), `videoSize`, `indexer` et un `bingeGroup`
`viewio|opaque|viewio%7C<torrent|ddl>%7C<resolution>%7C<langue>%7C<indexeur>`.
Les langues ne sont pas filtrees a la source : on y trouve aussi de l'anglais,
du portugais ou de l'hindi, que les filtres de Wacustom ecartent ensuite.

Quota inconnu (addon tout recent) : sur un refus 429, on met la source en
pause et la pause est ecrite sur disque pour survivre a un redemarrage, comme
pour Lumio.
"""
import os
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

from wastream.config.settings import settings
from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger
from wastream.utils.release_parser import tokenize_filename
from wastream.utils.quality import extract_quality_from_tokens, quality_sort_key
from wastream.utils.languages import (
    extract_language_from_tokens, extract_raw_language_from_tokens
)
from wastream.utils.helpers import normalize_size, episode_matches

# Etiquette portee par les resultats, telle qu'affichee a l'utilisateur.
SOURCE_LABEL = "Viewio"

_PLAY_PATH = "/playback/"
_LANG_HINT = {
    "fr": "FRENCH", "vf": "FRENCH", "vff": "TRUEFRENCH", "vfq": "FRENCH",
    "vostfr": "VOSTFR", "multi": "MULTI", "vo": "VO", "en": "ENGLISH",
}

_RATE_LIMIT_PAUSE_S = settings.VIEWIO_RATE_LIMIT_PAUSE
_PAUSE_FILE = Path(os.environ.get("VIEWIO_PAUSE_FILE", "/app/data/viewio_pause"))
# Repli memoire si /app/data n'est pas inscriptible (voir lumio/base.py).
_pause_until_mem = 0.0


def _paused_for() -> int:
    """Secondes restantes de pause quota, 0 si on peut interroger."""
    restant = int(_pause_until_mem - time.time())
    try:
        depuis_disque = int(float(_PAUSE_FILE.read_text().strip()) - time.time())
        restant = max(restant, depuis_disque)
    except (OSError, ValueError):
        pass
    return max(0, restant)


def _start_pause() -> None:
    global _pause_until_mem
    _pause_until_mem = time.time() + _RATE_LIMIT_PAUSE_S
    try:
        _PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PAUSE_FILE.write_text(str(_pause_until_mem))
    except OSError as exc:
        scraper_logger.warning(
            f"[Viewio] pause non persistee ({exc}) - repli en memoire, "
            "elle sera perdue au redemarrage"
        )


def _manifest_base() -> Optional[str]:
    """URL de l'installation sans le `/manifest.json` final (tolere les deux)."""
    url = (settings.VIEWIO_MANIFEST_URL or "").strip().rstrip("/")
    if not url:
        return None
    if url.lower().endswith("/manifest.json"):
        url = url[: -len("/manifest.json")]
    return url


def is_play_link(link: str) -> bool:
    """Vrai pour un lien de lecture Viewio (meme hote que le manifest configure)."""
    base = _manifest_base()
    if not base or not isinstance(link, str):
        return False
    parsed = urlparse(link)
    return parsed.netloc == urlparse(base).netloc and parsed.path.startswith(_PLAY_PATH)


def _binge_parts(hints: Dict) -> List[str]:
    """`[viewio, type, resolution, langue, indexeur]` lus dans le bingeGroup, ou []."""
    group = str(hints.get("bingeGroup") or "").split("|", 2)
    if len(group) < 3:
        return []
    return unquote(group[2]).split("|")


def parse_stream(
    s: Dict, title: str, season: Optional[str] = None, episode: Optional[str] = None,
) -> Optional[Dict]:
    """Convertit un flux Viewio en resultat Wacustom, ou None si a ecarter."""
    if not is_play_link(s.get("url") or ""):
        return None

    hints = s.get("behaviorHints") or {}
    # Viewio n'annonce que ce qu'il sait lire ; on ne garde que le cache confirme.
    if hints.get("cached") is not True and "⚡" not in (s.get("name") or ""):
        return None

    filename = (hints.get("filename") or hints.get("folderName") or "").strip()
    name = (s.get("name") or "").strip()
    desc = (s.get("title") or s.get("description") or "").strip()

    if filename and season and episode and episode_matches(filename, season, episode) is False:
        scraper_logger.debug(f"[Viewio] Skip (episode mismatch S{season}E{episode}): {filename}")
        return None

    parts = _binge_parts(hints)
    lang_hint = _LANG_HINT.get(parts[3].strip().lower(), "") if len(parts) > 3 else ""

    display_name = filename or " ".join(x for x in (title, name, desc) if x)
    if lang_hint == "VOSTFR" and "VOSTFR" not in display_name.upper():
        display_name = f"{display_name} VOSTFR"
    tokens = tokenize_filename(" ".join(x for x in (filename, name, desc, lang_hint) if x))

    size_bytes = hints.get("videoSize") or 0
    try:
        size_bytes = int(size_bytes)
    except (TypeError, ValueError):
        size_bytes = 0
    size_str = normalize_size(f"{size_bytes / (1024 ** 3):.2f} GB") if size_bytes > 0 else "Unknown"

    result = {
        "link": s["url"],
        "quality": extract_quality_from_tokens(tokens),
        "language": extract_language_from_tokens(tokens),
        "raw_language": extract_raw_language_from_tokens(tokens),
        "source": SOURCE_LABEL,
        "hoster": "Direct",
        "cache_status": "cached",
        "size": size_str,
        "display_name": display_name,
        "model_type": "direct",
    }
    if season:
        result["season"] = str(season)
    if episode:
        result["episode"] = str(episode)
    return result


class ViewioScraper:
    async def search(
        self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
        season: Optional[str] = None, episode: Optional[str] = None,
        config: Optional[Dict] = None,
    ) -> List[Dict]:
        base = _manifest_base()
        if not base:
            return []
        remaining = _paused_for()
        if remaining:
            scraper_logger.debug(f"[Viewio] pause quota, encore {remaining // 60} min")
            return []
        imdb_id = (metadata or {}).get("imdb_id")
        if not imdb_id:
            return []

        path = (
            f"stream/series/{imdb_id}:{season}:{episode}.json"
            if (season and episode) else f"stream/movie/{imdb_id}.json"
        )
        try:
            resp = await http_client.get(f"{base}/{path}", headers={"Accept": "application/json"})
            if resp.status_code == 429:
                _start_pause()
                scraper_logger.warning(
                    f"[Viewio] quota atteint (429) - pause {_RATE_LIMIT_PAUSE_S // 60} min"
                )
                return []
            if resp.status_code != 200:
                scraper_logger.error(f"[Viewio] HTTP {resp.status_code}")
                return []
            streams = resp.json().get("streams", []) or []
        except Exception as e:
            scraper_logger.error(f"[Viewio] Error: {e}")
            return []

        results = []
        for s in streams:
            parsed = parse_stream(s, title, season, episode)
            if parsed is not None:
                results.append(parsed)

        results.sort(key=quality_sort_key)
        scraper_logger.debug(f"[Viewio] {len(results)} flux deja en cache")
        return results


viewio_scraper = ViewioScraper()
