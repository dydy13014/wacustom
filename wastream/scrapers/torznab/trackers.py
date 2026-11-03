from typing import List, Dict, Optional
from wastream.config.settings import settings
from wastream.scrapers.torznab.base import BaseTorznab
from wastream.utils.tracker_keys import resolve_tracker_key

# Cle de chaque tracker : celle de l'utilisateur (config["tracker_api_keys"])
# si elle existe, sinon la cle globale (cf. utils/tracker_keys).


class YggRebornScraper:
    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        api_key, owner = resolve_tracker_key(config, "yggreborn")
        if not api_key or not settings.YGGREBORN_URL:
            return []
        # Pas d'override : les identifiants exacts supportes (imdbid/tmdbid/
        # tvdbid) sont auto-decouverts via ?t=caps et mis en cache (cf.
        # _CapsCache dans base.py) -- YggReborn n'en supporte aucun a ce jour
        # (verifie le 2026-09-25), reste donc naturellement en recherche texte.
        scraper = BaseTorznab("YggReborn", settings.YGGREBORN_URL, api_key, auth_type="query", key_owner=owner)
        return await scraper.search(title, year, metadata, season, episode, config)


class Tr4kerScraper:
    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        api_key, owner = resolve_tracker_key(config, "tr4ker")
        if not api_key or not settings.TR4KER_URL:
            return []
        scraper = BaseTorznab("Tr4ker", settings.TR4KER_URL, api_key, auth_type="query", key_owner=owner)
        return await scraper.search(title, year, metadata, season, episode, config)


class Torr9Scraper:
    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        api_key, owner = resolve_tracker_key(config, "torr9")
        if not api_key or not settings.TORR9_URL:
            return []
        scraper = BaseTorznab("Torr9", settings.TORR9_URL, api_key, auth_type="query", key_owner=owner)
        return await scraper.search(title, year, metadata, season, episode, config)


class V3XScraper:
    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        api_key, owner = resolve_tracker_key(config, "v3x")
        if not api_key or not settings.V3X_URL:
            return []
        scraper = BaseTorznab("V3X", settings.V3X_URL, api_key, auth_type="query", key_owner=owner)
        return await scraper.search(title, year, metadata, season, episode, config)


class C411Scraper:
    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        api_key, owner = resolve_tracker_key(config, "c411")
        if not api_key or not settings.C411_URL:
            return []
        scraper = BaseTorznab("C411", settings.C411_URL, api_key, auth_type="query", key_owner=owner)
        return await scraper.search(title, year, metadata, season, episode, config)


yggreborn_scraper = YggRebornScraper()
tr4ker_scraper = Tr4kerScraper()
torr9_scraper = Torr9Scraper()
c411_scraper = C411Scraper()
v3x_scraper = V3XScraper()


# ===========================
# Trackers UNIT3D (API JSON native, pas Torznab)
# ===========================
from wastream.scrapers.unit3d.base import BaseUnit3d


class GeminiScraper:
    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        api_key, _ = resolve_tracker_key(config, "gemini")
        if not api_key or not settings.GEMINI_URL:
            return []
        scraper = BaseUnit3d("Gemini", settings.GEMINI_URL, api_key)
        return await scraper.search(title, year, metadata, season, episode, config)


class GenerationFreeScraper:
    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        api_key, _ = resolve_tracker_key(config, "generation-free")
        if not api_key or not settings.GENERATIONFREE_URL:
            return []
        scraper = BaseUnit3d("Generation-Free", settings.GENERATIONFREE_URL, api_key)
        return await scraper.search(title, year, metadata, season, episode, config)


gemini_scraper = GeminiScraper()
generationfree_scraper = GenerationFreeScraper()
