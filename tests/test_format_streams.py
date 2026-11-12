"""Tests de StreamService._format_streams : transforme les resultats de
recherche en flux Stremio. C'est ce que VOIT chaque utilisateur : nom,
description, lien de lecture signe, ordre de tri, liens morts ecartes.

Les dependances externes (base des liens morts, sante des sources) sont
remplacees par des versions factices : aucun acces reseau ni base de donnees.
"""
import pytest

from wastream.config.settings import settings
from wastream.services import stream as stream_mod
from wastream.utils.database import DEAD_LINK_STATUS_CONFIRMED, DEAD_LINK_STATUS_RECHECKABLE
from wastream.utils.helpers import decode_playback_token

BASE = "https://addon.example"


def R(link="https://1fichier.com/?a", **kw):
    r = {"link": link, "source": "Wawacity", "hoster": "1fichier", "quality": "1080p",
         "language": "French", "size": "2 GB", "display_name": "Film.2024.MULTI.1080p",
         "cache_status": "cached", "model_type": "ddl", "debrid_service": "alldebrid"}
    r.update(kw)
    return r


@pytest.fixture
def fmt(monkeypatch):
    state = {"dead": {}, "recheck": False, "offline": set()}

    async def fake_statuses(links):
        return {l: state["dead"][l] for l in links if l in state["dead"]}

    monkeypatch.setattr(stream_mod, "get_dead_link_statuses_batch", fake_statuses)
    monkeypatch.setattr(stream_mod, "is_dead_link_recheck_enabled", lambda cfg: state["recheck"])
    monkeypatch.setattr(stream_mod, "is_source_online", lambda name: name not in state["offline"])
    svc = stream_mod.StreamService()

    async def run(results, config=None, season=None, episode=None, year="2024", content_type="movie"):
        return await svc._format_streams(results, config or {}, BASE, season, episode, year, content_type)

    run.state = state
    return run


def token_of(stream):
    return decode_playback_token(stream["url"].split("/playback/")[1].split("/")[0])


# ---------- liens ecartes ----------
@pytest.mark.asyncio
async def test_results_without_link_are_dropped(fmt):
    assert await fmt([R(link=""), {"source": "x"}]) == []


@pytest.mark.asyncio
async def test_confirmed_dead_link_is_dropped(fmt):
    fmt.state["dead"]["https://dead"] = {"failure_count": DEAD_LINK_STATUS_CONFIRMED}
    out = await fmt([R(link="https://dead"), R(link="https://alive")])
    assert [token_of(s)["l"] for s in out] == ["https://alive"]


@pytest.mark.asyncio
async def test_recheckable_dead_link_depends_on_recheck_setting(fmt):
    fmt.state["dead"]["https://maybe"] = {"failure_count": DEAD_LINK_STATUS_RECHECKABLE}
    assert await fmt([R(link="https://maybe")]) == []
    fmt.state["recheck"] = True
    assert len(await fmt([R(link="https://maybe")])) == 1


@pytest.mark.asyncio
async def test_nzb_result_from_offline_source_is_dropped(fmt):
    fmt.state["offline"].add("Usenet")
    assert await fmt([R(model_type="nzb", source="Usenet")]) == []
    assert len(await fmt([R(model_type="nzb", source="Wawacity")])) == 1


# ---------- champs du flux ----------
@pytest.mark.asyncio
async def test_stream_fields(fmt):
    s = (await fmt([R(seeders=12, peers=3, freeleech=True, trusted=True)], year="2024"))[0]
    assert s["name"] == f"[AD ⚡]\n{settings.ADDON_NAME}\n1080p"
    lines = s["description"].split("\r\n")
    assert lines[0] == "🌍 French"
    assert lines[1] == "🎞️ 1080p"
    assert lines[2] == "📦 2 GB 📅 2024"
    assert lines[3] == "🌐 Wawacity ☁️ 1fichier"
    assert "🌱 12 🔻 3" in lines
    assert "🆓 Freeleech ✅ Trusted" in lines
    assert lines[-1] == "📁 Film.2024.MULTI.1080p"
    assert s["url"].startswith(f"{BASE}/playback/") and s["url"].endswith("/Film.2024.MULTI.1080p")
    assert s["behaviorHints"]["filename"] == "Film.2024.MULTI.1080p"
    assert s["behaviorHints"]["bingeGroup"] == "wacustom-wawacity-1080p"
    assert s["behaviorHints"]["videoSize"] == 2 * 1024 ** 3 or s["behaviorHints"]["videoSize"] > 0


@pytest.mark.asyncio
async def test_uncached_uses_hourglass_and_service_abbreviation(fmt):
    s = (await fmt([R(cache_status="uncached", debrid_service="torbox")]))[0]
    assert s["name"].startswith("[TB ⏳]")


