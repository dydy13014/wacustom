"""Tests pour wastream/utils/quality.py : extraction de resolution,
normalisation et cle de tri par qualite.

Couvre en particulier la regression documentee dans le code : une release
"WEB-RIP" (avec tiret) tombait par erreur dans le repli generique "WEB" et
etait classee WEB-DL (type 2, premium) au lieu de WEBRip (type 4) -> un rip
remontait en tete de liste devant de vraies sources WEB-DL.
"""
from wastream.utils.quality import extract_resolution, normalize_quality, quality_sort_key


# ===========================
# extract_resolution
# ===========================
def test_extract_resolution_2160p_variants():
    for value in ("2160p", "4K", "UHD", "ULTRA HD"):
        assert extract_resolution(value) == "2160p"


def test_extract_resolution_1080p():
    assert extract_resolution("1080p BluRay") == "1080p"


def test_extract_resolution_hd_alone_means_1080p():
    assert extract_resolution("HD") == "1080p"


def test_extract_resolution_720p():
    assert extract_resolution("720p WEBRip") == "720p"


def test_extract_resolution_480p():
    assert extract_resolution("480p") == "480p"


def test_extract_resolution_unknown_for_empty_or_unrecognized():
    assert extract_resolution("") == "Unknown"
    assert extract_resolution("Unknown") == "Unknown"
    assert extract_resolution("CAM") == "Unknown"


# ===========================
# normalize_quality
# ===========================
def test_normalize_quality_passthrough():
    assert normalize_quality("1080p BluRay") == "1080p BluRay"


def test_normalize_quality_blank_values_become_unknown():
    for value in ("", "N/A", "null", "unknown", "inconnu"):
        assert normalize_quality(value) == "Unknown"


def test_normalize_quality_none_becomes_unknown():
    assert normalize_quality(None) == "Unknown"


def test_normalize_quality_ultra_hdlight_combo():
    assert normalize_quality("ULTRA HDLIGHT") == "HDLight 2160p"


# ===========================
# quality_sort_key — resolution ordering (plus petit = meilleur)
# ===========================
def test_quality_sort_key_2160p_beats_1080p():
    key_2160 = quality_sort_key({"quality": "2160p BluRay"})
    key_1080 = quality_sort_key({"quality": "1080p BluRay"})
    assert key_2160 < key_1080


def test_quality_sort_key_unknown_sorts_last():
    key_unknown = quality_sort_key({"quality": "Unknown"})
    key_720 = quality_sort_key({"quality": "720p"})
    assert key_unknown > key_720


def test_quality_sort_key_missing_field_treated_as_unknown():
    assert quality_sort_key({}) == quality_sort_key({"quality": "Unknown"})


# ===========================
# quality_sort_key — release type ordering (plus petit = meilleur)
# ===========================
def test_quality_sort_key_remux_beats_bluray():
    key_remux = quality_sort_key({"quality": "1080p REMUX"})
    key_bluray = quality_sort_key({"quality": "1080p BluRay"})
    assert key_remux < key_bluray


def test_quality_sort_key_bluray_beats_webdl():
    key_bluray = quality_sort_key({"quality": "1080p BluRay"})
    key_webdl = quality_sort_key({"quality": "1080p WEB-DL"})
    assert key_bluray < key_webdl


def test_quality_sort_key_webrip_with_dash_is_not_classified_as_webdl():
    # Regression : "WEB-RIP" doit tomber dans la branche WEBRip (type 4),
    # pas dans le repli generique "WEB" (type 2, WEB-DL).
    key_webrip_dash = quality_sort_key({"quality": "1080p WEB-RIP"})
    key_webrip_nodash = quality_sort_key({"quality": "1080p WEBRIP"})
    key_webdl = quality_sort_key({"quality": "1080p WEB-DL"})
    assert key_webrip_dash == key_webrip_nodash
    assert key_webrip_dash > key_webdl  # WEBRip moins bien classe que WEB-DL


def test_quality_sort_key_webdl_beats_webrip():
    key_webdl = quality_sort_key({"quality": "1080p WEB-DL"})
    key_webrip = quality_sort_key({"quality": "1080p WEBRip"})
    assert key_webdl < key_webrip


def test_quality_sort_key_full_ordering_within_same_resolution():
    order = [
        "1080p REMUX",
        "1080p BluRay",
        "1080p WEB-DL",
        "1080p HDLight",
        "1080p WEBRip",
        "1080p HDRip",
        "1080p HDTV",
        "1080p DVDRip",
        "1080p TVRip",
    ]
    keys = [quality_sort_key({"quality": q}) for q in order]
    assert keys == sorted(keys)
