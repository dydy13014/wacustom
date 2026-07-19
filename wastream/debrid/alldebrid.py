import re
import time
from asyncio import sleep
from typing import Optional, List, Dict
from urllib.parse import urlparse

from wastream.config.settings import settings
from wastream.debrid.base import BaseDebridService, HTTP_RETRY_ERRORS
from wastream.services.hoster_status import get_hoster_status, mark_hoster_down, is_hoster_up, schedule_recheck
from wastream.utils.helpers import select_episode_file
from wastream.utils.http_client import http_client
from wastream.utils.logger import debrid_logger, cache_logger
from wastream.utils.quality import quality_sort_key

# ===========================
# AllDebrid Error Constants
# ===========================
RETRY_ERRORS = [
    "LINK_HOST_UNAVAILABLE",
    "LINK_TEMPORARY_UNAVAILABLE",
    "LINK_TOO_MANY_DOWNLOADS",
    "LINK_HOST_FULL",
    "LINK_HOST_LIMIT_REACHED",
    "REDIRECTOR_ERROR",
]

DEAD_LINK_ERRORS = [
    "LINK_PASS_PROTECTED",
]

VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.webm')


def _flatten_magnet_files(entries: List[Dict], prefix: str = "") -> List[Dict]:
    """Aplatit l'arborescence de fichiers renvoyée par /magnet/files.
    Format AllDebrid : {"n": nom, "s": taille, "l": lien} pour un fichier,
    {"n": nom, "e": [...]} pour un dossier."""
    flat = []
    for entry in entries:
        name = entry.get("n", "")
        if "e" in entry:
            flat.extend(_flatten_magnet_files(entry["e"], prefix=f"{prefix}{name}/"))
        elif entry.get("l"):
            flat.append({
                "filename": f"{prefix}{name}",
                "size": entry.get("s", 0),
                "link": entry["l"],
            })
    return flat


