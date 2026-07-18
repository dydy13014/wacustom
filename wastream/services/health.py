import asyncio
import os
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from urllib.parse import urlparse

from wastream.config.settings import settings
from wastream.utils.http_client import http_client
from wastream.utils.logger import api_logger
from wastream.services.remote import get_remote_instances, check_remote_instance
from wastream.services.hoster_status import _hoster_status_cache, get_hoster_status
from wastream.utils.helpers import normalize_tracker_url


# ===========================
# Data Classes
# ===========================
@dataclass
class SourceStatus:
    name: str
    url: Optional[str]
    status: str = "unknown"
    response_time: Optional[int] = None
    last_check: Optional[float] = None
    error: Optional[str] = None
    category: str = "source"


@dataclass
class HealthState:
    sources: Dict[str, SourceStatus] = field(default_factory=dict)
    services: Dict[str, SourceStatus] = field(default_factory=dict)
    last_auto_check: Optional[float] = None
    last_services_check: Optional[float] = None
    last_sources_check: Optional[float] = None
    checking: bool = False


# ===========================
# Global State
# ===========================
_health_state = HealthState()


# ===========================
# Configuration
# ===========================
def _get_sources_config() -> Dict[str, Optional[str]]:
    return {
        "Wawacity": settings.WAWACITY_URL,
        "Free-Telecharger": settings.FREE_TELECHARGER_URL,
        "Darki-API": settings.DARKI_API_URL,
        "Movix": settings.MOVIX_API_URL,
        "Webshare": settings.WEBSHARE_URL,
        "YggReborn": settings.YGGREBORN_URL,
        "Tr4ker": settings.TR4KER_URL,
        "Torr9": settings.TORR9_URL,
        "C411": settings.C411_URL,
        "Gemini": settings.GEMINI_URL,
        "Generation-Free": settings.GENERATIONFREE_URL,
        "Zilean": settings.ZILEAN_URL,
    }


def _mask_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return url[:30] + "..." if len(url) > 30 else url


# ===========================
# Service Check Functions
# ===========================
async def _check_database() -> SourceStatus:
    start_time = time.time()
    try:
        from wastream.utils.database import database
        await database.fetch_val("SELECT 1")
        response_time = int((time.time() - start_time) * 1000)
        return SourceStatus(
            name="Database",
            url=settings.DATABASE_TYPE,
            status="online",
            response_time=response_time,
            last_check=time.time(),
            category="service"
        )
    except Exception as e:
        response_time = int((time.time() - start_time) * 1000)
        return SourceStatus(
            name="Database",
            url=settings.DATABASE_TYPE,
            status="offline",
            response_time=response_time,
            last_check=time.time(),
            error=type(e).__name__,
            category="service"
        )


async def _check_proxy() -> SourceStatus:
    if not settings.PROXY_URL:
        return SourceStatus(
            name="Proxy",
            url=None,
            status="unconfigured",
            last_check=time.time(),
            category="service"
        )

    start_time = time.time()
    try:
        response = await http_client.get(
            "https://httpbin.org/ip",
            timeout=settings.HEALTH_CHECK_TIMEOUT
        )
        response_time = int((time.time() - start_time) * 1000)

        if response.status_code == 200:
            return SourceStatus(
                name="Proxy",
                url=_mask_url(settings.PROXY_URL),
                status="online",
                response_time=response_time,
                last_check=time.time(),
                category="service"
            )
        else:
            return SourceStatus(
                name="Proxy",
                url=_mask_url(settings.PROXY_URL),
                status="offline",
                response_time=response_time,
                last_check=time.time(),
                error=f"HTTP {response.status_code}",
                category="service"
            )
    except Exception as e:
        response_time = int((time.time() - start_time) * 1000)
        return SourceStatus(
            name="Proxy",
            url=_mask_url(settings.PROXY_URL),
            status="offline",
            response_time=response_time if response_time < settings.HEALTH_CHECK_TIMEOUT * 1000 else None,
            last_check=time.time(),
            error=type(e).__name__,
            category="service"
        )


