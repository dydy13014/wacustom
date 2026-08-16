import asyncio
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from wastream.api.routes import router
from wastream.utils.database import setup_database, teardown_database, cleanup_expired_data, rebuild_cache_stats
from wastream.utils.helpers import decode_playback_token
from wastream.utils.http_client import http_client
from wastream.config.settings import settings
from wastream.utils.logger import setup_logger, addon_logger, api_logger, user_id_var
from wastream.services.health import start_background_health_check
from wastream.services.domain_sync import start_background_domain_sync
from wastream.services.pastebin_scraper import start_pastebin_scraper_loop
from wastream.services.idrix_scraper import start_idrix_scraper_loop
from wastream.services.settings_manager import apply_startup_overrides


UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


# ===========================
# Logger Setup
# ===========================
setup_logger(settings.LOG_LEVEL)


# ===========================
# Server Start Time
# ===========================
SERVER_START_TIME = int(time.time())


# ===========================
# Custom Middleware
# ===========================
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class LoguruMiddleware(BaseHTTPMiddleware):
    def _extract_user_id(self, request: Request) -> Optional[str]:
        path = request.url.path
        parts = path.split("/")

        if len(parts) >= 2 and UUID_PATTERN.match(parts[1]):
            return parts[1]

        if len(parts) >= 3 and parts[1] == "user" and UUID_PATTERN.match(parts[2]):
            return parts[2]

        if len(parts) >= 3 and parts[1] == "playback":
            data = decode_playback_token(parts[2])
            if data:
                user_uuid = data.get("u")
                if user_uuid and UUID_PATTERN.match(user_uuid):
                    return user_uuid

        if path.startswith("/resolve"):
            user_uuid = request.query_params.get("user_uuid")
            if user_uuid and UUID_PATTERN.match(user_uuid):
                return user_uuid

        return None

    async def dispatch(self, request: Request, call_next):
        token = user_id_var.set(self._extract_user_id(request))
        start_time = time.time()
        response = None
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            api_logger.error(f"Exception: {type(e).__name__}: {e}")
            raise
        finally:
            process_time = time.time() - start_time
            if request.url.path != "/health":
                safe_path = request.url.path
                path_parts = safe_path.split("/")
                if len(path_parts) > 3 and path_parts[3] in ("stream", "manifest.json", "configure"):
                    path_parts[2] = "***"
                    safe_path = "/".join(path_parts)
                api_logger.debug(f"{request.method} {safe_path} - {response.status_code if response else '500'} - {process_time:.2f}s")
            user_id_var.reset(token)


