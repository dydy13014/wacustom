"""Tests de AllDebridService.convert_link : c'est le chemin emprunte a CHAQUE
lecture d'un lien debride. On verifie le code renvoye pour chaque reponse de
l'API AllDebrid (lien direct, lien a redirecteur, erreurs, nouvelles
tentatives), sans aucun appel reseau ni attente reelle."""
import pytest

from wastream.config.settings import settings
from wastream.debrid import alldebrid as ad

DIRECT = "https://1fichier.com/?abc123"          # hebergeur supporte : /link/unlock direct
REDIRECTED = "https://dl-protect.example/xyz"     # protege par redirecteur : /link/redirector puis unlock


class _Resp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def json(self):
        return self._data


def _ok(**data):
    return _Resp(200, {"status": "success", "data": data})


def _err(code):
    return _Resp(200, {"status": "error", "error": {"code": code}})


class _FakeHttp:
    def __init__(self, *responses, error=None):
        self.responses = list(responses)
        self.error = error
        self.calls = []

    async def get(self, url, **kw):
        self.calls.append((url, kw.get("params", {})))
        if self.error:
            raise self.error
        # Une fois la liste epuisee, on repete la derniere reponse.
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


@pytest.fixture
def svc(monkeypatch):
    async def _no_sleep(*a, **kw):
        return None
    monkeypatch.setattr(ad, "sleep", _no_sleep)
    monkeypatch.setattr(settings, "DEBRID_MAX_RETRIES", 3)
    monkeypatch.setattr(settings, "DEBRID_HTTP_ERROR_MAX_RETRIES", 2)
    return ad.AllDebridService()


def _use(monkeypatch, fake):
    monkeypatch.setattr(ad, "http_client", fake)
    return fake


# ---------- entrees ----------
@pytest.mark.asyncio
async def test_empty_api_key_returns_none(svc, monkeypatch):
    fake = _use(monkeypatch, _FakeHttp(_ok()))
    assert await svc.convert_link(DIRECT, "") is None
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("link", ["magnet:?xt=urn:btih:abc", "a" * 40, "A1" * 16])
async def test_magnet_and_infohash_are_routed_to_torrent_flow(svc, monkeypatch, link):
    seen = {}

    async def fake_torrent(magnet, api_key, season, episode):
        seen["magnet"] = magnet
        return "TORRENT_RESULT"
    monkeypatch.setattr(svc, "_convert_torrent_link", fake_torrent)
    fake = _use(monkeypatch, _FakeHttp(_ok()))
    assert await svc.convert_link(link, "k") == "TORRENT_RESULT"
    assert seen["magnet"].startswith("magnet:?xt=urn:btih:")
    assert fake.calls == []


# ---------- lien direct (hebergeur supporte) ----------
@pytest.mark.asyncio
async def test_direct_link_success_calls_unlock(svc, monkeypatch):
    fake = _use(monkeypatch, _FakeHttp(_ok(link="https://cdn.example/video.mkv")))
    assert await svc.convert_link(DIRECT, "k") == "https://cdn.example/video.mkv"
    assert fake.calls[0][0].endswith("/link/unlock")
    assert fake.calls[0][1]["apikey"] == "k"


@pytest.mark.asyncio
async def test_direct_link_delayed_means_uncached(svc, monkeypatch):
    _use(monkeypatch, _FakeHttp(_ok(delayed=123)))
    assert await svc.convert_link(DIRECT, "k") == "LINK_UNCACHED"


@pytest.mark.asyncio
@pytest.mark.parametrize("code,expected", [
    ("LINK_DOWN", "LINK_DOWN"),
    ("LINK_PASS_PROTECTED", "LINK_UNSUPPORTED"),
    ("NO_SERVER", "FATAL_ERROR"),
    ("SOMETHING_UNKNOWN", "FATAL_ERROR"),
])
async def test_direct_link_error_codes(svc, monkeypatch, code, expected):
    fake = _use(monkeypatch, _FakeHttp(_err(code)))
    assert await svc.convert_link(DIRECT, "k") == expected
    assert len(fake.calls) == 1          # aucune nouvelle tentative inutile


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ad.RETRY_ERRORS)
async def test_retryable_errors_retry_then_give_retry_error(svc, monkeypatch, code):
    marked = []
    monkeypatch.setattr(ad, "mark_hoster_down", lambda *a: marked.append(a))
    fake = _use(monkeypatch, _FakeHttp(_err(code)))
    assert await svc.convert_link(DIRECT, "k") == "RETRY_ERROR"
    assert len(fake.calls) == settings.DEBRID_MAX_RETRIES
    assert bool(marked) == (code == "LINK_HOST_UNAVAILABLE")


