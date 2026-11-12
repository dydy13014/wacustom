"""Tests de GET /api/verify-api-key (routes.verify_api_key) : cette route sert
a la page /configure pour verifier qu'une cle de debrideur (ou un jeton TMDB)
saisie est valide. Elle n'authentifie PAS l'API.

On appelle la fonction directement avec un http_client factice : aucun appel
reseau reel. Les parametres FastAPI (Query) sont tous passes explicitement.
"""
import json

import pytest

from wastream.api import routes
from wastream.api.routes import ServiceType


class _Resp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def json(self):
        return self._data


class _FakeHttp:
    """Enregistre les appels et renvoie des reponses preparees dans l'ordre."""

    def __init__(self, *responses, error=None):
        self.responses = list(responses)
        self.error = error
        self.calls = []

    async def _next(self, verb, url, kwargs):
        self.calls.append({"verb": verb, "url": url, **kwargs})
        if self.error:
            raise self.error
        return self.responses.pop(0)

    async def get(self, url, **kw):
        return await self._next("GET", url, kw)

    async def post(self, url, **kw):
        return await self._next("POST", url, kw)

    async def request(self, method, url, **kw):
        return await self._next(method, url, kw)


async def _verify(monkeypatch, fake, service, api_key="k", nzbdav_url=None,
                  webdav_user=None, webdav_password=None):
    monkeypatch.setattr(routes, "http_client", fake)
    resp = await routes.verify_api_key(
        service=service, api_key=api_key, nzbdav_url=nzbdav_url,
        webdav_user=webdav_user, webdav_password=webdav_password,
    )
    return json.loads(resp.body)


# ---------- cle vide ----------
@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "   ", "\t\n"])
async def test_blank_key_is_invalid_without_any_request(monkeypatch, key):
    fake = _FakeHttp()
    assert await _verify(monkeypatch, fake, ServiceType.alldebrid, api_key=key) == {"valid": False}
    assert fake.calls == []


# ---------- AllDebrid ----------
@pytest.mark.asyncio
async def test_alldebrid_valid_and_key_is_stripped(monkeypatch):
    fake = _FakeHttp(_Resp(200, {"status": "success"}))
    assert await _verify(monkeypatch, fake, ServiceType.alldebrid, api_key="  abc123  ") == {"valid": True}
    assert fake.calls[0]["params"]["apikey"] == "abc123"


@pytest.mark.asyncio
@pytest.mark.parametrize("resp", [
    _Resp(200, {"status": "error", "error": {"code": "AUTH_BAD_APIKEY"}}),
    _Resp(200, {}),
    _Resp(401, {"status": "success"}),
    _Resp(500),
])
async def test_alldebrid_invalid_cases(monkeypatch, resp):
    assert await _verify(monkeypatch, _FakeHttp(resp), ServiceType.alldebrid) == {"valid": False}


# ---------- TorBox ----------
@pytest.mark.asyncio
async def test_torbox_valid_sends_bearer(monkeypatch):
    fake = _FakeHttp(_Resp(200, {"success": True}))
    assert await _verify(monkeypatch, fake, ServiceType.torbox, api_key="tb") == {"valid": True}
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tb"


@pytest.mark.asyncio
@pytest.mark.parametrize("resp", [_Resp(200, {"success": False}), _Resp(200, {}), _Resp(403)])
async def test_torbox_invalid_cases(monkeypatch, resp):
    assert await _verify(monkeypatch, _FakeHttp(resp), ServiceType.torbox) == {"valid": False}


# ---------- Premiumize ----------
@pytest.mark.asyncio
async def test_premiumize_valid_and_invalid(monkeypatch):
    assert await _verify(monkeypatch, _FakeHttp(_Resp(200, {"status": "success"})), ServiceType.premiumize) == {"valid": True}
    assert await _verify(monkeypatch, _FakeHttp(_Resp(200, {"status": "error"})), ServiceType.premiumize) == {"valid": False}


