"""Regression tests pour le filtre de pertinence Torznab (accents/apostrophes).

Bug reel du 2026-07-20 : certains trackers Torznab (C411) renvoient des
resultats hors-sujet quand la recherche ne matche rien en interne. Le filtre
_is_relevant doit rejeter ces faux positifs, mais sans rejeter a tort de
vrais resultats a cause d'un titre contenant une apostrophe ou un accent
(ex: "Charlie's Angels", "Amelie"). Voir les docstrings dans
wastream/scrapers/torznab/base.py.
"""
from wastream.scrapers.torznab.base import _is_relevant


def test_apostrophe_title_matches_release_with_merged_apostrophe():
    # Convention scene : "Charlie's Angels" -> "Charlies.Angels", pas separe.
    assert _is_relevant("Charlie's Angels", "Charlies.Angels.2000.1080p.BluRay") is True


def test_apostrophe_title_matches_release_with_curly_apostrophe():
    assert _is_relevant("Ocean's Eleven", "Oceans.Eleven.2001.FRENCH.1080p") is True


def test_accented_title_matches_unaccented_release():
    assert _is_relevant("Amélie", "Amelie.2001.FRENCH.1080p.BluRay") is True


def test_unrelated_release_is_rejected():
    # Cas reel : "comme les grands S01E01" retombait sur des animes isekai
    # totalement etrangers quand le tracker ne matchait rien en interne.
    assert _is_relevant(
        "Comme les grands S01E01",
        "Isekai.Another.World.Adventure.S03E12.VOSTFR.1080p"
    ) is False


def test_empty_title_is_always_relevant():
    assert _is_relevant("", "Anything.At.All.1080p") is True