# ===========================
# AllDebrid Service Class
# ===========================
class AllDebridService(BaseDebridService):
    def get_service_name(self) -> str:
        return "AllDebrid"

    async def check_cache_and_enrich(self, results: List[Dict], api_key: str, config: Dict, timeout_remaining: float, user_season: Optional[str] = None, user_episode: Optional[str] = None, user_hosts: Optional[List[str]] = None) -> List[Dict]:
        start_time = time.time()

        if not api_key or not results:
            for result in results:
                result["cache_status"] = "uncached"
            return results

        supported_hosts = user_hosts if user_hosts else settings.ALLDEBRID_SUPPORTED_HOSTS

        # Séparer DDL et torrents
        ddl_results = []
        torrent_results = []
        for result in results:
            if result.get("model_type") == "nzb":
                continue
            if result.get("model_type") == "torrent":
                torrent_results.append(result)
            else:
                hoster = result.get("hoster", "").lower()
                if any(supported_host in hoster for supported_host in supported_hosts):
                    ddl_results.append(result)

        debrid_logger.debug(f"[AllDebrid] {len(ddl_results)} DDL + {len(torrent_results)} torrents")

        if not ddl_results and not torrent_results:
            debrid_logger.debug("[AllDebrid] No supported results")
            return []

        # --- DDL : filtrer les hosters down (réactif + proactif via /v4.1/user/hosts) ---
        if ddl_results:
            await get_hoster_status("alldebrid", api_key)
        kept_ddl = []
        down_hosters = {}
        for r in ddl_results:
            hoster = r.get("hoster", "").lower()
            if is_hoster_up("alldebrid", hoster):
                kept_ddl.append(r)
            else:
                if hoster and hoster not in down_hosters:
                    down_hosters[hoster] = r.get("link", "")
        ddl_results = kept_ddl

        if down_hosters and config.get("recheck_hoster_status", False):
            for hoster_name, link in down_hosters.items():
                schedule_recheck(link, api_key, hoster_name)
                debrid_logger.debug(f"[AllDebrid] Background recheck scheduled for '{hoster_name}'")

        # DDL : marqués cached (vérification réelle à la conversion, comme avant).
        for result in ddl_results:
            result["cache_status"] = "cached"

        # Torrents : AllDebrid a supprimé /magnet/instant (404) — plus aucun moyen
        # de vérifier le cache sans uploader le magnet sur le compte. On les marque
        # "uncached" au listing ; la conversion au moment de la lecture (magnet/upload
        # → ready → files → unlock) fonctionne, elle, normalement.
        for result in torrent_results:
            result["cache_status"] = "uncached"

        all_results = ddl_results + torrent_results

        deduplicate_results = config.get("deduplicate_results", False)
        if deduplicate_results:
            groups = self.group_identical_links(all_results)
            cached_results = [group_links[0] for group_links in groups.values()]
        else:
            cached_results = self.deduplicate_by_exact_link(all_results)

        cached_results.sort(key=quality_sort_key)

        elapsed = time.time() - start_time
        cache_logger.debug(f"[AllDebrid] Done in {elapsed:.1f}s: {len(cached_results)} results")
        return cached_results

    async def _convert_torrent_link(self, magnet: str, api_key: str, season: Optional[str] = None, episode: Optional[str] = None) -> Optional[str]:
        """Convertit un magnet en lien direct : upload → ready → files → unlock.
        Si le torrent n'est pas en cache AllDebrid, le magnet reste sur le compte
        et se télécharge en tâche de fond (comportement debrid standard)."""
        debrid_logger.debug("[AllDebrid] Converting torrent via magnet upload")

        # 1. Upload du magnet (idempotent : si déjà présent, renvoie le même id)
        try:
            upload_resp = await http_client.post(
                f"{settings.ALLDEBRID_API_URL}/magnet/upload",
                params={"agent": settings.ADDON_NAME, "apikey": api_key},
                data={"magnets[]": magnet},
                timeout=settings.HTTP_TIMEOUT
            )
        except Exception as e:
            debrid_logger.error(f"[AllDebrid] Magnet upload error: {e}")
            return "FATAL_ERROR"

        if upload_resp.status_code != 200:
            debrid_logger.error(f"[AllDebrid] Magnet upload HTTP {upload_resp.status_code}")
            return "FATAL_ERROR"

        upload_data = upload_resp.json()
        if upload_data.get("status") != "success":
            error_code = upload_data.get("error", {}).get("code", "")
            debrid_logger.error(f"[AllDebrid] Magnet upload failed: {error_code}")
            if error_code == "MAGNET_MUST_BE_PREMIUM":
                return "FATAL_ERROR"
            return "FATAL_ERROR"

        magnets = upload_data.get("data", {}).get("magnets", [])
        if not magnets:
            debrid_logger.error("[AllDebrid] No magnet data in upload response")
            return "FATAL_ERROR"

        magnet_info = magnets[0]
        magnet_id = magnet_info.get("id")
        magnet_error = magnet_info.get("error")
        if magnet_error:
            debrid_logger.error(f"[AllDebrid] Magnet error: {magnet_error.get('code')}")
            return "FATAL_ERROR"
        if not magnet_id:
            debrid_logger.error("[AllDebrid] No magnet ID in upload response")
            return "FATAL_ERROR"

        # 2. Pas en cache → le téléchargement démarre côté AllDebrid
        if not magnet_info.get("ready", False):
            debrid_logger.debug("[AllDebrid] Torrent uncached - download started on AllDebrid")
            return "LINK_UNCACHED"

        # 3. Récupérer les fichiers (format n/s/l, dossiers imbriqués via "e")
        try:
            files_resp = await http_client.get(
                f"{settings.ALLDEBRID_API_URL}/magnet/files",
                params=[
                    ("agent", settings.ADDON_NAME),
                    ("apikey", api_key),
                    ("id[]", str(magnet_id)),
                ],
                timeout=settings.HTTP_TIMEOUT
            )
        except Exception as e:
            debrid_logger.error(f"[AllDebrid] Magnet files error: {e}")
            return "FATAL_ERROR"

        if files_resp.status_code != 200:
            debrid_logger.error(f"[AllDebrid] Magnet files HTTP {files_resp.status_code}")
            return "FATAL_ERROR"

        files_data = files_resp.json()
        if files_data.get("status") != "success":
            debrid_logger.error(f"[AllDebrid] Magnet files failed: {files_data.get('error')}")
            return "FATAL_ERROR"

        magnet_files = files_data.get("data", {}).get("magnets", [])
        if not magnet_files:
            debrid_logger.error("[AllDebrid] No magnet files data")
            return "FATAL_ERROR"

        all_files = _flatten_magnet_files(magnet_files[0].get("files", []))
        if not all_files:
            debrid_logger.error("[AllDebrid] No files in magnet")
            return "FATAL_ERROR"

        # 4. Sélectionner le meilleur fichier vidéo (exclut les samples,
        # match d'épisode multi-formats : SxxExx, 2x04, plages, multi-ep)
        selected_file = select_episode_file(all_files, season, episode)
        if not selected_file:
            debrid_logger.error("[AllDebrid] No selectable file in magnet")
            return "FATAL_ERROR"

        debrid_logger.debug(f"[AllDebrid] Selected file: {selected_file['filename']}")

        # 5. Débloquer le lien du fichier
        try:
            unlock_resp = await http_client.get(
                f"{settings.ALLDEBRID_API_URL}/link/unlock",
                params={"agent": settings.ADDON_NAME, "apikey": api_key, "link": selected_file["link"]},
                timeout=settings.HTTP_TIMEOUT
            )
        except Exception as e:
            debrid_logger.error(f"[AllDebrid] Unlock error: {e}")
            return "FATAL_ERROR"

        if unlock_resp.status_code != 200:
            debrid_logger.error(f"[AllDebrid] Unlock HTTP {unlock_resp.status_code}")
            return "FATAL_ERROR"

        unlock_data = unlock_resp.json()
        if unlock_data.get("status") != "success":
            error_code = unlock_data.get("error", {}).get("code", "")
            debrid_logger.error(f"[AllDebrid] Unlock failed: {error_code}")
            if error_code in RETRY_ERRORS:
                return "RETRY_ERROR"
            return "FATAL_ERROR"

        if "delayed" in unlock_data.get("data", {}):
            debrid_logger.debug("[AllDebrid] Unlock delayed - uncached")
            return "LINK_UNCACHED"

        direct_link = unlock_data.get("data", {}).get("link")
        if direct_link:
            debrid_logger.debug("[AllDebrid] Torrent converted successfully")
            return direct_link

        debrid_logger.error("[AllDebrid] No direct link from unlock")
        return "FATAL_ERROR"

    async def convert_link(self, link: str, api_key: str, season: Optional[str] = None, episode: Optional[str] = None, hoster: Optional[str] = None) -> Optional[str]:
        if not api_key:
            debrid_logger.error("[AllDebrid] Empty API key")
            return None

        # Routing : magnet ou infohash brut → flux torrent AllDebrid
        if link.startswith("magnet:") or re.fullmatch(r"[a-fA-F0-9]{32,40}", link):
            magnet = link if link.startswith("magnet:") else f"magnet:?xt=urn:btih:{link}"
            return await self._convert_torrent_link(magnet, api_key, season, episode)

        debrid_logger.debug("[AllDebrid] Converting DDL link")

        is_direct_link = any(host in link for host in ["1fichier.com", "turbobit.net", "rapidgator.net", "alldebrid.com"])
        http_error_count = 0

        for attempt in range(settings.DEBRID_MAX_RETRIES):
            try:
                if is_direct_link:
                    response1 = await http_client.get(
                        f"{settings.ALLDEBRID_API_URL}/link/unlock",
                        params={"agent": settings.ADDON_NAME, "apikey": api_key, "link": link}
                    )
                else:
                    response1 = await http_client.get(
                        f"{settings.ALLDEBRID_API_URL}/link/redirector",
                        params={"agent": settings.ADDON_NAME, "apikey": api_key, "link": link}
                    )

                should_retry, http_error_count = await self._handle_http_retry_error(
                    response1, http_error_count, "ALLDEBRID",
                    settings.DEBRID_HTTP_ERROR_RETRY_DELAY, settings.DEBRID_HTTP_ERROR_MAX_RETRIES
                )
                if should_retry:
                    continue
                elif response1.status_code in HTTP_RETRY_ERRORS:
                    debrid_logger.error(f"[AllDebrid] Max HTTP retries ({settings.DEBRID_HTTP_ERROR_MAX_RETRIES})")
                    return "RETRY_ERROR"

                http_error_count = 0

                if response1.status_code != 200:
                    debrid_logger.error(f"[AllDebrid] Redirector HTTP {response1.status_code}")
                    if attempt >= settings.DEBRID_MAX_RETRIES - 1:
                        return "FATAL_ERROR"
                    await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                    continue

                data1 = response1.json()
                if data1.get("status") != "success":
                    error = data1.get("error", {})
                    error_code = error.get("code")

                    if error_code == "LINK_DOWN":
                        debrid_logger.debug(f"[AllDebrid] {error_code}")
                        return "LINK_DOWN"

                    if error_code in DEAD_LINK_ERRORS:
                        debrid_logger.debug(f"[AllDebrid] {error_code} - link is password-protected (universally undebridable)")
                        return "LINK_DOWN"

                    if error_code == "NO_SERVER":
                        debrid_logger.warning(f"[AllDebrid] NO_SERVER - server blocked by AllDebrid (VPN/datacenter detected)")
                        return "FATAL_ERROR"

                    if error_code in RETRY_ERRORS:
                        if error_code == "LINK_HOST_UNAVAILABLE":
                            hoster_name = hoster if hoster else urlparse(link).netloc.replace("www.", "")
                            mark_hoster_down("alldebrid", hoster_name)
                        debrid_logger.error(f"[AllDebrid] {error_code}")
                        if attempt >= settings.DEBRID_MAX_RETRIES - 1:
                            return "RETRY_ERROR"
                        await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                        continue

                    debrid_logger.error(f"[AllDebrid] Fatal: {error_code}")
                    return "FATAL_ERROR"

                if is_direct_link:
                    if "delayed" in data1.get("data", {}):
                        debrid_logger.debug("[AllDebrid] Delayed - uncached")
                        return "LINK_UNCACHED"

                    direct_link = data1.get("data", {}).get("link")
                    if direct_link:
                        debrid_logger.debug("[AllDebrid] Converted")
                        return direct_link
                    else:
                        debrid_logger.error("[AllDebrid] No direct link")
                        await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                        continue

                redirected_links = data1.get("data", {}).get("links", [])
                if not redirected_links:
                    debrid_logger.error("[AllDebrid] No redirected links")
                    await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                    continue

                first_link = redirected_links[0]
                response2 = await http_client.get(
                    f"{settings.ALLDEBRID_API_URL}/link/unlock",
                    params={"agent": settings.ADDON_NAME, "apikey": api_key, "link": first_link}
                )

                should_retry, http_error_count = await self._handle_http_retry_error(
                    response2, http_error_count, "ALLDEBRID",
                    settings.DEBRID_HTTP_ERROR_RETRY_DELAY, settings.DEBRID_HTTP_ERROR_MAX_RETRIES
                )
                if should_retry:
                    continue
                elif response2.status_code in HTTP_RETRY_ERRORS:
                    debrid_logger.error(f"[AllDebrid] Max HTTP retries ({settings.DEBRID_HTTP_ERROR_MAX_RETRIES})")
                    return "RETRY_ERROR"

                http_error_count = 0

                if response2.status_code != 200:
                    debrid_logger.error(f"[AllDebrid] Unlock HTTP {response2.status_code}")
                    if attempt >= settings.DEBRID_MAX_RETRIES - 1:
                        return "FATAL_ERROR"
                    await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                    continue

                data2 = response2.json()
                if data2.get("status") != "success":
                    error = data2.get("error", {})
                    error_code2 = error.get("code")

                    if error_code2 == "LINK_DOWN":
                        debrid_logger.debug(f"[AllDebrid] {error_code2}")
                        return "LINK_DOWN"

                    if error_code2 in DEAD_LINK_ERRORS:
                        debrid_logger.debug(f"[AllDebrid] {error_code2} - link is password-protected (universally undebridable)")
                        return "LINK_DOWN"

                    if error_code2 == "NO_SERVER":
                        debrid_logger.warning(f"[AllDebrid] NO_SERVER - server blocked by AllDebrid (VPN/datacenter detected)")
                        return "FATAL_ERROR"

                    if error_code2 in RETRY_ERRORS:
                        if error_code2 == "LINK_HOST_UNAVAILABLE":
                            hoster_name = hoster if hoster else urlparse(link).netloc.replace("www.", "")
                            mark_hoster_down("alldebrid", hoster_name)
                        debrid_logger.error(f"[AllDebrid] {error_code2}")
                        if attempt >= settings.DEBRID_MAX_RETRIES - 1:
                            return "RETRY_ERROR"
                        await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                        continue

                    debrid_logger.error(f"[AllDebrid] Fatal: {error_code2}")
                    return "FATAL_ERROR"

                if "delayed" in data2.get("data", {}):
                    debrid_logger.debug("[AllDebrid] Delayed - uncached")
                    return "LINK_UNCACHED"

                direct_link = data2.get("data", {}).get("link")
                if direct_link:
                    debrid_logger.debug("[AllDebrid] Converted")
                    return direct_link

            except Exception as e:
                debrid_logger.error(f"[AllDebrid] Attempt {attempt + 1} failed: {type(e).__name__}: {e}")
                if attempt < settings.DEBRID_MAX_RETRIES - 1:
                    await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)

        debrid_logger.error(f"[AllDebrid] Failed after {settings.DEBRID_MAX_RETRIES} attempts")
        return "FATAL_ERROR"


# ===========================
# Singleton Instance
# ===========================
alldebrid_service = AllDebridService()
