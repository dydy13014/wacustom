import asyncio
from typing import Optional

import httpx

from wastream.config.settings import settings


# ===========================
# HTTP Client Singleton
# ===========================
class HTTPClient:

    _instance: Optional['HTTPClient'] = None
    _client: Optional[httpx.AsyncClient] = None
    _drain_tasks: set = set()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            client_args = {
                "timeout": httpx.Timeout(float(settings.HTTP_TIMEOUT)),
                "follow_redirects": True,
                "limits": httpx.Limits(max_connections=None, max_keepalive_connections=None)
            }
            if settings.PROXY_URL:
                client_args["proxy"] = settings.PROXY_URL
            self._client = httpx.AsyncClient(**client_args)
        return self._client

    async def get(self, url: str, **kwargs) -> httpx.Response:
        client = await self.get_client()
        return await client.get(url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        client = await self.get_client()
        return await client.post(url, **kwargs)

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        client = await self.get_client()
        return await client.request(method, url, **kwargs)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def reload(self):
        old = self._client
        self._client = None
        if old is not None:
            task = asyncio.create_task(self._drain(old))
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
