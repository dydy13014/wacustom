"""Tests pour wastream/utils/filters.py.

Couvre en priorite le filtre anti-poison (filter_implausible_size), avec les
cas reels documentes dans le code (commits du 2026-07-19/20/21) : torrents
"1080p BluRay" a 698 Mo / 2.19 Go (trop petit), "1080p" a 231 Go (trop gros),
et le fait que ce filtre doit rester desactive hors films (episodes/packs).
"""
from wastream.utils.filters import (
    filter_by_languages,
    filter_by_resolutions,
    limit_results_per_resolution,
    filter_by_max_size,
    filter_implausible_size,
    filter_archive_files,
    filter_excluded_keywords,
    apply_all_filters,
)


def _result(quality="1080p", language="French", size="4 GB"):
    return {"quality": quality, "language": language, "size": size}


# ===========================
# filter_implausible_size
# ===========================
def test_implausible_size_rejects_tiny_bluray_698mb():
    results = [_result(quality="1080p BluRay", size="698 MB")]
    assert filter_implausible_size(results, "movie") == []


def test_implausible_size_rejects_tiny_bluray_2_19gb():
    results = [_result(quality="1080p BluRay", size="2.19 GB")]
    assert filter_implausible_size(results, "movie") == []


def test_implausible_size_rejects_bloated_231gb():
    results = [_result(quality="1080p", size="231.77 GB")]
    assert filter_implausible_size(results, "movie") == []


def test_implausible_size_accepts_plausible_bluray():
    results = [_result(quality="1080p BluRay", size="8 GB")]
    assert filter_implausible_size(results, "movie") == results


def test_implausible_size_accepts_small_legit_webrip():
    # Seuil plus bas pour WEBRip/WEB-DL (compression plus variable) : un
    # 1080p WEBRip a moins de 1 Go doit rester accepte, contrairement au
    # meme poids en BluRay.
    results = [_result(quality="1080p WEBRip", size="0.8 GB")]
    assert filter_implausible_size(results, "movie") == results


def test_implausible_size_rejects_same_small_size_as_bluray():
    results = [_result(quality="1080p BluRay", size="0.8 GB")]
    assert filter_implausible_size(results, "movie") == []


def test_implausible_size_disabled_for_non_movie_content():
    # Cas reel "Comme les grands" : un episode de serie peut peser 20x moins
    # qu'un film, ce filtre ne doit pas s'appliquer du tout hors "movie".
    results = [_result(quality="1080p BluRay", size="698 MB")]
    assert filter_implausible_size(results, "series") == results


def test_implausible_size_ignores_unknown_resolution():
    results = [_result(quality="Unknown", size="10 MB")]
    assert filter_implausible_size(results, "movie") == results


# ===========================
# filter_by_languages
# ===========================
def test_filter_by_languages_empty_means_no_filter():
    results = [_result(language="French"), _result(language="English")]
    assert filter_by_languages(results, []) == results


def test_filter_by_languages_matches_simple_language():
    results = [_result(language="French"), _result(language="English")]
    filtered = filter_by_languages(results, ["French"])
    assert filtered == [results[0]]


def test_filter_by_languages_matches_inside_multi():
    results = [_result(language="Multi (French, English)")]
    assert filter_by_languages(results, ["English"]) == results


def test_filter_by_languages_rejects_multi_without_match():
    results = [_result(language="Multi (German, Italian)")]
    assert filter_by_languages(results, ["French"]) == []


def test_filter_by_languages_keeps_bare_multi():
    # « Multi » BRUT (sans detail) = joker : la source ne liste pas les langues
    # (cas Lumio). Upstream le rejetait faute de correspondance exacte, ce qui
    # faisait perdre 36 resultats deja en cache debrid sur Solo Leveling S02E09.
    for tag in ("Multi", "MULTI", "multi", "Multi-Audio", "MultiLang"):
        results = [_result(language=tag)]
        assert filter_by_languages(results, ["French"]) == results, tag


def test_filter_by_languages_bare_multi_is_not_a_global_bypass():
    # Le joker ne doit concerner QUE « Multi » : une autre langue non demandee
    # reste ecartee, sinon le filtre ne servirait plus a rien.
    results = [_result(language="German")]
    assert filter_by_languages(results, ["French"]) == []
    # ...et « Multi (…) » detaille garde son comportement d'origine.
    detailed = [_result(language="Multi (German, Italian)")]
    assert filter_by_languages(detailed, ["French"]) == []


# ===========================
# filter_by_resolutions
# ===========================
def test_filter_by_resolutions_basic_match():
    results = [_result(quality="1080p"), _result(quality="720p")]
    assert filter_by_resolutions(results, ["1080p"]) == [results[0]]


# ===========================
# limit_results_per_resolution
# ===========================
def test_limit_results_per_resolution_zero_means_no_limit():
    results = [_result(quality="1080p") for _ in range(5)]
    assert limit_results_per_resolution(results, 0) == results


def test_limit_results_per_resolution_caps_each_group():
    results = [_result(quality="1080p") for _ in range(3)] + [_result(quality="720p") for _ in range(3)]
    limited = limit_results_per_resolution(results, 2)
    assert len(limited) == 4


# ===========================
# filter_by_max_size
# ===========================
def test_filter_by_max_size_zero_means_no_filter():
    results = [_result(size="100 GB")]
    assert filter_by_max_size(results, 0.0) == results


def test_filter_by_max_size_rejects_oversized():
    results = [_result(size="10 GB"), _result(size="2 GB")]
    assert filter_by_max_size(results, 5.0) == [results[1]]


# ===========================
# filter_archive_files
# ===========================
def test_filter_archive_files_rejects_rar():
    streams = [{"description": "📁 movie.name.2026.1080p.rar"}]
    assert filter_archive_files(streams) == []


def test_filter_archive_files_accepts_mkv():
    streams = [{"description": "📁 movie.name.2026.1080p.mkv"}]
    assert filter_archive_files(streams) == streams


# ===========================
# filter_excluded_keywords
# ===========================
def test_filter_excluded_keywords_case_insensitive():
    streams = [{"name": "Movie CAM Rip", "description": ""}]
    assert filter_excluded_keywords(streams, ["cam"]) == []


def test_filter_excluded_keywords_empty_list_means_no_filter():
    streams = [{"name": "Movie CAM Rip", "description": ""}]
    assert filter_excluded_keywords(streams, []) == streams


# ===========================
# apply_all_filters — regression du 2026-07-19
# ===========================
def test_apply_all_filters_empty_resolutions_does_not_wipe_everything():
    # Bug reel : resolutions=[] (utilisateur decochant toutes les cases)
    # supprimait TOUS les flux au lieu de ne pas filtrer du tout.
    results = [_result(quality="1080p"), _result(quality="720p")]
    config = {"languages": [], "resolutions": [], "max_results_per_resolution": 0, "max_size_gb": 0.0}
    filtered = apply_all_filters(results, config, content_type="movie")
    assert len(filtered) == 2
