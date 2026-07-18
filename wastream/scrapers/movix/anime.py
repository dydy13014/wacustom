from typing import List, Dict, Optional
from wastream.scrapers.movix.base import BaseMovix


# ===========================
# Anime Scraper Class
# ===========================
class AnimeScraper(BaseMovix):

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None, season: Optional[str] = None, episode: Optional[str] = None, config: Optional[Dict] = None) -> List[Dict]:
        results = await self.search_content(title, year, metadata, content_type="anime", season=season, episode=episode, config=config)
        return results


# ===========================
# Singleton Instance
# ===========================
anime_scraper = AnimeScraper()
