"""Tests du scraper Nyaa (wastream/scrapers/nyaa/base.py) : parsing du flux
RSS et extraction des champs du namespace nyaa: (seeders, leechers, trusted,
remake), avec un faux client HTTP -- aucun appel reseau reel."""
import pytest

from wastream.scrapers.nyaa import base as nyaa_mod

NS = nyaa_mod.NYAA_NS
HASH_A = "a" * 40
HASH_B = "b" * 40


class _Resp:
    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.content = content


class _FakeHttp:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.resp


def _feed(items: str) -> bytes:
    return f"""<?xml version="1.0"?>
<rss xmlns:nyaa="{NS}"><channel>
{items}
</channel></rss>""".encode()


_FULL_ITEM = f"""<item>
  <title>Some.Anime.S01E01.1080p</title>
  <nyaa:infoHash>{HASH_A}</nyaa:infoHash>
  <nyaa:size>861.3 MiB</nyaa:size>
  <nyaa:seeders>42</nyaa:seeders>
  <nyaa:leechers>3</nyaa:leechers>
  <nyaa:trusted>Yes</nyaa:trusted>
  <nyaa:remake>No</nyaa:remake>
</item>"""

_REMAKE_ITEM = f"""<item>
  <title>Some.Anime.S01E01.720p.Remake</title>
  <nyaa:infoHash>{HASH_B}</nyaa:infoHash>
  <nyaa:size>400 MiB</nyaa:size>
  <nyaa:seeders>0</nyaa:seeders>
  <nyaa:leechers>0</nyaa:leechers>
  <nyaa:trusted>No</nyaa:trusted>
  <nyaa:remake>Yes</nyaa:remake>
</item>"""

_NO_HEALTH_ITEM = f"""<item>
  <title>Some.Anime.S01E01.480p</title>
  <nyaa:infoHash>{"c" * 40}</nyaa:infoHash>
  <nyaa:size>200 MiB</nyaa:size>
</item>"""


@pytest.mark.asyncio
async def test_extracts_seeders_peers_trusted_remake(monkeypatch):
    monkeypatch.setattr(nyaa_mod.settings, "NYAA_URL", "https://nyaa.example")
    monkeypatch.setattr(nyaa_mod, "http_client", _FakeHttp(_Resp(content=_feed(_FULL_ITEM))))

    results = await nyaa_mod.nyaa_scraper.search("Some Anime", season="1", episode="1")

    assert len(results) == 1
    r = results[0]
    assert r["seeders"] == 42
    assert r["peers"] == 3
    assert r["trusted"] is True
    assert r["remake"] is False


@pytest.mark.asyncio
async def test_remake_flag_true_when_marked(monkeypatch):
    monkeypatch.setattr(nyaa_mod.settings, "NYAA_URL", "https://nyaa.example")
    monkeypatch.setattr(nyaa_mod, "http_client", _FakeHttp(_Resp(content=_feed(_REMAKE_ITEM))))

    results = await nyaa_mod.nyaa_scraper.search("Some Anime", season="1", episode="1")

    assert len(results) == 1
    assert results[0]["remake"] is True
    assert results[0]["trusted"] is False
    assert results[0]["seeders"] == 0
    assert results[0]["peers"] == 0


@pytest.mark.asyncio
async def test_missing_health_fields_are_omitted_not_zero(monkeypatch):
    monkeypatch.setattr(nyaa_mod.settings, "NYAA_URL", "https://nyaa.example")
    monkeypatch.setattr(nyaa_mod, "http_client", _FakeHttp(_Resp(content=_feed(_NO_HEALTH_ITEM))))

    results = await nyaa_mod.nyaa_scraper.search("Some Anime", season="1", episode="1")

    assert len(results) == 1
    r = results[0]
    assert "seeders" not in r
    assert "peers" not in r
    # trusted/remake restent toujours presents (defaut False si balise absente)
    assert r["trusted"] is False
    assert r["remake"] is False
