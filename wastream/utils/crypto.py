import os
import json
import zlib
import uuid
import hashlib
from base64 import b64encode, b64decode
from typing import Optional, Dict, Any

import bcrypt
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from wastream.config.settings import settings


# ===========================
# Constants
# ===========================
PBKDF2_ITERATIONS = 100000
AES_KEY_SIZE = 32
IV_SIZE = 16
NONCE_SIZE = 12          # taille recommandée pour AES-GCM
SALT_SIZE = 32
BCRYPT_ROUNDS = 10
# bcrypt ignore silencieusement tout ce qui dépasse : deux phrases de passe
# partageant leurs 72 premiers octets deviendraient interchangeables.
BCRYPT_MAX_BYTES = 72


# ===========================
# Password Hashing (Bcrypt)
# ===========================
def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Mot de passe trop long : {len(raw)} octets, maximum {BCRYPT_MAX_BYTES}. "
            "Au-delà, bcrypt tronque sans prévenir et la longueur supplémentaire "
            "n'apporte aucune sécurité."
        )
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(raw, salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


# ===========================
# Key Derivation (PBKDF2)
# ===========================
def derive_key(password: str, salt: bytes) -> bytes:
    secret_key = settings.SECRET_KEY.encode("utf-8")
    combined = password.encode("utf-8") + secret_key
    return hashlib.pbkdf2_hmac(
        "sha512",
        combined,
        salt,
        PBKDF2_ITERATIONS,
        dklen=AES_KEY_SIZE
    )


# ===========================
# Config Encryption (AES-256-CBC)
# ===========================
def encrypt_config(config: Dict[str, Any], password: str) -> tuple[str, str]:
    salt = os.urandom(SALT_SIZE)
    key = derive_key(password, salt)
    iv = os.urandom(IV_SIZE)

    config_json = json.dumps(config, separators=(",", ":"))
    compressed = zlib.compress(config_json.encode("utf-8"), level=9)

    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(compressed, AES.block_size))

    encrypted_data = {
        "iv": b64encode(iv).decode("utf-8"),
        "data": b64encode(encrypted).decode("utf-8")
    }

    encrypted_config = b64encode(json.dumps(encrypted_data).encode("utf-8")).decode("utf-8")
    salt_b64 = b64encode(salt).decode("utf-8")

    return encrypted_config, salt_b64


def decrypt_config(encrypted_config: str, password: str, salt_b64: str) -> Optional[Dict[str, Any]]:
    try:
        salt = b64decode(salt_b64)
        key = derive_key(password, salt)

        encrypted_data = json.loads(b64decode(encrypted_config).decode("utf-8"))
        iv = b64decode(encrypted_data["iv"])
        encrypted = b64decode(encrypted_data["data"])

        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(encrypted), AES.block_size)

        decompressed = zlib.decompress(decrypted)
        return json.loads(decompressed.decode("utf-8"))

    except Exception as e:
        # Un mot de passe faux est un cas NORMAL (l'appelant teste des identifiants)
        # et ne doit pas polluer les logs -> niveau debug, pas error. Mais sans
        # aucune trace, une config reellement corrompue (base abimee, changement
        # de SECRET_KEY, migration de format) etait indiscernable d'un simple
        # mauvais mot de passe : les deux renvoyaient None en silence.
        # On ne journalise que le TYPE d'exception, jamais le contenu dechiffre
        # ni le mot de passe.
        from wastream.utils.logger import database_logger   # import tardif : evite un cycle
        database_logger.debug(f"[Crypto] Dechiffrement de config impossible: {type(e).__name__}")
        return None


# ===========================
# Password Encryption for URL
# ===========================
def encrypt_password_for_url(password: str) -> str:
    """AES-256-GCM. Cette valeur voyage dans l'URL de manifest — donc dans les
    journaux du reverse proxy, l'historique du navigateur et chez tout
    intermédiaire réseau. GCM authentifie : toute altération du chiffré est
    détectée au déchiffrement, là où le CBC utilisé auparavant était malléable
    et laissait passer les modifications sans le moindre signal."""
    key = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    nonce = os.urandom(NONCE_SIZE)

    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    encrypted, tag = cipher.encrypt_and_digest(password.encode("utf-8"))

    encrypted_data = {
        "n": b64encode(nonce).decode("utf-8"),
        "data": b64encode(encrypted).decode("utf-8"),
        "t": b64encode(tag).decode("utf-8"),
    }

    return b64encode(json.dumps(encrypted_data).encode("utf-8")).decode("utf-8")


def decrypt_password_from_url(encrypted_password: str) -> Optional[str]:
    """N'accepte QUE le format GCM. Les anciennes URL en CBC sont rejetées : les
    accepter en repli aurait maintenu la faille en vie, puisqu'un attaquant
    aurait pu se contenter de forger un chiffré à l'ancien format."""
    try:
        key = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        d = json.loads(b64decode(encrypted_password).decode("utf-8"))

        cipher = AES.new(key, AES.MODE_GCM, nonce=b64decode(d["n"]))
        decrypted = cipher.decrypt_and_verify(b64decode(d["data"]), b64decode(d["t"]))
        return decrypted.decode("utf-8")

    except Exception:
        # Couvre aussi l'échec d'authentification GCM (chiffré altéré) et les
        # anciennes URL CBC, qui n'ont pas les champs attendus.
        return None


# fast_hash / verify_fast_hash ont été retirés : sans aucun appelant dans tout
# le dépôt, ils constituaient du code mort — et leur comparaison par `==` était
# vulnérable au minutage. Pour signer quoi que ce soit, utiliser hmac.new() et
# hmac.compare_digest() (cf. helpers.sign_token), jamais une concaténation
# « données + secret » comparée avec `==`.


# ===========================
# UUID Generation
# ===========================
def generate_uuid() -> str:
    return str(uuid.uuid4())
