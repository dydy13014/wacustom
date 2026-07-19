import asyncio
import time
from typing import Dict, Optional

from wastream.config.settings import settings
from wastream.utils.http_client import http_client
from wastream.utils.logger import debrid_logger


# ===========================
# Hoster Status Cache
# ===========================
# TorBox: proactive check via public API (/webdl/hosters)
# AllDebrid: double détection —
#   - reactive (cache["hosts"]) via convert_link() errors (LINK_HOST_UNAVAILABLE)
#   - proactive (cache["proactive"]) via /v4.1/user/hosts (status + quota restant)
#     Quota critique (<QUOTA_CRITICAL_PCT restant) traité comme "down" pour
#     éviter de continuer à recommander un hébergeur qui va bientôt refuser
#     les nouveaux téléchargements du jour.
_hoster_status_cache = {
    "alldebrid": {"hosts": {}, "proactive": {}, "proactive_last_check": None},
    "torbox": {"hosts": {}, "last_check": None},
}

QUOTA_CRITICAL_PCT = 5.0


# ===========================
# TorBox Proactive Check
# ===========================
async def _check_torbox_hosters() -> Dict[str, bool]:
    try:
        response = await http_client.get(
            f"{settings.TORBOX_API_URL}/webdl/hosters",
            timeout=settings.HEALTH_CHECK_TIMEOUT
        )

        if response.status_code != 200:
            debrid_logger.debug(f"[HosterStatus] TorBox hosters API HTTP {response.status_code}")
            return {}

        data = response.json()
        hoster_list = data.get("data", [])
        if not isinstance(hoster_list, list):
            debrid_logger.debug("[HosterStatus] TorBox unexpected response format")
            return {}

        supported_hosts = [h.lower() for h in settings.TORBOX_SUPPORTED_HOSTS]
        hosts = {}

        for hoster in hoster_list:
            name = hoster.get("name", "").lower().strip()
            if name and name in supported_hosts:
                hosts[name] = hoster.get("status", True)

        debrid_logger.debug(f"[HosterStatus] TorBox: {hosts}")
        return hosts

    except Exception as e:
        debrid_logger.debug(f"[HosterStatus] TorBox check failed: {type(e).__name__}: {e}")
        return {}


# ===========================
# AllDebrid Proactive Check
# ===========================
async def _check_alldebrid_hosters(api_key: str) -> Dict[str, dict]:
    """Interroge /v4.1/user/hosts (statut + quota restant par hébergeur, propre
    au compte). Renvoie {hoster: {"up": bool, "quota_pct": float|None}} pour
    les hébergeurs configurés (ALLDEBRID_SUPPORTED_HOSTS, hors pseudo-entrées
    "alldebrid"/"torrent")."""
    try:
        response = await http_client.get(
            settings.ALLDEBRID_API_URL.replace("/v4", "/v4.1") + "/user/hosts",
            params={"agent": settings.ADDON_NAME, "apikey": api_key},
            timeout=settings.HEALTH_CHECK_TIMEOUT
        )

        if response.status_code != 200:
            debrid_logger.debug(f"[HosterStatus] AllDebrid user/hosts HTTP {response.status_code}")
            return {}

        data = response.json()
        if data.get("status") != "success":
            debrid_logger.debug("[HosterStatus] AllDebrid user/hosts request failed")
            return {}

        all_hosts = data.get("data", {}).get("hosts", {})
        tracked = {h.lower() for h in settings.ALLDEBRID_SUPPORTED_HOSTS if h not in ("alldebrid", "torrent")}
        result = {}

        for name, info in all_hosts.items():
            if name.lower() not in tracked:
                continue
            quota = info.get("quota")
            quota_max = info.get("quotaMax")
            quota_pct = (quota / quota_max * 100) if quota_max else None
            result[name.lower()] = {"up": bool(info.get("status", True)), "quota_pct": quota_pct}

        debrid_logger.debug(f"[HosterStatus] AllDebrid: {result}")
        return result

    except Exception as e:
        debrid_logger.debug(f"[HosterStatus] AllDebrid check failed: {type(e).__name__}: {e}")
        return {}


# ===========================
# AllDebrid Reactive Detection
# ===========================
def mark_hoster_down(service: str, hoster_name: str):
    cache = _hoster_status_cache.get(service)
    if not cache:
        return

    hoster_key = hoster_name.lower().strip()
    cache["hosts"][hoster_key] = time.time()
    _recheck_success_count.pop(hoster_key, None)
    debrid_logger.debug(f"[HosterStatus] {service}: marked '{hoster_key}' as DOWN for {settings.HOSTER_STATUS_CACHE_TTL}s")