# ===========================
# System Info Functions
# ===========================
def _get_system_info() -> Dict[str, Any]:
    try:
        import psutil
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        cpu_percent = process.cpu_percent(interval=0.1)

        return {
            "memory_used_mb": round(memory_info.rss / 1024 / 1024, 1),
            "memory_percent": round(process.memory_percent(), 1),
            "cpu_percent": round(cpu_percent, 1),
            "available": True
        }
    except ImportError:
        return {"available": False}
    except Exception:
        return {"available": False}


def get_system_info() -> Dict[str, Any]:
    return _get_system_info()


# ===========================
# Source Health Check Functions
# ===========================
async def _check_single_source(name: str, url: Optional[str]) -> SourceStatus:
    if not url:
        return SourceStatus(
            name=name,
            url=None,
            status="unconfigured",
            last_check=time.time()
        )

    start_time = time.time()
    try:
        if name == "Darki-API":
            response = await http_client.get(
                f"{url}/health",
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            response_time = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                data = response.json()
                darkiworld_status = data.get("darkiworld_status", "unknown")
                api_status = data.get("status", "unknown")

                if api_status == "healthy" and darkiworld_status == "reachable":
                    return SourceStatus(
                        name=name,
                        url=url,
                        status="online",
                        response_time=response_time,
                        last_check=time.time()
                    )
                else:
                    return SourceStatus(
                        name=name,
                        url=url,
                        status="offline",
                        response_time=response_time,
                        last_check=time.time(),
                        error=f"darkiworld: {darkiworld_status}"
                    )
            else:
                return SourceStatus(
                    name=name,
                    url=url,
                    status="offline",
                    response_time=response_time,
                    last_check=time.time(),
                    error=f"HTTP {response.status_code}"
                )
        elif name == "Movix":
            headers = {}
            if settings.MOVIX_URL:
                headers["Origin"] = settings.MOVIX_URL
                headers["Referer"] = f"{settings.MOVIX_URL}/"

            response = await http_client.get(
                f"{url}/api/search",
                params={"title": "test"},
                headers=headers,
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            response_time = int((time.time() - start_time) * 1000)

            if response.status_code < 400:
                return SourceStatus(
                    name=name,
                    url=url,
                    status="online",
                    response_time=response_time,
                    last_check=time.time()
                )
            else:
                return SourceStatus(
                    name=name,
                    url=url,
                    status="offline",
                    response_time=response_time,
                    last_check=time.time(),
                    error=f"HTTP {response.status_code}"
                )
        elif name in ["YggReborn", "Tr4ker", "Torr9", "C411"]:
            tracker_url = normalize_tracker_url(name, url)
            headers = {
                "User-Agent": "WAStream/1.0"
            }
            if name == "YggReborn":
                tracker_url += f"?t=caps&apikey={settings.YGGREBORN_API_KEY}"
            elif name == "Tr4ker":
                tracker_url += f"?t=caps&apikey={settings.TR4KER_API_KEY}"
            elif name == "Torr9":
                tracker_url += f"?t=caps&apikey={settings.TORR9_API_KEY}"
            elif name == "C411":
                tracker_url += f"?t=caps&apikey={settings.C411_API_KEY}"

            response = await http_client.get(
                tracker_url,
                headers=headers,
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            response_time = int((time.time() - start_time) * 1000)

            if response.status_code == 200:
                return SourceStatus(
                    name=name,
                    url=_mask_url(url),
                    status="online",
                    response_time=response_time,
                    last_check=time.time()
                )
            else:
                return SourceStatus(
                    name=name,
                    url=_mask_url(url),
                    status="offline",
                    response_time=response_time,
                    last_check=time.time(),
                    error=f"HTTP {response.status_code}"
                )
        else:
            response = await http_client.get(
                url,
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            response_time = int((time.time() - start_time) * 1000)

            if response.status_code < 400:
                return SourceStatus(
                    name=name,
                    url=url,
                    status="online",
                    response_time=response_time,
                    last_check=time.time()
                )
            else:
                return SourceStatus(
                    name=name,
                    url=url,
                    status="offline",
                    response_time=response_time,
                    last_check=time.time(),
                    error=f"HTTP {response.status_code}"
                )
    except Exception as e:
        response_time = int((time.time() - start_time) * 1000)
        return SourceStatus(
            name=name,
            url=url,
            status="offline",
            response_time=response_time if response_time < settings.HEALTH_CHECK_TIMEOUT * 1000 else None,
            last_check=time.time(),
            error=type(e).__name__
        )


async def check_all_sources(force: bool = False) -> Dict[str, Any]:
    if _health_state.checking and not force:
        return get_health_status()

    _health_state.checking = True

    try:
        sources = _get_sources_config()
        source_tasks = [_check_single_source(name, url) for name, url in sources.items()]
        service_tasks = [_check_database(), _check_proxy()]

        all_results = await asyncio.gather(*source_tasks, *service_tasks)

        source_results = all_results[:len(source_tasks)]
        service_results = all_results[len(source_tasks):]

        for status in source_results:
            _health_state.sources[status.name] = status

        for status in service_results:
            _health_state.services[status.name] = status

        now = time.time()
        _health_state.last_auto_check = now
        _health_state.last_services_check = now
        _health_state.last_sources_check = now
        api_logger.debug(f"Health check completed for {len(source_results)} sources, {len(service_results)} services")

    except Exception as e:
        api_logger.error(f"Health check failed: {type(e).__name__}: {e}")
    finally:
        _health_state.checking = False

    return get_health_status()


async def check_services_only() -> Dict[str, Any]:
    try:
        service_tasks = [_check_database(), _check_proxy()]
        service_results = await asyncio.gather(*service_tasks)

        for status in service_results:
            _health_state.services[status.name] = status

        _health_state.last_services_check = time.time()
        api_logger.debug(f"Services check completed for {len(service_results)} services")

    except Exception as e:
        api_logger.error(f"Services check failed: {type(e).__name__}: {e}")

    return get_health_status()


async def check_sources_only() -> Dict[str, Any]:
    try:
        sources = _get_sources_config()
        source_tasks = [_check_single_source(name, url) for name, url in sources.items()]
        source_results = await asyncio.gather(*source_tasks)

        for status in source_results:
            _health_state.sources[status.name] = status

        _health_state.last_sources_check = time.time()
        api_logger.debug(f"Sources check completed for {len(source_results)} sources")

    except Exception as e:
        api_logger.error(f"Sources check failed: {type(e).__name__}: {e}")

    return get_health_status()


# ===========================
# Status Retrieval
# ===========================
def _build_hosters_list() -> list:
    hosters = []

    # TorBox hosters
    tb_cache = _hoster_status_cache.get("torbox", {})
    tb_hosts = tb_cache.get("hosts", {})
    tb_last_check = tb_cache.get("last_check")
    for host_name in settings.TORBOX_SUPPORTED_HOSTS:
        key = host_name.lower()
        if key in tb_hosts:
            status = "online" if tb_hosts[key] else "offline"
        else:
            status = "unknown"
        hosters.append({
            "name": f"{host_name} (TorBox)",
            "status": status,
            "response_time": None,
            "last_check": tb_last_check,
            "error": None
        })

    # AllDebrid hosters (reactive)
    ad_cache = _hoster_status_cache.get("alldebrid", {})
    ad_hosts = ad_cache.get("hosts", {})
    now = time.time()
    for host_name in settings.ALLDEBRID_SUPPORTED_HOSTS:
        key = host_name.lower()
        status = "online"
        last_check = None
        for cached_name, marked_at in ad_hosts.items():
            if cached_name in key or key in cached_name:
                if (now - marked_at) < settings.HOSTER_STATUS_CACHE_TTL:
                    status = "offline"
                    last_check = marked_at
                break
        hosters.append({
            "name": f"{host_name} (AllDebrid)",
            "status": status,
            "response_time": None,
            "last_check": last_check,
            "error": None
        })

    return hosters


def get_health_status() -> Dict[str, Any]:
    sources_list = []
    for name, status in _health_state.sources.items():
        sources_list.append({
            "name": status.name,
            "url": _mask_url(status.url) if status.url else None,
            "status": status.status,
            "response_time": status.response_time,
            "last_check": status.last_check,
            "error": status.error
        })

    for name, url in _get_sources_config().items():
        if name not in _health_state.sources:
            sources_list.append({
                "name": name,
                "url": _mask_url(url) if url else None,
                "status": "unconfigured" if not url else "unknown",
                "response_time": None,
                "last_check": None,
                "error": None
            })

    sources_list.sort(key=lambda x: x["name"])

    services_list = []
    for name, status in _health_state.services.items():
        services_list.append({
            "name": status.name,
            "url": status.url,
            "status": status.status,
            "response_time": status.response_time,
            "last_check": status.last_check,
            "error": status.error
        })

    if "Database" not in _health_state.services:
        services_list.append({
            "name": "Database",
            "url": settings.DATABASE_TYPE,
            "status": "unknown",
            "response_time": None,
            "last_check": None,
            "error": None
        })
    if "Proxy" not in _health_state.services:
        services_list.append({
            "name": "Proxy",
            "url": _mask_url(settings.PROXY_URL) if settings.PROXY_URL else None,
            "status": "unconfigured" if not settings.PROXY_URL else "unknown",
            "response_time": None,
            "last_check": None,
            "error": None
        })

    services_list.sort(key=lambda x: x["name"])

    online_count = sum(1 for s in sources_list if s["status"] == "online")
    offline_count = sum(1 for s in sources_list if s["status"] == "offline")
    unconfigured_count = sum(1 for s in sources_list if s["status"] == "unconfigured")

    system_info = _get_system_info()

    hosters_list = _build_hosters_list()

    return {
        "sources": sources_list,
        "services": services_list,
        "hosters": hosters_list,
        "system": system_info,
        "summary": {
            "online": online_count,
            "offline": offline_count,
            "unconfigured": unconfigured_count,
            "total": len(sources_list)
        },
        "last_check": _health_state.last_auto_check,
        "last_services_check": _health_state.last_services_check,
        "last_sources_check": _health_state.last_sources_check,
        "check_interval": settings.HEALTH_CHECK_INTERVAL,
        "checking": _health_state.checking
    }


def is_source_online(source_name: str) -> bool:
    status = _health_state.sources.get(source_name)
    if not status:
        return True
    return status.status != "offline"


# ===========================
# Remote Instances Check
# ===========================
async def check_remote_instances():
    try:
        instances = await get_remote_instances()
        enabled_instances = [i for i in instances if i.get("enabled")]

        if not enabled_instances:
            return

        tasks = [check_remote_instance(i["id"]) for i in enabled_instances]
        await asyncio.gather(*tasks, return_exceptions=True)

        api_logger.debug(f"Remote instances check completed for {len(enabled_instances)} instances")

    except Exception as e:
        api_logger.error(f"Remote instances check failed: {type(e).__name__}: {e}")


# ===========================
# Background Task
# ===========================
async def start_background_health_check():
    api_logger.info(f"Starting background health check (interval: {settings.HEALTH_CHECK_INTERVAL}s)")

    await check_all_sources()
    await check_remote_instances()
    await get_hoster_status("torbox")

    while True:
        await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL)
        try:
            await check_all_sources()
            await check_remote_instances()
            await get_hoster_status("torbox")
        except Exception as e:
            api_logger.error(f"Background health check error: {type(e).__name__}: {e}")
