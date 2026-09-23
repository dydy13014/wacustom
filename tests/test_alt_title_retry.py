"""Relance sur titres alternatifs (services/stream.py, get_streams) : complète
quand il y a peu de résultats, limitée aux trackers interrogés par titre quand
les résultats ne viennent que d'autres sources, absente sinon."""
import pytest

from wastream.config.settings import settings
from wastream.services import stream as st

TRACKERS = set(st.TITLE_SEARCH_TRACKERS)
BASE_CONFIG = {"tmdb_api_token": "x", "debrid_services": [{"service": "alldebrid", "api_key": "k"}]}


def _ddl(n):
    return [{"source": "Wawacity", "model_type": "link", "hoster": "1fichier", "link": f"https://1fichier.com/?{i}",
             "quality": "1080p", "language": "French", "display_name": f"ddl {i}"} for i in range(n)]


def _torrent(source):
    return [{"source": source, "model_type": "torrent", "hoster": "Torrent", "link": "magnet:?xt=urn:btih:" + "a" * 40,
             "infohash": "a" * 40, "quality": "1080p", "language": "French", "display_name": "t"}]


def _sources_of(service, config):
    entry = config["debrid_services"][0]
    return set(service._get_sources_for_service(entry["service"], entry))


async def _run(monkeypatch, main_results, config=BASE_CONFIG):
    service = st.StreamService()
    calls = []

    async def fake_metadata(imdb_id, token):
        return {"title": "Surveillant !", "year": "2025",
                "enhanced": {"titles": ["Surveillant !", "Minimum Security"]}}

    async def fake_search(title, year, content_type, season, episode, enhanced, config):
        calls.append((title, config))
        return list(main_results) if title == "Surveillant !" else []

    async def fake_enrich(*args, **kwargs):
        return []

    monkeypatch.setattr(service, "_get_metadata", fake_metadata)
    monkeypatch.setattr(service, "_search_content", fake_search)
    monkeypatch.setattr(service, "_check_cache_and_enrich", fake_enrich)
    await service.get_streams("movie", "tt1234567", dict(config), "http://x")
    return service, calls


@pytest.mark.asyncio
async def test_only_ddl_results_retries_trackers_only(monkeypatch):
    service, calls = await _run(monkeypatch, _ddl(20))
    retries = [c for c in calls if c[0] == "Minimum Security"]
    assert len(retries) == 1
    assert _sources_of(service, retries[0][1]) == TRACKERS


@pytest.mark.asyncio
async def test_few_results_retries_all_sources(monkeypatch):
    service, calls = await _run(monkeypatch, _ddl(5))
    retries = [c for c in calls if c[0] == "Minimum Security"]
    assert len(retries) == 1
    assert _sources_of(service, retries[0][1]) == set(settings.ALLDEBRID_SUPPORTED_SOURCES)


@pytest.mark.asyncio
async def test_tracker_answered_means_no_retry(monkeypatch):
    _, calls = await _run(monkeypatch, _ddl(20) + _torrent("C411"))
    assert [c[0] for c in calls] == ["Surveillant !"]


@pytest.mark.asyncio
async def test_v3x_alone_does_not_count_as_tracker_answer(monkeypatch):
    _, calls = await _run(monkeypatch, _ddl(20) + _torrent("V3X"))
    assert "Minimum Security" in [c[0] for c in calls]


@pytest.mark.asyncio
async def test_user_without_trackers_gets_no_retry(monkeypatch):
    config = {"tmdb_api_token": "x", "debrid_services": [{"service": "alldebrid", "api_key": "k", "sources": ["wawacity"]}]}
    _, calls = await _run(monkeypatch, _ddl(20), config)
    assert [c[0] for c in calls] == ["Surveillant !"]


# ===========================
# _restrict_to_sources
# ===========================
def test_restrict_keeps_only_enabled_trackers():
    service = st.StreamService()
    config = {"debrid_services": [{"service": "alldebrid", "api_key": "k", "sources": ["wawacity", "c411", "tr4ker"]}]}
    restricted = service._restrict_to_sources(config, st.TITLE_SEARCH_TRACKERS)
    assert restricted["debrid_services"][0]["sources"] == ["c411", "tr4ker"]
    assert config["debrid_services"][0]["sources"] == ["wawacity", "c411", "tr4ker"]


def test_restrict_uses_service_defaults_when_no_sources():
    service = st.StreamService()
    restricted = service._restrict_to_sources(BASE_CONFIG, st.TITLE_SEARCH_TRACKERS)
    assert set(restricted["debrid_services"][0]["sources"]) == TRACKERS


def test_restrict_drops_services_without_tracker():
    service = st.StreamService()
    config = {"debrid_services": [
        {"service": "alldebrid", "api_key": "k", "sources": ["wawacity"]},
        {"service": "torbox", "api_key": "k2", "sources": ["yggreborn"]},
    ]}
    restricted = service._restrict_to_sources(config, st.TITLE_SEARCH_TRACKERS)
    assert [s["service"] for s in restricted["debrid_services"]] == ["torbox"]


def test_restrict_returns_none_when_no_tracker_enabled():
    service = st.StreamService()
    config = {"debrid_services": [{"service": "alldebrid", "api_key": "k", "sources": ["wawacity"]}]}
    assert service._restrict_to_sources(config, st.TITLE_SEARCH_TRACKERS) is None
