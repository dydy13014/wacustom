from typing import Optional, List, Dict, Any
from pydantic import AliasChoices, Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ===========================
# Versions
# ===========================
# Version du fork, indépendante de celle d'upstream : c'est elle qui identifie
# ce qui tourne réellement (nos sources, nos correctifs), et elle avance à un
# rythme qui n'est pas celui de WAStream.
WACUSTOM_VERSION = "1.1.1"

# Version WAStream servant de base au fork. Mise à jour uniquement lors d'un
# rebase sur une nouvelle version upstream — sert à savoir d'où l'on part quand
# on compare un comportement avec le projet d'origine.
WASTREAM_BASE_VERSION = "3.8.2"


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        validate_assignment=True
    )

    # ===========================
    # Addon Customization
    # ===========================
    ADDON_ID: str = "community.wastream"
    ADDON_NAME: str = "Wacustom"

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
    ZONE_TELECHARGEMENT_URL: Optional[str] = None
    DARKIMOVIX_KITSU_TMDB_MAPPING: List[str] = []
    KITSU_IMDB_OVERRIDE: List[str] = []

    # ===========================
    # Pagination Configuration
    # ===========================
    WAWACITY_MAX_SEARCH_PAGES: int = 3
    FREE_TELECHARGER_MAX_SEARCH_PAGES: int = 3
    WEBSHARE_MAX_SEARCH_PAGES: int = 3
    ZONE_TELECHARGEMENT_MAX_SEARCH_PAGES: int = 3
    ZONE_TELECHARGEMENT_MAX_CONCURRENCY: int = 16
    DARKI_API_MAX_LINK_PAGES: int = 5
    DARKIBOX_LINK_TIMEOUT: int = 2

    # ===========================
    # Database Configuration
    # ===========================
    DATABASE_VERSION: str = "1.0"
    DATABASE_TYPE: str = "sqlite"
    DATABASE_PATH: str = "/app/data/wastream.db"
    DATABASE_URL: str = ""
    DATABASE_BUSY_TIMEOUT_SECONDS: int = 30
    DATABASE_RETRY_MAX_ATTEMPTS: int = 8
    DATABASE_RETRY_DELAY_SECONDS: float = 0.25

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
    # Duree de vie d'un verrou de scrape. Sert de filet si un scrape meurt
    # sans liberer : au-dela, le verrou est considere perime. 5 min etait bien
    # plus long qu'un scrape reel, donc un verrou orphelin faisait travailler
    # tout le monde en double pendant tout ce temps.
    SCRAPE_LOCK_TTL: int = 60
    # Attente max d'un scrape concurrent sur le meme contenu. Doit rester sous
    # le timeout du client appelant (un agregateur type AIOStreams abandonne
    # souvent en 7-20s) : au-dela, on attend un verrou plus longtemps que
    # l'appelant ne patiente, et il repart avec zero flux alors que le scrape
    # aboutit quand meme. Passe de 30 a 8s (2026-07-25) apres une panne reelle
    # ou un tracker en 502 a fait expirer 20 requetes sur 20.
    SCRAPE_WAIT_TIMEOUT: int = 8

    # Seconde chance sur erreur serveur d'un tracker Torznab (cf. scrapers/
    # torznab/base.py). Assez court pour rester sous le timeout de l'appelant.
    TORZNAB_RETRY_DELAY: float = 0.5

    # (EXTRA_SOURCE_MAX_RESULTS retire le 2026-07-27 : conditionner la source
    # d'appoint au NOMBRE de resultats etait une erreur de conception. Elle
    # n'apporte que des torrents deja verifies en cache, donc lisibles tout de
    # suite ; or une recherche peut remonter 80 flux dont aucun ne demarre.
    # Elle tourne desormais en parallele des autres sources, sur chaque
    # recherche, et se protege du quota par sa pause de 24h persistee.)

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
    ALLDEBRID_SUPPORTED_SOURCES: List[str] = ["wawacity", "free-telecharger", "darki-api", "wasource", "movix", "zone-telechargement", "yggreborn", "tr4ker", "torr9", "c411", "v3x", "gemini", "generation-free", "zilean", "nyaa", "lumio"]

    # ===========================
    # TorBox Configuration
    # ===========================
    TORBOX_API_URL: str = "https://api.torbox.app/v1/api"
    TORBOX_SUPPORTED_HOSTS: List[str] = ["1fichier", "turbobit", "rapidgator", "sendcm", "send.now", "darkibox", "webshare"]
    TORBOX_SUPPORTED_SOURCES: List[str] = ["darki-api", "free-telecharger", "wasource", "movix", "webshare", "zone-telechargement", "yggreborn", "tr4ker", "torr9", "c411", "v3x", "gemini", "generation-free", "zilean", "nyaa", "lumio"]

    # ===========================
    # Premiumize Configuration
    # ===========================
    PREMIUMIZE_API_URL: str = "https://www.premiumize.me/api"
    PREMIUMIZE_SUPPORTED_HOSTS: List[str] = ["1fichier", "turbobit", "rapidgator"]
    PREMIUMIZE_SUPPORTED_SOURCES: List[str] = ["darki-api", "free-telecharger", "wasource", "movix", "zone-telechargement", "yggreborn", "tr4ker", "torr9", "c411", "v3x", "gemini", "generation-free", "zilean", "nyaa", "lumio"]

    # ===========================
    # 1fichier Configuration
    # ===========================
    ONEFICHIER_API_URL: str = "https://api.1fichier.com/v1"
    ONEFICHIER_SUPPORTED_HOSTS: List[str] = ["1fichier"]
    ONEFICHIER_SUPPORTED_SOURCES: List[str] = ["darki-api", "free-telecharger", "wasource", "movix", "zone-telechargement"]

    # ===========================
    # NZBDav Configuration
    # ===========================
    NZBDAV_SUPPORTED_SOURCES: List[str] = ["darki-api"]

    # ===========================
    # WASource Configuration
    # ===========================
    WASOURCE_SUPPORTED_HOSTS: List[str] = ["1fichier", "turbobit", "rapidgator", "sendcm", "darkibox", "alldebrid"]

    # ===========================
    # Resilient Playback Configuration
    # ===========================
    RESILIENT_MAX_FALLBACKS: int = 10
    RESILIENT_MAX_CONCURRENCY: int = 4
    RESILIENT_ATTEMPT_TIMEOUT_MAX: int = 60
    RESILIENT_TOKEN_MAX_BYTES: int = 3500

    # ===========================
    # Tracker Configuration (Torznab)
    # ===========================
    YGGREBORN_URL: Optional[str] = None
    YGGREBORN_API_KEY: Optional[str] = None
    TR4KER_URL: Optional[str] = None
    TR4KER_API_KEY: Optional[str] = None
    TORR9_URL: Optional[str] = None
    TORR9_API_KEY: Optional[str] = None
    C411_URL: Optional[str] = None
    C411_API_KEY: Optional[str] = None
    V3X_URL: Optional[str] = None
    V3X_API_KEY: Optional[str] = None

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
    # Lumio Configuration
    # ===========================
    # Identifiant de manifest Lumio (mylumio.tv), propre a chaque hebergeur.
    # Vide = source desactivee. Ne fournit que des torrents deja verifies en
    # cache debrid, donc lisibles immediatement.
    LUMIO_MANIFEST_ID: Optional[str] = None
    # Pause apres un refus de quota (429). Le blocage de Lumio est global et
    # chaque appel emis pendant le blocage le prolonge : une pause courte est
    # pire que rien.
    LUMIO_RATE_LIMIT_PAUSE: int = 86400

    # ===========================
    # Nyaa Configuration (tracker public anime, pas d'API key)
    # ===========================
    NYAA_URL: Optional[str] = "https://nyaa.si"

    # ===========================
    # Pastebin Scraper Configuration
    # ===========================
    PASTEBIN_SCRAPER_URLS: List[str] = []
    PASTEBIN_SCRAPER_INTERVAL: int = 86400
    PASTEBIN_SCRAPER_MAX_DEPTH: int = 5
    PASTEBIN_SCRAPER_MAX_PAGES: int = 1000

    # ===========================
    # Idrix Scraper Configuration
    # ===========================
    IDRIX_SCRAPER_URLS: List[str] = []
    IDRIX_SCRAPER_INTERVAL: int = 86400
    IDRIX_SCRAPER_MAX_DEPTH: int = 5
    IDRIX_SCRAPER_MAX_PAGES: int = 1000
    IDRIX_SCRAPER_RETRY_MAX_ATTEMPTS: int = 3
    IDRIX_SCRAPER_RETRY_DELAY_SECONDS: int = 2
    IDRIX_SCRAPER_REQUEST_DELAY_SECONDS: float = 0.15
    IDRIX_SCRAPER_MAX_RETRY_DELAY_SECONDS: float = 30.0

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
    # Domain Synchronization
    # ===========================
    DOMAIN_SYNC_ENABLED: bool = False
    DOMAIN_SYNC_INTERVAL: int = 7200
    DOMAIN_SYNC_RECHECK_ON_HEALTH_ERROR: bool = True
    DOMAIN_SYNC_HEALTH_ERROR_RECHECK_DELAY_SECONDS: int = 900
    DOMAIN_SYNC_WAWACITY_TELEGRAM_URL: Optional[str] = None
    DOMAIN_SYNC_FREE_TELECHARGER_TELEGRAM_URL: Optional[str] = None
    DOMAIN_SYNC_MOVIX_TELEGRAM_URL: Optional[str] = None
    DOMAIN_SYNC_ZONE_TELECHARGEMENT_TELEGRAM_URL: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "DOMAIN_SYNC_ZONE_TELECHARGEMENT_TELEGRAM_URL",
            "DOMAIN_SYNC_ZT_TELEGRAM_URL",
        ),
    )

    # ===========================
    # Field Validators
    # ===========================
    @field_validator(
        "WAWACITY_URL", "FREE_TELECHARGER_URL", "DARKI_API_URL", "MOVIX_URL",
        "WEBSHARE_URL", "ZONE_TELECHARGEMENT_URL", "PROXY_URL",
        "DOMAIN_SYNC_WAWACITY_TELEGRAM_URL", "DOMAIN_SYNC_FREE_TELECHARGER_TELEGRAM_URL",
        "DOMAIN_SYNC_MOVIX_TELEGRAM_URL", "DOMAIN_SYNC_ZONE_TELECHARGEMENT_TELEGRAM_URL",
        # Nos sources maison (trackers Torznab/UNIT3D, Zilean, Nyaa)
        "YGGREBORN_URL", "TR4KER_URL", "TORR9_URL", "C411_URL", "V3X_URL",
        "GEMINI_URL", "GENERATIONFREE_URL", "ZILEAN_URL", "NYAA_URL"
    )
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
            "version": WACUSTOM_VERSION,
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
    "zone-telechargement": "Zone-Telechargement",
    # ⚠️ Toute source doit figurer ici avec la casse EXACTE de son champ
    # "source" : sinon _check_cache_and_enrich la filtre silencieusement après
    # le scraping (0 résultat malgré un scraper qui fonctionne).
    "yggreborn": "YggReborn",
    "tr4ker": "Tr4ker",
    "torr9": "Torr9",
    "c411": "C411",
    "v3x": "V3X",
    "gemini": "Gemini",
    "generation-free": "Generation-Free",
    "zilean": "Zilean",
    "nyaa": "Nyaa",
    "lumio": "Lumio"
}