@pytest.mark.asyncio
async def test_retry_error_then_success(svc, monkeypatch):
    fake = _use(monkeypatch, _FakeHttp(_err("LINK_TEMPORARY_UNAVAILABLE"), _ok(link="https://cdn/ok")))
    assert await svc.convert_link(DIRECT, "k") == "https://cdn/ok"
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_direct_success_without_link_retries_then_fails(svc, monkeypatch):
    fake = _use(monkeypatch, _FakeHttp(_ok()))
    assert await svc.convert_link(DIRECT, "k") == "FATAL_ERROR"
    assert len(fake.calls) == settings.DEBRID_MAX_RETRIES


# ---------- erreurs HTTP ----------
@pytest.mark.asyncio
async def test_http_5xx_gives_up_with_retry_error(svc, monkeypatch):
    fake = _use(monkeypatch, _FakeHttp(_Resp(503)))
    assert await svc.convert_link(DIRECT, "k") == "RETRY_ERROR"
    assert len(fake.calls) == settings.DEBRID_HTTP_ERROR_MAX_RETRIES


@pytest.mark.asyncio
async def test_http_403_is_fatal_after_all_attempts(svc, monkeypatch):
    fake = _use(monkeypatch, _FakeHttp(_Resp(403)))
    assert await svc.convert_link(DIRECT, "k") == "FATAL_ERROR"
    assert len(fake.calls) == settings.DEBRID_MAX_RETRIES


@pytest.mark.asyncio
async def test_exception_is_retried_then_fatal(svc, monkeypatch):
    fake = _use(monkeypatch, _FakeHttp(error=RuntimeError("boom")))
    assert await svc.convert_link(DIRECT, "k") == "FATAL_ERROR"
    assert len(fake.calls) == settings.DEBRID_MAX_RETRIES


# ---------- lien a redirecteur ----------
@pytest.mark.asyncio
async def test_redirector_link_down_is_service_down(svc, monkeypatch):
    fake = _use(monkeypatch, _FakeHttp(_err("LINK_DOWN")))
    assert await svc.convert_link(REDIRECTED, "k") == "LINK_SERVICE_DOWN"
    assert fake.calls[0][0].endswith("/link/redirector")


@pytest.mark.asyncio
async def test_redirector_without_links_ends_fatal(svc, monkeypatch):
    fake = _use(monkeypatch, _FakeHttp(_ok(links=[])))
    assert await svc.convert_link(REDIRECTED, "k") == "FATAL_ERROR"
    assert len(fake.calls) == settings.DEBRID_MAX_RETRIES


@pytest.mark.asyncio
async def test_redirector_no_usable_link_is_unsupported(svc, monkeypatch):
    async def none_selected(*a, **kw):
        return None
    monkeypatch.setattr(svc, "_select_redirected_link", none_selected)
    _use(monkeypatch, _FakeHttp(_ok(links=["https://host/a"])))
    assert await svc.convert_link(REDIRECTED, "k") == "LINK_UNSUPPORTED"


@pytest.mark.asyncio
async def test_redirector_then_unlock_success(svc, monkeypatch):
    async def pick(links, api_key, hoster):
        return links[0]
    monkeypatch.setattr(svc, "_select_redirected_link", pick)
    fake = _use(monkeypatch, _FakeHttp(_ok(links=["https://host/a"]), _ok(link="https://cdn/final")))
    assert await svc.convert_link(REDIRECTED, "k") == "https://cdn/final"
    assert fake.calls[0][0].endswith("/link/redirector")
    assert fake.calls[1][0].endswith("/link/unlock")
    assert fake.calls[1][1]["link"] == "https://host/a"


@pytest.mark.asyncio
@pytest.mark.parametrize("second,expected", [
    (_err("LINK_DOWN"), "LINK_SERVICE_DOWN"),
    (_err("LINK_PASS_PROTECTED"), "LINK_UNSUPPORTED"),
    (_err("NO_SERVER"), "FATAL_ERROR"),
    (_ok(delayed=1), "LINK_UNCACHED"),
])
async def test_redirector_unlock_step_outcomes(svc, monkeypatch, second, expected):
    async def pick(links, api_key, hoster):
        return links[0]
    monkeypatch.setattr(svc, "_select_redirected_link", pick)
    _use(monkeypatch, _FakeHttp(_ok(links=["https://host/a"]), second))
    assert await svc.convert_link(REDIRECTED, "k") == expected
