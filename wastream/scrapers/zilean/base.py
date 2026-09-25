from typing import List, Dict, Optional

from wastream.config.settings import settings
from wastream.scrapers.torznab.base import BaseTorznab


class ZileanScraper:
    """Zilean expose un vrai endpoint Torznab standard (voir
    docs/Torznab-Indexer.md du projet iPromKnight/zilean), sans authentification
    (AllowAnonymous cote serveur) -- inutile de maintenir un client JSON maison
    separe (/dmm/search, /dmm/filtered) alors que BaseTorznab (deja utilise pour
    C411/Tr4ker/V3X/YggReborn) fait tout : recherche par imdbid, auto-decouverte
    des caps, pagination, parsing des erreurs XML. Seeders/peers renvoyes par
    Zilean valent toujours 999/999 (constante cote serveur, pas une vraie
    donnee de sante du torrent -- a ne pas confondre avec les vrais chiffres
    des autres trackers)."""

    async def search(self, title: str, year: Optional[str] = None, metadata: Optional[Dict] = None,
                     season: Optional[str] = None, episode: Optional[str] = None,
                     config: Optional[Dict] = None) -> List[Dict]:
        if not settings.ZILEAN_URL:
            return []
        scraper = BaseTorznab(
            "Zilean",
            f"{settings.ZILEAN_URL.rstrip('/')}/torznab/api",
            api_key="",
            require_api_key=False,
        )
        return await scraper.search(title, year, metadata, season, episode, config)


zilean_scraper = ZileanScraper()
