import time
from typing import Optional, Dict, Any

from wastream.utils.database import database
from wastream.utils.crypto import (
    hash_password,
    verify_password,
    encrypt_config,
    decrypt_config,
    encrypt_password_for_url,
    decrypt_password_from_url,
    generate_uuid
)
from wastream.utils.logger import database_logger


# ===========================
# User Creation
# ===========================
async def create_user(password: str, config: Dict[str, Any]) -> Optional[Dict[str, str]]:
    try:
        user_uuid = generate_uuid()

        for _ in range(10):
            existing = await database.fetch_one(
                "SELECT uuid FROM users WHERE uuid = :uuid",
                {"uuid": user_uuid}
            )
            if not existing:
                break
            user_uuid = generate_uuid()
        else:
            database_logger.error("[Users] Failed to generate unique UUID")
            return None

        password_hash = hash_password(password)
        encrypted_config, salt = encrypt_config(config, password)
        encrypted_password = encrypt_password_for_url(password)

        current_time = int(time.time())

        query = """INSERT INTO users (uuid, password_hash, encrypted_config, salt, created_at, updated_at, accessed_at)
                   VALUES (:uuid, :password_hash, :encrypted_config, :salt, :created_at, :updated_at, :accessed_at)"""

        await database.execute(query, {
            "uuid": user_uuid,
            "password_hash": password_hash,
            "encrypted_config": encrypted_config,
            "salt": salt,
            "created_at": current_time,
            "updated_at": current_time,
            "accessed_at": current_time
        })

        database_logger.debug(f"[Users] Created: {user_uuid}")
        return {
            "uuid": user_uuid,
            "encrypted_password": encrypted_password
        }

    except Exception as e:
        database_logger.error(f"[Users] Creation failed: {type(e).__name__}: {e}")
        return None


# ===========================
# User Retrieval by UUID
# ===========================
async def get_user_by_uuid(user_uuid: str) -> Optional[Dict[str, Any]]:
    try:
        result = await database.fetch_one(
            "SELECT uuid, password_hash, encrypted_config, salt, created_at, updated_at, accessed_at FROM users WHERE uuid = :uuid",
            {"uuid": user_uuid}
        )
        if result:
            return dict(result)
        return None
    except Exception as e:
        database_logger.error(f"[Users] Retrieval failed: {type(e).__name__}: {e}")
        return None


# ===========================
# User Config Retrieval
# ===========================
async def get_user_config(user_uuid: str, password: str) -> Optional[Dict[str, Any]]:
    try:
        user = await get_user_by_uuid(user_uuid)
        if not user:
            return None

        if not verify_password(password, user["password_hash"]):
            return None

        config = decrypt_config(user["encrypted_config"], password, user["salt"])
        if not config:
            return None

        current_time = int(time.time())
        await database.execute(
            "UPDATE users SET accessed_at = :accessed_at WHERE uuid = :uuid",
            {"accessed_at": current_time, "uuid": user_uuid}
        )

        return config

    except Exception as e:
        database_logger.error(f"[Users] Config retrieval failed: {type(e).__name__}: {e}")
        return None


# ===========================
# User Config Retrieval with Error Details
# ===========================
async def get_user_config_detailed(user_uuid: str, password: str) -> Dict[str, Any]:
    try:
        user = await get_user_by_uuid(user_uuid)
        if not user:
            return {"success": False, "error": "user_not_found"}

        if not verify_password(password, user["password_hash"]):
            return {"success": False, "error": "invalid_password"}

        config = decrypt_config(user["encrypted_config"], password, user["salt"])
        if not config:
            return {"success": False, "error": "decrypt_failed"}

        current_time = int(time.time())
        await database.execute(
            "UPDATE users SET accessed_at = :accessed_at WHERE uuid = :uuid",
            {"accessed_at": current_time, "uuid": user_uuid}
        )

        return {"success": True, "config": config}

    except Exception as e:
        database_logger.error(f"[Users] Config retrieval failed: {type(e).__name__}: {e}")
        return {"success": False, "error": "server_error"}


# ===========================
# User Config from URL
# ===========================
async def get_config_from_url_params(user_uuid: str, encrypted_password: str) -> Optional[Dict[str, Any]]:
    try:
        password = decrypt_password_from_url(encrypted_password)
        if not password:
            return None

        return await get_user_config(user_uuid, password)

    except Exception as e:
        database_logger.error(f"[Users] Config from URL failed: {type(e).__name__}: {e}")
        return None


# ===========================
# User Config Update
# ===========================
async def update_user_config(user_uuid: str, password: str, new_config: Dict[str, Any]) -> bool:
    try:
        user = await get_user_by_uuid(user_uuid)
        if not user:
            return False

        if not verify_password(password, user["password_hash"]):
            return False

        encrypted_config, salt = encrypt_config(new_config, password)
        current_time = int(time.time())

        await database.execute(
            "UPDATE users SET encrypted_config = :encrypted_config, salt = :salt, updated_at = :updated_at WHERE uuid = :uuid",
            {
                "encrypted_config": encrypted_config,
                "salt": salt,
                "updated_at": current_time,
                "uuid": user_uuid
            }
        )

        database_logger.debug(f"[Users] Config updated: {user_uuid}")
        return True

    except Exception as e:
        database_logger.error(f"[Users] Config update failed: {type(e).__name__}: {e}")
        return False


# ===========================
# User Deletion
# ===========================
async def delete_user(user_uuid: str, password: str) -> bool:
    try:
        user = await get_user_by_uuid(user_uuid)
        if not user:
            return False

        if not verify_password(password, user["password_hash"]):
            return False

        await database.execute(
            "DELETE FROM users WHERE uuid = :uuid",
            {"uuid": user_uuid}
        )

        database_logger.debug(f"[Users] Deleted: {user_uuid}")
        return True

    except Exception as e:
        database_logger.error(f"[Users] Deletion failed: {type(e).__name__}: {e}")
        return False


# ===========================
# User Deletion with Error Details
# ===========================
async def delete_user_detailed(user_uuid: str, password: str) -> Dict[str, Any]:
    try:
        user = await get_user_by_uuid(user_uuid)
        if not user:
            return {"success": False, "error": "user_not_found"}

        if not verify_password(password, user["password_hash"]):
            return {"success": False, "error": "invalid_password"}

        await database.execute(
            "DELETE FROM users WHERE uuid = :uuid",
            {"uuid": user_uuid}
        )

        database_logger.debug(f"[Users] Deleted: {user_uuid}")
        return {"success": True}

    except Exception as e:
        database_logger.error(f"[Users] Deletion failed: {type(e).__name__}: {e}")
        return {"success": False, "error": "server_error"}


# ===========================
# User Verification
# ===========================
async def verify_user(user_uuid: str, password: str) -> bool:
    try:
        user = await get_user_by_uuid(user_uuid)
        if not user:
            return False

        return verify_password(password, user["password_hash"])

    except Exception as e:
        database_logger.error(f"[Users] Verification failed: {type(e).__name__}: {e}")
        return False
