"""Tests de normalisation des URL de trackers (normalize_tracker_url).

Chaque tracker attend son endpoint Torznab a un chemin different. La fonction
laisse l'utilisateur saisir l'URL "naturelle" du service et complete le chemin
manquant — une URL mal completee ne provoque pas d'erreur visible, juste une
source qui ne remonte jamais rien.
"""
from wastream.utils.helpers import normalize_tracker_url


# ===========================
# Suffixe /api simple
# ===========================
def test_yggreborn_appends_api():
    assert normalize_tracker_url("YggReborn", "https://www.yggreborn.org") == "https://www.yggreborn.org/api"


def test_tr4ker_appends_api():
    assert normalize_tracker_url("Tr4ker", "https://tr4ker.net") == "https://tr4ker.net/api"


def test_already_suffixed_is_left_alone():
    assert normalize_tracker_url("Tr4ker", "https://tr4ker.net/api") == "https://tr4ker.net/api"


def test_trailing_slash_is_stripped():
    assert normalize_tracker_url("Tr4ker", "https://tr4ker.net/") == "https://tr4ker.net/api"


# ===========================
# Chemins specifiques
# ===========================
def test_torr9_full_torznab_path():
    assert normalize_tracker_url("Torr9", "https://api.torr9.net") == "https://api.torr9.net/api/v1/torznab"


def test_c411_torznab_path():
    assert normalize_tracker_url("C411", "https://c411.org") == "https://c411.org/api/torznab"


# ===========================
# V3X : l'API vit sur un sous-domaine dedie
# ===========================
def test_v3x_from_site_url():
    # Cas courant : l'utilisateur saisit l'adresse du site, pas celle de l'API.
    assert normalize_tracker_url("V3X", "https://v3x.club") == "https://api.v3x.club/torznab/api"


def test_v3x_from_api_url():
    assert normalize_tracker_url("V3X", "https://api.v3x.club/torznab") == "https://api.v3x.club/torznab/api"


def test_v3x_already_complete_is_idempotent():
    url = "https://api.v3x.club/torznab/api"
    assert normalize_tracker_url("V3X", url) == url


# ===========================
# Cas limites
# ===========================
def test_empty_url_returns_as_is():
    assert normalize_tracker_url("Tr4ker", "") == ""


def test_unknown_tracker_is_untouched():
    assert normalize_tracker_url("Inconnu", "https://exemple.test/api") == "https://exemple.test/api"
