"""Tests du routage Nyaa dans _search_content_common (wastream/services/stream.py) :
un vrai anime (content_name == "anime", detecte via TMDB genre 16 + mot-cle
210024) doit interroger Nyaa en categorie Anime ("1_0"), et non plus etre
ignore comme avant ce correctif. Le bloc "Live Action" (categorie "4_0",
gate sur content_name in ("movie","series")) reste inchange et continue
d'exclure "anime". _search_source_with_cache est monkeypatche pour executer
la recherche directement, sans toucher au cache/DB reel."""
import pytest

from wastream.services import stream as stream_mod

SEVEN_UNUSED_SCRAPERS = (None,) * 7


class _StubScraper:
    """Renvoie [] pour toute source non testee ici (wasource, movix, etc.)."""
    async def search(self, *args, **kwargs):
        return []


async def _run_search_content_common(monkeypatch, content_type, content_name, metadata, nyaa_calls):
    service = stream_mod.StreamService()

    monkeypatch.setattr(service, "_get_supported_sources", lambda config: ["nyaa"])
    monkeypatch.setattr(service, "_get_early_stop_config", lambda config: {"enabled": False, "min_streams": 3, "min_hosts": 2, "sources": [], "hosts": []})
    monkeypatch.setattr(service, "_is_source_allowed_for_content", lambda *a, **kw: True)

    async def _fake_cache(source_name, ct, do_search, *a, **kw):
        return await do_search()
    monkeypatch.setattr(service, "_search_source_with_cache", _fake_cache)

    async def _fake_search(t, year, meta, season=None, episode=None, config=None, category="1_0", **kw):
        nyaa_calls.append({"title": t, "category": category, "content_name": content_name})
        return []
    monkeypatch.setattr(stream_mod.nyaa_scraper, "search", _fake_search)

    stub = _StubScraper()
    await service._search_content_common(
        content_type, content_name,
        stub, stub, stub, stub, stub, stub, stub,
        "Some Anime", "2024", metadata, season="1", episode="1", config={}, use_episode_cache=True,
    )


@pytest.mark.asyncio
async def test_anime_content_queries_nyaa_in_anime_category(monkeypatch):
    nyaa_calls = []
    metadata = {"content_type": "anime", "original_language": "ja"}

    await _run_search_content_common(monkeypatch, "anime", "anime", metadata, nyaa_calls)

    assert len(nyaa_calls) == 1
    assert nyaa_calls[0]["category"] == "1_0"


@pytest.mark.asyncio
async def test_series_content_still_queries_nyaa_in_live_action_category(monkeypatch):
    nyaa_calls = []
    metadata = {"content_type": "series", "original_language": "ja"}

    await _run_search_content_common(monkeypatch, "series", "series", metadata, nyaa_calls)

    assert len(nyaa_calls) == 1
    assert nyaa_calls[0]["category"] == "4_0"


@pytest.mark.asyncio
async def test_non_japanese_series_does_not_query_nyaa(monkeypatch):
    nyaa_calls = []
    metadata = {"content_type": "series", "original_language": "en"}

    await _run_search_content_common(monkeypatch, "series", "series", metadata, nyaa_calls)

    assert nyaa_calls == []
