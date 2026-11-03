import re
from typing import Optional, Dict, Any, List

from wastream.utils.crypto import encrypt_password_for_url


# ===========================
# Multi-utilisateurs
# ===========================
# Un compte (uuid + mot de passe) porte plusieurs utilisateurs. L'utilisateur
# principal garde le format historique : ses services sont a la racine de la
# config, a cote des reglages communs (langues, qualite, tri...). Les autres
# vivent dans `profiles` et ne portent QUE leurs services :
#
#   profile_id / profile_name : identifiant (segment d'URL) et nom du principal
#   default_services          : services du principal partages par tous
#   profiles                  : [{id, name, debrid_services, tmdb_api_token, ...}]
#
# Une config sans ces cles (creee avant le multi-utilisateurs) reste valide :
# c'est un compte a un seul utilisateur.

# Champs propres a chaque utilisateur ; tout le reste de la config est commun.
PROFILE_SERVICE_FIELDS = ("debrid_services", "tmdb_api_token", "nzbdav_url", "webdav_user", "webdav_password",
                          "tracker_api_keys")
_FIELD_DEFAULTS = {"debrid_services": list, "tracker_api_keys": dict}

# Services que le principal peut definir « par defaut » pour tous. Les services
# debrid n'y figurent volontairement pas : chacun utilise son propre compte.
SHAREABLE_SERVICES = {"tmdb": ("tmdb_api_token",), "trackers": ("tracker_api_keys",)}

PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def _shared_fields(config: Dict[str, Any]) -> List[str]:
    fields = []
    for service in config.get("default_services") or []:
        fields.extend(SHAREABLE_SERVICES.get(service, ()))
    return fields


def resolve_profile_config(config: Dict[str, Any], profile_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Config effective d'un utilisateur : reglages communs + ses services
    (+ services par defaut du principal). None si l'utilisateur n'existe pas.

    Les services des AUTRES utilisateurs sont retires : la config effective
    circule dans tout le pipeline de streams, elle ne doit porter que les cles
    de celui qui regarde."""
    profiles = config.get("profiles") or []
    effective = {k: v for k, v in config.items() if k != "profiles"}

    if not profile_id or profile_id == config.get("profile_id"):
        if profile_id:
            effective["_profile_id"] = profile_id
            effective["_profile_name"] = config.get("profile_name") or profile_id
        return effective

    profile = next((p for p in profiles if isinstance(p, dict) and p.get("id") == profile_id), None)
    if not profile:
        return None

    for field in PROFILE_SERVICE_FIELDS:
        effective[field] = profile.get(field, _FIELD_DEFAULTS.get(field, str)())
    for field in _shared_fields(config):
        effective[field] = config.get(field, "")

    effective["_profile_id"] = profile_id
    effective["_profile_name"] = profile.get("name") or profile_id
    return effective


def validate_profiles(config: Dict[str, Any]) -> Optional[str]:
    """Message d'erreur si la partie multi-utilisateurs est incoherente, sinon None."""
    profiles = config.get("profiles") or []
    if not isinstance(profiles, list):
        return "profiles must be a list"

    main_id = config.get("profile_id")
    if not profiles and not main_id:
        return None
    if not main_id or not PROFILE_ID_PATTERN.match(str(main_id)):
        return "Invalid main user id"

    unknown = set(config.get("default_services") or []) - set(SHAREABLE_SERVICES)
    if unknown:
        return f"Service cannot be shared: {', '.join(sorted(unknown))}"

    shared = set(_shared_fields(config))
    seen = {main_id}
    for profile in profiles:
        if not isinstance(profile, dict):
            return "Invalid user entry"
        pid = profile.get("id")
        name = profile.get("name") or pid
        if not pid or not PROFILE_ID_PATTERN.match(str(pid)):
            return f"Invalid user id: {pid}"
        if pid in seen:
            return f"Duplicate user id: {pid}"
        seen.add(pid)
        services = profile.get("debrid_services") or []
        if not services or not all(isinstance(s, dict) and s.get("service") and s.get("api_key") for s in services):
            return f"User {name}: at least one debrid service is required"
        if "tmdb_api_token" not in shared and not profile.get("tmdb_api_token"):
            return f"User {name}: TMDB token required"
    return None


def build_addon_urls(base_url: str, user_uuid: str, password: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Une URL de manifest par utilisateur, chacune avec son propre jeton
    (lie a l'utilisateur, cf. crypto._profile_aad). Sans multi-utilisateurs :
    l'URL historique, inchangee."""
    main_id = config.get("profile_id")
    if not main_id:
        return [{
            "id": None,
            "name": None,
            "main": True,
            "url": f"{base_url}/{user_uuid}/{encrypt_password_for_url(password)}/manifest.json",
        }]

    entries = [(main_id, config.get("profile_name") or main_id, True)]
    entries += [(p["id"], p.get("name") or p["id"], False) for p in config.get("profiles") or []]
    return [
        {
            "id": pid,
            "name": name,
            "main": main,
            "url": f"{base_url}/{user_uuid}/{pid}/{encrypt_password_for_url(password, pid)}/manifest.json",
        }
        for pid, name, main in entries
    ]
