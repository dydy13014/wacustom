"""Tests pour wastream/utils/crypto.py : hachage bcrypt, chiffrement de
config (AES-CBC) et chiffrement de mot de passe pour URL (AES-GCM).

Ces fonctions protegent des donnees sensibles (mots de passe, cles API des
services debrid) qui transitent par des URLs de manifest -> tout defaut de
round-trip ou de validation ici a un impact securite direct.
"""
import pytest

from wastream.utils.crypto import (
    hash_password,
    verify_password,
    BCRYPT_MAX_BYTES,
    encrypt_config,
    decrypt_config,
    encrypt_password_for_url,
    decrypt_password_from_url,
)


# ===========================
# Password Hashing
# ===========================
def test_hash_password_round_trip():
    hashed = hash_password("MonMotDePasse123")
    assert verify_password("MonMotDePasse123", hashed) is True


def test_verify_password_rejects_wrong_password():
    hashed = hash_password("MonMotDePasse123")
    assert verify_password("MauvaisMotDePasse", hashed) is False


def test_hash_password_rejects_over_bcrypt_limit():
    # Au-dela de 72 octets, bcrypt tronque silencieusement -> refuse plutot
    # que de laisser deux mots de passe differents devenir interchangeables.
    too_long = "a" * (BCRYPT_MAX_BYTES + 1)
    with pytest.raises(ValueError):
        hash_password(too_long)


def test_hash_password_accepts_exactly_max_bytes():
    exactly_max = "a" * BCRYPT_MAX_BYTES
    hashed = hash_password(exactly_max)
    assert verify_password(exactly_max, hashed) is True


def test_verify_password_invalid_hash_returns_false_not_raise():
    assert verify_password("anything", "not-a-valid-bcrypt-hash") is False


# ===========================
# Config Encryption (AES-CBC)
# ===========================
def test_encrypt_decrypt_config_round_trip():
    config = {"debrid_services": {"alldebrid": {"api_key": "secret123"}}, "languages": ["French"]}
    encrypted, salt = encrypt_config(config, "password123")
    decrypted = decrypt_config(encrypted, "password123", salt)
    assert decrypted == config


def test_decrypt_config_wrong_password_returns_none():
    config = {"debrid_services": {"alldebrid": {"api_key": "secret123"}}}
    encrypted, salt = encrypt_config(config, "password123")
    assert decrypt_config(encrypted, "wrong-password", salt) is None


def test_decrypt_config_garbage_input_returns_none_not_raise():
    assert decrypt_config("not-valid-base64!!", "password123", "also-not-valid") is None


# ===========================
# Password Encryption for URL (AES-GCM)
# ===========================
def test_encrypt_decrypt_password_for_url_round_trip():
    encrypted = encrypt_password_for_url("MonMotDePasse123")
    assert decrypt_password_from_url(encrypted) == "MonMotDePasse123"


def test_decrypt_password_from_url_rejects_tampered_ciphertext():
    encrypted = encrypt_password_for_url("MonMotDePasse123")
    # Altere un caractere au milieu de la valeur encodee -> l'authentification
    # GCM doit detecter la modification et rejeter, jamais renvoyer un mot de
    # passe errone en silence.
    tampered = encrypted[:-4] + ("A" if encrypted[-4] != "A" else "B") + encrypted[-3:]
    assert decrypt_password_from_url(tampered) is None


def test_decrypt_password_from_url_rejects_old_cbc_format():
    # Regression : les anciennes URL au format CBC (sans champs GCM "n"/"t")
    # ne doivent jamais etre acceptees en repli silencieux.
    import json
    from base64 import b64encode
    fake_cbc_payload = b64encode(json.dumps({"iv": "irrelevant", "data": "irrelevant"}).encode()).decode()
    assert decrypt_password_from_url(fake_cbc_payload) is None


def test_decrypt_password_from_url_rejects_garbage():
    assert decrypt_password_from_url("not-valid-base64-at-all!!!") is None
