import time
from asyncio import sleep
from base64 import b64encode
from typing import Optional, List, Dict
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree

from wastream.config.settings import settings
from wastream.debrid.base import BaseDebridService, HTTP_RETRY_ERRORS
from wastream.utils.http_client import http_client
from wastream.utils.logger import debrid_logger, cache_logger
from wastream.utils.quality import quality_sort_key

# ===========================
# NZBDav Constants
# ===========================
VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".ogv"
}


# ===========================
# NZBDav Service Class
# ===========================
class NZBDavService(BaseDebridService):
    def get_service_name(self) -> str:
        return "NZBDav"

    def _build_api_url(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/api"

    async def check_cache_and_enrich(self, results: List[Dict], api_key: str, config: Dict, timeout_remaining: float, user_season: Optional[str] = None, user_episode: Optional[str] = None, user_hosts: Optional[List[str]] = None) -> List[Dict]:
        start_time = time.time()

        nzbdav_url = config.get("nzbdav_url", "")
        if not nzbdav_url or not results:
            for result in results:
                result["cache_status"] = "uncached"
            return results

        initial_count = len(results)
        filtered_results = [r for r in results if r.get("model_type") == "nzb"]

        if len(filtered_results) < initial_count:
            debrid_logger.debug(f"[NZBDav] Filtered: {initial_count} → {len(filtered_results)} NZB only")

        if not filtered_results:
            return []

        for result in filtered_results:
            result["cache_status"] = "cached"

        groups = self.group_identical_links(filtered_results)
        cached_results = [group_links[0] for group_links in groups.values()]
        cached_results.sort(key=quality_sort_key)

        elapsed = time.time() - start_time
        cache_logger.debug(f"[NZBDav] {len(cached_results)} results in {elapsed:.1f}s")

        return cached_results

    async def convert_link(self, link: str, api_key: str, season: Optional[str] = None, episode: Optional[str] = None, nzbdav_url: Optional[str] = None, webdav_user: Optional[str] = None, webdav_password: Optional[str] = None, category: Optional[str] = None, title: Optional[str] = None, hoster: Optional[str] = None) -> Optional[str]:
        if not nzbdav_url or not api_key:
            debrid_logger.error("[NZBDav] Missing NZBDav URL or API key")
            return "FATAL_ERROR"

        nzb_id = link.split("/")[-1] if "/nzb/" in link else link
        nzb_filename = f"{title}.nzb"

        try:
            url = await self._find_existing_folder(nzbdav_url, webdav_user, webdav_password, title, season, episode)
            if url:
                debrid_logger.debug("[NZBDav] Found existing folder")
                return url
        except Exception:
            pass

        nzb_download_url = f"{settings.DARKI_API_URL}/nzb/{nzb_id}/download"
        darki_headers = {"X-API-Key": settings.DARKI_API_KEY} if settings.DARKI_API_KEY else {}
        http_error_count = 0

        for attempt in range(settings.DEBRID_MAX_RETRIES):
            try:
                nzb_response = await http_client.get(nzb_download_url, headers=darki_headers, timeout=settings.HTTP_TIMEOUT)

                if nzb_response.status_code != 200:
                    if nzb_response.status_code == 404:
                        debrid_logger.debug("[NZBDav] NZB not found")
                        return "LINK_DOWN"
                    if nzb_response.status_code in [401, 403]:
                        debrid_logger.error(f"[NZBDav] NZB download HTTP {nzb_response.status_code}")
                        return "FATAL_ERROR"
                    if attempt < settings.DEBRID_MAX_RETRIES - 1:
                        await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                        continue
                    return "FATAL_ERROR"

                api_url = self._build_api_url(nzbdav_url)
                params = {"mode": "addfile", "apikey": api_key}
                if category:
                    params["cat"] = category
                response = await http_client.post(
                    api_url,
                    params=params,
                    files={"nzbfile": (nzb_filename, nzb_response.content, "application/x-nzb")},
                    timeout=settings.HTTP_TIMEOUT
                )

                should_retry, http_error_count = await self._handle_http_retry_error(
                    response, http_error_count, "NZBDAV",
                    settings.DEBRID_HTTP_ERROR_RETRY_DELAY, settings.DEBRID_HTTP_ERROR_MAX_RETRIES
                )
                if should_retry:
                    continue
                if response.status_code in HTTP_RETRY_ERRORS:
                    debrid_logger.error(f"[NZBDav] Max HTTP retries ({settings.DEBRID_HTTP_ERROR_MAX_RETRIES})")
                    return "RETRY_ERROR"

                if response.status_code in [401, 403]:
                    debrid_logger.error(f"[NZBDav] SABnzbd HTTP {response.status_code}")
                    return "FATAL_ERROR"

                if response.status_code != 200:
                    debrid_logger.error(f"[NZBDav] SABnzbd HTTP {response.status_code}")
                    if attempt < settings.DEBRID_MAX_RETRIES - 1:
                        await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                        continue
                    return "FATAL_ERROR"

                data = response.json()
                if not data.get("status"):
                    debrid_logger.error("[NZBDav] SABnzbd status false")
                    if attempt < settings.DEBRID_MAX_RETRIES - 1:
                        await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                        continue
                    return "FATAL_ERROR"

                nzo_ids = data.get("nzo_ids", [])
                if not nzo_ids:
                    debrid_logger.error("[NZBDav] No nzo_ids returned")
                    if attempt < settings.DEBRID_MAX_RETRIES - 1:
                        await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                        continue
                    return "FATAL_ERROR"

                webdav_url = await self._get_webdav_stream_url(
                    nzbdav_url, api_key, nzo_ids[0], season, episode, webdav_user, webdav_password
                )

                if webdav_url == "NZB_FAILED":
                    debrid_logger.debug("[NZBDav] NZB failed")
                    return "LINK_DOWN"
                if webdav_url:
                    debrid_logger.debug("[NZBDav] Converted")
                    return webdav_url

                if attempt < settings.DEBRID_MAX_RETRIES - 1:
                    await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                    continue
                return "FATAL_ERROR"

            except Exception as e:
                debrid_logger.error(f"[NZBDav] Attempt {attempt + 1} failed: {type(e).__name__}: {e}")
                if attempt < settings.DEBRID_MAX_RETRIES - 1:
                    await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)

        debrid_logger.error(f"[NZBDav] Failed after {settings.DEBRID_MAX_RETRIES} attempts")
        return "FATAL_ERROR"

    async def _find_existing_folder(self, nzbdav_url: str, webdav_user: Optional[str], webdav_password: Optional[str], title: str, season: Optional[str] = None, episode: Optional[str] = None) -> Optional[str]:
        base = nzbdav_url.rstrip("/")
        if base.endswith("/api"):
            base = base[:-4]

        webdav_base = f"{base}/content"
        auth_string = b64encode(f"{webdav_user or ''}:{webdav_password or ''}".encode()).decode()

        response = await http_client.request(
            "PROPFIND", f"{webdav_base}/",
            headers={"Depth": "1", "Authorization": f"Basic {auth_string}"},
            timeout=settings.HTTP_TIMEOUT
        )

        if response.status_code not in [200, 207]:
            return None

        root = ElementTree.fromstring(response.content)
        ns = {"d": "DAV:"}
        categories = []
        for resp in root.findall(".//d:response", ns):
            href = resp.find("d:href", ns)
            if href is not None and href.text:
                folder = href.text.rstrip("/").split("/")[-1]
                if folder and folder != "content":
                    categories.append(folder)

        for category in categories:
            try:
                cat_response = await http_client.request(
                    "PROPFIND", f"{webdav_base}/{quote(category)}/",
                    headers={"Depth": "1", "Authorization": f"Basic {auth_string}"},
                    timeout=settings.HTTP_TIMEOUT
                )

                if cat_response.status_code not in [200, 207]:
                    continue

                cat_root = ElementTree.fromstring(cat_response.content)
                for resp in cat_root.findall(".//d:response", ns):
                    href = resp.find("d:href", ns)
                    if href is not None and href.text:
                        folder_name = unquote(href.text.rstrip("/").split("/")[-1])
                        if folder_name.startswith(title):
                            return await self._get_video_file_url(
                                nzbdav_url, webdav_user, webdav_password,
                                f"{category}/{folder_name}", season, episode
                            )
            except Exception:
                continue

        return None

    async def _get_webdav_stream_url(self, nzbdav_url: str, api_key: str, nzo_id: str, season: Optional[str] = None, episode: Optional[str] = None, webdav_user: Optional[str] = None, webdav_password: Optional[str] = None) -> Optional[str]:
        max_wait = 30
        poll_interval = 2
        waited = 0
        api_url = self._build_api_url(nzbdav_url)

        while waited < max_wait:
            try:
                response = await http_client.get(
                    api_url,
                    params={"mode": "queue", "apikey": api_key, "output": "json"},
                    timeout=settings.HTTP_TIMEOUT
                )

                if response.status_code == 200:
                    slots = response.json().get("queue", {}).get("slots", [])
                    for slot in slots:
                        if slot.get("nzo_id") == nzo_id:
                            status = slot.get("status", "").lower()
                            if status in ["completed", "extracting", "verifying"]:
                                return await self._get_video_file_url(
                                    nzbdav_url, webdav_user, webdav_password,
                                    slot.get("storage", ""), season, episode
                                )
                            if status in ["failed", "deleted"]:
                                return "NZB_FAILED"
                            break

                history_response = await http_client.get(
                    api_url,
                    params={"mode": "history", "apikey": api_key, "output": "json", "limit": 10},
                    timeout=settings.HTTP_TIMEOUT
                )

                if history_response.status_code == 200:
                    slots = history_response.json().get("history", {}).get("slots", [])
                    for slot in slots:
                        if slot.get("nzo_id") == nzo_id:
                            status = slot.get("status", "").lower()
                            if status == "completed":
                                return await self._get_video_file_url(
                                    nzbdav_url, webdav_user, webdav_password,
                                    slot.get("storage", ""), season, episode
                                )
                            if status == "failed":
                                return "NZB_FAILED"
                            break

                await sleep(poll_interval)
                waited += poll_interval

            except Exception:
                await sleep(poll_interval)
                waited += poll_interval

        return None

    async def _get_video_file_url(self, nzbdav_url: str, webdav_user: Optional[str], webdav_password: Optional[str], storage_path: str, season: Optional[str] = None, episode: Optional[str] = None) -> Optional[str]:
        base = nzbdav_url.rstrip("/")
        if base.endswith("/api"):
            base = base[:-4]

        parsed = urlparse(base)
        user_encoded = quote(webdav_user or '', safe='')
        pass_encoded = quote(webdav_password or '', safe='')
        auth_base = f"{parsed.scheme}://{user_encoded}:{pass_encoded}@{parsed.netloc}"
        webdav_base = f"{base}/content"
        auth_webdav_base = f"{auth_base}/content"

        path_parts = storage_path.replace("\\", "/").rstrip("/").split("/")
        relative_path = f"{path_parts[-2]}/{path_parts[-1]}" if len(path_parts) >= 2 else (path_parts[-1] if path_parts else storage_path)

        folder_url = f"{webdav_base}/{quote(relative_path, safe='/')}"

        try:
            auth_string = b64encode(f"{webdav_user or ''}:{webdav_password or ''}".encode()).decode()
            response = await http_client.request(
                "PROPFIND", folder_url,
                headers={"Depth": "1", "Authorization": f"Basic {auth_string}"},
                timeout=settings.HTTP_TIMEOUT
            )

            if response.status_code not in [200, 207]:
                return None

            root = ElementTree.fromstring(response.content)
            ns = {"d": "DAV:"}
            files = []

            for response_elem in root.findall(".//d:response", ns):
                href_elem = response_elem.find("d:href", ns)
                if href_elem is None or href_elem.text is None:
                    continue

                href = href_elem.text
                filename = href.rstrip("/").split("/")[-1]
                if not filename:
                    continue

                if not any(filename.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
                    continue

                size = 0
                contentlength_elem = response_elem.find(".//d:getcontentlength", ns)
                if contentlength_elem is not None and contentlength_elem.text:
                    try:
                        size = int(contentlength_elem.text)
                    except ValueError:
                        pass

                files.append({"name": filename, "size": size, "href": href})

            if not files:
                return None

            selected_file = None
            if season and episode:
                try:
                    pattern = f"S{int(season):02d}E{int(episode):02d}"
                    matching = [f for f in files if pattern.upper() in f["name"].upper()]
                    if matching:
                        selected_file = max(matching, key=lambda f: f["size"])
                except (ValueError, TypeError):
                    pass

            if not selected_file:
                selected_file = max(files, key=lambda f: f["size"])

            selected_href = selected_file["href"]
            if selected_href.startswith("http"):
                href_parsed = urlparse(selected_href)
                return f"{auth_base}{href_parsed.path}"
            if selected_href.startswith("/"):
                return f"{auth_base}{selected_href}"
            return f"{auth_webdav_base}/{quote(relative_path, safe='/')}/{quote(selected_file['name'], safe='')}"

        except Exception:
            return None


# ===========================
# Singleton Instance
# ===========================
nzbdav_service = NZBDavService()
