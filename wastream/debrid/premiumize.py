import time
from asyncio import sleep
from typing import Optional, List, Dict

from wastream.config.settings import settings
from wastream.debrid.base import BaseDebridService, HTTP_RETRY_ERRORS
from wastream.utils.http_client import http_client
from wastream.utils.logger import debrid_logger, cache_logger
from wastream.utils.quality import quality_sort_key

# ===========================
# Premiumize Error Constants
# ===========================
RETRY_CODES = [
    "service_down",
    "rate_limit_reached",
    "transient_error",
    "link_generation_failed",
    "semi_permanent_error",
    "account_limit_reached",
    "service_limit_reached",
]

LINK_DOWN_CODES = [
    "not_found",
]


# ===========================
# Premiumize Service Class
# ===========================
class PremiumizeService(BaseDebridService):
    def get_service_name(self) -> str:
        return "Premiumize"

    def _handle_api_error(
        self,
        error_code: str,
        message: str,
        attempt: int
    ) -> str:
        if error_code in LINK_DOWN_CODES:
            debrid_logger.debug(f"[Premiumize] LINK_DOWN: {error_code} - {message}")
            return "LINK_DOWN"

        if error_code in RETRY_CODES:
            debrid_logger.error(f"[Premiumize] RETRY: {error_code} - {message}")
            if attempt >= settings.DEBRID_MAX_RETRIES - 1:
                return "RETRY_ERROR"
            return "RETRY"

        debrid_logger.error(f"[Premiumize] Fatal: {error_code} - {message}")
        return "FATAL_ERROR"

    async def check_cache_and_enrich(self, results: List[Dict], api_key: str, config: Dict, timeout_remaining: float, user_season: Optional[str] = None, user_episode: Optional[str] = None, user_hosts: Optional[List[str]] = None) -> List[Dict]:
        start_time = time.time()

        if not api_key or not results:
            for result in results:
                result["cache_status"] = "uncached"
            return results

        supported_hosts = user_hosts if user_hosts else settings.PREMIUMIZE_SUPPORTED_HOSTS

        initial_count = len(results)
        filtered_results = []
        for result in results:
            if result.get("model_type") == "nzb":
                continue
            hoster = result.get("hoster", "").lower()
            if any(supported_host in hoster for supported_host in supported_hosts):
                filtered_results.append(result)

        if len(filtered_results) < initial_count:
            debrid_logger.debug(f"[Premiumize] Filtered: {initial_count} → {len(filtered_results)} links (DDL only)")

        if not filtered_results:
            debrid_logger.debug("[Premiumize] No supported hosts")
            return []

        results = filtered_results

        for result in results:
            result["cache_status"] = "cached"

        deduplicate_results = config.get("deduplicate_results", False)

        if deduplicate_results:
            groups = self.group_identical_links(results)
            cached_results = []
            for group_key, group_links in groups.items():
                cached_results.append(group_links[0])
        else:
            cached_results = self.deduplicate_by_exact_link(results)

        cached_results.sort(key=quality_sort_key)

        elapsed = time.time() - start_time
        cache_logger.debug(f"[Premiumize] Done in {elapsed:.1f}s: {len(cached_results)} results (all marked cached)")

        return cached_results

    async def convert_link(self, link: str, api_key: str, season: Optional[str] = None, episode: Optional[str] = None, hoster: Optional[str] = None) -> Optional[str]:
        if not api_key:
            debrid_logger.error("[Premiumize] Empty API key")
            return "FATAL_ERROR"

        debrid_logger.debug("[Premiumize] Converting link")

        http_error_count = 0

        for attempt in range(settings.DEBRID_MAX_RETRIES):
            try:
                response = await http_client.post(
                    f"{settings.PREMIUMIZE_API_URL}/transfer/directdl",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={"src": link},
                    timeout=settings.HTTP_TIMEOUT
                )

                should_retry, http_error_count = await self._handle_http_retry_error(
                    response, http_error_count, "PREMIUMIZE",
                    settings.DEBRID_HTTP_ERROR_RETRY_DELAY, settings.DEBRID_HTTP_ERROR_MAX_RETRIES
                )
                if should_retry:
                    continue
                elif response.status_code in HTTP_RETRY_ERRORS:
                    debrid_logger.error(f"[Premiumize] Max HTTP retries ({settings.DEBRID_HTTP_ERROR_MAX_RETRIES})")
                    return "RETRY_ERROR"

                http_error_count = 0

                if response.status_code == 401:
                    debrid_logger.error("[Premiumize] Invalid API key (401)")
                    return "FATAL_ERROR"

                if response.status_code == 402:
                    debrid_logger.error("[Premiumize] Account not premium (402)")
                    return "FATAL_ERROR"

                if response.status_code == 404:
                    debrid_logger.error("[Premiumize] Endpoint not found (404)")
                    return "FATAL_ERROR"

                if response.status_code != 200:
                    debrid_logger.error(f"[Premiumize] HTTP {response.status_code}")
                    if attempt >= settings.DEBRID_MAX_RETRIES - 1:
                        return "FATAL_ERROR"
                    await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                    continue

                data = response.json()

                if data.get("status") != "success":
                    error_code = data.get("code", "")
                    message = data.get("message", "Unknown error")

                    api_error_result = self._handle_api_error(error_code, message, attempt)
                    if api_error_result == "LINK_DOWN":
                        return "LINK_DOWN"
                    elif api_error_result == "FATAL_ERROR":
                        return "FATAL_ERROR"
                    elif api_error_result == "RETRY_ERROR":
                        return "RETRY_ERROR"
                    elif api_error_result == "RETRY":
                        await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                        continue

                content_list = data.get("content")
                if content_list and isinstance(content_list, list) and len(content_list) > 0:
                    first = content_list[0]
                    direct_link = first.get("link") if isinstance(first, dict) else None
                else:
                    direct_link = data.get("location")

                if direct_link:
                    debrid_logger.debug("[Premiumize] Converted")
                    return direct_link
                else:
                    debrid_logger.error("[Premiumize] No direct link in response")
                    if attempt >= settings.DEBRID_MAX_RETRIES - 1:
                        return "FATAL_ERROR"
                    await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                    continue

            except Exception as e:
                debrid_logger.error(f"[Premiumize] Attempt {attempt + 1} failed: {type(e).__name__}: {e}")
                if attempt < settings.DEBRID_MAX_RETRIES - 1:
                    await sleep(settings.DEBRID_RETRY_DELAY_SECONDS)
                    continue

        debrid_logger.error(f"[Premiumize] Failed after {settings.DEBRID_MAX_RETRIES} attempts")
        return "FATAL_ERROR"


# ===========================
# Singleton Instance
# ===========================
premiumize_service = PremiumizeService()
