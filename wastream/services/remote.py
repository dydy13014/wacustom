import os
import json
import time
import secrets
import asyncio
import hashlib
from typing import List, Dict, Any, Optional
from base64 import b64encode, b64decode

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from wastream.config.settings import settings
from wastream.utils.database import database
from wastream.utils.logger import remote_logger
from wastream.utils.http_client import http_client


# ===========================
# Constants
# ===========================
API_KEY_PREFIX = "wa"
API_KEY_LENGTH = 32
IV_SIZE = 16


# ===========================
# API Key Generation
# ===========================
def generate_api_key() -> str:
    random_bytes = secrets.token_bytes(API_KEY_LENGTH)
    key_body = b64encode(random_bytes).decode("utf-8").rstrip("=")[:API_KEY_LENGTH]
    return f"{API_KEY_PREFIX}_{key_body}"


# ===========================
# API Key Encryption for Storage
# ===========================
def encrypt_api_key_for_storage(api_key: str) -> str:
    secret_key = settings.SECRET_KEY.encode("utf-8")
    key = hashlib.sha256(secret_key).digest()
    iv = os.urandom(IV_SIZE)

    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(api_key.encode("utf-8"), AES.block_size))

    encrypted_data = {
        "iv": b64encode(iv).decode("utf-8"),
        "data": b64encode(encrypted).decode("utf-8")
    }

    return b64encode(json.dumps(encrypted_data).encode("utf-8")).decode("utf-8")


def decrypt_api_key_from_storage(encrypted_key: str) -> Optional[str]:
    try:
        secret_key = settings.SECRET_KEY.encode("utf-8")
        key = hashlib.sha256(secret_key).digest()

        encrypted_data = json.loads(b64decode(encrypted_key).decode("utf-8"))
        iv = b64decode(encrypted_data["iv"])
        encrypted = b64decode(encrypted_data["data"])

        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(encrypted), AES.block_size)

        return decrypted.decode("utf-8")

    except Exception:
        return None


# ===========================
# API Keys Management
# ===========================
async def create_api_key(
    name: str,
    permissions: Dict[str, bool]
) -> Dict[str, Any]:
    try:
        full_key = generate_api_key()
        key_hash = _hash_api_key(full_key)
        key_encrypted = encrypt_api_key_for_storage(full_key)
        current_time = int(time.time())

        await database.execute(
            """INSERT INTO remote_api_keys
               (name, key_hash, key_encrypted, permissions, enabled, created_at)
               VALUES (:name, :key_hash, :key_encrypted, :permissions, 1, :created_at)""",
            {
                "name": name,
                "key_hash": key_hash,
                "key_encrypted": key_encrypted,
                "permissions": json.dumps(permissions),
                "created_at": current_time
            }
        )

        row = await database.fetch_one(
            "SELECT id FROM remote_api_keys ORDER BY id DESC LIMIT 1"
        )

        remote_logger.debug(f"[API-Key] Created: {name}")
        return {
            "success": True,
            "id": row["id"] if row else None,
            "api_key": full_key,
            "name": name,
            "permissions": permissions
        }

    except Exception as e:
        remote_logger.error(f"[API-Key] Failed to create: {type(e).__name__}: {e}")
        return {"success": False, "error": str(e)}


async def get_api_keys() -> List[Dict[str, Any]]:
    try:
        rows = await database.fetch_all(
            "SELECT * FROM remote_api_keys ORDER BY created_at DESC"
        )

        keys = []
        for row in rows:
            api_key = decrypt_api_key_from_storage(row["key_encrypted"]) if row["key_encrypted"] else None
            keys.append({
                "id": row["id"],
                "name": row["name"],
                "api_key": api_key,
                "permissions": json.loads(row["permissions"]),
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"]
            })

        return keys

    except Exception as e:
        remote_logger.error(f"[API-Key] Failed to get: {type(e).__name__}: {e}")
        return []


async def get_api_key_by_id(key_id: int) -> Optional[Dict[str, Any]]:
    try:
        row = await database.fetch_one(
            "SELECT * FROM remote_api_keys WHERE id = :id",
            {"id": key_id}
        )

        if not row:
            return None

        api_key = decrypt_api_key_from_storage(row["key_encrypted"]) if row["key_encrypted"] else None
        return {
            "id": row["id"],
            "name": row["name"],
            "api_key": api_key,
            "permissions": json.loads(row["permissions"]),
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"]
        }

    except Exception as e:
        remote_logger.error(f"[API-Key] Failed to get: {type(e).__name__}: {e}")
        return None