# ===========================
# Application Lifecycle
# ===========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await setup_database()
    await apply_startup_overrides()

    # Nos sources maison sont comptées ici au même titre que celles d'upstream :
    # sans elles, un déploiement n'utilisant QUE nos trackers verrait
    # l'avertissement à tort. ⚠️ NYAA_URL ayant une valeur par défaut, cette
    # condition est en pratique toujours vraie — l'avertissement ne se
    # déclenche donc que si quelqu'un vide explicitement ce réglage.
    has_direct_source = any((
        settings.WAWACITY_URL,
        settings.FREE_TELECHARGER_URL,
        settings.DARKI_API_URL,
        settings.MOVIX_URL,
        settings.WEBSHARE_URL,
        settings.ZONE_TELECHARGEMENT_URL,
        # Sources propres au fork, absentes d'upstream : sans elles, un
        # deploiement n'utilisant QUE ces sources verrait l'avertissement
        # ci-dessous alors que tout fonctionne.
        settings.NYAA_URL,
        settings.ZILEAN_URL,
        settings.YGGREBORN_URL,
        settings.TR4KER_URL,
        settings.TORR9_URL,
        settings.C411_URL,
        settings.V3X_URL,
        settings.GEMINI_URL,
        settings.GENERATIONFREE_URL,
        settings.LUMIO_MANIFEST_ID,
    ))
    has_import_feeder = bool(settings.PASTEBIN_SCRAPER_URLS or settings.IDRIX_SCRAPER_URLS)
    if not has_direct_source and not has_import_feeder:
        addon_logger.warning(
            "No direct source or import feeder configured; the addon will not find content"
        )
    addon_logger.info(f"Wawacity: {settings.WAWACITY_URL or 'NOT CONFIGURED'}")
    addon_logger.info(
        f"Free-Telecharger: {settings.FREE_TELECHARGER_URL or 'NOT CONFIGURED'}"
    )
    addon_logger.info(
        f"Darki-API: {settings.DARKI_API_URL or 'NOT CONFIGURED'}"
    )
    addon_logger.info(f"Movix: {settings.MOVIX_URL or 'NOT CONFIGURED'}")
    addon_logger.info(f"Webshare: {settings.WEBSHARE_URL or 'NOT CONFIGURED'}")
    addon_logger.info(
        f"Zone-Telechargement: "
        f"{settings.ZONE_TELECHARGEMENT_URL or 'NOT CONFIGURED'}"
    )
    addon_logger.info(f"Nyaa: {settings.NYAA_URL or 'NOT CONFIGURED'}")
    addon_logger.info(
        f"Pastebin Scraper: {len(settings.PASTEBIN_SCRAPER_URLS)} URL(s)"
        if settings.PASTEBIN_SCRAPER_URLS
        else "Pastebin Scraper: NOT CONFIGURED"
    )
    addon_logger.info(
        f"Idrix Scraper: {len(settings.IDRIX_SCRAPER_URLS)} URL(s)"
        if settings.IDRIX_SCRAPER_URLS
        else "Idrix Scraper: NOT CONFIGURED"
    )

    # Toutes les tâches de fond doivent être référencées : l'event loop ne
    # garde qu'une référence FAIBLE, donc une tâche dont plus personne ne
    # detient le handle peut etre ramassee par le garbage collector en pleine
    # execution, et donc annulee silencieusement. rebuild_cache_stats() etait
    # dans ce cas : elle parcourt tout le cache par lots de 500 lignes, ce qui
    # laisse une vraie fenetre pour se faire interrompre sans la moindre trace
    # dans les logs (statistiques de cache incompletes au demarrage).
    # ⚠️ Upstream 3.8.2 laisse toujours rebuild_cache_stats() sans référence —
    # ne pas revenir à sa version lors d'une future remontée de version.
    background_tasks = [
        asyncio.create_task(rebuild_cache_stats()),
        asyncio.create_task(cleanup_expired_data()),
        asyncio.create_task(start_background_health_check()),
        asyncio.create_task(start_background_domain_sync()),
        asyncio.create_task(start_pastebin_scraper_loop()),
        asyncio.create_task(start_idrix_scraper_loop()),
    ]

    yield

    for task in background_tasks:
        task.cancel()
    # return_exceptions=True : rebuild_cache_stats() est one-shot et peut etre
    # deja terminee au moment de l'arret ; on ne veut ni que son resultat ni
    # une eventuelle exception ne fasse echouer la fermeture.
    await asyncio.gather(*background_tasks, return_exceptions=True)

    await http_client.close()
    await teardown_database()


# ===========================
# FastAPI Application Setup
# ===========================
app = FastAPI(
    title=settings.ADDON_NAME,
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(LoguruMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "public"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="public")
app.include_router(router)


# ===========================
# Application Entry Point
# ===========================
if __name__ == "__main__":
    addon_logger.info(f"Starting {settings.ADDON_NAME} v{settings.ADDON_MANIFEST['version']} ({settings.ADDON_ID})")
    addon_logger.info(f"Server: http://localhost:{settings.PORT}/")
    addon_logger.info(f"Database: {settings.DATABASE_TYPE} v{settings.DATABASE_VERSION}")
    addon_logger.info(f"Proxy: {'enabled' if settings.PROXY_URL else 'disabled'}")
    addon_logger.info(f"Log level: {settings.LOG_LEVEL}")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.PORT,
        log_config=None
    )