# ---------- 1fichier : « KO » = invalide, tout autre 200 = valide ----------
@pytest.mark.asyncio
async def test_onefichier_ko_is_invalid_other_200_is_valid(monkeypatch):
    assert await _verify(monkeypatch, _FakeHttp(_Resp(200, {"status": "KO"})), ServiceType.onefichier) == {"valid": False}
    assert await _verify(monkeypatch, _FakeHttp(_Resp(200, {"items": []})), ServiceType.onefichier) == {"valid": True}
    assert await _verify(monkeypatch, _FakeHttp(_Resp(403)), ServiceType.onefichier) == {"valid": False}


# ---------- TMDB ----------
@pytest.mark.asyncio
async def test_tmdb_depends_only_on_status_code(monkeypatch):
    assert await _verify(monkeypatch, _FakeHttp(_Resp(200)), ServiceType.tmdb) == {"valid": True}
    assert await _verify(monkeypatch, _FakeHttp(_Resp(401)), ServiceType.tmdb) == {"valid": False}


# ---------- une erreur reseau ne fait jamais planter la route ----------
@pytest.mark.asyncio
@pytest.mark.parametrize("service", [ServiceType.alldebrid, ServiceType.torbox, ServiceType.tmdb])
async def test_network_error_returns_invalid(monkeypatch, service):
    fake = _FakeHttp(error=RuntimeError("boom"))
    assert await _verify(monkeypatch, fake, service) == {"valid": False}


# ---------- NZBDav ----------
@pytest.mark.asyncio
@pytest.mark.parametrize("kw,err", [
    ({"nzbdav_url": None, "webdav_user": "u", "webdav_password": "p"}, "Missing NZBDav URL"),
    ({"nzbdav_url": "  ", "webdav_user": "u", "webdav_password": "p"}, "Missing NZBDav URL"),
    ({"nzbdav_url": "http://x", "webdav_user": None, "webdav_password": "p"}, "Missing WebDAV user"),
    ({"nzbdav_url": "http://x", "webdav_user": "u", "webdav_password": " "}, "Missing WebDAV password"),
])
async def test_nzbdav_missing_fields(monkeypatch, kw, err):
    fake = _FakeHttp()
    out = await _verify(monkeypatch, fake, ServiceType.nzbdav, **kw)
    assert out == {"valid": False, "error": err}
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["ftp://host", "file:///etc/passwd", "gopher://h", "javascript:alert(1)", "host-sans-schema"])
async def test_nzbdav_refuses_non_http_schemes_without_request(monkeypatch, url):
    fake = _FakeHttp()
    out = await _verify(monkeypatch, fake, ServiceType.nzbdav, nzbdav_url=url, webdav_user="u", webdav_password="p")
    assert out["valid"] is False and "HTTP/HTTPS" in out["error"]
    assert fake.calls == []


@pytest.mark.asyncio
async def test_nzbdav_full_success_builds_expected_requests(monkeypatch):
    fake = _FakeHttp(_Resp(200), _Resp(207))
    out = await _verify(monkeypatch, fake, ServiceType.nzbdav, api_key="sab",
                        nzbdav_url=" https://nzb.example/ ", webdav_user="u", webdav_password="p")
    assert out == {"valid": True}
    assert fake.calls[0]["url"] == "https://nzb.example/api"
    assert fake.calls[0]["params"]["apikey"] == "sab"
    assert fake.calls[1]["verb"] == "PROPFIND" and fake.calls[1]["url"] == "https://nzb.example/content/"


@pytest.mark.asyncio
async def test_nzbdav_bad_sabnzbd_key_stops_before_webdav(monkeypatch):
    fake = _FakeHttp(_Resp(403))
    out = await _verify(monkeypatch, fake, ServiceType.nzbdav, nzbdav_url="http://n", webdav_user="u", webdav_password="p")
    assert out == {"valid": False, "error": "Invalid SABnzbd API key"}
    assert len(fake.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("webdav_status,expected", [
    (401, {"valid": False, "error": "Invalid WebDAV credentials"}),
    (500, {"valid": False, "error": "WebDAV error: 500"}),
    (200, {"valid": True}),
    (404, {"valid": True}),
])
async def test_nzbdav_webdav_statuses(monkeypatch, webdav_status, expected):
    fake = _FakeHttp(_Resp(200), _Resp(webdav_status))
    assert await _verify(monkeypatch, fake, ServiceType.nzbdav, nzbdav_url="http://n", webdav_user="u", webdav_password="p") == expected
