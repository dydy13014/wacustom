import re
import time
import json
import secrets
from enum import Enum
from typing import Optional, Dict, Any, List
from base64 import b64encode

from fastapi import APIRouter, Request, Query, Path, Body, Cookie, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse, HTMLResponse

from wastream.config.settings import settings, DEBRID_ABBREVIATIONS
from wastream.utils.crypto import encrypt_password_for_url
from wastream.utils.validators import validate_config
from wastream.services.stream import stream_service
from wastream.services.users import (
    create_user,
    get_user_config_detailed,
    get_config_from_url_params,
    update_user_config,
    delete_user_detailed,
    verify_user
)
from wastream.services.stats import (
    get_stats_summary,
    get_dead_links_list,
    delete_dead_links,
    delete_all_dead_links,
    add_dead_links,
    purge_cached_links
)
from wastream.services.wasource import (
    get_wasource_links,
    add_wasource_links_bulk,
    delete_wasource_links,
    delete_all_wasource_links,
    update_wasource_content,
    delete_wasource_url,
    get_wasource_stats,
    get_links_by_imdb,
    get_links_by_title
)
from wastream.services.remote import (
    create_api_key,
    get_api_keys,
    update_api_key,
    delete_api_key,
    validate_api_key,
    add_remote_instance,
    get_remote_instances,
    update_remote_instance,
    delete_remote_instance,
    check_remote_instance,
    get_remote_stats
)
from wastream.services.health import (
    check_all_sources,
    check_services_only,
    check_sources_only,
    get_health_status,
    get_system_info
)
from wastream.utils.languages import AVAILABLE_LANGUAGES, LANGUAGES
from wastream.utils.quality import AVAILABLE_RESOLUTIONS
from wastream.utils.logger import api_logger, remote_logger, get_logs, get_available_contexts, get_available_prefixes, clear_logs
from wastream.utils.http_client import http_client
from wastream.utils.database import database
from wastream.utils.helpers import create_cache_key, decode_playback_token
from wastream.utils.http_cache import (
    NO_CACHE_HEADERS, CachedJSONResponse, CachePolicies,
    check_etag_match, generate_etag,
    not_modified_response
)
from wastream.services.pastebin_scraper import get_pastebin_scraper_status


# ===========================
# Router Instance
# ===========================
router = APIRouter()


def get_base_url(request: Request) -> str:
    scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    host = request.headers.get("X-Forwarded-Host", request.headers.get("Host", str(request.url.netloc)))
    return f"{scheme}://{host}"


# ===========================
# Content Type Enum
# ===========================
class ContentType(str, Enum):
    movie = "movie"
    series = "series"


# ===========================
# Service Type Enum
# ===========================
class ServiceType(str, Enum):
    alldebrid = "alldebrid"
    torbox = "torbox"
    premiumize = "premiumize"
    onefichier = "1fichier"
    nzbdav = "nzbdav"
    tmdb = "tmdb"


# ===========================
# Web Interface Endpoints
# ===========================
@router.get("/", tags=["General"], summary="Root Redirect", description="Redirects to the configuration page")
async def root():
    return RedirectResponse("/configure")


@router.get("/configure", tags=["Configuration"], summary="Configuration Page", description="Web interface to configure the addon")
async def configure():
    with open("wastream/templates/index.html", "r", encoding="utf-8") as f:
        html_content = f.read()

    html_content = html_content.replace("{{CUSTOM_HTML}}", settings.CUSTOM_HTML)
    html_content = html_content.replace("{{ADDON_NAME}}", settings.ADDON_NAME)
    html_content = html_content.replace("{{VERSION}}", settings.ADDON_MANIFEST["version"])

    response = HTMLResponse(content=html_content)

    if settings.ADDON_PASSWORD:
        for key, value in NO_CACHE_HEADERS.items():
            response.headers[key] = value
    elif settings.HTTP_CACHE_ENABLED:
        response.headers["Cache-Control"] = CachePolicies.configure_page().build()
        response.headers["Vary"] = "Accept, Accept-Encoding"

    return response


# ===========================
# User Endpoints
# ===========================
@router.post("/user",
             tags=["User"],
             summary="Create User",
             description="Creates a new user with encrypted configuration")
async def create_user_route(
    request: Request,
    password: str = Body(..., embed=True, description="User password"),
    config: Dict[str, Any] = Body(..., embed=True, description="User configuration")
):
    if not config.get("debrid_services"):
        return JSONResponse(
            status_code=400,
            content={"error": "Configuration must include debrid_services"}
        )

    result = await create_user(password, config)
    if not result:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to create user"}
        )

    addon_url = f"{get_base_url(request)}/{result['uuid']}/{result['encrypted_password']}/manifest.json"

    return JSONResponse(content={
        "uuid": result["uuid"],
        "encrypted_password": result["encrypted_password"],
        "addon_url": addon_url
    })


@router.post("/user/{user_uuid}/config",
             tags=["User"],
             summary="Get User Config",
             description="Retrieves decrypted user configuration")
async def get_user_route(
    user_uuid: str = Path(..., description="User UUID"),
    password: str = Body(..., embed=True, description="User password")
):
    result = await get_user_config_detailed(user_uuid, password)

    if not result["success"]:
        error_code = result["error"]
        if error_code == "user_not_found":
            return JSONResponse(
                status_code=404,
                content={"error": "user_not_found", "message": "User not found"}
            )
        elif error_code == "invalid_password":
            return JSONResponse(
                status_code=401,
                content={"error": "invalid_password", "message": "Invalid password"}
            )
        else:
            return JSONResponse(
                status_code=500,
                content={"error": "server_error", "message": "Server error"}
            )

    return JSONResponse(content={"config": result["config"]})


@router.put("/user/{user_uuid}",
            tags=["User"],
            summary="Update User Config",
            description="Updates user configuration")
async def update_user_route(
    request: Request,
    user_uuid: str = Path(..., description="User UUID"),
    password: str = Body(..., embed=True, description="User password"),
    config: Dict[str, Any] = Body(..., embed=True, description="New configuration")
):
    if not config.get("debrid_services"):
        return JSONResponse(
            status_code=400,
            content={"error": "Configuration must include debrid_services"}
        )

    success = await update_user_config(user_uuid, password, config)
    if not success:
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid credentials or user not found"}
        )

    encrypted_password = encrypt_password_for_url(password)
    addon_url = f"{get_base_url(request)}/{user_uuid}/{encrypted_password}/manifest.json"

    return JSONResponse(content={
        "success": True,
        "addon_url": addon_url
    })


@router.post("/user/{user_uuid}/delete",
             tags=["User"],
             summary="Delete User",
             description="Deletes user account")
async def delete_user_route(
    user_uuid: str = Path(..., description="User UUID"),
    password: str = Body(..., embed=True, description="User password")
):
    result = await delete_user_detailed(user_uuid, password)

    if not result["success"]:
        error_code = result["error"]
        if error_code == "user_not_found":
            return JSONResponse(
                status_code=404,
                content={"error": "user_not_found", "message": "User not found"}
            )
        elif error_code == "invalid_password":
            return JSONResponse(
                status_code=401,
                content={"error": "invalid_password", "message": "Invalid password"}
            )
        else:
            return JSONResponse(
                status_code=500,
                content={"error": "server_error", "message": "Server error"}
            )

    return JSONResponse(content={"success": True})


@router.post("/user/verify",
             tags=["User"],
             summary="Verify Credentials",
             description="Verifies user credentials")
async def verify_user_route(
    user_uuid: str = Body(..., embed=True, description="User UUID"),
    password: str = Body(..., embed=True, description="User password")
):
    valid = await verify_user(user_uuid, password)
    return JSONResponse(content={"valid": valid})