async def update_api_key(
    key_id: int,
    name: Optional[str] = None,
    permissions: Optional[Dict[str, bool]] = None,
    enabled: Optional[bool] = None
) -> bool:
    try:
        existing = await database.fetch_one(
            "SELECT * FROM remote_api_keys WHERE id = :id",
            {"id": key_id}
        )

        if not existing:
            return False

        new_name = name if name is not None else existing["name"]
        new_permissions = json.dumps(permissions) if permissions is not None else existing["permissions"]
        new_enabled = 1 if enabled else 0 if enabled is not None else existing["enabled"]

        await database.execute(
            """UPDATE remote_api_keys SET
               name = :name, permissions = :permissions, enabled = :enabled
               WHERE id = :id""",
            {
                "id": key_id,
                "name": new_name,
                "permissions": new_permissions,
                "enabled": new_enabled
            }
        )

        remote_logger.debug(f"[API-Key] Updated: {key_id}")
        return True

    except Exception as e:
        remote_logger.error(f"[API-Key] Failed to update: {type(e).__name__}: {e}")
        return False


async def delete_api_key(key_id: int) -> bool:
    try:
        result = await database.execute(
            "DELETE FROM remote_api_keys WHERE id = :id",
            {"id": key_id}
        )
        if result > 0:
            remote_logger.debug(f"[API-Key] Deleted: {key_id}")
        return result > 0

    except Exception as e:
        remote_logger.error(f"[API-Key] Failed to delete: {type(e).__name__}: {e}")
        return False


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


async def validate_api_key(api_key: str) -> Optional[Dict[str, Any]]:
    try:
        if not api_key or not api_key.startswith(f"{API_KEY_PREFIX}_"):
            return None

        key_hash = _hash_api_key(api_key)

        row = await database.fetch_one(
            "SELECT * FROM remote_api_keys WHERE key_hash = :key_hash AND enabled = 1",
            {"key_hash": key_hash}
        )

        if not row:
            return None

        return {
            "id": row["id"],
            "name": row["name"],
            "permissions": json.loads(row["permissions"])
        }

    except Exception as e:
        remote_logger.error(f"[API-Key] Failed to validate: {type(e).__name__}: {e}")
        return None


# ===========================
# Remote Instances Management
# ===========================
async def add_remote_instance(
    name: str,
    url: str,
    api_key: Optional[str] = None,
    fetch_preferences: Optional[Dict[str, bool]] = None,
    store_preferences: Optional[Dict[str, bool]] = None
) -> Dict[str, Any]:
    try:
        url = url.rstrip("/")
        current_time = int(time.time())

        existing = await database.fetch_one(
            "SELECT id FROM remote_instances WHERE url = :url",
            {"url": url}
        )

        if existing:
            return {"success": False, "error": "An instance with this URL already exists"}

        encrypted_key = encrypt_api_key_for_storage(api_key) if api_key else None

        default_fetch_prefs = {
            "dead_links": True,
            "cache": True,
            "wasource": True
        }
        fetch_prefs = fetch_preferences if fetch_preferences else default_fetch_prefs

        default_store_prefs = {
            "dead_links": True,
            "cache": True,
            "wasource": True
        }
        store_prefs = store_preferences if store_preferences else default_store_prefs

        await database.execute(
            """INSERT INTO remote_instances
               (name, url, api_key_encrypted, fetch_preferences, store_preferences, enabled, created_at)
               VALUES (:name, :url, :api_key_encrypted, :fetch_preferences, :store_preferences, 1, :created_at)""",
            {
                "name": name,
                "url": url,
                "api_key_encrypted": encrypted_key,
                "fetch_preferences": json.dumps(fetch_prefs),
                "store_preferences": json.dumps(store_prefs),
                "created_at": current_time
            }
        )

        row = await database.fetch_one(
            "SELECT id FROM remote_instances WHERE url = :url",
            {"url": url}
        )

        remote_logger.debug(f"[Instance] Added: {name}")
        return {
            "success": True,
            "id": row["id"],
            "name": name,
            "url": url
        }

    except Exception as e:
        remote_logger.error(f"[Instance] Failed to add: {type(e).__name__}: {e}")
        return {"success": False, "error": str(e)}


