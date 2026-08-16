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
# episode_matches - format animé "numéro nu" (Nyaa)
# ===========================
def test_episode_matches_anime_bare_dash():
    assert episode_matches("[Group] Show - 05 [1080p].mkv", 1, 5) is True
    assert episode_matches("[Group] Show - 05 [1080p].mkv", 1, 6) is False


def test_episode_matches_anime_bare_ep_prefix():
    assert episode_matches("[Group] Show EP05 [1080p].mkv", 1, 5) is True
    assert episode_matches("[Group] Show E05 [1080p].mkv", 1, 5) is True


def test_episode_matches_anime_bare_hash_prefix():
    assert episode_matches("[Group] Show #12 [1080p].mkv", 1, 12) is True


def test_episode_matches_anime_bare_avoids_title_numbers():
    # Faux positifs classiques : des chiffres qui font partie du TITRE, pas
    # d'un numero d'episode (pas de separateur fort avant, ou pas d'assertion
    # de suite technique apres).
    assert episode_matches("86 EIGHTY-SIX - 05 [1080p].mkv", 1, 86) is False
    assert episode_matches("Mob Psycho 100 - 05 [1080p].mkv", 1, 100) is False
    assert episode_matches("Gundam 00 - 05 [1080p].mkv", 1, 0) is False


def test_episode_matches_anime_bare_no_match_returns_false_not_none():
    # Un numero EST present mais ce n'est pas le bon -> False (pas None),
    # meme sans strict=True (point 5 de la fonction, distinct du point 6).
    assert episode_matches("[Group] Show - 05 [1080p].mkv", 1, 6) is False


# ===========================
# episode_matches - strict=True (aucune info detectee)
# ===========================
def test_episode_matches_strict_false_when_nothing_detected():
    assert episode_matches("Show.Title.1080p.BluRay.mkv", 1, 5, strict=True) is False


def test_episode_matches_non_strict_none_when_nothing_detected():
    # Comportement par defaut inchange (non strict) : None, pas False.
    assert episode_matches("Show.Title.1080p.BluRay.mkv", 1, 5, strict=False) is None
    assert episode_matches("Show.Title.1080p.BluRay.mkv", 1, 5) is None


# ===========================
# episode_matches - absolute_episode (numerotation continue animé)
# ===========================
def test_episode_matches_absolute_episode_without_season_marker():
    # "Titre - 13" demande en S02E01, avec absolute_episode=13 (numerotation
    # continue) -> accepte car pas de marqueur de saison explicite dans le nom.
    assert episode_matches("[Group] Show - 13 [1080p].mkv", 2, 1, absolute_episode=13) is True


def test_episode_matches_absolute_episode_ignored_with_explicit_season():
    # "S02 - 13" : le "13" est relatif a la saison 2 explicitement marquee ->
    # ne doit PAS matcher un absolute_episode=13 pour une demande S01E13.
    assert episode_matches("[Group] Show S02 - 13 [1080p].mkv", 1, 13, absolute_episode=13) is False


def test_episode_matches_absolute_episode_hybrid_season_and_bare_number():
    # Cas hybride : saison explicite ET numero nu (« Show S02 - 13 »). Le
    # numero est alors relatif a la saison marquee, jamais absolu.
    assert episode_matches("[Group] Show S02 - 13 [1080p].mkv", 2, 13, absolute_episode=25) is True
    # Meme nom, mais on demande l'episode absolu 13 (= S02E01) : refuse, car
    # « 13 » designe ici le 13e episode DE la saison 2.
    assert episode_matches("[Group] Show S02 - 13 [1080p].mkv", 2, 1, absolute_episode=13) is False


def test_episode_matches_season_marker_with_bare_number_is_not_a_pack():
    # Format Nyaa dominant (« S3 - 07 », « Season 3 - 7 ») : la saison est
    # marquee mais le nom designe UN episode, pas la saison entiere.
    for name in ("[ASW] Mushoku Tensei S3 - 07 [1080p HEVC x265 10Bit][AAC]",
                 "[Doomdos] - Mushoku Tensei Season 3 - 7 [2160p IQ WEB-DL]"):
        assert episode_matches(name, 3, 7) is True
        assert episode_matches(name, 3, 1) is False
        assert episode_matches(name, 2, 7) is False


def test_episode_matches_batch_marker_stays_a_season_pack():
    # Un marqueur de batch explicite : le numero present dans le nom ne doit
    # PAS restreindre la release a ce seul episode.
    name = ("[Anime Chap] Kaguya-sama Season 3 [WEB 1080p] Improved Subs v2 "
            "- Episode 1 - 13 (Love is War) {Batch}")
    assert episode_matches(name, 3, 5) is True
    assert episode_matches(name, 3, 13) is True
    assert episode_matches(name, 2, 5) is False


def test_episode_matches_bare_range_batch():
    # Batch sans marqueur de saison : « 1017-1024 » couvre toute la plage,
    # alors que seul le dernier numero etait reconnu auparavant.
    name = "[HatSubs] One Piece 1017-1024 (BD 1080p 10-bit) (v0)"
    assert episode_matches(name, 1, 1020, absolute_episode=1020) is True
    assert episode_matches(name, 1, 1017, absolute_episode=1017) is True
    assert episode_matches(name, 1, 1030, absolute_episode=1030) is not True


def test_episode_matches_ignores_codec_and_year_false_numbers():
    # « HEVC-265 » et « -2022 » ne sont pas des numeros d'episode : le tiret
    # doit etre precede d'un espace pour compter.
    pack = "[DKB] Jujutsu Kaisen (Season 1) [1080p][HEVC-265 10bit][Multi-Subs][batch]"
    assert episode_matches(pack, 1, 5) is True
    film = ("Kaguya-sama: Love Is War -The First Kiss That Never Ends- 2022 "
            "1080p BluRay REMUX AVC Dual-Audio DTS-HD MA 5.1-NAN0")
    assert episode_matches(film, 1, 2022, strict=True) is False


def test_episode_matches_absolute_episode_none_has_no_effect():
    assert episode_matches("[Group] Show - 05 [1080p].mkv", 1, 5, absolute_episode=None) is True


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
