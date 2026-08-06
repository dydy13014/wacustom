"""Tests pour wastream/utils/validators.py : validation de config utilisateur
(base64 + JSON + pydantic) et extraction d'infos depuis un content_id Stremio.
"""
import json
from base64 import b64encode

from wastream.utils.validators import validate_config, extract_media_info


def _encode(config: dict) -> str:
    return b64encode(json.dumps(config).encode("utf-8")).decode("utf-8")


def _valid_multi_service_config(**overrides):
    config = {
        "tmdb_api_token": "abc123",
        "debrid_services": [{"service": "alldebrid", "api_key": "key123"}],
    }
    config.update(overrides)
    return config


# ===========================
# validate_config — cas basiques
# ===========================
def test_validate_config_none_or_empty_returns_none():
    assert validate_config(None) is None
    assert validate_config("") is None


def test_validate_config_invalid_base64_returns_none():
    assert validate_config("not-valid-base64-!!!") is None


def test_validate_config_valid_json_not_a_dict_returns_none():
    assert validate_config(_encode(["not", "a", "dict"])) is None


def test_validate_config_missing_debrid_info_returns_none():
    assert validate_config(_encode({"tmdb_api_token": "abc123"})) is None


def test_validate_config_valid_multi_service_returns_dict():
    result = validate_config(_encode(_valid_multi_service_config()))
    assert result is not None
    assert result["tmdb_api_token"] == "abc123"
    assert result["debrid_services"][0]["service"] == "alldebrid"


# ===========================
# validate_config — format legacy (mono-service)
# ===========================
def test_validate_config_legacy_single_service_is_converted():
    legacy = {
        "tmdb_api_token": "abc123",
        "debrid_service": "alldebrid",
        "debrid_api_key": "key123",
    }
    result = validate_config(_encode(legacy))
    assert result is not None
    assert result["debrid_services"] == [{"service": "alldebrid", "api_key": "key123", "hosts": [], "sources": [], "enable_nzb": False, "enable_full_season": False}]


def test_validate_config_legacy_empty_service_returns_none():
    legacy = {"tmdb_api_token": "abc123", "debrid_service": "", "debrid_api_key": "key123"}
    assert validate_config(_encode(legacy)) is None


def test_validate_config_legacy_empty_api_key_returns_none():
    legacy = {"tmdb_api_token": "abc123", "debrid_service": "alldebrid", "debrid_api_key": ""}
    assert validate_config(_encode(legacy)) is None


# ===========================
# validate_config — validation pydantic
# ===========================
def test_validate_config_unknown_debrid_service_returns_none():
    config = _valid_multi_service_config(debrid_services=[{"service": "not-a-real-service", "api_key": "key123"}])
    assert validate_config(_encode(config)) is None


def test_validate_config_empty_api_key_in_service_returns_none():
    config = _valid_multi_service_config(debrid_services=[{"service": "alldebrid", "api_key": ""}])
    assert validate_config(_encode(config)) is None


def test_validate_config_empty_tmdb_token_returns_none():
    config = _valid_multi_service_config(tmdb_api_token="")
    assert validate_config(_encode(config)) is None


def test_validate_config_no_debrid_services_returns_none():
    config = _valid_multi_service_config(debrid_services=[])
    assert validate_config(_encode(config)) is None


def test_validate_config_negative_max_results_returns_none():
    config = _valid_multi_service_config(max_results_per_resolution=-1)
    assert validate_config(_encode(config)) is None


def test_validate_config_negative_max_size_returns_none():
    config = _valid_multi_service_config(max_size_gb=-5.0)
    assert validate_config(_encode(config)) is None


def test_validate_config_preserves_extra_fields():
    config = _valid_multi_service_config(sort_order="quality", early_stop=True)
    result = validate_config(_encode(config))
    assert result["sort_order"] == "quality"
    assert result["early_stop"] is True


def test_validate_config_defaults_applied():
    result = validate_config(_encode(_valid_multi_service_config()))
    assert result["max_results_per_resolution"] == 0
    assert result["max_size_gb"] == 0.0
    assert result["excluded_keywords"] == []


# ===========================
# extract_media_info
# ===========================
def test_extract_media_info_kitsu_with_episode():
    info = extract_media_info("kitsu:12345:7", "series")
    assert info == {"kitsu_id": "12345", "episode": "7", "season": "1", "imdb_id": None}


def test_extract_media_info_kitsu_without_episode():
    info = extract_media_info("kitsu:12345", "movie")
    assert info == {"kitsu_id": "12345", "episode": None, "season": "1", "imdb_id": None}


def test_extract_media_info_series_with_season_episode():
    info = extract_media_info("tt1234567:2:5", "series")
    assert info == {"imdb_id": "tt1234567", "season": "2", "episode": "5", "kitsu_id": None}


def test_extract_media_info_series_missing_episode_defaults_to_1():
    # "tt1234567:2" (saison seule, pas d'episode) -> parts = ["tt1234567", "2"],
    # len(parts) == 2 donc l'episode (parts[2], absent) retombe sur "1".
    info = extract_media_info("tt1234567:2", "series")
    assert info["season"] == "2"
    assert info["episode"] == "1"


def test_extract_media_info_movie_plain_imdb_id():
    info = extract_media_info("tt1234567", "movie")
    assert info == {"imdb_id": "tt1234567", "season": None, "episode": None, "kitsu_id": None}


def test_extract_media_info_strips_json_suffix():
    info = extract_media_info("tt1234567.json", "movie")
    assert info["imdb_id"] == "tt1234567"


def test_extract_media_info_series_without_colon_treated_as_movie_like():
    # Pas de ":" dans le content_id pour une serie -> branche finale (pas de
    # saison/episode extraits), comportement identique a un film.
    info = extract_media_info("tt1234567", "series")
    assert info == {"imdb_id": "tt1234567", "season": None, "episode": None, "kitsu_id": None}