async def get_remote_instances() -> List[Dict[str, Any]]:
    try:
        rows = await database.fetch_all(
            "SELECT * FROM remote_instances ORDER BY created_at DESC"
        )

        instances = []
        for row in rows:
            fetch_prefs = json.loads(row["fetch_preferences"]) if row["fetch_preferences"] else {
                "dead_links": True, "cache": True, "wasource": True
            }
            store_prefs = json.loads(row["store_preferences"]) if row["store_preferences"] else {
                "dead_links": True, "cache": True, "wasource": True
            }
            instances.append({
                "id": row["id"],
                "name": row["name"],
                "url": row["url"],
                "has_api_key": bool(row["api_key_encrypted"]),
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
                "last_check_at": row["last_check_at"],
                "last_success_at": row["last_success_at"],
                "is_online": bool(row["is_online"]),
                "permissions": json.loads(row["permissions"]) if row["permissions"] else None,
                "fetch_preferences": fetch_prefs,
                "store_preferences": store_prefs
            })

        return instances

    except Exception as e:
        remote_logger.error(f"[Instance] Failed to get: {type(e).__name__}: {e}")
        return []


async def get_enabled_remote_instances() -> List[Dict[str, Any]]:
    try:
        rows = await database.fetch_all(
            "SELECT * FROM remote_instances WHERE enabled = 1"
        )

        instances = []
        for row in rows:
            api_key = None
            if row["api_key_encrypted"]:
                api_key = decrypt_api_key_from_storage(row["api_key_encrypted"])

            fetch_prefs = json.loads(row["fetch_preferences"]) if row["fetch_preferences"] else {
                "dead_links": True, "cache": True, "wasource": True
            }
            store_prefs = json.loads(row["store_preferences"]) if row["store_preferences"] else {
                "dead_links": True, "cache": True, "wasource": True
            }

            instances.append({
                "id": row["id"],
                "name": row["name"],
                "url": row["url"],
                "api_key": api_key,
                "permissions": json.loads(row["permissions"]) if row["permissions"] else None,
                "fetch_preferences": fetch_prefs,
                "store_preferences": store_prefs
            })

        return instances

    except Exception as e:
        remote_logger.error(f"[Instance] Failed to get enabled: {type(e).__name__}: {e}")
        return []


async def update_remote_instance(
    instance_id: int,
    name: Optional[str] = None,
    url: Optional[str] = None,
    api_key: Optional[str] = None,
    enabled: Optional[bool] = None,
    fetch_preferences: Optional[Dict[str, bool]] = None,
    store_preferences: Optional[Dict[str, bool]] = None
) -> Dict[str, Any]:
    try:
        existing = await database.fetch_one(
            "SELECT * FROM remote_instances WHERE id = :id",
            {"id": instance_id}
        )

        if not existing:
            return {"success": False, "error": "Instance not found"}

        new_name = name if name is not None else existing["name"]
        new_url = url.rstrip("/") if url is not None else existing["url"]
        new_enabled = 1 if enabled else 0 if enabled is not None else existing["enabled"]

        if url is not None and new_url != existing["url"]:
            url_exists = await database.fetch_one(
                "SELECT id FROM remote_instances WHERE url = :url AND id != :id",
                {"url": new_url, "id": instance_id}
            )
            if url_exists:
                return {"success": False, "error": "An instance with this URL already exists"}

        if api_key is not None:
            new_api_key_encrypted = encrypt_api_key_for_storage(api_key)
        else:
            new_api_key_encrypted = existing["api_key_encrypted"]

        if fetch_preferences is not None:
            new_fetch_prefs = json.dumps(fetch_preferences)
        else:
            new_fetch_prefs = existing["fetch_preferences"]

        if store_preferences is not None:
            new_store_prefs = json.dumps(store_preferences)
        else:
            new_store_prefs = existing.get("store_preferences") or json.dumps({"dead_links": True, "cache": True, "wasource": True})

        await database.execute(
            """UPDATE remote_instances SET
               name = :name, url = :url, api_key_encrypted = :api_key_encrypted, enabled = :enabled, fetch_preferences = :fetch_preferences, store_preferences = :store_preferences
               WHERE id = :id""",
            {
                "id": instance_id,
                "name": new_name,
                "url": new_url,
                "api_key_encrypted": new_api_key_encrypted,
                "enabled": new_enabled,
                "fetch_preferences": new_fetch_prefs,
                "store_preferences": new_store_prefs
            }
        )

        remote_logger.debug(f"[Instance] Updated: {instance_id}")
        return {"success": True}

    except Exception as e:
        remote_logger.error(f"[Instance] Failed to update: {type(e).__name__}: {e}")
        return {"success": False, "error": str(e)}


