"""Tests de l'arret anticipe de la recherche (StreamService.
_check_early_stop_conditions) : quand l'utilisateur a fixe un minimum de
flux, d'hebergeurs, de resolutions ou une liste de sources, la recherche
s'arrete des que ces conditions sont reunies. Une erreur ici rend la
recherche soit trop courte (resultats manquants), soit inutilement longue."""
import pytest

from wastream.services.stream import StreamService

svc = StreamService()


def cfg(**kw):
    base = {"enabled": True, "min_streams": 0, "min_hosts": 0, "sources": [],
            "hosts": [], "resolutions": [], "include_nzb": False}
    base.update(kw)
    return base


def res(link, hoster="1fichier", quality="1080p"):
    return {"link": link, "hoster": hoster, "quality": quality}


def check(results, config, source="Wawacity"):
    return svc._check_early_stop_conditions(results, config, source)


# ---------- configuration ----------
def test_get_early_stop_config_defaults_and_clamps_negatives():
    assert svc._get_early_stop_config({}) == {
        "enabled": False, "min_streams": 0, "min_hosts": 0, "sources": [],
        "hosts": [], "resolutions": [], "include_nzb": False}
    out = svc._get_early_stop_config({"early_stop": True, "early_stop_min_streams": -5, "early_stop_min_hosts": -1})
    assert out["enabled"] is True and out["min_streams"] == 0 and out["min_hosts"] == 0


# ---------- desactive / sans condition ----------
def test_disabled_never_stops():
    assert check([res("a")] * 5, cfg(enabled=False, min_streams=1)) is False


def test_enabled_without_any_condition_never_stops():
    assert check([res("a"), res("b")], cfg()) is False


# ---------- nombre de flux (liens uniques) ----------
def test_min_streams_counts_unique_links_only():
    results = [res("a"), res("a"), res("b")]
    assert check(results, cfg(min_streams=3)) is False       # 2 liens uniques
    assert check(results, cfg(min_streams=2)) is True


def test_results_without_link_are_not_counted():
    assert check([res(""), {"hoster": "x"}], cfg(min_streams=1)) is False


# ---------- NZB ----------
def test_nzb_links_excluded_unless_included():
    results = [res("https://h/nzb/1"), res("https://h/nzb/2"), res("https://h/f/3")]
    assert check(results, cfg(min_streams=2)) is False
    assert check(results, cfg(min_streams=2, include_nzb=True)) is True


# ---------- sources autorisees ----------
def test_source_must_be_in_allowed_list():
    results = [res("a")]
    assert check(results, cfg(min_streams=1, sources=["Movix"]), source="Wawacity") is False
    assert check(results, cfg(min_streams=1, sources=["Movix"]), source="Movix") is True


def test_sources_alone_is_a_valid_condition():
    assert check([], cfg(sources=["Wawacity"]), source="Wawacity") is True


# ---------- hebergeurs ----------
def test_min_hosts_counts_distinct_hosts_case_insensitive():
    results = [res("a", "1Fichier"), res("b", "1fichier"), res("c", "Turbobit")]
    assert check(results, cfg(min_hosts=2)) is True
    assert check(results, cfg(min_hosts=3)) is False


def test_min_hosts_with_host_whitelist_counts_only_matching_hosts():
    results = [res("a", "1fichier"), res("b", "rapidgator"), res("c", "turbobit")]
    wanted = cfg(min_hosts=2, hosts=["1FICHIER", "Turbobit"])
    assert check(results, wanted) is True
    assert check(results, cfg(min_hosts=3, hosts=["1fichier", "turbobit"])) is False


def test_results_without_hoster_do_not_count_as_hosts():
    assert check([res("a", ""), res("b", "")], cfg(min_hosts=1)) is False


# ---------- resolutions ----------
@pytest.mark.parametrize("quality,required,expected", [
    ("2160p HDR", ["2160p"], True),
    ("4K", ["2160p"], True),
    ("1080p", ["1080p"], True),
    ("1080p", ["720p"], False),
    ("720p", ["720p"], True),
    ("480p", ["480p"], True),
    ("720p", ["1080p", "2160p"], False),
    ("", ["1080p"], False),
])
def test_resolution_matching(quality, required, expected):
    assert check([res("a", quality=quality)], cfg(resolutions=required)) is expected


def test_resolution_requirement_is_case_insensitive():
    assert check([res("a", quality="1080p")], cfg(resolutions=["1080P"])) is True


# ---------- combinaison : toutes les conditions doivent etre reunies ----------
def test_all_conditions_must_hold_together():
    results = [res("a", "1fichier", "1080p"), res("b", "turbobit", "720p")]
    ok = cfg(min_streams=2, min_hosts=2, resolutions=["1080p"], sources=["Wawacity"])
    assert check(results, ok) is True
    assert check(results, {**ok, "min_streams": 3}) is False
    assert check(results, {**ok, "min_hosts": 3}) is False
    assert check(results, {**ok, "resolutions": ["2160p"]}) is False