@pytest.mark.asyncio
async def test_unknown_values_are_not_displayed(fmt):
    s = (await fmt([R(language="Unknown", quality="Unknown", size="Unknown", hoster="Unknown", display_name="Unknown")], year=None))[0]
    assert s["description"] == "🌐 Wawacity"
    assert s["url"].endswith("/stream")


# ---------- jeton de lecture ----------
@pytest.mark.asyncio
async def test_token_carries_link_service_source_and_episode(fmt):
    s = (await fmt([R()], season="2", episode="5", content_type="series"))[0]
    t = token_of(s)
    assert (t["l"], t["s"], t["so"], t["se"], t["ep"], t["ct"], t["h"]) == (
        "https://1fichier.com/?a", "alldebrid", "Wawacity", "2", "5", "series", "1fichier")


@pytest.mark.asyncio
async def test_token_uses_user_credentials_instead_of_embedding_config(fmt):
    s = (await fmt([R()], config={"_user_uuid": "uuid-1", "_enc_password": "enc", "_profile_id": "p2"}))[0]
    t = token_of(s)
    assert t["u"] == "uuid-1" and t["p"] == "enc" and t["pr"] == "p2" and "c" not in t


@pytest.mark.asyncio
async def test_token_embeds_config_when_no_user(fmt):
    t = token_of((await fmt([R()], config={"languages": ["French"]}))[0])
    assert "c" in t and "u" not in t


# ---------- langues Multi ----------
@pytest.mark.asyncio
async def test_multi_language_is_filtered_to_user_languages(fmt):
    r = R(language="Multi (French, English, German)", display_name="Film.Multi (French, English, German).1080p")
    s = (await fmt([r], config={"languages": ["French", "English"]}))[0]
    assert "🌍 Multi (French, English)" in s["description"]
    assert "Multi (French, English)" in s["behaviorHints"]["filename"]


@pytest.mark.asyncio
async def test_multi_language_untouched_without_user_languages_or_match(fmt):
    r = R(language="Multi (French, English)")
    assert "🌍 Multi (French, English)" in (await fmt([r]))[0]["description"]
    assert "🌍 Multi (French, English)" in (await fmt([r], config={"languages": ["Spanish"]}))[0]["description"]


# ---------- tri ----------
def _links(out):
    return [token_of(s)["l"] for s in out]


@pytest.mark.asyncio
async def test_cached_first_then_higher_resolution(fmt):
    out = await fmt([
        R(link="u720c", quality="720p"),
        R(link="u1080u", quality="1080p", cache_status="uncached"),
        R(link="u1080c", quality="1080p"),
    ])
    assert _links(out) == ["u1080c", "u720c", "u1080u"]


@pytest.mark.asyncio
async def test_bigger_size_first_when_same_resolution(fmt):
    out = await fmt([R(link="small", size="1 GB"), R(link="big", size="10 GB")])
    assert _links(out) == ["big", "small"]


@pytest.mark.asyncio
async def test_preferred_keywords_come_first(fmt):
    out = await fmt([R(link="a", display_name="Film.VOSTFR"), R(link="b", display_name="Film.TRUEFRENCH")],
                    config={"preferred_keywords": ["truefrench"], "sort_order": ["preferred"]})
    assert _links(out)[0] == "b"


@pytest.mark.asyncio
async def test_stream_type_preference(fmt):
    results = [R(link="torr", model_type="torrent"), R(link="ddl", model_type="ddl")]
    assert _links(await fmt(results, config={"stream_type_preference": "ddl_first"})) == ["ddl", "torr"]
    assert _links(await fmt(results, config={"stream_type_preference": "torrent_first"})) == ["torr", "ddl"]


@pytest.mark.asyncio
async def test_invalid_sort_keys_are_ignored(fmt):
    out = await fmt([R(link="a"), R(link="b")], config={"sort_order": ["nonsense"]})
    assert len(out) == 2


@pytest.mark.asyncio
async def test_prioritize_vff_multi_ranks_vostfr_after_french_audio(fmt):
    out = await fmt([R(link="vostfr", display_name="Film.VOSTFR.1080p"), R(link="vff", display_name="Film.VFF.1080p")],
                    config={"prioritize_vff_multi": True, "sort_order": ["language"]})
    assert _links(out) == ["vff", "vostfr"]


# ---------- sortie propre ----------
@pytest.mark.asyncio
async def test_internal_sort_values_never_leak(fmt):
    out = await fmt([R()])
    assert "_sort_values" not in out[0]
    assert set(out[0]) == {"name", "description", "behaviorHints", "url"}