async def delete_remote_instance(instance_id: int) -> bool:
    try:
        result = await database.execute(
            "DELETE FROM remote_instances WHERE id = :id",
            {"id": instance_id}
        )
        if result > 0:
            remote_logger.debug(f"[Instance] Deleted: {instance_id}")
        return result > 0

    except Exception as e:
        remote_logger.error(f"[Instance] Failed to delete: {type(e).__name__}: {e}")
        return False


async def check_remote_instance(instance_id: int) -> Dict[str, Any]:
    try:
        row = await database.fetch_one(
            "SELECT * FROM remote_instances WHERE id = :id",
            {"id": instance_id}
        )

        if not row:
            return {"success": False, "error": "Instance not found"}

        api_key = None
        if row["api_key_encrypted"]:
            api_key = decrypt_api_key_from_storage(row["api_key_encrypted"])

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        current_time = int(time.time())

        try:
            response = await http_client.get(
                f"{row['url']}/remote/permissions",
                headers=headers,
                timeout=settings.HEALTH_CHECK_TIMEOUT
            )

            if response.status_code == 200:
                data = response.json()
                permissions = data.get("permissions", {})

                await database.execute(
                    """UPDATE remote_instances SET
                       last_check_at = :time, last_success_at = :time, is_online = 1, permissions = :permissions
                       WHERE id = :id""",
                    {"id": instance_id, "time": current_time, "permissions": json.dumps(permissions)}
                )

                return {
                    "success": True,
                    "is_online": True,
                    "permissions": permissions,
                    "instance_name": data.get("instance_name", "Unknown")
                }

            elif response.status_code == 401:
                await database.execute(
                    "UPDATE remote_instances SET last_check_at = :time, is_online = 0 WHERE id = :id",
                    {"id": instance_id, "time": current_time}
                )
                return {"success": False, "error": "Invalid API key", "is_online": False}

            else:
                await database.execute(
                    "UPDATE remote_instances SET last_check_at = :time, is_online = 0 WHERE id = :id",
                    {"id": instance_id, "time": current_time}
                )
                return {"success": False, "error": f"HTTP {response.status_code}", "is_online": False}

        except Exception as e:
            await database.execute(
                "UPDATE remote_instances SET last_check_at = :time, is_online = 0 WHERE id = :id",
                {"id": instance_id, "time": current_time}
            )
            return {"success": False, "error": f"Connection failed: {type(e).__name__}", "is_online": False}

    except Exception as e:
        remote_logger.error(f"[Instance] Failed to check: {type(e).__name__}: {e}")
        return {"success": False, "error": str(e)}


