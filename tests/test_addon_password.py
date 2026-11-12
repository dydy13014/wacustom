"""Mot de passe d'addon (ADDON_PASSWORD) applique cote serveur : creation de compte
(POST /user), verification des cles (/config/api-key/verify) et /resolve?b64config=.

Aucun appel reseau ni ecriture en base : les cas "mot de passe correct" s'arretent
volontairement sur une validation (400 / valid=False) situee APRES le controle.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wastream.api import routes

SECRET = "mot-de-passe-addon"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


@pytest.fixture
def protege(monkeypatch):
    monkeypatch.setattr(routes.settings, "ADDON_PASSWORD", SECRET)


@pytest.fixture
def ouvert(monkeypatch):
    monkeypatch.setattr(routes.settings, "ADDON_PASSWORD", "")


BODY = {"password": "x", "config": {}}  # config vide : 400 "debrid_services" si le controle est passe


def test_creation_de_compte_refusee_sans_mot_de_passe(client, protege):
    r = client.post("/user", json=BODY)
    assert r.status_code == 403


def test_creation_de_compte_refusee_avec_mauvais_mot_de_passe(client, protege):
    r = client.post("/user", json=BODY, headers={"X-Addon-Password": "faux"})
    assert r.status_code == 403


def test_creation_de_compte_passe_le_controle_avec_le_bon(client, protege):
    r = client.post("/user", json=BODY, headers={"X-Addon-Password": SECRET})
    assert r.status_code == 400 and "debrid_services" in r.json()["error"]


def test_plusieurs_mots_de_passe_separes_par_des_virgules(client, monkeypatch):
    monkeypatch.setattr(routes.settings, "ADDON_PASSWORD", "un, deux")
    assert client.post("/user", json=BODY, headers={"X-Addon-Password": "deux"}).status_code == 400
    assert client.post("/user", json=BODY, headers={"X-Addon-Password": "trois"}).status_code == 403


def test_sans_mot_de_passe_configure_rien_ne_change(client, ouvert):
    assert client.post("/user", json=BODY).status_code == 400


def test_en_tete_non_ascii_ne_plante_pas(client, protege):
    r = client.post("/user", json=BODY, headers={"X-Addon-Password": "é".encode("utf-8")})
    assert r.status_code == 403  # avant : TypeError dans compare_digest


def test_verification_de_cle_refusee_sans_mot_de_passe(client, protege):
    r = client.get("/config/api-key/verify", params={"service": "tmdb", "api_key": " "})
    assert r.status_code == 403


def test_verification_de_cle_passe_avec_le_bon_mot_de_passe(client, protege):
    r = client.get("/config/api-key/verify", params={"service": "tmdb", "api_key": " "},
                   headers={"X-Addon-Password": SECRET})
    assert r.status_code == 200 and r.json() == {"valid": False}


def test_verification_de_cle_ouverte_sans_mot_de_passe_configure(client, ouvert):
    r = client.get("/config/api-key/verify", params={"service": "tmdb", "api_key": " "})
    assert r.status_code == 200


def test_verify_password_non_ascii_ne_leve_plus_de_500(client, protege):
    r = client.post("/config/password/verify", json={"password": "é"})
    assert r.status_code == 200 and r.json() == {"valid": False}
    assert client.post("/config/password/verify", json={"password": SECRET}).json() == {"valid": True}


def test_resolve_ignore_b64config_quand_un_mot_de_passe_protege(client, protege, monkeypatch):
    appels = []
    monkeypatch.setattr(routes, "validate_config", lambda b: appels.append(b) or None)
    client.get("/resolve", params={"link": "magnet:?xt=urn:btih:" + "a" * 40, "b64config": "abc"})
    assert appels == []  # config en clair dans l'URL non acceptee


def test_resolve_accepte_b64config_sans_mot_de_passe(client, ouvert, monkeypatch):
    appels = []
    monkeypatch.setattr(routes, "validate_config", lambda b: appels.append(b) or None)
    client.get("/resolve", params={"link": "magnet:?xt=urn:btih:" + "a" * 40, "b64config": "abc"})
    assert appels == ["abc"]  # comportement historique inchange
