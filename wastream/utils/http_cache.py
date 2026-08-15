import hashlib
import json
from typing import Any, Optional, List

from fastapi import Request
from fastapi.responses import Response

from wastream.config.settings import settings


# ===========================
# No Cache Headers
# ===========================
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


# ===========================
# Cache Control Builder
# ===========================
class CacheControl:

    def __init__(self):
        self._directives = []
        self._max_age = None
        self._s_maxage = None
        self._stale_while_revalidate = None
        self._stale_if_error = None

    def public(self):
        self._directives.append("public")
        return self

    def private(self):
        self._directives.append("private")
        return self

    def no_cache(self):
        self._directives.append("no-cache")
        return self

    def no_store(self):
        self._directives.append("no-store")
        return self

    def must_revalidate(self):
        self._directives.append("must-revalidate")
        return self

    def immutable(self):
        self._directives.append("immutable")
        return self

    def max_age(self, seconds: int):
        self._max_age = seconds
        return self

    def s_maxage(self, seconds: int):
        self._s_maxage = seconds
        return self

    def stale_while_revalidate(self, seconds: int):
        self._stale_while_revalidate = seconds
        return self

    def stale_if_error(self, seconds: int):
        self._stale_if_error = seconds
        return self

    def build(self) -> str:
        parts = list(self._directives)
        if self._max_age is not None:
            parts.append(f"max-age={self._max_age}")
        if self._s_maxage is not None:
            parts.append(f"s-maxage={self._s_maxage}")
        if self._stale_while_revalidate is not None:
            parts.append(f"stale-while-revalidate={self._stale_while_revalidate}")
        if self._stale_if_error is not None:
            parts.append(f"stale-if-error={self._stale_if_error}")
        return ", ".join(parts)


# ===========================
# ETag Generation
# ===========================
def generate_etag(data: Any) -> str:
    if isinstance(data, bytes):
        content = data
    elif isinstance(data, str):
        content = data.encode("utf-8")
    else:
        content = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return f'W/"{hashlib.md5(content, usedforsecurity=False).hexdigest()[:16]}"'


# ===========================
# ETag Matching
# ===========================
def check_etag_match(request: Request, etag: str) -> bool:
    if_none_match = request.headers.get("if-none-match")
    if not if_none_match:
        return False

    normalized_etag = etag.replace('W/"', '"')
    for client_etag in if_none_match.split(","):
        normalized_client = client_etag.strip().replace('W/"', '"')
        if normalized_client == normalized_etag or client_etag.strip() == "*":
            return True
    return False


# ===========================
# Cached JSON Response
# ===========================
class CachedJSONResponse(Response):

    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        cache_control: Optional[CacheControl] = None,
        etag: Optional[str] = None,
        vary: Optional[List[str]] = None,
        **kwargs,
    ):
        body = json.dumps(content, separators=(",", ":")).encode()
        super().__init__(
            content=body,
            status_code=status_code,
            media_type="application/json",
            **kwargs,
        )

        if cache_control:
            self.headers["Cache-Control"] = cache_control.build()

        self.headers["ETag"] = etag or generate_etag(body)

        if vary:
            self.headers["Vary"] = ", ".join(vary)


# ===========================
# 304 Not Modified Response
# ===========================
def not_modified_response(etag: str, cache_control: str = "must-revalidate") -> Response:
    return Response(
        status_code=304,
        headers={
            "ETag": etag,
            "Cache-Control": cache_control,
        },
    )


# ===========================
# Cache Policies
# ===========================
class CachePolicies:

    @staticmethod
    def streams() -> CacheControl:
        ttl = settings.HTTP_CACHE_STREAMS_TTL
        swr = settings.HTTP_CACHE_STALE_WHILE_REVALIDATE
        return (
            CacheControl()
            .public()
            .max_age(ttl // 2)
            .s_maxage(ttl)
            .stale_while_revalidate(swr)
            .stale_if_error(300)
        )

    @staticmethod
    def manifest() -> CacheControl:
        ttl = settings.HTTP_CACHE_MANIFEST_TTL
        swr = settings.HTTP_CACHE_STALE_WHILE_REVALIDATE
        return CacheControl().public().max_age(ttl).stale_while_revalidate(swr)

    @staticmethod
    def configure_page() -> CacheControl:
        ttl = settings.HTTP_CACHE_CONFIGURE_TTL
        swr = settings.HTTP_CACHE_STALE_WHILE_REVALIDATE
        return CacheControl().public().max_age(ttl).stale_while_revalidate(swr)

    @staticmethod
    def empty_results() -> CacheControl:
        return (
            CacheControl()
            .public()
            .max_age(15)
            .s_maxage(30)
            .stale_if_error(60)
        )

    @staticmethod
    def no_cache() -> CacheControl:
        return CacheControl().private().no_store().no_cache().max_age(0)
