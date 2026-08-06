"""Tests pour build_display_name / _clean_display_name (helpers.py)."""
from wastream.utils.helpers import build_display_name, _clean_display_name


# ===========================
# _clean_display_name
# ===========================
def test_clean_display_name_replaces_punctuation_with_dots():
    assert _clean_display_name("Movie: The Sequel!") == "Movie.The.Sequel"


def test_clean_display_name_collapses_multiple_dots():
    assert _clean_display_name("Movie   Title") == "Movie.Title"


def test_clean_display_name_replaces_underscores():
    assert _clean_display_name("Movie_Title") == "Movie.Title"


def test_clean_display_name_strips_leading_trailing_dots():
    assert _clean_display_name("!Movie Title!") == "Movie.Title"


# ===========================
# build_display_name
# ===========================
def test_build_display_name_title_only():
    assert build_display_name("Movie Title") == "Movie.Title"


def test_build_display_name_adds_year_if_absent():
    assert build_display_name("Movie Title", year="2026") == "Movie.Title.2026"


def test_build_display_name_does_not_duplicate_year_already_in_title():
    assert build_display_name("Movie Title 2026", year="2026") == "Movie.Title.2026"


def test_build_display_name_season_only():
    assert build_display_name("Show Title", season="2") == "Show.Title.S02"


def test_build_display_name_season_and_episode_zero_padded():
    assert build_display_name("Show Title", season="2", episode="5") == "Show.Title.S02E05"


def test_build_display_name_prefers_raw_language_over_normalized():
    result = build_display_name("Movie Title", language="French", raw_language="VOSTFR")
    assert result == "Movie.Title.VOSTFR"


def test_build_display_name_falls_back_to_language_if_no_raw_language():
    result = build_display_name("Movie Title", language="French")
    assert result == "Movie.Title.French"


def test_build_display_name_formats_multi_language():
    result = build_display_name("Movie Title", raw_language="Multi (French, English)")
    assert result == "Movie.Title.MULTi.French.English"


def test_build_display_name_appends_quality():
    result = build_display_name("Movie Title", quality="1080p BluRay")
    assert result == "Movie.Title.1080p.BluRay"


def test_build_display_name_unknown_language_and_quality_are_omitted():
    result = build_display_name("Movie Title", language="Unknown", quality="Unknown")
    assert result == "Movie.Title"


def test_build_display_name_full_combination():
    result = build_display_name(
        "Show Title", year="2026", raw_language="VOSTFR", quality="1080p",
        season="1", episode="3"
    )
    assert result == "Show.Title.2026.S01E03.VOSTFR.1080p"
