"""Tests pour les deux fonctions de deduplication du projet :
- deduplicate_and_sort_results (helpers.py) : par lien canonicalise + infohash
- BaseDebridService.deduplicate_by_exact_link (debrid/base.py) : par lien
  brut + infohash, SANS canonicalisation de domaine

Les deux existent separement et ne sont pas interchangeables : le second ne
fusionne pas les liens miroirs (ex: trbt.cc / turbobit.net) contrairement au
premier. Verifie explicitement ci-dessous pour eviter qu'un futur refactor
ne les fusionne par erreur en supposant un comportement identique.
"""
from typing import List, Dict, Optional

from wastream.utils.helpers import deduplicate_and_sort_results
from wastream.debrid.base import BaseDebridService


def _by_link(r: Dict):
    return r.get("link", "")


# ===========================
# deduplicate_and_sort_results (helpers.py)
# ===========================
def test_dedup_removes_exact_duplicate_links():
    results = [
        {"link": "https://example.com/a", "quality": "1080p"},
        {"link": "https://example.com/a", "quality": "1080p"},
    ]
    deduped = deduplicate_and_sort_results(results, _by_link)
    assert len(deduped) == 1


def test_dedup_merges_domain_alias_mirrors():
    # trbt.cc et turbobit.net sont le meme hebergeur (alias de domaine) :
    # deux liens identiques a part le domaine doivent fusionner.
    results = [
        {"link": "https://trbt.cc/file/xyz", "quality": "1080p"},
        {"link": "https://turbobit.net/file/xyz", "quality": "1080p"},
    ]
    deduped = deduplicate_and_sort_results(results, _by_link)
    assert len(deduped) == 1


def test_dedup_by_infohash_even_with_different_links():
    results = [
        {"link": "https://tracker1.example/torrent/a", "infohash": "ABC123", "quality": "1080p"},
        {"link": "https://tracker2.example/torrent/b", "infohash": "abc123", "quality": "1080p"},
    ]
    deduped = deduplicate_and_sort_results(results, _by_link)
    assert len(deduped) == 1


def test_dedup_drops_results_without_link():
    results = [{"link": "", "quality": "1080p"}]
    assert deduplicate_and_sort_results(results, _by_link) == []


def test_dedup_keeps_distinct_results():
    results = [
        {"link": "https://example.com/a", "quality": "1080p"},
        {"link": "https://example.com/b", "quality": "720p"},
    ]
    deduped = deduplicate_and_sort_results(results, _by_link)
    assert len(deduped) == 2


def test_dedup_applies_sort_key():
    results = [
        {"link": "https://example.com/a", "quality": "z"},
        {"link": "https://example.com/b", "quality": "a"},
    ]
    deduped = deduplicate_and_sort_results(results, lambda r: r["quality"])
    assert [r["link"] for r in deduped] == ["https://example.com/b", "https://example.com/a"]


# ===========================
# BaseDebridService.deduplicate_by_exact_link (debrid/base.py)
# ===========================
class _DummyDebridService(BaseDebridService):
    """Sous-classe minimale pour instancier la classe abstraite en test."""
    async def check_cache_and_enrich(self, results, api_key, config, timeout_remaining=0,
                                      user_season=None, user_episode=None, user_hosts=None):
        return results

    async def convert_link(self, link, api_key, season=None, episode=None, hoster=None):
        return link

    def get_service_name(self) -> str:
        return "dummy"


def test_debrid_dedup_removes_exact_duplicate_links():
    service = _DummyDebridService()
    results = [
        {"link": "https://example.com/a", "quality": "1080p"},
        {"link": "https://example.com/a", "quality": "1080p"},
    ]
    deduped = service.deduplicate_by_exact_link(results)
    assert len(deduped) == 1


def test_debrid_dedup_does_not_merge_domain_alias_mirrors():
    # Difference cle avec deduplicate_and_sort_results : pas de
    # canonicalisation ici, donc deux miroirs restent deux entrees distinctes.
    service = _DummyDebridService()
    results = [
        {"link": "https://trbt.cc/file/xyz", "quality": "1080p"},
        {"link": "https://turbobit.net/file/xyz", "quality": "1080p"},
    ]
    deduped = service.deduplicate_by_exact_link(results)
    assert len(deduped) == 2


def test_debrid_dedup_by_infohash_even_with_different_links():
    service = _DummyDebridService()
    results = [
        {"link": "https://tracker1.example/torrent/a", "infohash": "ABC123", "quality": "1080p"},
        {"link": "https://tracker2.example/torrent/b", "infohash": "abc123", "quality": "1080p"},
    ]
    deduped = service.deduplicate_by_exact_link(results)
    assert len(deduped) == 1
