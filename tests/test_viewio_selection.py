"""Viewio doit respecter la selection des sources : decochee, elle n'est
jamais interrogee, meme si VIEWIO_MANIFEST_URL est defini (regression : un
repli de compatibilite la relancait malgre la case decochee)."""
import pytest

from wastream.config.settings import settings
from wastream.services import stream as stream_mod


class _StubScraper:
    async def search(self, *args, **kwargs):
        return []


async def _run(monkeypatch, supported_sources, calls):
    service = stream_mod.StreamService()
    monkeypatch.setattr(settings, "VIEWIO_MANIFEST_URL", "https://addon.viewio.me/u/abcDEF123_-xyz789")
    monkeypatch.setattr(service, "_get_supported_sources", lambda config: supported_sources)
    monkeypatch.setattr(service, "_get_early_stop_config", lambda config: {"enabled": False, "min_streams": 3, "min_hosts": 2, "sources": [], "hosts": []})
    monkeypatch.setattr(service, "_is_source_allowed_for_content", lambda *a, **kw: True)

    async def _fake_cache(source_name, ct, do_search, *a, **kw):
        return await do_search()
    monkeypatch.setattr(service, "_search_source_with_cache", _fake_cache)

    async def _fake_search(*a, **kw):
        calls.append("viewio")
        return []
    monkeypatch.setattr(stream_mod.viewio_scraper, "search", _fake_search)

    stub = _StubScraper()
    await service._search_content_common(
        "movie", "movie", stub, stub, stub, stub, stub, stub, stub,
        "Drive", "2026", {}, config={"x": 1}, use_episode_cache=False,
    )


@pytest.mark.asyncio
async def test_viewio_not_queried_when_deselected(monkeypatch):
    calls = []
    await _run(monkeypatch, ["nyaa", "lumio"], calls)
    assert calls == []


@pytest.mark.asyncio
async def test_viewio_queried_when_selected(monkeypatch):
    calls = []
    await _run(monkeypatch, ["nyaa", "viewio"], calls)
    assert calls == ["viewio"]