# ===========================
# Secure Stremio Endpoints
# ===========================
@router.get("/{user_uuid}/{enc_password}/manifest.json",
            tags=["Stremio"],
            summary="Addon Manifest",
            description="Returns addon metadata for secure installation")
async def get_secure_manifest(
    request: Request,
    user_uuid: str = Path(..., description="User UUID"),
    enc_password: str = Path(..., description="Encrypted password")
):
    config = await get_config_from_url_params(user_uuid, enc_password)
    if not config:
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid credentials"}
        )

    manifest = settings.ADDON_MANIFEST.copy()

    debrid_services = config.get("debrid_services", [])
    if len(debrid_services) == 1:
        service_name = debrid_services[0].get("service", "alldebrid")
        abbr = DEBRID_ABBREVIATIONS.get(service_name, "AD")
        manifest["name"] = f"{settings.ADDON_NAME} | {abbr}"
    elif len(debrid_services) > 1:
        manifest["name"] = f"{settings.ADDON_NAME} | MULTI"

    if settings.HTTP_CACHE_ENABLED:
        etag = generate_etag(manifest)
        if check_etag_match(request, etag):
            return not_modified_response(etag)

        return CachedJSONResponse(
            content=manifest,
            cache_control=CachePolicies.manifest(),
            etag=etag,
            vary=["Accept", "Accept-Encoding"],
        )

    return JSONResponse(content=manifest)


@router.get("/{user_uuid}/{enc_password}/stream/{content_type}/{content_id}",
            tags=["Stremio"],
            summary="Stream Provider",
            description="Returns available streams for secure users")
async def get_secure_streams(
    request: Request,
    user_uuid: str = Path(..., description="User UUID"),
    enc_password: str = Path(..., description="Encrypted password"),
    content_type: ContentType = Path(..., description="Content type"),
    content_id: str = Path(..., description="Content identifier")
):
    config = await get_config_from_url_params(user_uuid, enc_password)
    if not config:
        return JSONResponse(content={"streams": []})

    config["_user_uuid"] = user_uuid
    config["_enc_password"] = enc_password

    content_id_formatted = content_id.replace(".json", "")

    try:
        base_url = str(request.base_url).rstrip("/")

        streams = await stream_service.get_streams(
            content_type=content_type.value,
            content_id=content_id_formatted,
            config=config,
            base_url=base_url
        )

        result = {"streams": streams}

        if settings.HTTP_CACHE_ENABLED:
            is_empty = len(streams) == 0
            cache_policy = CachePolicies.empty_results() if is_empty else CachePolicies.streams()
            cache_control = cache_policy.build()

            etag = generate_etag(result)
            if check_etag_match(request, etag):
                return not_modified_response(etag, cache_control=cache_control)

            return CachedJSONResponse(
                content=result,
                cache_control=cache_policy,
                etag=etag,
                vary=["Accept", "Accept-Encoding"],
            )

        return JSONResponse(content=result)

    except Exception as e:
        api_logger.error(f"Secure stream failed: {type(e).__name__}: {e}")
        return JSONResponse(content={"streams": []})


@router.get("/{user_uuid}/{enc_password}/configure",
            tags=["Configuration"],
            summary="Configuration Page",
            description="Modify existing secure configuration")
async def configure_secure_addon(
    user_uuid: str = Path(..., description="User UUID"),
    enc_password: str = Path(..., description="Encrypted password")
):
    with open("wastream/templates/index.html", "r", encoding="utf-8") as f:
        html_content = f.read()

    html_content = html_content.replace("{{CUSTOM_HTML}}", settings.CUSTOM_HTML)
    html_content = html_content.replace("{{ADDON_NAME}}", settings.ADDON_NAME)
    html_content = html_content.replace("{{VERSION}}", settings.ADDON_MANIFEST["version"])

    response = HTMLResponse(content=html_content)

    if settings.ADDON_PASSWORD:
        for key, value in NO_CACHE_HEADERS.items():
            response.headers[key] = value
    elif settings.HTTP_CACHE_ENABLED:
        response.headers["Cache-Control"] = CachePolicies.configure_page().build()
        response.headers["Vary"] = "Accept, Accept-Encoding"

    return response


# ===========================
# Utility Endpoints
# ===========================
@router.get("/resolve",
            tags=["Stremio"],
            summary="Resolve Link",
            description="Converts a link to a direct streaming URL")
async def resolve(
    link: str = Query(..., description="Link to resolve"),
    b64config: Optional[str] = Query(None, description="Base64 encoded configuration"),
    user_uuid: Optional[str] = Query(None, description="User UUID for secure auth"),
    enc_password: Optional[str] = Query(None, description="Encrypted password for secure auth"),
    season: Optional[str] = Query(None, description="Season number for series"),
    episode: Optional[str] = Query(None, description="Episode number for series"),
    service: Optional[str] = Query(None, description="Debrid service to use"),
    content_type: Optional[str] = Query(None, description="Content type (movie/series)"),
    title: Optional[str] = Query(None, description="Title for NZBDav"),
    source: Optional[str] = Query(None, description="Source (Wawacity, Darki-API, Free-Telecharger)"),
    hoster: Optional[str] = Query(None, description="Hoster name for status tracking")
):
    config = None

    if user_uuid and enc_password:
        config = await get_config_from_url_params(user_uuid, enc_password)
    elif b64config:
        config = validate_config(b64config)

    if not config:
        return FileResponse("wastream/public/fatal_error.mp4")

    debrid_services = config.get("debrid_services", [])
    if not debrid_services:
        return FileResponse("wastream/public/fatal_error.mp4")

    return await stream_service.resolve_link_with_response(link, config, season, episode, service, content_type, title, source, hoster)


@router.get("/playback/{token}/{filename}",
            tags=["Stremio"],
            summary="Playback",
            description="Resolves and redirects to streaming URL")
async def playback(
    token: str = Path(..., description="Encoded playback token"),
    filename: str = Path(..., description="Filename")
):
    data = decode_playback_token(token)
    if not data:
        return FileResponse("wastream/public/fatal_error.mp4")

    link = data.get("l")
    service = data.get("s")
    user_uuid = data.get("u")
    enc_password = data.get("p")
    b64config = data.get("c")
    season = data.get("se")
    episode = data.get("ep")
    content_type = data.get("ct")
    title = data.get("t")
    source = data.get("so")
    hoster = data.get("h")

    if not link:
        return FileResponse("wastream/public/fatal_error.mp4")

    config = None
    if user_uuid and enc_password:
        config = await get_config_from_url_params(user_uuid, enc_password)
    elif b64config:
        config = validate_config(b64config)

    if not config:
        return FileResponse("wastream/public/fatal_error.mp4")

    debrid_services = config.get("debrid_services", [])
    if not debrid_services:
        return FileResponse("wastream/public/fatal_error.mp4")

    return await stream_service.resolve_link_with_response(link, config, season, episode, service, content_type, title, source, hoster)


# ===========================
# Configuration Options Endpoints
# ===========================
@router.get("/available/languages",
            tags=["Configuration"],
            summary="Available Languages",
            description="Returns available language options")
async def get_available_languages():
    return JSONResponse(content={
        "languages": AVAILABLE_LANGUAGES
    })


@router.get("/available/resolutions",
            tags=["Configuration"],
            summary="Available Resolutions",
            description="Returns available quality options")
async def get_available_resolutions():
    return JSONResponse(content={
        "resolutions": AVAILABLE_RESOLUTIONS
    })


