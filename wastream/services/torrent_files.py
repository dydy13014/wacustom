"""Fichiers .torrent des trackers prives (Torznab), gardes cote serveur.

Un tracker prive desactive DHT/PEX : un magnet nu (juste l'infohash) n'y
trouve jamais de pair, AllDebrid le laisse en "No peer after 30 minutes".
Seul le vrai .torrent, qui embarque l'URL d'annonce avec la passkey, permet
de telecharger un contenu hors cache.

Les URL de telechargement sont sensibles : selon le tracker, elles contiennent
la cle d'API ou donnent directement acces a un .torrent personnel.
Elles ne doivent donc jamais sortir du serveur -- ni dans le jeton de lecture
(signe mais lisible), ni dans le cache de resultats (en base, partageable
entre instances), ni dans un log. D'ou ce registre en memoire, alimente a la
recherche et consulte a la lecture par l'infohash. Wacustom tourne en un seul
processus : pas besoin de le partager.
"""
import time
from collections import OrderedDict
from typing import Optional, Tuple

from wastream.config.settings import settings
from wastream.utils.http_client import http_client
from wastream.utils.logger import debrid_logger

_TTL = 86400
_MAX_ENTRIES = 20000
_MAX_TORRENT_BYTES = 5 * 1024 * 1024

# infohash -> (source, url, expire_a en monotonic)
_registry: "OrderedDict[str, Tuple[str, str, float]]" = OrderedDict()


def remember_torrent_url(infohash: str, source: str, url: str) -> None:
    if not infohash or not url or not url.startswith(("http://", "https://")):
        return
    infohash = infohash.lower()
    _registry.pop(infohash, None)
    _registry[infohash] = (source, url, time.monotonic() + _TTL)
    while len(_registry) > _MAX_ENTRIES:
        _registry.popitem(last=False)


def torrent_source(infohash: str) -> Optional[str]:
    entry = _lookup(infohash)
    return entry[0] if entry else None


def _lookup(infohash: str) -> Optional[Tuple[str, str]]:
    if not infohash:
        return None
    infohash = infohash.lower()
    entry = _registry.get(infohash)
    if entry is None:
        return None
    source, url, expires_at = entry
    if expires_at <= time.monotonic():
        del _registry[infohash]
        return None
    return source, url


async def fetch_torrent_file(infohash: str) -> Optional[bytes]:
    """Telecharge le .torrent connu pour cet infohash, ou None. N'ecrit jamais
    l'URL dans un log : elle contient la cle du tracker."""
    entry = _lookup(infohash)
    if not entry:
        return None
    source, url = entry
    try:
        response = await http_client.get(url, headers={"User-Agent": "WAStream/1.0"}, timeout=settings.HTTP_TIMEOUT)
    except Exception as e:
        debrid_logger.debug(f"[TorrentFile] {source} {infohash[:8]}: {type(e).__name__}")
        return None
    if response.status_code != 200:
        debrid_logger.debug(f"[TorrentFile] {source} {infohash[:8]}: HTTP {response.status_code}")
        return None
    content = response.content
    if len(content) > _MAX_TORRENT_BYTES or not content.startswith(b"d") or b"4:info" not in content:
        debrid_logger.debug(f"[TorrentFile] {source} {infohash[:8]}: reponse qui n'est pas un .torrent")
        return None
    return content
