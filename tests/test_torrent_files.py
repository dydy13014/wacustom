"""Tests du remplacement magnet nu -> vrai .torrent pour les trackers prives
(services/torrent_files.py, debrid/alldebrid.py, scrapers/torznab/base.py),
avec de faux clients HTTP : aucun appel reseau reel."""
import time

import pytest

from wastream.debrid import alldebrid as ad
from wastream.scrapers.torznab import base as tz
from wastream.services import torrent_files as tf

HASH = "ab" * 20
TORRENT = b"d8:announce40:https://tracker.example/announce/PASSKEY4:infod4:name3:abcee"
SECRET_URL = "https://tracker.example/api?t=get&id=abc&apikey=SECRET"


@pytest.fixture(autouse=True)
def _reset_registry():
    tf._registry.clear()
    yield
    tf._registry.clear()


class _Resp:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _reply(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        for suffix, resp in self.routes.items():
            if url.endswith(suffix):
                return resp(kwargs) if callable(resp) else resp
        raise AssertionError(f"appel inattendu : {method} {url}")

    async def get(self, url, **kwargs):
        return self._reply("GET", url, kwargs)

    async def post(self, url, **kwargs):
        return self._reply("POST", url, kwargs)

    def urls(self):
        return [url for _, url, _ in self.calls]


# ===========================
# Registre
# ===========================
def test_remember_and_lookup():
    tf.remember_torrent_url(HASH.upper(), "C411", SECRET_URL)
    assert tf.torrent_source(HASH) == "C411"
    assert tf._lookup(HASH) == ("C411", SECRET_URL)


def test_magnet_or_empty_url_is_not_remembered():
    tf.remember_torrent_url(HASH, "C411", "magnet:?xt=urn:btih:" + HASH)
    tf.remember_torrent_url(HASH, "C411", "")
    assert tf.torrent_source(HASH) is None


def test_expired_entry_is_dropped():
    tf.remember_torrent_url(HASH, "C411", SECRET_URL)
    source, url, _ = tf._registry[HASH]
    tf._registry[HASH] = (source, url, time.monotonic() - 1)
    assert tf.torrent_source(HASH) is None
    assert HASH not in tf._registry


def test_registry_is_capped(monkeypatch):
    monkeypatch.setattr(tf, "_MAX_ENTRIES", 2)
    for h in ("1" * 40, "2" * 40, "3" * 40):
        tf.remember_torrent_url(h, "C411", SECRET_URL)
    assert list(tf._registry) == ["2" * 40, "3" * 40]


# ===========================
# Telechargement du .torrent
# ===========================
@pytest.mark.asyncio
async def test_fetch_returns_torrent_bytes(monkeypatch):
    fake = _FakeHttp({"apikey=SECRET": _Resp(content=TORRENT)})
    monkeypatch.setattr(tf, "http_client", fake)
    tf.remember_torrent_url(HASH, "C411", SECRET_URL)
    assert await tf.fetch_torrent_file(HASH) == TORRENT


@pytest.mark.asyncio
@pytest.mark.parametrize("resp", [
    _Resp(status_code=403, content=TORRENT),
    _Resp(content=b"<html>login</html>"),
    _Resp(content=b"d8:announce3:abce"),
])
async def test_fetch_rejects_non_torrent(monkeypatch, resp):
    monkeypatch.setattr(tf, "http_client", _FakeHttp({"apikey=SECRET": resp}))
    tf.remember_torrent_url(HASH, "C411", SECRET_URL)
    assert await tf.fetch_torrent_file(HASH) is None


@pytest.mark.asyncio
async def test_fetch_unknown_hash_makes_no_request(monkeypatch):
    fake = _FakeHttp({})
    monkeypatch.setattr(tf, "http_client", fake)
    assert await tf.fetch_torrent_file(HASH) is None
    assert fake.calls == []


# ===========================
# AllDebrid : remplacement du magnet nu
# ===========================
def _alldebrid_routes(upload_magnet, delete_ok=True, file_ok=True):
    return {
        "/magnet/upload": _Resp(payload={"status": "success", "data": {"magnets": [upload_magnet]}}),
        "/magnet/delete": _Resp(payload={"status": "success" if delete_ok else "error"}),
        "/magnet/upload/file": _Resp(payload=(
            {"status": "success", "data": {"files": [{"id": 77, "hash": HASH, "ready": False}]}}
            if file_ok else {"status": "error", "error": {"code": "MAGNET_FILE_UPLOAD_FAILED"}}
        )),
    }


def _setup(monkeypatch, upload_magnet, known=True, **kwargs):
    fake = _FakeHttp(_alldebrid_routes(upload_magnet, **kwargs))
    monkeypatch.setattr(ad, "http_client", fake)
    monkeypatch.setattr(tf, "http_client", _FakeHttp({"apikey=SECRET": _Resp(content=TORRENT)}))
    if known:
        tf.remember_torrent_url(HASH, "C411", SECRET_URL)
    return fake


async def _play(monkeypatch=None):
    return await ad.AllDebridService()._convert_torrent_link(f"magnet:?xt=urn:btih:{HASH}", "key")


@pytest.mark.asyncio
async def test_bare_uncached_magnet_is_replaced_by_torrent(monkeypatch):
    fake = _setup(monkeypatch, {"id": 12, "hash": HASH, "size": 0, "ready": False})

    assert await _play() == "LINK_UNCACHED"

    delete = [kw for method, url, kw in fake.calls if url.endswith("/magnet/delete")]
    assert delete and delete[0]["params"]["id"] == 12
    upload_file = [kw for method, url, kw in fake.calls if url.endswith("/magnet/upload/file")]
    assert upload_file and upload_file[0]["files"]["files[0]"][1] == TORRENT


@pytest.mark.asyncio
async def test_magnet_with_metadata_is_left_alone(monkeypatch):
    fake = _setup(monkeypatch, {"id": 12, "hash": HASH, "size": 123456, "ready": False})

    assert await _play() == "LINK_UNCACHED"

    assert not any(u.endswith(("/magnet/delete", "/magnet/upload/file")) for u in fake.urls())


@pytest.mark.asyncio
async def test_unknown_torrent_keeps_bare_magnet(monkeypatch):
    fake = _setup(monkeypatch, {"id": 12, "hash": HASH, "size": 0, "ready": False}, known=False)

    assert await _play() == "LINK_UNCACHED"

    assert not any(u.endswith(("/magnet/delete", "/magnet/upload/file")) for u in fake.urls())


@pytest.mark.asyncio
async def test_failed_delete_skips_file_upload(monkeypatch):
    fake = _setup(monkeypatch, {"id": 12, "hash": HASH, "size": 0, "ready": False}, delete_ok=False)

    assert await _play() == "LINK_UNCACHED"

    assert not any(u.endswith("/magnet/upload/file") for u in fake.urls())


@pytest.mark.asyncio
async def test_failed_file_upload_still_returns_uncached(monkeypatch):
    _setup(monkeypatch, {"id": 12, "hash": HASH, "size": 0, "ready": False}, file_ok=False)
    assert await _play() == "LINK_UNCACHED"


# ===========================
# Scraper Torznab
# ===========================
_FEED = """<?xml version="1.0"?>
<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>
<item>
  <title>Mountain.Men.S01.FRENCH.1080p.WEB.x264</title>
  <enclosure url="https://tracker.example/api?t=get&amp;id=1&amp;apikey=SECRET" type="application/x-bittorrent"/>
  <torznab:attr name="infohash" value="{h1}"/>
  <torznab:attr name="size" value="1073741824"/>
</item>
<item>
  <title>Mountain.Men.S01.FRENCH.720p.WEB.x264</title>
  <enclosure url="magnet:?xt=urn:btih:{h2}" type="application/x-bittorrent"/>
  <torznab:attr name="infohash" value="{h2}"/>
</item>
</channel></rss>""".format(h1="1" * 40, h2="2" * 40)


@pytest.mark.asyncio
async def test_torznab_remembers_torrent_url_without_leaking_key(monkeypatch):
    monkeypatch.setattr(tz, "http_client", _FakeHttp({"/api/torznab": _Resp(content=_FEED.encode())}))
    scraper = tz.BaseTorznab("C411", "https://tracker.example/api", "SECRET")

    results = await scraper.search("Mountain Men", season="1", episode="1")

    assert {r["infohash"] for r in results} == {"1" * 40, "2" * 40}
    assert tf.torrent_source("1" * 40) == "C411"
    assert tf.torrent_source("2" * 40) is None
    assert all("SECRET" not in str(value) for r in results for value in r.values())
