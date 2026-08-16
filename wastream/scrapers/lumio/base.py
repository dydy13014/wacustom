"""Source Lumio (mylumio.tv) — torrents déjà vérifiés en cache.

Interroge l'endpoint Stremio de Lumio avec l'identifiant de manifest de
l'instance, et ne retient QUE les torrents que Lumio marque lui-même comme
déjà présents en cache debrid (badge ⚡). On ne s'en sert pas comme d'un
indexeur de plus : depuis le retrait de `/magnet/instant` chez AllDebrid,
Wacustom n'a plus aucun moyen de vérifier le cache lui-même et marque tout
en « uncached » — ces résultats sont donc les seuls garantis lisibles
immédiatement.

**Chacun apporte son propre identifiant** (`LUMIO_MANIFEST_ID`, vide par
défaut = source désactivée), au même titre qu'une clé de tracker.

Mécanisme du cache, confirmé par l'auteur de Lumio (pas une supposition) : le
check AllDebrid est fait avec la clé du PREMIER utilisateur Lumio qui consulte
ce contenu, puis mutualisé en base pour les suivants — ce n'est pas un compte
AllDebrid dédié. Un « cached » n'est ensuite JAMAIS revérifié (contrairement à
« uncached », qui a un TTL adaptatif) : si le torrent sort du cache AllDebrid
après coup, le badge ⚡ reste affiché à tort, sans correctif possible côté
Lumio — d'où l'intérêt du mode Resilient de Wacustom en repli.

Le token de lecture Lumio est un JSON en base64 (signé mais pas chiffré)
contenant le candidat réel (hash torrent ou lien DDL, taille, résolution) —
même principe que `extract_link` dans les tokens WAStream.

⚠️ Quota bas et blocage global : chaque appel émis PENDANT un blocage le
prolonge (l'auteur est explicite là-dessus). D'où une pause de 24 h, écrite
sur disque pour survivre à un redémarrage du conteneur.
"""
import base64
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from wastream.config.settings import settings
from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger
from wastream.utils.release_parser import tokenize_filename
from wastream.utils.quality import extract_quality_from_tokens
from wastream.utils.languages import (
    extract_language_from_tokens, extract_raw_language_from_tokens
)
from wastream.utils.helpers import (
    normalize_size, episode_matches
)
from wastream.utils.quality import quality_sort_key

_BASE_URL = "https://mylumio.tv"

# Étiquette portée par les résultats, telle qu'affichée à l'utilisateur.
SOURCE_LABEL = "Lumio"

# Le quota de Lumio est tres bas ET son blocage est global (toute requete
# compte, pas seulement les recherches). Surtout : chaque appel emis PENDANT
# un blocage le prolonge — l'auteur de Lumio est explicite la-dessus. Une
# pause courte est donc pire que rien, elle entretient le blocage a l'infini.
# D'ou 24h, et surtout une pause ECRITE SUR DISQUE : gardee en memoire, elle
# etait perdue a chaque redemarrage du conteneur et on repartait taper aussitot.
_RATE_LIMIT_PAUSE_S = settings.LUMIO_RATE_LIMIT_PAUSE
_PAUSE_FILE = Path(os.environ.get("LUMIO_PAUSE_FILE", "/app/data/lumio_pause"))

# Repli memoire : si /app/data n'est pas monte ou n'est pas inscriptible, la
# pause ne pouvait pas etre enregistree du tout et on repartait interroger
# Lumio immediatement — ce qui PROLONGE le blocage au lieu de l'attendre.
# Moins durable qu'un fichier (perdu au redemarrage), mais infiniment mieux
# que rien pour qui heberge Wacustom sans le volume.
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
            f"[Lumio] pause non persistee ({exc}) - repli en memoire, "
            "elle sera perdue au redemarrage"
        )


def _decode_token(url: str) -> Optional[dict]:
    """Décode le token de lecture Lumio (base64 JSON, signature après le
    premier point ignorée) pour extraire le candidat réel."""
    try:
        token = url.rsplit("/playback/", 1)[1].split("/", 1)[0].split(".")[0]
        padding = "=" * (-len(token) % 4)
        return json.loads(base64.urlsafe_b64decode(token + padding))
    except (IndexError, ValueError, KeyError, TypeError):
        return None


