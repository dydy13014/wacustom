from typing import List, Dict, Optional
from wastream.scrapers.webshare.base import BaseWebshare
from wastream.utils.logger import scraper_logger


# ===========================
# Anime Scraper Class
# ===========================
class AnimeScraper(BaseWebshare):

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None, season: Optional[str] = None, episode: Optional[str] = None, config: Optional[Dict] = None) -> List[Dict]:
        scraper_logger.debug(f"[Webshare] Searching anime: '{title}' S{season}E{episode}")
        results = await self.search_content(title, year, metadata, content_type="anime", season=season, episode=episode, config=config)
        return results


# ===========================
# Singleton Instance
# ===========================
anime_scraper = AnimeScraper()