# ===========================
# Remote Data Fetching
# ===========================
async def fetch_remote_dead_links(urls: List[str]) -> tuple[Dict[str, bool], bool]:
    if not urls:
        return {}, False

    instances = await get_enabled_remote_instances()
    if not instances:
        return {}, False

    instances = [i for i in instances if i.get("fetch_preferences", {}).get("dead_links", True) and (i.get("permissions") is None or i.get("permissions", {}).get("dead_links_read", False))]
    if not instances:
        return {}, False

    should_store = any(i.get("store_preferences", {}).get("dead_links", True) for i in instances)
    results = {}

    async def check_instance(instance: Dict[str, Any]):
        try:
            headers = {"Content-Type": "application/json"}
            if instance["api_key"]:
                headers["Authorization"] = f"Bearer {instance['api_key']}"

            response = await http_client.post(
                f"{instance['url']}/remote/dead-links/check",
                headers=headers,
                json={"urls": urls},
                timeout=settings.HTTP_TIMEOUT
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("dead_links", {})

        except Exception as e:
            remote_logger.debug(f"[Fetch] Dead links check failed for {instance['name']}: {type(e).__name__}: {e}")

        return {}

    tasks = [check_instance(instance) for instance in instances]
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in task_results:
        if isinstance(result, dict):
            for url, is_dead in result.items():
                if is_dead:
                    results[url] = True

    if results:
        remote_logger.debug(f"[Fetch] Dead links: found {len(results)} from {len(instances)} instances")

    return results, should_store


async def fetch_remote_cache(
    content_type: str,
    title: str,
    year: Optional[str] = None
) -> tuple[Optional[Dict[str, Any]], bool]:
    instances = await get_enabled_remote_instances()
    if not instances:
        return None, False

    instances = [i for i in instances if i.get("fetch_preferences", {}).get("cache", True) and (i.get("permissions") is None or i.get("permissions", {}).get("cache_read", False))]
    if not instances:
        return None, False

    should_store = any(i.get("store_preferences", {}).get("cache", True) for i in instances)

    async def check_instance(instance: Dict[str, Any]):
        try:
            headers = {}
            if instance["api_key"]:
                headers["Authorization"] = f"Bearer {instance['api_key']}"

            params = {"content_type": content_type, "title": title}
            if year is not None:
                params["year"] = year

            response = await http_client.get(
                f"{instance['url']}/remote/cache/check",
                headers=headers,
                params=params,
                timeout=settings.HTTP_TIMEOUT
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("found"):
                    return data.get("content")

        except Exception:
            pass

        return None

    tasks = [check_instance(instance) for instance in instances]
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in task_results:
        if result and not isinstance(result, Exception):
            remote_logger.debug("[Fetch] Cache: found data from remote instances")
            return result, should_store

    return None, False


async def fetch_remote_wasource(
    imdb_id: Optional[str] = None,
    title: Optional[str] = None,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None
) -> tuple[List[Dict[str, Any]], bool]:
    instances = await get_enabled_remote_instances()
    if not instances:
        return [], False

    instances = [i for i in instances if i.get("fetch_preferences", {}).get("wasource", True) and (i.get("permissions") is None or i.get("permissions", {}).get("wasource_read", False))]
    if not instances:
        return [], False

    should_store = any(i.get("store_preferences", {}).get("wasource", True) for i in instances)
    all_results = []

    async def check_instance(instance: Dict[str, Any]):
        try:
            headers = {}
            if instance["api_key"]:
                headers["Authorization"] = f"Bearer {instance['api_key']}"

            params = {}
            if imdb_id:
                params["imdb_id"] = imdb_id
            if title:
                params["title"] = title
            if year is not None:
                params["year"] = year
            if season is not None:
                params["season"] = season
            if episode is not None:
                params["episode"] = episode

            response = await http_client.get(
                f"{instance['url']}/remote/wasource/search",
                headers=headers,
                params=params,
                timeout=settings.HTTP_TIMEOUT
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("results", [])

        except Exception as e:
            remote_logger.debug(f"[Fetch] WASource search failed for {instance['name']}: {type(e).__name__}: {e}")

        return []

    tasks = [check_instance(instance) for instance in instances]
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in task_results:
        if isinstance(result, list):
            all_results.extend(result)

    if all_results:
        remote_logger.debug(f"[Fetch] WASource: found {len(all_results)} links from {len(instances)} instances")

    return all_results, should_store


# ===========================
# Stats
# ===========================
async def get_remote_stats() -> Dict[str, Any]:
    try:
        api_keys_count = await database.fetch_val("SELECT COUNT(*) FROM remote_api_keys") or 0
        api_keys_active = await database.fetch_val("SELECT COUNT(*) FROM remote_api_keys WHERE enabled = 1") or 0
        instances_count = await database.fetch_val("SELECT COUNT(*) FROM remote_instances") or 0
        instances_online = await database.fetch_val(
            "SELECT COUNT(*) FROM remote_instances WHERE is_online = 1 AND enabled = 1"
        ) or 0

        return {
            "api_keys": {
                "total": api_keys_count,
                "active": api_keys_active
            },
            "instances": {
                "total": instances_count,
                "online": instances_online
            }
        }

    except Exception as e:
        remote_logger.error(f"[Stats] Failed to get remote stats: {type(e).__name__}: {e}")
        return {
            "api_keys": {"total": 0, "active": 0},
            "instances": {"total": 0, "online": 0}
        }
