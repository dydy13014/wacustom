"""Regression tests pour la collision de codes langue ISO courts (helpers.py).

Bug reel du 2026-08-01 : un titre romanise japonais comme "Kimi No Na Wa"
matchait "no" (Norwegian) puis "wa" (Walloon) une fois "no" ecarte, alors
qu'aucune des deux langues n'a de rapport avec le titre. Voir le commentaire
au-dessus de _is_plausible_language_token dans wastream/utils/helpers.py.
"""
from wastream.utils.helpers import _is_plausible_language_token, extract_language_from_tokens


def test_short_syllables_from_romanized_japanese_titles_are_rejected():
    # Tokens issus de "Kimi No Na Wa" une fois tokenizes.
    for token in ("no", "na", "wa", "de", "la", "en", "it", "ka", "ta"):
        assert _is_plausible_language_token(token) is False, (
            f"'{token}' ne devrait pas etre traite comme un code langue plausible"
        )


def test_vf_vo_remain_accepted_as_short_codes():
    assert _is_plausible_language_token("vf") is True
    assert _is_plausible_language_token("vo") is True


def test_ambiguous_3letter_codes_are_rejected():
    for token in ("cat", "fin", "ben"):
        assert _is_plausible_language_token(token) is False


def test_other_3letter_codes_remain_accepted():
    # Un code 3 lettres non ambigu doit rester utilisable normalement.
    assert _is_plausible_language_token("fre") is True


def test_extract_language_ignores_romanized_japanese_syllables():
    tokens = ["kimi", "no", "na", "wa", "1080p", "bluray"]
    assert extract_language_from_tokens(tokens) == "Unknown"


def test_extract_language_still_detects_real_language_tokens():
    # Un token non-ambigu de 3 lettres+ doit toujours matcher une vraie langue.
    tokens = ["movie", "title", "french", "1080p"]
    assert extract_language_from_tokens(tokens) == "French"