@router.get("/available/services",
            tags=["Configuration"],
            summary="Available Services",
            description="Returns available debrid services with their supported hosts and sources")
async def get_available_services():
    return JSONResponse(content={
        "alldebrid": {
            "hosts": settings.ALLDEBRID_SUPPORTED_HOSTS,
            "sources": settings.ALLDEBRID_SUPPORTED_SOURCES
        },
        "torbox": {
            "hosts": settings.TORBOX_SUPPORTED_HOSTS,
            "sources": settings.TORBOX_SUPPORTED_SOURCES
        },
        "premiumize": {
            "hosts": settings.PREMIUMIZE_SUPPORTED_HOSTS,
            "sources": settings.PREMIUMIZE_SUPPORTED_SOURCES
        },
        "1fichier": {
            "hosts": settings.ONEFICHIER_SUPPORTED_HOSTS,
            "sources": settings.ONEFICHIER_SUPPORTED_SOURCES
        },
        "nzbdav": {
            "hosts": [],
            "sources": settings.NZBDAV_SUPPORTED_SOURCES
        },
        "wasource": {
            "hosts": settings.WASOURCE_SUPPORTED_HOSTS
        }
    })


@router.get("/config/password",
            tags=["Configuration"],
            summary="Password Status",
            description="Checks if password protection is enabled")
async def get_password_config():
    return JSONResponse(content={
        "password_required": bool(settings.ADDON_PASSWORD.strip())
    })


@router.post("/config/verify-password",
             tags=["Configuration"],
             summary="Verify Password",
             description="Validates the provided password")
async def verify_password(password: str = Body(..., embed=True, description="Password to verify")):
    if not settings.ADDON_PASSWORD.strip():
        return JSONResponse(content={"valid": True})

    valid_passwords = [pwd.strip() for pwd in settings.ADDON_PASSWORD.split(",") if pwd.strip()]
    is_valid = any(secrets.compare_digest(password, pwd) for pwd in valid_passwords)

    return JSONResponse(content={"valid": is_valid})


@router.get("/config/verify-api-key",
            tags=["Configuration"],
            summary="Verify API Key",
            description="Validates debrid service API key or TMDB token")
