"""Tests pour la logique synchrone de wastream/services/hoster_status.py
(mark_hoster_down, is_hoster_up, garde de schedule_recheck).

Hors perimetre volontairement : _check_torbox_hosters / _check_alldebrid_hosters
/ _recheck_alldebrid_hoster font de vrais appels HTTP -> necessiteraient un
mock httpx (respx, pas encore une dependance du projet). Cette suite couvre
la logique de cache/matching/TTL, qui est deja purement synchrone et la plus
riche en bugs potentiels (matching par sous-chaine, expiration, quota).
"""
import time

import pytest

from wastream.services import hoster_status as hs


@pytest.fixture(autouse=True)
def _reset_hoster_status_state():
    """Isole chaque test : l'etat est un dict/set au niveau module, partage
    entre tous les appels de get_hoster_status/is_hoster_up en production."""
    original_cache = {
        "alldebrid": {"hosts": {}, "proactive": {}, "proactive_last_check": None},
        "torbox": {"hosts": {}, "last_check": None},
    }
    hs._hoster_status_cache.clear()
    hs._hoster_status_cache.update(original_cache)
    hs._recheck_in_progress.clear()
    hs._recheck_success_count.clear()
    yield
    hs._hoster_status_cache.clear()
    hs._hoster_status_cache.update(original_cache)
    hs._recheck_in_progress.clear()
    hs._recheck_success_count.clear()


# ===========================
# mark_hoster_down
# ===========================
def test_mark_hoster_down_records_timestamp():
    hs.mark_hoster_down("alldebrid", "RapidGator")
    assert "rapidgator" in hs._hoster_status_cache["alldebrid"]["hosts"]


def test_mark_hoster_down_unknown_service_is_noop():
    hs.mark_hoster_down("not-a-service", "rapidgator")
    # Ne doit pas planter, et ne cree pas d'entree fantome.
    assert "not-a-service" not in hs._hoster_status_cache


def test_mark_hoster_down_clears_recheck_success_count():
    hs._recheck_success_count["rapidgator"] = 2
    hs.mark_hoster_down("alldebrid", "rapidgator")
    assert "rapidgator" not in hs._recheck_success_count


# ===========================
# is_hoster_up — service inconnu / cache vide
# ===========================
def test_is_hoster_up_unknown_service_defaults_true():
    assert hs.is_hoster_up("not-a-service", "rapidgator") is True


def test_is_hoster_up_no_entries_defaults_true():
    assert hs.is_hoster_up("alldebrid", "rapidgator.net") is True
    assert hs.is_hoster_up("torbox", "rapidgator.net") is True


# ===========================
# is_hoster_up — AllDebrid reactif (mark_hoster_down)
# ===========================
def test_is_hoster_up_alldebrid_down_within_ttl():
    hs.mark_hoster_down("alldebrid", "rapidgator")
    assert hs.is_hoster_up("alldebrid", "rapidgator.net") is False


def test_is_hoster_up_alldebrid_substring_match_both_directions():
    # cached_name ("rapidgator") est une sous-chaine de hoster_key
    # ("rapidgator.net") : doit matcher.
    hs.mark_hoster_down("alldebrid", "rapidgator")
    assert hs.is_hoster_up("alldebrid", "rapidgator.net") is False

    # Et l'inverse : hoster_key sous-chaine de cached_name.
    hs._hoster_status_cache["alldebrid"]["hosts"].clear()
    hs.mark_hoster_down("alldebrid", "rapidgator.net.mirror")
    assert hs.is_hoster_up("alldebrid", "rapidgator") is False


def test_is_hoster_up_alldebrid_expired_ttl_is_cleaned_up_and_up():
    expired_time = time.time() - hs.settings.HOSTER_STATUS_CACHE_TTL - 10
    hs._hoster_status_cache["alldebrid"]["hosts"]["rapidgator"] = expired_time

    assert hs.is_hoster_up("alldebrid", "rapidgator.net") is True
    # Effet de bord attendu : l'entree expiree est purgee du cache.
    assert "rapidgator" not in hs._hoster_status_cache["alldebrid"]["hosts"]


def test_is_hoster_up_alldebrid_no_match_is_up():
    hs.mark_hoster_down("alldebrid", "rapidgator")
    assert hs.is_hoster_up("alldebrid", "1fichier.com") is True


# ===========================
# is_hoster_up — AllDebrid proactif (statut + quota)
# ===========================
def test_is_hoster_up_alldebrid_proactive_down_status():
    hs._hoster_status_cache["alldebrid"]["proactive"]["rapidgator"] = {"up": False, "quota_pct": 50.0}
    assert hs.is_hoster_up("alldebrid", "rapidgator.net") is False


def test_is_hoster_up_alldebrid_proactive_critical_quota_is_down():
    hs._hoster_status_cache["alldebrid"]["proactive"]["rapidgator"] = {"up": True, "quota_pct": 2.0}
    assert hs.is_hoster_up("alldebrid", "rapidgator.net") is False


def test_is_hoster_up_alldebrid_proactive_healthy_quota_is_up():
    hs._hoster_status_cache["alldebrid"]["proactive"]["rapidgator"] = {"up": True, "quota_pct": 50.0}
    assert hs.is_hoster_up("alldebrid", "rapidgator.net") is True


def test_is_hoster_up_alldebrid_proactive_no_quota_info_is_up():
    hs._hoster_status_cache["alldebrid"]["proactive"]["rapidgator"] = {"up": True, "quota_pct": None}
    assert hs.is_hoster_up("alldebrid", "rapidgator.net") is True


# ===========================
# is_hoster_up — TorBox (proactif, hosts = {name: bool})
# ===========================
def test_is_hoster_up_torbox_down():
    hs._hoster_status_cache["torbox"]["hosts"]["rapidgator"] = False
    assert hs.is_hoster_up("torbox", "rapidgator.net") is False


def test_is_hoster_up_torbox_up():
    hs._hoster_status_cache["torbox"]["hosts"]["rapidgator"] = True
    assert hs.is_hoster_up("torbox", "rapidgator.net") is True


def test_is_hoster_up_torbox_no_match_defaults_true():
    hs._hoster_status_cache["torbox"]["hosts"]["rapidgator"] = False
    assert hs.is_hoster_up("torbox", "1fichier.com") is True


# ===========================
# schedule_recheck — garde anti-doublon
# ===========================
def test_schedule_recheck_empty_hoster_name_is_noop(monkeypatch):
    called = []
    monkeypatch.setattr(hs, "lancer_tache", lambda coro: called.append(coro))
    hs.schedule_recheck("http://example.com/link", "apikey", "")
    assert called == []


def test_schedule_recheck_marks_in_progress(monkeypatch):
    called = []
    monkeypatch.setattr(hs, "lancer_tache", lambda coro: called.append(coro))
    hs.schedule_recheck("http://example.com/link", "apikey", "RapidGator")
    assert "rapidgator" in hs._recheck_in_progress
    assert len(called) == 1
    called[0].close()  # evite un warning "coroutine was never awaited"


def test_schedule_recheck_skips_if_already_in_progress(monkeypatch):
    called = []
    monkeypatch.setattr(hs, "lancer_tache", lambda coro: called.append(coro))
    hs._recheck_in_progress.add("rapidgator")
    hs.schedule_recheck("http://example.com/link", "apikey", "rapidgator")
    assert called == []
