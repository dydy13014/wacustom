"""Tests directs pour normalize_text (helpers.py).

Point notable verifie ici : normalize_text remplace la ponctuation (dont
l'apostrophe) par un espace, il ne la supprime pas. C'est justement pour ca
que wastream/scrapers/torznab/base.py._relevance_tokens retire les
apostrophes A LA MAIN avant d'appeler normalize_text (cf. test_torznab_
relevance.py) : sans ce pre-traitement, "Charlie's" deviendrait "charlie s"
(avec un "s" parasite) plutot que "charlies".
"""
from wastream.utils.helpers import normalize_text


def test_lowercases():
    assert normalize_text("HELLO World") == "hello world"


def test_strips_accents():
    assert normalize_text("Amélie") == "amelie"
    assert normalize_text("À Nos Étoiles") == "a nos etoiles"


def test_apostrophe_becomes_space_not_removed():
    # Documente le comportement exact (voir docstring du module) : sans
    # pre-traitement specifique, une apostrophe devient un espace, pas rien.
    assert normalize_text("Charlie's Angels") == "charlie s angels"


def test_punctuation_becomes_space():
    assert normalize_text("Movie: The-Sequel!") == "movie the sequel"


def test_collapses_multiple_whitespace():
    assert normalize_text("Too    many   spaces") == "too many spaces"


def test_strips_leading_trailing_whitespace():
    assert normalize_text("  padded  ") == "padded"


def test_empty_string_returns_empty():
    assert normalize_text("") == ""


def test_none_like_falsy_returns_empty():
    assert normalize_text(None) == ""


def test_digits_are_preserved():
    assert normalize_text("Movie 2026 1080p") == "movie 2026 1080p"
