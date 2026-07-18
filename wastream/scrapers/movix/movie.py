from typing import List, Dict, Optional
from wastream.scrapers.movix.base import BaseMovix


# ===========================
# Movie Scraper Class
# ===========================
class MovieScraper(BaseMovix):

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None, config: Optional[Dict] = None) -> List[Dict]:
        results = await self.search_content(title, year, metadata, content_type="movie", config=config)
        return results


# ===========================
# Singleton Instance
# ===========================
movie_scraper = MovieScraper()
