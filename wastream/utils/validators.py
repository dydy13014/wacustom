import binascii
import json
from base64 import b64decode
from typing import Optional, Dict, List

from pydantic import BaseModel, field_validator, ConfigDict

from wastream.config.settings import settings
from wastream.utils.logger import api_logger
from wastream.utils.quality import AVAILABLE_RESOLUTIONS


# ===========================
# Pydantic Config Models
# ===========================
class DebridServiceEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    service: str
    api_key: str
    hosts: List[str] = []
    sources: List[str] = []
    enable_nzb: bool = False
    enable_full_season: bool = False

    @field_validator("service")
    @classmethod
    def validate_service(cls, v):
        if v not in settings.DEBRID_SERVICES:
            raise ValueError(f"Invalid debrid service: {v}")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v):
        if not v:
            raise ValueError("api_key must not be empty")
        return v


class UserConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    tmdb_api_token: str
    debrid_services: List[DebridServiceEntry]
    excluded_keywords: List[str] = []
    languages: List[str] = []
    resolutions: List[str] = AVAILABLE_RESOLUTIONS
    max_results_per_resolution: int = 0
    max_size_gb: float = 0.0
    recheck_hoster_status: bool = False

    @field_validator("tmdb_api_token")
    @classmethod
    def validate_tmdb_token(cls, v):
        if not v:
            raise ValueError("tmdb_api_token must not be empty")
        return v

    @field_validator("debrid_services")
    @classmethod
    def validate_debrid_services(cls, v):
        if not v:
            raise ValueError("At least one debrid service is required")
        return v

    @field_validator("max_results_per_resolution")
    @classmethod
    def validate_max_results(cls, v):
        if v < 0:
            raise ValueError("max_results_per_resolution must be >= 0")
        return v

    @field_validator("max_size_gb", mode="before")
    @classmethod
    def coerce_max_size(cls, v):
        if isinstance(v, (int, float)):
            if v < 0:
                raise ValueError("max_size_gb must be >= 0")
            return float(v)
        return v


# ===========================
# Configuration Validation
# ===========================
def validate_config(config_base64: Optional[str]) -> Optional[Dict]:
    """Decode and validate a base64-encoded user configuration.

    Validates debrid services, filters, languages, resolutions and returns
    the config dict if valid, or None if invalid. Supports both multi-service
    (debrid_services list) and legacy single-service (debrid_service/debrid_api_key) formats.
    """
    if not config_base64:
        api_logger.debug("Empty config provided")
        return None

    try:
        api_logger.debug("Validating configuration")
        decoded_bytes = b64decode(config_base64, validate=True)
        decoded_str = decoded_bytes.decode('utf-8')

        config_dict = json.loads(decoded_str)

        if not isinstance(config_dict, dict):
            api_logger.debug("Config is not a dict")
            return None

        # Legacy single-service format → convert to multi-service
        if "debrid_services" not in config_dict:
            if "debrid_service" in config_dict and "debrid_api_key" in config_dict:
                if not config_dict["debrid_service"] or not config_dict["debrid_api_key"]:
                    api_logger.debug("Missing or empty debrid_service/debrid_api_key")
                    return None

                config_dict["debrid_services"] = [{
                    "service": config_dict["debrid_service"],
                    "api_key": config_dict["debrid_api_key"]
                }]
            else:
                api_logger.debug("Missing debrid configuration")
                return None

        validated = UserConfig.model_validate(config_dict)
        result = validated.model_dump()

        # Merge back any extra fields from the original config (sort_order, early_stop, nzbdav_url, etc.)
        for key, value in config_dict.items():
            if key not in result:
                result[key] = value

        api_logger.debug("Configuration validated successfully")
        return result

    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as e:
        api_logger.debug(f"Config validation failed: {type(e).__name__}")
        return None
    except Exception as e:
        api_logger.debug(f"Config validation failed: {type(e).__name__}")
        return None


# ===========================
# Media Info Extraction
# ===========================
def extract_media_info(content_id: str, content_type: str) -> Dict[str, Optional[str]]:
    """Parse a Stremio content ID into imdb_id/kitsu_id, season, and episode components."""
    content_id_formatted = content_id.replace(".json", "")

    if content_id_formatted.startswith("kitsu:"):
        parts = content_id_formatted.split(":")
        return {
            "kitsu_id": parts[1] if len(parts) > 1 else "",
            "episode": parts[2] if len(parts) > 2 else None,
            "season": "1",
            "imdb_id": None
        }

    if content_type == "series" and ":" in content_id_formatted:
        parts = content_id_formatted.split(":")
        return {
            "imdb_id": parts[0],
            "season": parts[1] if len(parts) > 1 else "1",
            "episode": parts[2] if len(parts) > 2 else "1",
            "kitsu_id": None
        }

    return {
        "imdb_id": content_id_formatted,
        "season": None,
        "episode": None,
        "kitsu_id": None
    }
