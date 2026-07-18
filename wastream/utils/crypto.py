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
SALT_SIZE = 32
BCRYPT_ROUNDS = 10


# ===========================
# Password Hashing (Bcrypt)
# ===========================
def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


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

    except Exception:
        return None


# ===========================
# Password Encryption for URL
# ===========================
def encrypt_password_for_url(password: str) -> str:
    secret_key = settings.SECRET_KEY.encode("utf-8")
    key = hashlib.sha256(secret_key).digest()
    iv = os.urandom(IV_SIZE)

    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(password.encode("utf-8"), AES.block_size))

    encrypted_data = {
        "iv": b64encode(iv).decode("utf-8"),
        "data": b64encode(encrypted).decode("utf-8")
    }

    return b64encode(json.dumps(encrypted_data).encode("utf-8")).decode("utf-8")


def decrypt_password_from_url(encrypted_password: str) -> Optional[str]:
    try:
        secret_key = settings.SECRET_KEY.encode("utf-8")
        key = hashlib.sha256(secret_key).digest()

        encrypted_data = json.loads(b64decode(encrypted_password).decode("utf-8"))
        iv = b64decode(encrypted_data["iv"])
        encrypted = b64decode(encrypted_data["data"])

        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(encrypted), AES.block_size)

        return decrypted.decode("utf-8")

    except Exception:
        return None


# ===========================
# Fast Hash (for URL verification)
# ===========================
def fast_hash(data: str) -> str:
    secret_key = settings.SECRET_KEY.encode("utf-8")
    combined = data.encode("utf-8") + secret_key
    return hashlib.sha256(combined).hexdigest()


def verify_fast_hash(data: str, hash_value: str) -> bool:
    return fast_hash(data) == hash_value


# ===========================
# UUID Generation
# ===========================
def generate_uuid() -> str:
    return str(uuid.uuid4())
