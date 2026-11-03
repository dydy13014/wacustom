"""Tests pour le multi-utilisateurs (wastream/utils/profiles.py) et le jeton
d'URL lie a un utilisateur (crypto._profile_aad).

Le point critique : un utilisateur du compte ne doit jamais pouvoir obtenir
les cles debrid d'un autre, ni en changeant le segment utilisateur de son URL,
ni via la config effective qui circule dans le pipeline de streams.
"""
from wastream.utils.crypto import encrypt_password_for_url, decrypt_password_from_url
from wastream.utils.profiles import resolve_profile_config, validate_profiles, build_addon_urls


def _config(**overrides):
    config = {
        "profile_id": "papa",
        "profile_name": "Papa",
        "default_services": ["tmdb"],
        "debrid_services": [{"service": "alldebrid", "api_key": "cle-papa"}],
        "tmdb_api_token": "tmdb-papa",
        "languages": ["French"],
        "profiles": [
            {"id": "lea", "name": "Léa",
             "debrid_services": [{"service": "torbox", "api_key": "cle-lea"}],
             "tmdb_api_token": "tmdb-lea"},
        ],
    }
    config.update(overrides)
    return config


# ===========================
# Jeton lie a l'utilisateur
# ===========================
def test_profile_token_round_trip():
    token = encrypt_password_for_url("secret", "lea")
    assert decrypt_password_from_url(token, "lea") == "secret"


def test_profile_token_rejected_for_other_profile():
    token = encrypt_password_for_url("secret", "lea")
    assert decrypt_password_from_url(token, "papa") is None
    assert decrypt_password_from_url(token) is None


def test_legacy_token_rejected_on_profile_url():
    token = encrypt_password_for_url("secret")
    assert decrypt_password_from_url(token) == "secret"
    assert decrypt_password_from_url(token, "lea") is None


# ===========================
# Config effective
# ===========================
def test_main_profile_keeps_own_services_and_drops_others():
    effective = resolve_profile_config(_config(), "papa")
    assert effective["debrid_services"][0]["api_key"] == "cle-papa"
    assert "profiles" not in effective
    assert effective["_profile_name"] == "Papa"


def test_legacy_url_resolves_to_main_without_profiles():
    effective = resolve_profile_config(_config(), None)
    assert effective["debrid_services"][0]["api_key"] == "cle-papa"
    assert "profiles" not in effective


def test_secondary_profile_uses_own_debrid_and_shared_tmdb():
    effective = resolve_profile_config(_config(), "lea")
    assert effective["debrid_services"] == [{"service": "torbox", "api_key": "cle-lea"}]
    assert effective["tmdb_api_token"] == "tmdb-papa"
    assert effective["languages"] == ["French"]
    assert "profiles" not in effective


def test_secondary_profile_uses_own_tmdb_when_not_shared():
    effective = resolve_profile_config(_config(default_services=[]), "lea")
    assert effective["tmdb_api_token"] == "tmdb-lea"


def test_unknown_profile_returns_none():
    assert resolve_profile_config(_config(), "inconnu") is None


# ===========================
# Validation
# ===========================
def test_validate_accepts_valid_config():
    assert validate_profiles(_config()) is None


def test_validate_accepts_legacy_config():
    assert validate_profiles({"debrid_services": [{"service": "alldebrid", "api_key": "k"}]}) is None


def test_validate_rejects_duplicate_ids():
    config = _config()
    config["profiles"][0]["id"] = "papa"
    assert validate_profiles(config) is not None


def test_validate_rejects_invalid_id():
    config = _config()
    config["profiles"][0]["id"] = "Léa/../x"
    assert validate_profiles(config) is not None


def test_validate_rejects_sharing_debrid():
    assert validate_profiles(_config(default_services=["alldebrid"])) is not None


def test_validate_requires_debrid_per_profile():
    config = _config()
    config["profiles"][0]["debrid_services"] = []
    assert validate_profiles(config) is not None


def test_validate_requires_tmdb_when_not_shared():
    config = _config(default_services=[])
    config["profiles"][0]["tmdb_api_token"] = ""
    assert validate_profiles(config) is not None


# ===========================
# URLs de manifest
# ===========================
def test_build_addon_urls_one_bound_token_per_profile():
    urls = build_addon_urls("https://x", "uuid", "secret", _config())
    assert [u["id"] for u in urls] == ["papa", "lea"]
    for entry in urls:
        _, pid, token, _ = entry["url"].removeprefix("https://x/").split("/")
        assert pid == entry["id"]
        assert decrypt_password_from_url(token, pid) == "secret"


def test_build_addon_urls_legacy_format_without_profiles():
    urls = build_addon_urls("https://x", "uuid", "secret", {"debrid_services": []})
    assert len(urls) == 1
    assert urls[0]["url"].count("/") == 5  # https://x/uuid/token/manifest.json


# ===========================
# Compte debrid partage (case « Default for all profiles of this account »)
# ===========================
def test_shared_debrid_gives_main_account_to_every_profile():
    config = _config(default_services=["tmdb", "debrid"])
    config["profiles"][0]["debrid_services"] = []
    effective = resolve_profile_config(config, "lea")
    assert effective["debrid_services"] == [{"service": "alldebrid", "api_key": "cle-papa"}]


def test_shared_debrid_overrides_profile_own_account():
    effective = resolve_profile_config(_config(default_services=["tmdb", "debrid"]), "lea")
    assert effective["debrid_services"][0]["api_key"] == "cle-papa"
    assert "cle-lea" not in str(effective)


def test_without_sharing_profile_keeps_its_own_account():
    effective = resolve_profile_config(_config(), "lea")
    assert effective["debrid_services"] == [{"service": "torbox", "api_key": "cle-lea"}]


def test_validate_accepts_profile_without_debrid_when_shared():
    config = _config(default_services=["tmdb", "debrid"])
    config["profiles"][0]["debrid_services"] = []
    assert validate_profiles(config) is None


def test_validate_still_requires_debrid_when_not_shared():
    config = _config()
    config["profiles"][0]["debrid_services"] = []
    assert validate_profiles(config) is not None