class LumioScraper:
    async def search(
        self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
        season: Optional[str] = None, episode: Optional[str] = None,
        config: Optional[Dict] = None,
    ) -> List[Dict]:
        manifest_id = (settings.LUMIO_MANIFEST_ID or "").strip()
        if not manifest_id:
            return []
        remaining = _paused_for()
        if remaining:
            scraper_logger.debug(f"[Lumio] pause quota, encore {remaining // 60} min")
            return []
        imdb_id = (metadata or {}).get("imdb_id")
        if not imdb_id:
            return []

        path = (
            f"stream/series/{imdb_id}:{season}:{episode}.json"
            if (season and episode) else f"stream/movie/{imdb_id}.json"
        )
        try:
            resp = await http_client.get(
                f"{_BASE_URL}/{manifest_id}/{path}", headers={"Accept": "application/json"},
            )
            if resp.status_code == 429:
                _start_pause()
                scraper_logger.warning(
                    f"[Lumio] quota atteint (429) - pause {_RATE_LIMIT_PAUSE_S // 3600}h"
                )
                return []
            if resp.status_code != 200:
                scraper_logger.error(f"[Lumio] HTTP {resp.status_code}")
                return []
            streams = resp.json().get("streams", [])
        except Exception as e:
            scraper_logger.error(f"[Lumio] Error: {e}")
            return []

        results = []
        for s in streams:
            # Uniquement ce qui est déjà vérifié en cache par leur base
            # mutualisée — on ne s'en sert pas comme simple indexeur de plus.
            if "⚡" not in s.get("name", ""):
                continue
            decoded = _decode_token(s.get("url", ""))
            if not decoded or not decoded.get("c"):
                continue
            cand = decoded["c"][0]
            if cand.get("t") != "t":
                continue  # DDL (hébergeurs tiers) ignoré — déjà couvert par nos propres sources DDL
            infohash = (cand.get("h") or "").lower()
            if not infohash:
                continue

            size_bytes = cand.get("z") or 0
            size_str = normalize_size(f"{size_bytes / (1024 ** 3):.2f} GB") if size_bytes else "Unknown"
            display_name = (s.get("behaviorHints") or {}).get("filename") or s.get("description", "") or title

            # Lumio est la SEULE source qui ne repasse jamais par
            # episode_matches() sur le nom réel du fichier — elle fait
            # confiance à la saison/épisode demandés dans sa propre requête
            # (interrogée en /stream/series/{imdb}:{season}:{episode}.json).
            # Bug réel constaté (Solo Leveling, 2026-08-15) : Lumio répond
            # avec des fichiers S02 pour une requête S01E08 — toutes les
            # autres sources (torznab, nyaa, zilean, unit3d) ont ce
            # garde-fou, celle-ci ne l'avait pas.
            if season and episode:
                if episode_matches(display_name, season, episode) is False:
                    scraper_logger.debug(
                        f"[Lumio] Skip (episode mismatch S{season}E{episode}): {display_name}"
                    )
                    continue

            tokens = tokenize_filename(display_name)
            quality = extract_quality_from_tokens(tokens)
            language = extract_language_from_tokens(tokens)
            raw_language = extract_raw_language_from_tokens(tokens)

            result = {
                "link": f"magnet:?xt=urn:btih:{infohash}",
                "infohash": infohash,
                "quality": quality,
                "language": language,
                "raw_language": raw_language,
                "source": SOURCE_LABEL,
                "hoster": "Torrent",
                # On ne garde que les flux marqués ⚡ par Lumio, c'est-à-dire
                # vérifiés présents dans son cache AllDebrid mutualisé. Cette
                # information est précieuse : depuis le retrait de /magnet/instant,
                # Wacustom n'a plus aucun moyen de vérifier le cache lui-même et
                # marque tous les torrents « uncached » par défaut.
                "cache_status": "cached",
                "size": size_str,
                "display_name": display_name,
                "model_type": "torrent",
            }
            if season:
                result["season"] = str(season)
            if episode:
                result["episode"] = str(episode)
            results.append(result)

        results.sort(key=quality_sort_key)
        scraper_logger.debug(f"[Lumio] {len(results)} torrent(s) déjà vérifié(s) en cache")
        return results


lumio_scraper = LumioScraper()
