from typing import List, Dict, Optional
from wastream.scrapers.zone_telechargement.base import BaseZoneTelechargement
from wastream.utils.logger import scraper_logger


# ===========================
# Series Scraper Class
# ===========================
class SeriesScraper(BaseZoneTelechargement):

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None) -> List[Dict]:
        scraper_logger.debug(f"[Zone-Telechargement] Searching series: '{title}' ({year})")
        results = await self.search_content(title, year, metadata, "series", season, episode)
        return results


# ===========================
# Singleton Instance
# ===========================
series_scraper = SeriesScraper()