# ===========================
# Cache Management
# ===========================
async def get_hoster_status(service: str, api_key: Optional[str] = None) -> Dict[str, bool]:
    cache = _hoster_status_cache.get(service)
    if not cache:
        return {}

    if service == "torbox":
        now = time.time()
        if cache["last_check"] and (now - cache["last_check"]) < settings.HOSTER_STATUS_CACHE_TTL:
            return cache["hosts"]

        hosts = await _check_torbox_hosters()
        if hosts:
            cache["hosts"] = hosts
            cache["last_check"] = now
        return cache["hosts"]

    if service == "alldebrid" and api_key:
        now = time.time()
        if cache["proactive_last_check"] and (now - cache["proactive_last_check"]) < settings.HOSTER_STATUS_CACHE_TTL:
            return cache["proactive"]

        proactive = await _check_alldebrid_hosters(api_key)
        if proactive:
            cache["proactive"] = proactive
            cache["proactive_last_check"] = now
        return cache["proactive"]

    return {}


# ===========================
# Hoster Status Lookup
# ===========================
def is_hoster_up(service: str, hoster_name: str) -> bool:
    cache = _hoster_status_cache.get(service)
    if not cache:
        return True

    hoster_key = hoster_name.lower().strip()

    if service == "alldebrid":
        # Reactive: hosts dict = {name: timestamp_when_marked_down}
        now = time.time()
        expired_keys = []
        result = True

        for cached_name, marked_at in cache["hosts"].items():
            if cached_name in hoster_key or hoster_key in cached_name:
                if (now - marked_at) < settings.HOSTER_STATUS_CACHE_TTL:
                    result = False
                else:
                    expired_keys.append(cached_name)

        for key in expired_keys:
            del cache["hosts"][key]

        if not result:
            return False

        # Proactive : statut serveur + quota critique (/v4.1/user/hosts)
        for cached_name, info in cache["proactive"].items():
            if cached_name in hoster_key or hoster_key in cached_name:
                if not info["up"]:
                    return False
                if info["quota_pct"] is not None and info["quota_pct"] < QUOTA_CRITICAL_PCT:
                    return False

        return True

    if not cache["hosts"]:
        return True

    # TorBox: proactive, hosts dict = {name: bool}
    for cached_name, status in cache["hosts"].items():
        if cached_name in hoster_key or hoster_key in cached_name:
            return status

    return True


# ===========================
# AllDebrid Background Recheck
# ===========================
_recheck_in_progress = set()
_recheck_success_count = {}


async def _recheck_alldebrid_hoster(link: str, api_key: str, hoster_key: str):
    is_direct_link = any(host in link for host in ["1fichier.com", "turbobit.net", "rapidgator.net"])
    endpoint = "link/unlock" if is_direct_link else "link/redirector"

    try:
        response = await http_client.get(
            f"{settings.ALLDEBRID_API_URL}/{endpoint}",
            params={"agent": settings.ADDON_NAME, "apikey": api_key, "link": link},
            timeout=settings.HEALTH_CHECK_TIMEOUT
        )

        if response.status_code != 200:
            debrid_logger.debug(f"[HosterRecheck] {hoster_key}: HTTP {response.status_code}, ignored")
            return

        data = response.json()
        if data.get("status") != "success":
            error_code = data.get("error", {}).get("code")
            if error_code == "LINK_HOST_UNAVAILABLE":
                debrid_logger.debug(f"[HosterRecheck] {hoster_key}: still DOWN, resetting TTL")
                _recheck_success_count[hoster_key] = 0
                cache = _hoster_status_cache.get("alldebrid")
                if cache:
                    cache["hosts"][hoster_key] = time.time()
            else:
                debrid_logger.debug(f"[HosterRecheck] {hoster_key}: {error_code}, ignored")
        else:
            count = _recheck_success_count.get(hoster_key, 0) + 1
            _recheck_success_count[hoster_key] = count
            threshold = settings.HOSTER_STATUS_RECHECK_THRESHOLD
            if count >= threshold:
                debrid_logger.debug(f"[HosterRecheck] {hoster_key}: {count}/{threshold} success, marking UP")
                cache = _hoster_status_cache.get("alldebrid")
                if cache and hoster_key in cache["hosts"]:
                    del cache["hosts"][hoster_key]
                _recheck_success_count.pop(hoster_key, None)
            else:
                debrid_logger.debug(f"[HosterRecheck] {hoster_key}: {count}/{threshold} success, keeping DOWN")

    except Exception as e:
        debrid_logger.debug(f"[HosterRecheck] {hoster_key}: failed ({type(e).__name__}: {e}), ignored")
    finally:
        _recheck_in_progress.discard(hoster_key)


def schedule_recheck(link: str, api_key: str, hoster_name: str):
    hoster_key = hoster_name.lower().strip()
    if not hoster_key or hoster_key in _recheck_in_progress:
        return
    _recheck_in_progress.add(hoster_key)
    try:
        asyncio.create_task(_recheck_alldebrid_hoster(link, api_key, hoster_key))
    except RuntimeError:
        _recheck_in_progress.discard(hoster_key)
