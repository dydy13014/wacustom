from typing import Optional, List, Dict, Any
from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    # ===========================
    # Addon Customization
    # ===========================
    ADDON_ID: str = "community.wastream"
    ADDON_NAME: str = "WAStream"

    # ===========================
    # Server Configuration
    # ===========================
    PORT: int = 7000

    # ===========================
    # Source Configuration
    # ===========================
    WAWACITY_URL: Optional[str] = None
    FREE_TELECHARGER_URL: Optional[str] = None
    DARKI_API_URL: Optional[str] = None
    DARKI_API_KEY: Optional[str] = None
    MOVIX_URL: Optional[str] = None
    WEBSHARE_URL: Optional[str] = None
    DARKIMOVIX_KITSU_TMDB_MAPPING: List[str] = ["tt0388629"]
    KITSU_IMDB_OVERRIDE: List[str] = ["6589=1,8174=2,13893=3,42213=4-1,42927=4-2:tt2250192"]

    # ===========================
    # Pagination Configuration
    # ===========================
    WAWACITY_MAX_SEARCH_PAGES: int = 3
    FREE_TELECHARGER_MAX_SEARCH_PAGES: int = 3
    WEBSHARE_MAX_SEARCH_PAGES: int = 3
    DARKI_API_MAX_LINK_PAGES: int = 5
    DARKIBOX_LINK_TIMEOUT: int = 2

    # ===========================
    # Database Configuration
    # ===========================
    DATABASE_VERSION: str = "1.0"
    DATABASE_TYPE: str = "sqlite"
    DATABASE_PATH: str = "/app/data/wastream.db"
    DATABASE_URL: str = ""

    # ===========================
    # Cache Configuration
    # ===========================
    CONTENT_CACHE_TTL: int = 3600
    CONTENT_CACHE_MODE: str = "background"
    DEAD_LINK_TTL: int = -1
    HOSTER_STATUS_CACHE_TTL: int = 3600
    HOSTER_STATUS_RECHECK_THRESHOLD: int = 3

    # ===========================
    # Lock Configuration
    # ===========================
    SCRAPE_LOCK_TTL: int = 300
    SCRAPE_WAIT_TIMEOUT: int = 30

    # ===========================
    # HTTP Timeout Configuration
    # ===========================
    HTTP_TIMEOUT: int = 15
    METADATA_TIMEOUT: int = 10
    HEALTH_CHECK_TIMEOUT: int = 5

    # ===========================
    # Debrid Services Configuration
    # ===========================
    DEBRID_SERVICES: List[str] = ["alldebrid", "torbox", "premiumize", "1fichier", "nzbdav"]
    DEBRID_MAX_RETRIES: int = 5
    DEBRID_RETRY_DELAY_SECONDS: int = 4
    STREAM_REQUEST_TIMEOUT: int = 20
    DEBRID_CACHE_CHECK_HTTP_TIMEOUT: int = 3
    DEBRID_HTTP_ERROR_MAX_RETRIES: int = 5
    DEBRID_HTTP_ERROR_RETRY_DELAY: int = 1

    # ===========================
    # AllDebrid Configuration
    # ===========================
    ALLDEBRID_API_URL: str = "https://api.alldebrid.com/v4"
    ALLDEBRID_BATCH_SIZE: int = 12
    ALLDEBRID_SUPPORTED_HOSTS: List[str] = ["1fichier", "turbobit", "rapidgator", "vidoza", "alldebrid", "torrent"]
    ALLDEBRID_SUPPORTED_SOURCES: List[str] = ["wawacity", "free-telecharger", "darki-api", "wasource", "movix", "yggreborn", "tr4ker", "torr9", "c411", "gemini", "generation-free", "zilean", "nyaa"]

    # ===========================
    # TorBox Configuration
    # ===========================
    TORBOX_API_URL: str = "https://api.torbox.app/v1/api"
    TORBOX_SUPPORTED_HOSTS: List[str] = ["1fichier", "turbobit", "rapidgator", "sendcm", "darkibox", "webshare"]
    TORBOX_SUPPORTED_SOURCES: List[str] = ["darki-api", "free-telecharger", "wasource", "movix", "webshare", "yggreborn", "tr4ker", "torr9", "c411", "gemini", "generation-free", "zilean", "nyaa"]

    # ===========================
    # Premiumize Configuration
    # ===========================
    PREMIUMIZE_API_URL: str = "https://www.premiumize.me/api"
    PREMIUMIZE_SUPPORTED_HOSTS: List[str] = ["1fichier", "turbobit", "rapidgator"]
    PREMIUMIZE_SUPPORTED_SOURCES: List[str] = ["darki-api", "free-telecharger", "wasource", "movix", "yggreborn", "tr4ker", "torr9", "c411", "gemini", "generation-free", "zilean", "nyaa"]

    # ===========================
    # 1fichier Configuration
    # ===========================
    ONEFICHIER_API_URL: str = "https://api.1fichier.com/v1"
    ONEFICHIER_SUPPORTED_HOSTS: List[str] = ["1fichier"]
    ONEFICHIER_SUPPORTED_SOURCES: List[str] = ["darki-api", "free-telecharger", "wasource", "movix"]

    # ===========================
    # NZBDav Configuration
    # ===========================
    NZBDAV_SUPPORTED_SOURCES: List[str] = ["darki-api"]

    # ===========================
    # WASource Configuration
    # ===========================
    WASOURCE_SUPPORTED_HOSTS: List[str] = ["1fichier", "turbobit", "rapidgator", "sendcm", "darkibox", "alldebrid"]

    # ===========================
    # Tracker Configuration (Torznab)
    # ===========================
    YGGREBORN_URL: Optional[str] = "https://www.yggreborn.org/api"
    YGGREBORN_API_KEY: Optional[str] = None
    TR4KER_URL: Optional[str] = "https://tr4ker.net/api"
    TR4KER_API_KEY: Optional[str] = None
    TORR9_URL: Optional[str] = "https://api.torr9.net/api/v1/torznab"
    TORR9_API_KEY: Optional[str] = None
    C411_URL: Optional[str] = "https://c411.org/api"
    C411_API_KEY: Optional[str] = None

    # ===========================
    # Tracker Configuration (UNIT3D — API JSON native)
    # ===========================
    GEMINI_URL: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    GENERATIONFREE_URL: Optional[str] = None
    GENERATIONFREE_API_KEY: Optional[str] = None

    # ===========================
    # Zilean Configuration (index DMM, pas d'API key)
    # ===========================
    ZILEAN_URL: Optional[str] = None

    # ===========================
    # Nyaa Configuration (tracker public anime, pas d'API key)
    # ===========================
    NYAA_URL: Optional[str] = "https://nyaa.si"

    # ===========================
    # Pastebin Scraper Configuration
    # ===========================
    PASTEBIN_SCRAPER_URLS: List[str] = []
    PASTEBIN_SCRAPER_INTERVAL: int = 86400

    # ===========================
    # TMDB Configuration
    # ===========================
    TMDB_API_URL: str = "https://api.themoviedb.org/3"
    TMDB_API_KEY: Optional[str] = None

    # ===========================
    # Kitsu Configuration
    # ===========================
    KITSU_API_URL: str = "https://kitsu.io/api/edge"
    KITSU_ALIAS_URL: str = "https://find-my-anime.dtimur.de/api"

    # ===========================
    # Proxy Configuration
    # ===========================
    PROXY_URL: Optional[str] = None

    # ===========================
    # Security Configuration
    # ===========================
    SECRET_KEY: str = ""
    ADDON_PASSWORD: str = ""

    # ===========================
    # Admin Configuration
    # ===========================
    ADMIN_PASSWORD: str = ""

    @field_validator("SECRET_KEY", mode="after")
    @classmethod
    def validate_secret_key(cls, v):
        if not v or len(v) < 32:
            raise ValueError("SECRET_KEY is required and must be at least 32 characters. Generate one with: openssl rand -hex 32")
        return v

    # ===========================
    # Logging Configuration
    # ===========================
    LOG_LEVEL: str = "DEBUG"

    # ===========================
    # Interface Customization
    # ===========================
    CUSTOM_HTML: str = ""

    # ===========================
    # HTTP Cache Configuration
    # ===========================
    HTTP_CACHE_ENABLED: bool = False
    HTTP_CACHE_STREAMS_TTL: int = 300
    HTTP_CACHE_MANIFEST_TTL: int = 86400
    HTTP_CACHE_CONFIGURE_TTL: int = 86400
    HTTP_CACHE_STALE_WHILE_REVALIDATE: int = 60

    # ===========================
    # Internal Configuration
    # ===========================
    CLEANUP_INTERVAL: int = 60
    HEALTH_CHECK_INTERVAL: int = 60

    # ===========================
    # Field Validators
    # ===========================
    @field_validator("WAWACITY_URL", "FREE_TELECHARGER_URL", "DARKI_API_URL", "MOVIX_URL", "WEBSHARE_URL", "PROXY_URL", "YGGREBORN_URL", "TR4KER_URL", "TORR9_URL", "C411_URL", "GEMINI_URL", "GENERATIONFREE_URL", "ZILEAN_URL", "NYAA_URL")
    @classmethod
    def normalize_urls(cls, v):
        if isinstance(v, str):
            val = v.strip()
            if " #" in val:
                val = val.split(" #")[0].strip()
            if not val or val.startswith("#") or val.startswith(";"):
                return None
            return val.rstrip("/")
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def normalize_log_level(cls, v):
        if isinstance(v, str):
            return v.upper()
        return v

    # ===========================
    # Computed Properties
    # ===========================
    @computed_field
    @property
    def MOVIX_API_URL(self) -> Optional[str]:
        if not self.MOVIX_URL:
            return None
        from urllib.parse import urlparse
        parsed = urlparse(self.MOVIX_URL)
        return f"{parsed.scheme}://api.{parsed.netloc}"

    @computed_field
    @property
    def ADDON_MANIFEST(self) -> Dict[str, Any]:
        return {
            "id": self.ADDON_ID,
            "name": self.ADDON_NAME,
            "version": "3.6.3",
            "description": "Stremio addon to convert DDL to streams via debrid services",
            "catalogs": [],
            "resources": ["stream"],
            "types": ["movie", "series", "anime"],
            "idPrefixes": ["tt", "kitsu"],
            "behaviorHints": {
                "configurable": True
            },
            "logo": "https://gitlab.com/10ho/wastream/-/raw/main/wastream/public/wastream-logo.jpg",
            "background": "https://gitlab.com/10ho/wastream/-/raw/main/wastream/public/wastream-background.png"
        }

    def get_database_url(self) -> str:
        if self.DATABASE_TYPE == "sqlite":
            return f"sqlite:///{self.DATABASE_PATH}"
        return f"postgresql://{self.DATABASE_URL}"


# ===========================
# Settings Instance
# ===========================
settings = Settings()


# ===========================
# Constants
# ===========================
DEBRID_ABBREVIATIONS = {
    "alldebrid": "AD",
    "torbox": "TB",
    "premiumize": "PM",
    "1fichier": "1F",
    "nzbdav": "ND"
}

SOURCE_DISPLAY_NAMES = {
    "wawacity": "Wawacity",
    "free-telecharger": "Free-Telecharger",
    "darki-api": "Darki-API",
    "wasource": "WASource",
    "movix": "Movix",
    "webshare": "Webshare",
    "yggreborn": "YggReborn",
    "tr4ker": "Tr4ker",
    "torr9": "Torr9",
    "c411": "C411",
    "gemini": "Gemini",
    "generation-free": "Generation-Free",
    "zilean": "Zilean",
    "nyaa": "Nyaa"
}
