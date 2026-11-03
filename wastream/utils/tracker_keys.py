"""Cles de trackers prives par utilisateur.

Chaque utilisateur peut saisir ses propres cles dans sa config
(`tracker_api_keys`, ex. {"yggreborn": "..."}). A defaut, la cle globale du
dashboard admin / .env s'applique, comme avant.

Une cle d'utilisateur ne doit jamais profiter a un autre :
- le cache de resultats est cloisonne par cle (nom de cache suffixe par une
  empreinte HMAC de la cle, jamais la cle elle-meme) ;
- les URL de .torrent (qui portent la passkey) sont rangees par proprietaire
  dans torrent_files, et une requete ne peut lire que celles de sa propre
  config (+ celles de la cle globale), via un ContextVar pose au chargement
  de la config (services/users.py).
"""
import hashlib
import hmac
from contextvars import ContextVar
from typing import Any, Dict, FrozenSet, Optional, Tuple

from wastream.config.settings import settings

GLOBAL_OWNER = "global"

# Identifiant de source (config/UI) -> reglage de la cle globale.
TRACKER_KEY_SETTINGS = {
    "yggreborn": "YGGREBORN_API_KEY",
    "tr4ker": "TR4KER_API_KEY",
    "torr9": "TORR9_API_KEY",
    "c411": "C411_API_KEY",
    "v3x": "V3X_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "generation-free": "GENERATIONFREE_API_KEY",
}

_request_owners: ContextVar[FrozenSet[str]] = ContextVar("tracker_key_owners", default=frozenset({GLOBAL_OWNER}))


def _fingerprint(key: str) -> str:
    digest = hmac.new(settings.SECRET_KEY.encode("utf-8"), key.encode("utf-8"), hashlib.sha256)
    return "u" + digest.hexdigest()[:16]


def user_tracker_keys(config: Optional[Dict[str, Any]]) -> Dict[str, str]:
    keys = (config or {}).get("tracker_api_keys")
    if not isinstance(keys, dict):
        return {}
    return {
        tracker: value.strip()
        for tracker, value in keys.items()
        if tracker in TRACKER_KEY_SETTINGS and isinstance(value, str) and value.strip()
    }


def resolve_tracker_key(config: Optional[Dict[str, Any]], tracker: str) -> Tuple[Optional[str], str]:
    """(cle a utiliser, proprietaire) : la cle de l'utilisateur si elle existe,
    sinon la cle globale."""
    user_key = user_tracker_keys(config).get(tracker)
    if user_key:
        return user_key, _fingerprint(user_key)
    return getattr(settings, TRACKER_KEY_SETTINGS[tracker], None), GLOBAL_OWNER


def tracker_cache_name(tracker: str, config: Optional[Dict[str, Any]], base: Optional[str] = None) -> str:
    """Nom de cache de la source : inchange avec la cle globale, cloisonne
    par empreinte avec une cle d'utilisateur. `base` : nom de cache historique
    s'il differe de l'identifiant (ex. generation_free)."""
    base = base or tracker
    _, owner = resolve_tracker_key(config, tracker)
    return base if owner == GLOBAL_OWNER else f"{base}-{owner}"


def set_request_tracker_owners(config: Optional[Dict[str, Any]]) -> None:
    owners = {GLOBAL_OWNER}
    owners.update(_fingerprint(key) for key in user_tracker_keys(config).values())
    _request_owners.set(frozenset(owners))


def request_tracker_owners() -> FrozenSet[str]:
    return _request_owners.get()
