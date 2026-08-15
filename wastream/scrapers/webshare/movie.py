from typing import List, Dict, Optional
from wastream.scrapers.webshare.base import BaseWebshare
from wastream.utils.logger import scraper_logger


# ===========================
# Movie Scraper Class
# ===========================
class MovieScraper(BaseWebshare):

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None, config: Optional[Dict] = None) -> List[Dict]:
        scraper_logger.debug(f"[Webshare] Searching movie: '{title}' ({year})")
        results = await self.search_content(title, year, metadata, content_type="movie", config=config)
        return results


# ===========================
# Singleton Instance
# ===========================
movie_scraper = MovieScraper()
