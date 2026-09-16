import asyncio
import re
from typing import Dict, Optional
from urllib.parse import urlparse

import httpx

from wastream.config.settings import settings
from wastream.utils.logger import scraper_logger
from wastream.utils.urls import (
    get_url_origin,
    get_www_url_variant,
    replace_url_origin,
    same_source_host,
)


# ===========================
# Source Request Helpers
# ===========================
# Source sites may reject HTTPX's default non-browser User-Agent.
SOURCE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0"
)
SOURCE_RETRY_STATUS_CODES = {401, 403, 404, 408, 410, 421, 451}
BLOCKED_SOURCE_PAGE_RE = re.compile(
    r"(?:just\s+a\s+moment|verify\s+you\s+are\s+human|cf[-_ ]?challenge|access\s+denied)",
    re.IGNORECASE,
)


def source_request_headers(headers: Optional[Dict] = None) -> Dict:
    merged = httpx.Headers({"User-Agent": SOURCE_USER_AGENT})
    merged.update(headers or {})
    return dict(merged)


def is_rejected_source_page(url: str, body: str) -> bool:
    path = (urlparse(url).path or "").lower()
    if any(marker in path for marker in ("/login", "/signin", "/challenge", "/cdn-cgi/")):
        return True
    return bool(BLOCKED_SOURCE_PAGE_RE.search((body or "")[:20000]))


def should_retry_source_response(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return False
    if response.status_code in SOURCE_RETRY_STATUS_CODES or response.status_code >= 500:
        return True
    return is_rejected_source_page(str(response.url), response.text)


# ===========================
# HTTP Client Singleton
# ===========================
class HTTPClient:

    _instance: Optional['HTTPClient'] = None
    _client: Optional[httpx.AsyncClient] = None
    _insecure_client: Optional[httpx.AsyncClient] = None
    _drain_tasks: set = set()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._source_origins = {}
            cls._instance._source_locks = {}
        return cls._instance

    @staticmethod
    def _client_args(verify: bool = True, follow_redirects: bool = True) -> Dict:
        client_args = {
            "timeout": httpx.Timeout(float(settings.HTTP_TIMEOUT)),
            "follow_redirects": follow_redirects,
            "limits": httpx.Limits(max_connections=None, max_keepalive_connections=None),
            "verify": verify,
        }
        if settings.PROXY_URL:
            client_args["proxy"] = settings.PROXY_URL
        return client_args

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(**self._client_args())
        return self._client

    async def get_insecure_client(self) -> httpx.AsyncClient:
        if self._insecure_client is None:
            self._insecure_client = httpx.AsyncClient(
                **self._client_args(verify=False, follow_redirects=False)
            )
        return self._insecure_client

    async def get(self, url: str, **kwargs) -> httpx.Response:
        client = await self.get_client()
        return await client.get(url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        client = await self.get_client()
        return await client.post(url, **kwargs)

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        client = await self.get_client()
        return await client.request(method, url, **kwargs)

    async def get_insecure(self, url: str, **kwargs) -> httpx.Response:
        client = await self.get_insecure_client()
        return await client.get(url, **kwargs)

    @staticmethod
    def _source_request_url(url: str, source_url: str, origin: str, rewrite_url: bool) -> str:
        if rewrite_url and same_source_host(url, source_url):
            return replace_url_origin(url, origin)
        return url

    @staticmethod
    def _source_request_kwargs(kwargs: Dict, source_url: str, origin: str) -> Dict:
        request_kwargs = dict(kwargs)
        headers = source_request_headers(request_kwargs.get("headers"))
        for name, value in list(headers.items()):
            if name.lower() in {"origin", "referer"} and value and same_source_host(value, source_url):
                headers[name] = replace_url_origin(value, origin)
        request_kwargs["headers"] = headers
        return request_kwargs

    def _preferred_source_origin(self, source: str, source_url: str) -> str:
        configured_origin = get_url_origin(source_url) or source_url
        preferred = self._source_origins.get(source)
        if preferred and same_source_host(preferred, configured_origin):
            return preferred
        self._source_origins.pop(source, None)
        return configured_origin

    def get_source_origin(self, source: str, source_url: str) -> str:
        return self._preferred_source_origin(source, source_url)

    def _remember_source_origin(self, source: str, source_url: str,
                                attempted_origin: str, response: httpx.Response):
        final_origin = get_url_origin(str(response.url))
        if final_origin and same_source_host(final_origin, source_url):
            self._source_origins[source] = final_origin
        else:
            self._source_origins[source] = attempted_origin

    async def get_source(self, source: str, url: str, source_url: str,
                         rewrite_url: bool = True, **kwargs) -> httpx.Response:
        primary_origin = self._preferred_source_origin(source, source_url)
        primary_url = self._source_request_url(url, source_url, primary_origin, rewrite_url)
        primary_kwargs = self._source_request_kwargs(kwargs, source_url, primary_origin)
        primary_response = None
        primary_error = None

        try:
            primary_response = await self.get(primary_url, **primary_kwargs)
            if not should_retry_source_response(primary_response):
                self._remember_source_origin(source, source_url, primary_origin, primary_response)
                return primary_response
        except httpx.RequestError as error:
            primary_error = error

        lock = self._source_locks.setdefault(source, asyncio.Lock())
        async with lock:
            current_origin = self._source_origins.get(source)
            if current_origin and current_origin != primary_origin and same_source_host(current_origin, source_url):
                alternate_origin = current_origin
            else:
                alternate_origin = get_url_origin(get_www_url_variant(primary_origin))

            if not alternate_origin or alternate_origin == primary_origin:
                if primary_response is not None:
                    return primary_response
                if primary_error is not None:
                    raise primary_error
                raise RuntimeError(f"[{source}] Source request failed without a response")

            alternate_url = self._source_request_url(url, source_url, alternate_origin, rewrite_url)
            alternate_kwargs = self._source_request_kwargs(kwargs, source_url, alternate_origin)
            scraper_logger.debug(f"[{source}] Retrying source request with alternate www host")

            try:
                alternate_response = await self.get(alternate_url, **alternate_kwargs)
            except httpx.RequestError as alternate_error:
                if primary_response is not None:
                    return primary_response
                raise alternate_error from primary_error

            if not should_retry_source_response(alternate_response):
                self._remember_source_origin(source, source_url, alternate_origin, alternate_response)
            return alternate_response

    async def close(self):
        clients = [client for client in (self._client, self._insecure_client) if client]
        self._client = None
        self._insecure_client = None
        self._source_origins.clear()
        self._source_locks.clear()
        for client in clients:
            await client.aclose()

    async def reload(self):
        old_clients = [client for client in (self._client, self._insecure_client) if client]
        self._client = None
        self._insecure_client = None
        self._source_origins.clear()
        self._source_locks.clear()
        for client in old_clients:
            task = asyncio.create_task(self._drain(client))
            self._drain_tasks.add(task)
            task.add_done_callback(self._drain_tasks.discard)

    async def _drain(self, client: httpx.AsyncClient):
        try:
            await asyncio.sleep(float(settings.STREAM_REQUEST_TIMEOUT))
        finally:
            try:
                await client.aclose()
            except Exception:
                pass


# ===========================
# Global HTTP Client Instance
# ===========================
http_client = HTTPClient()