async def verify_api_key(
    service: ServiceType = Query(..., description="Service"),
    api_key: str = Query(..., description="API key"),
    nzbdav_url: Optional[str] = Query(None, description="NZBDav URL"),
    webdav_user: Optional[str] = Query(None, description="WebDAV username"),
    webdav_password: Optional[str] = Query(None, description="WebDAV password")
):
    if not api_key or not api_key.strip():
        return JSONResponse(content={"valid": False})

    api_key = api_key.strip()

    try:
        if service == ServiceType.alldebrid:
            response = await http_client.get(
                f"{settings.ALLDEBRID_API_URL}/user",
                params={"agent": settings.ADDON_NAME, "apikey": api_key},
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    return JSONResponse(content={"valid": True})
            return JSONResponse(content={"valid": False})

        elif service == ServiceType.torbox:
            response = await http_client.get(
                f"{settings.TORBOX_API_URL}/user/me",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return JSONResponse(content={"valid": True})
            return JSONResponse(content={"valid": False})

        elif service == ServiceType.premiumize:
            response = await http_client.get(
                f"{settings.PREMIUMIZE_API_URL}/account/info",
                params={"apikey": api_key},
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    return JSONResponse(content={"valid": True})
            return JSONResponse(content={"valid": False})

        elif service == ServiceType.onefichier:
            response = await http_client.post(
                f"{settings.ONEFICHIER_API_URL}/folder/ls.cgi",
                json={"folder_id": 0},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": settings.ADDON_NAME
                },
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "KO":
                    return JSONResponse(content={"valid": False})
                return JSONResponse(content={"valid": True})
            return JSONResponse(content={"valid": False})

        elif service == ServiceType.nzbdav:
            api_logger.debug(f"NZBDav verify - URL: {bool(nzbdav_url)}, user: {bool(webdav_user)}, pass: {bool(webdav_password)}")
            if not nzbdav_url or not nzbdav_url.strip():
                return JSONResponse(content={"valid": False, "error": "Missing NZBDav URL"})
            if not webdav_user or not webdav_user.strip():
                return JSONResponse(content={"valid": False, "error": "Missing WebDAV user"})
            if not webdav_password or not webdav_password.strip():
                return JSONResponse(content={"valid": False, "error": "Missing WebDAV password"})

            nzbdav_url = nzbdav_url.strip().rstrip("/")

            if not nzbdav_url.startswith(("http://", "https://")):
                return JSONResponse(content={"valid": False, "error": "Only HTTP/HTTPS URLs are allowed"})

            api_url = f"{nzbdav_url}/api"

            api_logger.debug("NZBDav SABnzbd check")
            response = await http_client.get(
                api_url,
                params={"mode": "queue", "apikey": api_key, "output": "json"},
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            api_logger.debug(f"NZBDav SABnzbd response: {response.status_code}")
            if response.status_code != 200:
                return JSONResponse(content={"valid": False, "error": "Invalid SABnzbd API key"})

            auth_string = b64encode(f"{webdav_user.strip()}:{webdav_password.strip()}".encode()).decode()
            content_url = f"{nzbdav_url}/content/"

            api_logger.debug("NZBDav WebDAV check")
            webdav_response = await http_client.request(
                "PROPFIND",
                content_url,
                headers={"Depth": "0", "Authorization": f"Basic {auth_string}"},
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            api_logger.debug(f"NZBDav WebDAV response: {webdav_response.status_code}")
            if webdav_response.status_code == 401:
                return JSONResponse(content={"valid": False, "error": "Invalid WebDAV credentials"})
            if webdav_response.status_code not in [200, 207, 404]:
                return JSONResponse(content={"valid": False, "error": f"WebDAV error: {webdav_response.status_code}"})

            return JSONResponse(content={"valid": True})

        elif service == ServiceType.tmdb:
            response = await http_client.get(
                f"{settings.TMDB_API_URL}/configuration",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )
            if response.status_code == 200:
                return JSONResponse(content={"valid": True})
            return JSONResponse(content={"valid": False})

        else:
            return JSONResponse(content={"valid": False})

    except Exception:
        return JSONResponse(content={"valid": False})


# ===========================
# Health Check Endpoint
# ===========================
@router.get("/health",
            tags=["General"],
            summary="Health Check",
            description="Returns the current health status of the service")
async def health_check():
    start_time = time.time()
    health_status = {
        "status": "healthy",
        "version": settings.ADDON_MANIFEST["version"],
        "timestamp": int(time.time()),
        "checks": {}
    }

    health_status["checks"]["server"] = {
        "status": "ok",
        "message": "Addon server running"
    }

    try:
        await database.fetch_val("SELECT 1")
        health_status["checks"]["database"] = {
            "status": "ok",
            "message": "Database connection active"
        }
    except Exception as e:
        health_status["checks"]["database"] = {
            "status": "error",
            "message": f"Database error: {type(e).__name__}"
        }
        health_status["status"] = "degraded"

    if settings.WAWACITY_URL:
        wastream_start = time.time()
        try:
            response = await http_client.get(settings.WAWACITY_URL, timeout=settings.HEALTH_CHECK_TIMEOUT)
            wawacity_time = round((time.time() - wastream_start) * 1000)

            if response.status_code == 200:
                health_status["checks"]["wawacity"] = {
                    "status": "ok",
                    "message": "Wawacity accessible",
                    "response_time_ms": wawacity_time
                }
            else:
                health_status["checks"]["wawacity"] = {
                    "status": "error",
                    "message": f"Wawacity HTTP {response.status_code}",
                    "response_time_ms": wawacity_time
                }
                health_status["status"] = "degraded"

        except Exception as e:
            wawacity_time = round((time.time() - wastream_start) * 1000)
            health_status["checks"]["wawacity"] = {
                "status": "error",
                "message": f"Wawacity unreachable: {type(e).__name__}",
                "response_time_ms": wawacity_time
            }
            health_status["status"] = "unhealthy"
    else:
        health_status["checks"]["wawacity"] = {
            "status": "disabled",
            "message": "Wawacity not configured"
        }

    if settings.FREE_TELECHARGER_URL:
        free_telecharger_start = time.time()
        try:
            response = await http_client.get(settings.FREE_TELECHARGER_URL, timeout=settings.HEALTH_CHECK_TIMEOUT)
            free_telecharger_time = round((time.time() - free_telecharger_start) * 1000)

            if response.status_code == 200:
                health_status["checks"]["free_telecharger"] = {
                    "status": "ok",
                    "message": "Free-Telecharger accessible",
                    "response_time_ms": free_telecharger_time
                }
            else:
                health_status["checks"]["free_telecharger"] = {
                    "status": "error",
                    "message": f"Free-Telecharger HTTP {response.status_code}",
                    "response_time_ms": free_telecharger_time
                }
                health_status["status"] = "degraded"

        except Exception as e:
            free_telecharger_time = round((time.time() - free_telecharger_start) * 1000)
            health_status["checks"]["free_telecharger"] = {
                "status": "error",
                "message": f"Free-Telecharger unreachable: {type(e).__name__}",
                "response_time_ms": free_telecharger_time
            }
            health_status["status"] = "unhealthy"
    else:
        health_status["checks"]["free_telecharger"] = {
            "status": "disabled",
            "message": "Free-Telecharger not configured"
        }

    if settings.DARKI_API_URL:
        darki_api_start = time.time()
        try:
            response = await http_client.get(f"{settings.DARKI_API_URL}/health", timeout=settings.HEALTH_CHECK_TIMEOUT)
            darki_api_time = round((time.time() - darki_api_start) * 1000)

            if response.status_code == 200:
                data = response.json()
                darkiworld_status = data.get("darkiworld_status", "Unknown")
                api_status = data.get("status", "Unknown")

                if api_status == "healthy" and darkiworld_status == "reachable":
                    health_status["checks"]["darki_api"] = {
                        "status": "ok",
                        "message": "Darki-API accessible",
                        "response_time_ms": darki_api_time,
                        "darkiworld_status": darkiworld_status
                    }
                else:
                    health_status["checks"]["darki_api"] = {
                        "status": "degraded",
                        "message": f"Darki-API degraded (API: {api_status}, darkiworld: {darkiworld_status})",
                        "response_time_ms": darki_api_time,
                        "darkiworld_status": darkiworld_status
                    }
                    health_status["status"] = "degraded"
            else:
                health_status["checks"]["darki_api"] = {
                    "status": "error",
                    "message": f"Darki-API HTTP {response.status_code}",
                    "response_time_ms": darki_api_time
                }
                health_status["status"] = "degraded"

        except Exception as e:
            darki_api_time = round((time.time() - darki_api_start) * 1000)
            health_status["checks"]["darki_api"] = {
                "status": "error",
                "message": f"Darki-API unreachable: {type(e).__name__}",
                "response_time_ms": darki_api_time
            }
            health_status["status"] = "degraded"
    else:
        health_status["checks"]["darki_api"] = {
            "status": "disabled",
            "message": "Darki-API not configured"
        }

    if settings.MOVIX_API_URL:
        movix_start = time.time()
        try:
            headers = {}
            if settings.MOVIX_URL:
                headers["Origin"] = settings.MOVIX_URL
                headers["Referer"] = f"{settings.MOVIX_URL}/"

            response = await http_client.get(f"{settings.MOVIX_API_URL}/api/search", params={"title": "test"}, headers=headers, timeout=settings.HEALTH_CHECK_TIMEOUT)
            movix_time = round((time.time() - movix_start) * 1000)

            if response.status_code < 400:
                health_status["checks"]["movix"] = {
                    "status": "ok",
                    "message": "Movix accessible",
                    "response_time_ms": movix_time
                }
            else:
                health_status["checks"]["movix"] = {
                    "status": "error",
                    "message": f"Movix HTTP {response.status_code}",
                    "response_time_ms": movix_time
                }
                health_status["status"] = "degraded"

        except Exception as e:
            movix_time = round((time.time() - movix_start) * 1000)
            health_status["checks"]["movix"] = {
                "status": "error",
                "message": f"Movix unreachable: {type(e).__name__}",
                "response_time_ms": movix_time
            }
            health_status["status"] = "degraded"
    else:
        health_status["checks"]["movix"] = {
            "status": "disabled",
            "message": "Movix not configured"
        }

    if settings.WEBSHARE_URL:
        webshare_start = time.time()
        try:
            response = await http_client.get(settings.WEBSHARE_URL, timeout=settings.HEALTH_CHECK_TIMEOUT)
            webshare_time = round((time.time() - webshare_start) * 1000)

            if response.status_code == 200:
                health_status["checks"]["webshare"] = {
                    "status": "ok",
                    "message": "Webshare accessible",
                    "response_time_ms": webshare_time
                }
            else:
                health_status["checks"]["webshare"] = {
                    "status": "error",
                    "message": f"Webshare HTTP {response.status_code}",
                    "response_time_ms": webshare_time
                }
                health_status["status"] = "degraded"

        except Exception as e:
            webshare_time = round((time.time() - webshare_start) * 1000)
            health_status["checks"]["webshare"] = {
                "status": "error",
                "message": f"Webshare unreachable: {type(e).__name__}",
                "response_time_ms": webshare_time
            }
            health_status["status"] = "unhealthy"
    else:
        health_status["checks"]["webshare"] = {
            "status": "disabled",
            "message": "Webshare not configured"
        }

    if settings.PROXY_URL:
        try:
            test_response = await http_client.get("https://httpbin.org/ip", timeout=settings.HEALTH_CHECK_TIMEOUT)
            if test_response.status_code == 200:
                health_status["checks"]["proxy"] = {
                    "status": "ok",
                    "message": "Proxy functional"
                }
            else:
                health_status["checks"]["proxy"] = {
                    "status": "error",
                    "message": "Proxy not responding"
                }
                health_status["status"] = "degraded"
        except Exception as e:
            health_status["checks"]["proxy"] = {
                "status": "error",
                "message": f"Proxy error: {type(e).__name__}"
            }
            health_status["status"] = "degraded"
    else:
        health_status["checks"]["proxy"] = {
            "status": "disabled",
            "message": "No proxy configured"
        }

    total_time = round((time.time() - start_time) * 1000)
    health_status["total_response_time_ms"] = total_time

    return health_status


# ===========================
# Admin Session Manager
# ===========================
SESSION_DURATION = 24 * 60 * 60  # 24 hours


class AdminSessionManager:
    def __init__(self):
        self._sessions: Dict[str, Dict] = {}

    def create_session(self) -> str:
        self._cleanup_expired()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = {
            "created": time.time(),
            "expires": time.time() + SESSION_DURATION
        }
        return token

    def validate_session(self, token: str) -> bool:
        if not token or token not in self._sessions:
            return False
        if self._sessions[token]["expires"] < time.time():
            del self._sessions[token]
            return False
        return True

    def delete_session(self, token: str):
        if token in self._sessions:
            del self._sessions[token]

    def _cleanup_expired(self):
        now = time.time()
        expired = [t for t, s in self._sessions.items() if s["expires"] < now]
        for t in expired:
            del self._sessions[t]


admin_sessions = AdminSessionManager()


# ===========================
# Admin Helper Functions
# ===========================
def _is_admin_enabled() -> bool:
    return bool(settings.ADMIN_PASSWORD and settings.ADMIN_PASSWORD.strip())


def _verify_admin_password(password: str) -> bool:
    if not _is_admin_enabled():
        return False
    return secrets.compare_digest(password, settings.ADMIN_PASSWORD.strip())


def _verify_admin_token(token: str) -> bool:
    if not _is_admin_enabled():
        return False
    return admin_sessions.validate_session(token)


# ===========================
# Admin Web Interface
# ===========================
@router.get("/admin",
            tags=["Admin"],
            summary="Admin Login Page",
            description="Returns the admin login page")
async def admin_login_page(admin_token: Optional[str] = Cookie(None)):
    if not _is_admin_enabled():
        return RedirectResponse("/")

    if admin_token and _verify_admin_token(admin_token):
        return RedirectResponse("/admin/dashboard")

    with open("wastream/templates/login.html", "r", encoding="utf-8") as f:
        html_content = f.read()

    html_content = html_content.replace("{{ADDON_NAME}}", settings.ADDON_NAME)
    html_content = html_content.replace("{{VERSION}}", settings.ADDON_MANIFEST["version"])

    return HTMLResponse(content=html_content)


@router.get("/admin/dashboard",
            tags=["Admin"],
            summary="Admin Dashboard",
            description="Returns the admin dashboard")
async def admin_dashboard(admin_token: Optional[str] = Cookie(None)):
    if not _is_admin_enabled():
        return RedirectResponse("/")

    if not admin_token or not _verify_admin_token(admin_token):
        return RedirectResponse("/admin")

    with open("wastream/templates/dashboard.html", "r", encoding="utf-8") as f:
        html_content = f.read()

    html_content = html_content.replace("{{ADDON_NAME}}", settings.ADDON_NAME)
    html_content = html_content.replace("{{VERSION}}", settings.ADDON_MANIFEST["version"])
    html_content = html_content.replace("{{TMDB_CONFIGURED}}", "true" if settings.TMDB_API_KEY else "false")
    html_content = html_content.replace("{{LANGUAGE_PATTERNS}}", json.dumps(LANGUAGES))
    html_content = html_content.replace("{{AVAILABLE_RESOLUTIONS}}", json.dumps(AVAILABLE_RESOLUTIONS))

    return HTMLResponse(content=html_content)


# ===========================
# Admin Auth Endpoints
# ===========================
@router.post("/admin/login",
             tags=["Admin"],
             summary="Admin Login",
             description="Authenticates admin and returns session token")
async def admin_login(
    password: str = Body(..., embed=True, description="Admin password")
):
    if not _is_admin_enabled():
        return JSONResponse(
            status_code=403,
            content={"error": "Admin disabled"}
        )

    if not _verify_admin_password(password):
        api_logger.warning("Admin login failed → Invalid password")
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid password"}
        )

    token = admin_sessions.create_session()

    response = JSONResponse(content={"success": True})
    response.set_cookie(
        key="admin_token",
        value=token,
        httponly=True,
        samesite="strict",
        max_age=SESSION_DURATION
    )

    api_logger.info("Admin login successful")
    return response


@router.post("/admin/logout",
             tags=["Admin"],
             summary="Admin Logout",
             description="Clears admin session")
async def admin_logout(admin_token: Optional[str] = Cookie(None)):
    if admin_token:
        admin_sessions.delete_session(admin_token)

    response = JSONResponse(content={"success": True})
    response.delete_cookie(key="admin_token")
    return response


# ===========================
# Admin API Endpoints
# ===========================
@router.get("/admin/api/stats",
            tags=["Admin"],
            summary="Get Statistics",
            description="Returns statistics summary from database")
async def admin_get_stats(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    stats = await get_stats_summary()
    return JSONResponse(content=stats)


@router.get("/admin/api/pastebin-scraper",
            tags=["Admin"],
            summary="Pastebin Scraper Status",
            description="Returns current pastebin scraper status")
async def admin_get_pastebin_scraper_status(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    return JSONResponse(content=get_pastebin_scraper_status())


@router.get("/admin/api/dead-links",
            tags=["Admin"],
            summary="Get Dead Links",
            description="Returns list of dead links")
async def admin_get_dead_links(
    admin_token: Optional[str] = Cookie(None),
    limit: int = Query(100, description="Max results"),
    offset: int = Query(0, description="Offset for pagination"),
    url: Optional[str] = Query(None, description="Filter by URL (partial match)")
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    dead_links = await get_dead_links_list(limit, offset, url)
    return JSONResponse(content=dead_links)


@router.delete("/admin/api/dead-links",
               tags=["Admin"],
               summary="Delete Dead Links",
               description="Delete selected or all dead links")
async def admin_delete_dead_links(
    admin_token: Optional[str] = Cookie(None),
    urls: Optional[list] = Body(None, embed=True, description="List of URLs to delete"),
    delete_all: bool = Query(False, description="Delete all dead links")
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if delete_all:
        deleted = await delete_all_dead_links()
    else:
        deleted = await delete_dead_links(urls or [])

    return JSONResponse(content={"deleted": deleted})


@router.post("/admin/api/dead-links",
             tags=["Admin"],
             summary="Add Dead Links",
             description="Add dead links manually")
async def admin_add_dead_links(
    admin_token: Optional[str] = Cookie(None),
    urls: list = Body(..., embed=True, description="List of URLs to add")
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    added = await add_dead_links(urls or [])
    return JSONResponse(content={"added": added})


@router.get("/admin/api/logs",
            tags=["Admin"],
            summary="Get Logs",
            description="Returns logs with optional filtering")
async def admin_get_logs(
    admin_token: Optional[str] = Cookie(None),
    since: float = Query(0, description="Unix timestamp to get logs since"),
    context: Optional[str] = Query(None, description="Filter by context (ADDON, API, STREAM, etc.)"),
    level: Optional[str] = Query(None, description="Minimum log level (DEBUG, INFO, WARNING, ERROR)"),
    prefix: Optional[str] = Query(None, description="Filter by message prefix (Early-Stop, Torbox, etc.)"),
    search: Optional[str] = Query(None, description="Search text in log messages")
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    logs = get_logs(since=since, context=context, level=level, prefix=prefix, search=search)
    contexts = get_available_contexts()
    prefixes = get_available_prefixes()

    return JSONResponse(content={
        "logs": logs,
        "total": len(logs),
        "contexts": contexts,
        "prefixes": prefixes
    })


@router.delete("/admin/api/logs",
               tags=["Admin"],
               summary="Clear Logs",
               description="Clears the log buffer")
async def admin_clear_logs(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    clear_logs()
    api_logger.info("Log buffer cleared by admin")

    return JSONResponse(content={"success": True})


@router.delete("/admin/api/cache",
               tags=["Admin"],
               summary="Purge Cached Links",
               description="Purge all cached links from the content cache table")
async def admin_purge_cached_links(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    deleted = await purge_cached_links()
    api_logger.info(f"Cached links purged by admin ({deleted} entries)")

    return JSONResponse(content={"deleted": deleted})


# ===========================
# Admin Health Check Endpoints
# ===========================
@router.get("/admin/api/health",
            tags=["Admin"],
            summary="Get Health Status",
            description="Returns the health status of all configured sources")
async def admin_get_health(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    return JSONResponse(content=get_health_status())


@router.post("/admin/api/health/check",
             tags=["Admin"],
             summary="Trigger Health Check",
             description="Manually triggers a health check on all sources")
async def admin_trigger_health_check(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    result = await check_all_sources(force=True)
    api_logger.info("Health check triggered by admin")

    return JSONResponse(content=result)


@router.post("/admin/api/health/services",
             tags=["Admin"],
             summary="Trigger Services Check",
             description="Manually triggers a health check on services only (Database, Proxy)")
async def admin_check_services(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    result = await check_services_only()
    api_logger.info("Services check triggered by admin")

    return JSONResponse(content=result)


@router.post("/admin/api/health/sources",
             tags=["Admin"],
             summary="Trigger Sources Check",
             description="Manually triggers a health check on content sources only")
async def admin_check_sources(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    result = await check_sources_only()
    api_logger.info("Sources check triggered by admin")

    return JSONResponse(content=result)


@router.post("/admin/api/health/hosters",
             tags=["Admin"],
             summary="Refresh Hoster Status",
             description="Refreshes hoster status for TorBox (API) and returns current status for all")
async def admin_check_hosters(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    from wastream.services.hoster_status import get_hoster_status, _hoster_status_cache
    _hoster_status_cache["torbox"]["last_check"] = None
    await get_hoster_status("torbox")
    api_logger.info("Hoster status check triggered by admin")

    return JSONResponse(content=get_health_status())


@router.get("/admin/api/system",
            tags=["Admin"],
            summary="Get System Info",
            description="Returns real-time system information (CPU, RAM)")
async def admin_get_system_info(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    return JSONResponse(content=get_system_info())


# ===========================
# WASource Admin API Endpoints
# ===========================
@router.get("/admin/api/wasource",
            tags=["Admin"],
            summary="Get WASource Links",
            description="Returns list of custom WASource links")
async def admin_get_wasource_links(
    admin_token: Optional[str] = Cookie(None),
    limit: int = Query(100, description="Max results"),
    offset: int = Query(0, description="Offset for pagination"),
    imdb_id: Optional[str] = Query(None, description="Filter by IMDB ID"),
    title: Optional[str] = Query(None, description="Filter by title (partial match)"),
    release_name: Optional[str] = Query(None, description="Filter by release name (partial match)"),
    url: Optional[str] = Query(None, description="Filter by URL (partial match)")
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    links = await get_wasource_links(limit, offset, imdb_id, title, release_name, url)
    return JSONResponse(content=links)


@router.get("/admin/api/wasource/stats",
            tags=["Admin"],
            summary="Get WASource Stats",
            description="Returns statistics about WASource links")
async def admin_get_wasource_stats(
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    stats = await get_wasource_stats()
    return JSONResponse(content=stats)


@router.post("/admin/api/wasource",
             tags=["Admin"],
             summary="Add WASource Links",
             description="Add multiple custom links to WASource for the same content")
async def admin_add_wasource_links(
    admin_token: Optional[str] = Cookie(None),
    imdb_id: str = Body(..., embed=True, description="IMDB ID (e.g., tt1234567)"),
    tmdb_id: Optional[str] = Body(None, embed=True, description="TMDB ID (optional, auto-filled via Fetch)"),
    title: str = Body(..., embed=True, description="Title for search (e.g., Dan Da Dan)"),
    year: int = Body(..., embed=True, description="Release year (auto-filled via Fetch)"),
    release_name: Optional[str] = Body(None, embed=True, description="Release name for display"),
    quality: Optional[str] = Body(None, embed=True, description="Quality (720p, 1080p, 4K)"),
    language: Optional[str] = Body(None, embed=True, description="Language (FRENCH, MULTI, VOSTFR)"),
    size: Optional[int] = Body(None, embed=True, description="File size in bytes"),
    season: Optional[int] = Body(None, embed=True, description="Season number (for series)"),
    episode: Optional[int] = Body(None, embed=True, description="Episode number (for series)"),
    urls: list = Body(..., embed=True, description="List of {url, host} objects")
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if not imdb_id or not imdb_id.startswith("tt"):
        return JSONResponse(status_code=400, content={"error": "Invalid IMDB ID"})

    if not title or not title.strip():
        return JSONResponse(status_code=400, content={"error": "Title is required"})

    if not urls or not isinstance(urls, list) or len(urls) == 0:
        return JSONResponse(status_code=400, content={"error": "At least one URL is required"})

    result = await add_wasource_links_bulk(
        imdb_id=imdb_id,
        title=title,
        release_name=release_name,
        quality=quality,
        language=language,
        size=size,
        season=season,
        episode=episode,
        urls=urls,
        tmdb_id=tmdb_id,
        year=year
    )

    return JSONResponse(content=result)


@router.put("/admin/api/wasource/{content_id}",
            tags=["Admin"],
            summary="Update WASource Content",
            description="Update an existing WASource content entry")
async def admin_update_wasource_content_route(
    content_id: int = Path(..., description="Content ID"),
    admin_token: Optional[str] = Cookie(None),
    imdb_id: Optional[str] = Body(None, embed=True),
    tmdb_id: Optional[str] = Body(None, embed=True),
    title: Optional[str] = Body(None, embed=True),
    year: Optional[int] = Body(None, embed=True),
    release_name: Optional[str] = Body(None, embed=True),
    quality: Optional[str] = Body(None, embed=True),
    language: Optional[str] = Body(None, embed=True),
    size: Optional[int] = Body(None, embed=True),
    season: Optional[int] = Body(None, embed=True),
    episode: Optional[int] = Body(None, embed=True),
    urls: Optional[list] = Body(None, embed=True)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    success = await update_wasource_content(content_id, imdb_id, tmdb_id, title, year, release_name, quality, language, size, season, episode, urls)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Content not found"})

    return JSONResponse(content={"success": True})


@router.delete("/admin/api/wasource/{content_id}/url",
               tags=["Admin"],
               summary="Delete WASource URL",
               description="Delete a specific URL from WASource content")
async def admin_delete_wasource_url_route(
    content_id: int = Path(..., description="Content ID"),
    url: str = Body(..., embed=True, description="URL to delete"),
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    success = await delete_wasource_url(content_id, url)
    if not success:
        return JSONResponse(status_code=404, content={"error": "URL not found"})

    return JSONResponse(content={"success": True})


@router.delete("/admin/api/wasource",
               tags=["Admin"],
               summary="Delete WASource Links",
               description="Delete selected or all WASource links (release-specific)")
async def admin_delete_wasource_links(
    admin_token: Optional[str] = Cookie(None),
    ids: Optional[List[str]] = Body(None, embed=True, description="List of release IDs (format: contentId_releaseIdx)"),
    delete_all: bool = Query(False, description="Delete all WASource links")
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if delete_all:
        deleted = await delete_all_wasource_links()
    else:
        deleted = await delete_wasource_links(ids or [])

    return JSONResponse(content={"deleted": deleted})


# ===========================
# Remote API Public Endpoints
# ===========================
remote_security = HTTPBearer(
    scheme_name="Remote API Key",
    description="API key for remote access (wa_xxx)",
    auto_error=False
)


async def _get_remote_api_key(credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[Dict[str, Any]]:
    if credentials and credentials.credentials:
        return await validate_api_key(credentials.credentials)
    return None


@router.get("/remote/permissions",
            tags=["Remote"],
            summary="Get Permissions",
            description="Returns permissions for the provided API key")
async def remote_get_permissions(credentials: Optional[HTTPAuthorizationCredentials] = Depends(remote_security)):
    key_data = await _get_remote_api_key(credentials)

    if not key_data:
        return JSONResponse(status_code=401, content={"error": "Invalid API key"})

    return JSONResponse(content={
        "instance_name": settings.ADDON_NAME,
        "permissions": key_data["permissions"],
        "key_name": key_data["name"]
    })


@router.post("/remote/dead-links/check",
             tags=["Remote"],
             summary="Check Dead Links",
             description="Check if URLs are marked as dead (batch)")
async def remote_check_dead_links(
    urls: List[str] = Body(..., embed=True, description="List of URLs to check"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(remote_security)
):
    key_data = await _get_remote_api_key(credentials)

    if not key_data:
        return JSONResponse(status_code=401, content={"error": "Invalid API key"})

    if not key_data["permissions"].get("dead_links_read"):
        return JSONResponse(status_code=403, content={"error": "Permission denied"})

    if not urls or len(urls) > 500:
        return JSONResponse(status_code=400, content={"error": "Invalid request (0-500 URLs)"})

    current_time = int(time.time())
    results = {}

    placeholders = ", ".join([f":url{i}" for i in range(len(urls))])
    params = {f"url{i}": url for i, url in enumerate(urls)}

    rows = await database.fetch_all(
        f"SELECT url, expires_at FROM dead_links WHERE url IN ({placeholders})",
        params
    )

    for row in rows:
        expires_at = row["expires_at"]
        if expires_at == -1 or expires_at > current_time:
            results[row["url"]] = True

    remote_logger.debug(f"[Request] Dead-links check by {key_data['name']}: {len(urls)} URLs, {len(results)} dead")
    return JSONResponse(content={"dead_links": results, "checked": len(urls)})


@router.get("/remote/cache/check",
            tags=["Remote"],
            summary="Check Cache",
            description="Check if content is cached")
async def remote_check_cache(
    content_type: str = Query(..., description="Content type"),
    title: str = Query(..., description="Content title"),
    year: Optional[str] = Query(None, description="Content year"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(remote_security)
):
    key_data = await _get_remote_api_key(credentials)

    if not key_data:
        return JSONResponse(status_code=401, content={"error": "Invalid API key"})

    if not key_data["permissions"].get("cache_read"):
        return JSONResponse(status_code=403, content={"error": "Permission denied"})

    cache_key = create_cache_key(content_type, title, year)

    current_time = int(time.time())
    result = await database.fetch_one(
        "SELECT content FROM content_cache WHERE cache_key = :cache_key AND (expires_at = -1 OR expires_at > :current_time)",
        {"cache_key": cache_key, "current_time": current_time}
    )

    if result:
        content = json.loads(result["content"])
        remote_logger.debug(f"[Request] Cache hit by {key_data['name']}: {content_type}/{title} ({year})")
        return JSONResponse(content={"found": True, "content": content})

    return JSONResponse(content={"found": False})


@router.get("/remote/wasource/search",
            tags=["Remote"],
            summary="Search WASource",
            description="Search WASource by IMDB ID or title")
async def remote_search_wasource(
    imdb_id: Optional[str] = Query(None, description="IMDB ID"),
    title: Optional[str] = Query(None, description="Title"),
    year: Optional[int] = Query(None, description="Year"),
    season: Optional[int] = Query(None, description="Season number"),
    episode: Optional[int] = Query(None, description="Episode number"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(remote_security)
):
    key_data = await _get_remote_api_key(credentials)

    if not key_data:
        return JSONResponse(status_code=401, content={"error": "Invalid API key"})

    if not key_data["permissions"].get("wasource_read"):
        return JSONResponse(status_code=403, content={"error": "Permission denied"})

    if not imdb_id and not title:
        return JSONResponse(status_code=400, content={"error": "imdb_id or title required"})

    results = []

    if imdb_id:
        results = await get_links_by_imdb(imdb_id, season, episode)
    elif title:
        results = await get_links_by_title(title, year, season, episode)

    remote_logger.debug(f"[Request] WASource search by {key_data['name']}: {imdb_id or title}, {len(results)} results")
    return JSONResponse(content={"results": results, "count": len(results)})


# ===========================
# Admin Remote API Keys Endpoints
# ===========================
@router.get("/admin/api/remote/stats",
            tags=["Admin"],
            summary="Get Remote Stats",
            description="Returns remote system statistics")
async def admin_get_remote_stats(admin_token: Optional[str] = Cookie(None)):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    stats = await get_remote_stats()
    return JSONResponse(content=stats)


@router.get("/admin/api/remote/api-keys",
            tags=["Admin"],
            summary="Get API Keys",
            description="Returns all API keys")
async def admin_get_api_keys(admin_token: Optional[str] = Cookie(None)):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    keys = await get_api_keys()
    return JSONResponse(content={"api_keys": keys})


@router.post("/admin/api/remote/api-keys",
             tags=["Admin"],
             summary="Create API Key",
             description="Creates a new API key")
async def admin_create_api_key(
    admin_token: Optional[str] = Cookie(None),
    name: str = Body(..., embed=True, description="Key name"),
    permissions: Dict[str, bool] = Body(..., embed=True, description="Permissions")
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if not name or not name.strip():
        return JSONResponse(status_code=400, content={"error": "Name is required"})

    result = await create_api_key(name.strip(), permissions)

    if not result["success"]:
        return JSONResponse(status_code=500, content={"error": result.get("error", "Failed to create key")})

    api_logger.info(f"Remote API key created: {name}")
    return JSONResponse(content=result)


@router.put("/admin/api/remote/api-keys/{key_id}",
            tags=["Admin"],
            summary="Update API Key",
            description="Updates an API key")
async def admin_update_api_key(
    key_id: int = Path(..., description="Key ID"),
    admin_token: Optional[str] = Cookie(None),
    name: Optional[str] = Body(None, embed=True),
    permissions: Optional[Dict[str, bool]] = Body(None, embed=True),
    enabled: Optional[bool] = Body(None, embed=True)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    success = await update_api_key(key_id, name, permissions, enabled)
    if not success:
        return JSONResponse(status_code=404, content={"error": "API key not found"})

    api_logger.info(f"Remote API key updated: {key_id}")
    return JSONResponse(content={"success": True})


@router.delete("/admin/api/remote/api-keys/{key_id}",
               tags=["Admin"],
               summary="Delete API Key",
               description="Deletes an API key")
async def admin_delete_api_key(
    key_id: int = Path(..., description="Key ID"),
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    success = await delete_api_key(key_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Key not found"})

    api_logger.info(f"Remote API key deleted: {key_id}")
    return JSONResponse(content={"success": True})


# ===========================
# Admin Remote Instances Endpoints
# ===========================
@router.get("/admin/api/remote/instances",
            tags=["Admin"],
            summary="Get Remote Instances",
            description="Returns all remote instances")
async def admin_get_remote_instances(admin_token: Optional[str] = Cookie(None)):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    instances = await get_remote_instances()
    return JSONResponse(content={"instances": instances})


@router.post("/admin/api/remote/instances",
             tags=["Admin"],
             summary="Add Remote Instance",
             description="Adds a new remote instance")
async def admin_add_remote_instance(
    admin_token: Optional[str] = Cookie(None),
    name: str = Body(..., embed=True, description="Instance name"),
    url: str = Body(..., embed=True, description="Instance URL"),
    api_key: str = Body(..., embed=True, description="API key for this instance"),
    fetch_preferences: Optional[Dict[str, bool]] = Body(None, embed=True, description="Fetch preferences"),
    store_preferences: Optional[Dict[str, bool]] = Body(None, embed=True, description="Store preferences")
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if not name or not name.strip():
        return JSONResponse(status_code=400, content={"error": "Name is required"})

    if not url or not url.strip():
        return JSONResponse(status_code=400, content={"error": "URL is required"})

    url = url.strip()
    if not re.match(r'^https?://[^/\s]+', url):
        return JSONResponse(status_code=400, content={"error": "Invalid URL format (e.g., https://example.com)"})

    if not api_key or not api_key.strip():
        return JSONResponse(status_code=400, content={"error": "API key is required"})

    api_key = api_key.strip()
    if not api_key.startswith("wa_"):
        return JSONResponse(status_code=400, content={"error": "API key must start with wa_"})

    result = await add_remote_instance(name.strip(), url, api_key, fetch_preferences, store_preferences)

    if not result["success"]:
        return JSONResponse(status_code=400, content={"error": result.get("error", "Failed to add instance")})

    api_logger.info(f"Remote instance added: {name}")
    return JSONResponse(content=result)


@router.put("/admin/api/remote/instances/{instance_id}",
            tags=["Admin"],
            summary="Update Remote Instance",
            description="Updates a remote instance")
async def admin_update_remote_instance(
    instance_id: int = Path(..., description="Instance ID"),
    admin_token: Optional[str] = Cookie(None),
    name: Optional[str] = Body(None, embed=True),
    url: Optional[str] = Body(None, embed=True),
    api_key: Optional[str] = Body(None, embed=True),
    enabled: Optional[bool] = Body(None, embed=True),
    fetch_preferences: Optional[Dict[str, bool]] = Body(None, embed=True),
    store_preferences: Optional[Dict[str, bool]] = Body(None, embed=True)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if url is not None:
        url = url.strip()
        if not re.match(r'^https?://[^/\s]+', url):
            return JSONResponse(status_code=400, content={"error": "Invalid URL format (e.g., https://example.com)"})

    if api_key is not None:
        api_key = api_key.strip()
        if not api_key.startswith("wa_"):
            return JSONResponse(status_code=400, content={"error": "API key must start with wa_"})

    result = await update_remote_instance(instance_id, name, url, api_key, enabled, fetch_preferences, store_preferences)
    if not result["success"]:
        error = result.get("error", "Failed to update instance")
        status_code = 404 if error == "Instance not found" else 400
        return JSONResponse(status_code=status_code, content={"error": error})

    api_logger.info(f"Remote instance updated: {instance_id}")
    return JSONResponse(content={"success": True})


@router.delete("/admin/api/remote/instances/{instance_id}",
               tags=["Admin"],
               summary="Delete Remote Instance",
               description="Deletes a remote instance")
async def admin_delete_remote_instance(
    instance_id: int = Path(..., description="Instance ID"),
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    success = await delete_remote_instance(instance_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Instance not found"})

    api_logger.info(f"Remote instance deleted: {instance_id}")
    return JSONResponse(content={"success": True})


@router.post("/admin/api/remote/instances/{instance_id}/check",
             tags=["Admin"],
             summary="Check Remote Instance",
             description="Tests connection to a remote instance")
async def admin_check_remote_instance(
    instance_id: int = Path(..., description="Instance ID"),
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    result = await check_remote_instance(instance_id)
    return JSONResponse(content=result)


# ===========================
# Admin TMDB Fetch Endpoint
# ===========================
@router.get("/admin/api/tmdb/find/{imdb_id}",
            tags=["Admin"],
            summary="Find by IMDB ID",
            description="Fetch TMDB info from IMDB ID")
async def admin_tmdb_find_by_imdb(
    imdb_id: str = Path(..., description="IMDB ID (e.g., tt1234567)"),
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if not imdb_id or not imdb_id.startswith("tt"):
        return JSONResponse(status_code=400, content={"error": "Invalid IMDB ID"})

    if not settings.TMDB_API_KEY:
        return JSONResponse(status_code=400, content={"error": "TMDB_API_KEY not configured"})

    try:
        response = await http_client.get(
            f"{settings.TMDB_API_URL}/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": settings.TMDB_API_KEY},
            timeout=settings.METADATA_TIMEOUT
        )

        if response.status_code != 200:
            return JSONResponse(status_code=response.status_code, content={"error": "TMDB API error"})

        data = response.json()

        if data.get("movie_results") and len(data["movie_results"]) > 0:
            movie = data["movie_results"][0]
            year = None
            if movie.get("release_date"):
                year = int(movie["release_date"][:4])
            return JSONResponse(content={
                "found": True,
                "type": "movie",
                "tmdb_id": str(movie["id"]),
                "title": movie.get("title") or movie.get("original_title"),
                "year": year
            })

        if data.get("tv_results") and len(data["tv_results"]) > 0:
            tv = data["tv_results"][0]
            year = None
            if tv.get("first_air_date"):
                year = int(tv["first_air_date"][:4])
            return JSONResponse(content={
                "found": True,
                "type": "series",
                "tmdb_id": str(tv["id"]),
                "title": tv.get("name") or tv.get("original_name"),
                "year": year
            })

        return JSONResponse(content={"found": False, "error": "No results found"})

    except Exception as e:
        api_logger.error(f"TMDB fetch failed: {type(e).__name__}: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch TMDB data"})


# ===========================
# Admin Settings Endpoints
# ===========================
@router.get("/admin/api/settings",
            tags=["Admin"],
            summary="Get Settings",
            description="Returns editable system settings")
async def admin_get_settings(admin_token: Optional[str] = Cookie(None)):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    keys = [
        "WAWACITY_URL", "FREE_TELECHARGER_URL", "DARKI_API_URL", "MOVIX_URL", "WEBSHARE_URL",
        "YGGREBORN_URL", "YGGREBORN_API_KEY", "TR4KER_URL", "TR4KER_API_KEY",
        "TORR9_URL", "TORR9_API_KEY", "C411_URL", "C411_API_KEY",
        "GEMINI_URL", "GEMINI_API_KEY", "GENERATIONFREE_URL", "GENERATIONFREE_API_KEY",
        "ZILEAN_URL"
    ]
    
    settings_dict = {}
    for key in keys:
        settings_dict[key] = getattr(settings, key, "") or ""
    return JSONResponse(content=settings_dict)


@router.post("/admin/api/settings",
             tags=["Admin"],
             summary="Save Settings",
             description="Updates system settings and persists them to the database")
async def admin_save_settings(
    request: Request,
    admin_token: Optional[str] = Cookie(None)
):
    if not _verify_admin_token(admin_token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    allowed_keys = [
        "WAWACITY_URL", "FREE_TELECHARGER_URL", "DARKI_API_URL", "MOVIX_URL", "WEBSHARE_URL",
        "YGGREBORN_URL", "YGGREBORN_API_KEY", "TR4KER_URL", "TR4KER_API_KEY",
        "TORR9_URL", "TORR9_API_KEY", "C411_URL", "C411_API_KEY",
        "GEMINI_URL", "GEMINI_API_KEY", "GENERATIONFREE_URL", "GENERATIONFREE_API_KEY",
        "ZILEAN_URL"
    ]

    from wastream.utils.database import database

    try:
        for key, value in body.items():
            if key not in allowed_keys:
                continue

            # Apply to settings singleton in memory
            val_to_apply = value if (value and str(value).strip()) else None
            setattr(settings, key, val_to_apply)

            # Persist to database
            await database.execute(
                """
                INSERT OR REPLACE INTO admin_settings (key, value)
                VALUES (:key, :value)
                """,
                {"key": key, "value": str(value) if value is not None else ""}
            )

        return JSONResponse(content={"success": True})
    except Exception as e:
        api_logger.error(f"Failed to save settings: {e}")
        return JSONResponse(status_code=500, content={"error": f"Failed to save settings: {e}"})
