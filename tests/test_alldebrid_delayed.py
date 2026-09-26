"""Tests du polling /link/delayed AllDebrid (wastream/debrid/alldebrid.py) :
avant ce correctif, tout "delayed" renvoye par /link/unlock etait abandonne
immediatement (LINK_UNCACHED) sans jamais interroger /link/delayed. Faux
client HTTP + sleep instantane -- aucun appel reseau ni attente reelle."""
import pytest

from wastream.debrid import alldebrid as ad

API_KEY = "KEY"


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, responses):
        # liste de reponses successives pour /link/delayed (dans l'ordre)
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    async def _instant_sleep(_seconds):
        return None
    monkeypatch.setattr(ad, "sleep", _instant_sleep)


@pytest.mark.asyncio
async def test_returns_link_once_ready(monkeypatch):
    fake = _FakeHttp([
        _Resp(payload={"status": "success", "data": {"status": 1}}),
        _Resp(payload={"status": "success", "data": {"status": 2, "link": "https://final.example/f"}}),
    ])
    monkeypatch.setattr(ad, "http_client", fake)

    result = await ad.AllDebridService._wait_for_delayed_link(API_KEY, 12345)

    assert result == "https://final.example/f"
    assert len(fake.calls) == 2
    assert all(kw["params"]["id"] == 12345 for _, kw in fake.calls)


@pytest.mark.asyncio
async def test_status_3_is_definitive_failure(monkeypatch):
    fake = _FakeHttp([_Resp(payload={"status": "success", "data": {"status": 3}})])
    monkeypatch.setattr(ad, "http_client", fake)

    result = await ad.AllDebridService._wait_for_delayed_link(API_KEY, 1)

    assert result is None
    assert len(fake.calls) == 1  # pas de polling supplementaire apres un echec definitif


@pytest.mark.asyncio
async def test_gives_up_after_max_polls_still_pending(monkeypatch):
    fake = _FakeHttp([_Resp(payload={"status": "success", "data": {"status": 1}})] * ad.ALLDEBRID_DELAYED_MAX_POLLS)
    monkeypatch.setattr(ad, "http_client", fake)

    result = await ad.AllDebridService._wait_for_delayed_link(API_KEY, 1)

    assert result is None
    assert len(fake.calls) == ad.ALLDEBRID_DELAYED_MAX_POLLS


@pytest.mark.asyncio
async def test_api_error_stops_polling(monkeypatch):
    fake = _FakeHttp([_Resp(payload={"status": "error", "error": {"code": "DELAYED_INVALID_ID"}})])
    monkeypatch.setattr(ad, "http_client", fake)

    result = await ad.AllDebridService._wait_for_delayed_link(API_KEY, 1)

    assert result is None
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_http_error_stops_polling(monkeypatch):
    fake = _FakeHttp([_Resp(status_code=500)])
    monkeypatch.setattr(ad, "http_client", fake)

    result = await ad.AllDebridService._wait_for_delayed_link(API_KEY, 1)

    assert result is None
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------
# Integration : convert_link() bout en bout sur un lien hebergeur direct
# ---------------------------------------------------------------------
class _RoutedFakeHttp:
    """Route /link/unlock puis /link/delayed vers des reponses distinctes,
    contrairement a _FakeHttp qui depile une liste unique -- necessaire ici
    car convert_link() appelle les deux endpoints dans le meme test."""
    def __init__(self, unlock_resp, delayed_responses):
        self.unlock_resp = unlock_resp
        self.delayed_responses = list(delayed_responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/link/unlock"):
            return self.unlock_resp
        if url.endswith("/link/delayed"):
            return self.delayed_responses.pop(0)
        raise AssertionError(f"appel inattendu : {url}")


@pytest.mark.asyncio
async def test_convert_link_direct_hoster_becomes_ready_via_delayed(monkeypatch):
    fake = _RoutedFakeHttp(
        unlock_resp=_Resp(payload={"status": "success", "data": {"delayed": 999}}),
        delayed_responses=[_Resp(payload={"status": "success", "data": {"status": 2, "link": "https://final.example/movie"}})],
    )
    monkeypatch.setattr(ad, "http_client", fake)

    result = await ad.AllDebridService().convert_link("https://1fichier.com/abc123", API_KEY)

    assert result == "https://final.example/movie"


@pytest.mark.asyncio
async def test_convert_link_direct_hoster_falls_back_to_uncached(monkeypatch):
    fake = _RoutedFakeHttp(
        unlock_resp=_Resp(payload={"status": "success", "data": {"delayed": 999}}),
        delayed_responses=[_Resp(payload={"status": "success", "data": {"status": 3}})],
    )
    monkeypatch.setattr(ad, "http_client", fake)

    result = await ad.AllDebridService().convert_link("https://1fichier.com/abc123", API_KEY)

    assert result == "LINK_UNCACHED"
