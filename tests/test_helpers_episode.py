"""Tests pour la detection d'episode/season-pack (episode_matches) et les
fonctions associees (select_episode_file, is_sample_file) dans helpers.py."""
from wastream.utils.helpers import episode_matches, select_episode_file, is_sample_file


# ===========================
# episode_matches
# ===========================
def test_episode_matches_no_info_returns_none():
    assert episode_matches("Show.Title.1080p.BluRay.mkv", 2, 3) is None


def test_episode_matches_missing_args_returns_none():
    assert episode_matches("Show.S02E03.mkv", None, 3) is None
    assert episode_matches("", 2, 3) is None


def test_episode_matches_exact_sxexx():
    assert episode_matches("Show.S02E03.1080p.mkv", 2, 3) is True


def test_episode_matches_wrong_episode_in_same_season():
    assert episode_matches("Show.S02E03.1080p.mkv", 2, 5) is False


def test_episode_matches_multi_episode_sxexx():
    # "S02E03E04" (deux episodes explicitement listes, pas une plage)
    assert episode_matches("Show.S02E03E04.1080p.mkv", 2, 4) is True
    assert episode_matches("Show.S02E03E04.1080p.mkv", 2, 5) is False


def test_episode_matches_range_inside():
    assert episode_matches("Show.S02E01-E04.1080p.mkv", 2, 3) is True


def test_episode_matches_range_outside():
    assert episode_matches("Show.S02E01-E04.1080p.mkv", 2, 6) is False


def test_episode_matches_alt_format_2x04():
    assert episode_matches("Show.2x04.1080p.mkv", 2, 4) is True
    assert episode_matches("Show.2x04.1080p.mkv", 2, 5) is False


def test_episode_matches_season_pack_french():
    assert episode_matches("Show.Saison.2.COMPLETE.FRENCH.1080p", 2, 7) is True
    assert episode_matches("Show.Saison.2.COMPLETE.FRENCH.1080p", 3, 1) is False


def test_episode_matches_season_pack_s02_alone():
    assert episode_matches("Show.S02.COMPLETE.1080p.mkv", 2, 1) is True
    assert episode_matches("Show.S02.COMPLETE.1080p.mkv", 1, 1) is False


def test_episode_matches_invalid_season_episode_types():
    assert episode_matches("Show.S02E03.mkv", "abc", 3) is None


# ===========================
# is_sample_file
# ===========================
def test_is_sample_file_detects_dotted_sample():
    assert is_sample_file("Movie.Title.2026.Sample.mkv") is True


def test_is_sample_file_ignores_word_containing_sample():
    # "resampled" ne doit pas etre pris pour un fichier sample.
    assert is_sample_file("Movie.Title.resampled.audio.mkv") is False


def test_is_sample_file_regular_file_is_not_sample():
    assert is_sample_file("Movie.Title.2026.1080p.mkv") is False


# ===========================
# select_episode_file
# ===========================
def test_select_episode_file_excludes_samples():
    files = [
        {"filename": "Show.S02E03.sample.mkv", "size": 50_000_000},
        {"filename": "Show.S02E03.mkv", "size": 1_500_000_000},
    ]
    selected = select_episode_file(files, 2, 3)
    assert selected["filename"] == "Show.S02E03.mkv"


def test_select_episode_file_picks_matching_episode_over_bigger_wrong_one():
    files = [
        {"filename": "Show.S02E04.mkv", "size": 2_000_000_000},
        {"filename": "Show.S02E03.mkv", "size": 1_500_000_000},
    ]
    selected = select_episode_file(files, 2, 3)
    assert selected["filename"] == "Show.S02E03.mkv"


def test_select_episode_file_falls_back_to_biggest_video_without_match():
    files = [
        {"filename": "Show.S02E04.mkv", "size": 2_000_000_000},
        {"filename": "Show.S02E05.mkv", "size": 1_500_000_000},
    ]
    # Aucun des deux n'est l'episode 3 demande -> repli sur le plus gros.
    selected = select_episode_file(files, 2, 3)
    assert selected["filename"] == "Show.S02E04.mkv"


def test_select_episode_file_empty_list_returns_none():
    assert select_episode_file([], 2, 3) is None
