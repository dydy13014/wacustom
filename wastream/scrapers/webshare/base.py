import asyncio
import math
import re
from typing import List, Dict, Optional
from xml.etree import ElementTree

from wastream.config.settings import settings
from wastream.utils.helpers import (
    tokenize_filename, extract_quality_from_tokens, extract_language_from_tokens,
    extract_raw_language_from_tokens, normalize_size, build_display_name, deduplicate_and_sort_results
)
from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger
from wastream.utils.quality import quality_sort_key


# ===========================
# Constants
# ===========================
WEBSHARE_PAGE_SIZE = 100


# ===========================
# Base Webshare Client Class
# ===========================
class BaseWebshare:

    def _parse_xml_response(self, text: str) -> Optional[ElementTree.Element]:
        try:
            return ElementTree.fromstring(text)
        except ElementTree.ParseError:
            return None

    def _parse_files_from_xml(self, root: ElementTree.Element) -> List[Dict]:
        files = []
        for file_el in root.findall("file"):
            ident = file_el.findtext("ident", "")
            name = file_el.findtext("name", "")
            size = file_el.findtext("size", "0")
            password = file_el.findtext("password", "0")

            if not ident or not name or password == "1":
                continue

            files.append({
                "ident": ident,
                "name": name,
                "size": int(size) if size.isdigit() else 0,
            })
        return files

    def _verify_file(self, tokens: List[str], title_words: List[str], year: str, season: Optional[str] = None, episode: Optional[str] = None) -> bool:
        for word in title_words:
            if word not in tokens:
                return False

        if season and episode:
            season_padded = str(season).zfill(2)
            episode_padded = str(episode).zfill(2)
            pattern = f"s{season_padded}e{episode_padded}"
            if pattern not in tokens:
                joined = "".join(tokens)
                if pattern not in joined:
                    return False
        else:
            if year and year not in tokens:
                return False

        return True

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes <= 0:
            return "Unknown"

        gb = size_bytes / (1024 * 1024 * 1024)
        if gb >= 1:
            raw_size = f"{gb:.2f} GB"
        else:
            mb = size_bytes / (1024 * 1024)
            raw_size = f"{mb:.0f} MB"

        return normalize_size(raw_size)

    async def _search_page(self, query: str, offset: int = 0) -> tuple:
        try:
            response = await http_client.post(
                f"{settings.WEBSHARE_URL}/api/search/",
                data={"what": query, "sort": "rating", "category": "video", "offset": str(offset), "limit": "100"}
            )

            if response.status_code != 200:
                scraper_logger.debug(f"[Webshare] Search HTTP {response.status_code}")
                return [], 0

            root = self._parse_xml_response(response.text)
            if root is None or root.findtext("status") != "OK":
                scraper_logger.debug("[Webshare] Search response error")
                return [], 0

            total = int(root.findtext("total", "0"))
            files = self._parse_files_from_xml(root)
            return files, total

        except Exception as e:
            scraper_logger.error(f"[Webshare] Search failed: {type(e).__name__}: {e}")
            return [], 0

    async def _search_all_pages(self, query: str) -> List[Dict]:
        files, total = await self._search_page(query, offset=0)
        if not files:
            return []

        max_pages_available = math.ceil(total / WEBSHARE_PAGE_SIZE)
        max_pages = min(settings.WEBSHARE_MAX_SEARCH_PAGES, max_pages_available)

        scraper_logger.debug(f"[Webshare] Total: {total} results, fetching {max_pages} pages")

        all_files = list(files)

        for page in range(1, max_pages):
            offset = page * WEBSHARE_PAGE_SIZE
            page_files, _ = await self._search_page(query, offset=offset)
            if not page_files:
                break
            all_files.extend(page_files)

        return all_files

    def _format_results(self, files: List[Dict], title: str, year: Optional[str], title_words: List[str],
                        season: Optional[str] = None, episode: Optional[str] = None) -> List[Dict]:
        is_series = season is not None and episode is not None
        results = []

        for file_data in files:
            tokens = tokenize_filename(file_data["name"])

            if not self._verify_file(tokens, title_words, year, season if is_series else None, episode if is_series else None):
                continue

            quality = extract_quality_from_tokens(tokens)
            language = extract_language_from_tokens(tokens)
            raw_language = extract_raw_language_from_tokens(tokens)
            size = self._format_size(file_data["size"])
            ident = file_data["ident"]

            if is_series:
                display_name = build_display_name(
                    title=title,
                    year=year,
                    language=language,
                    quality=quality,
                    season=season,
                    episode=episode,
                    raw_language=raw_language
                )
            else:
                display_name = file_data["name"]

            result = {
                "link": f"{settings.WEBSHARE_URL}/#/file/{ident}/",
                "quality": quality,
                "language": language,
                "raw_language": raw_language,
                "source": "Webshare",
                "hoster": "Webshare",
                "size": size,
                "display_name": display_name,
                "model_type": "link"
            }

            if is_series:
                result["season"] = str(season)
                result["episode"] = str(episode)

            results.append(result)

        return results

    async def search_content(self, title: str, year: Optional[str] = None,
                             metadata: Optional[Dict] = None, content_type: str = "movie",
                             season: Optional[str] = None, episode: Optional[str] = None,
                             config: Optional[Dict] = None) -> List[Dict]:
        if not settings.WEBSHARE_URL:
            scraper_logger.error("[Webshare] settings.WEBSHARE_URL not configured")
            return []

        content_names = {"movie": "movie", "series": "series", "anime": "anime"}
        content_name = content_names.get(content_type, "content")

        if season and episode:
            scraper_logger.debug(f"[Webshare] Searching {content_name}: '{title}' S{season}E{episode}")
        else:
            scraper_logger.debug(f"[Webshare] Searching {content_name}: '{title}' ({year})")

        search_titles = []
        if metadata and metadata.get("original_titles"):
            for t in metadata["original_titles"]:
                if t not in search_titles:
                    search_titles.append(t)
        elif metadata and metadata.get("titles"):
            for t in metadata["titles"]:
                if t not in search_titles:
                    search_titles.append(t)

        if title not in search_titles and title.lower() not in [t.lower() for t in search_titles]:
            search_titles.append(title)

        if metadata and metadata.get("cz_title"):
            cz = metadata["cz_title"]
            if cz.lower() not in [t.lower() for t in search_titles]:
                search_titles.append(cz)
                scraper_logger.debug(f"[Webshare] Added Czech title: '{cz}'")

        is_series = season is not None and episode is not None
        if is_series:
            season_padded = str(season).zfill(2)
            episode_padded = str(episode).zfill(2)

        queries = []
        for search_title in search_titles:
            if is_series:
                query = f"{search_title} S{season_padded}E{episode_padded}"
            else:
                if not year:
                    scraper_logger.debug(f"[Webshare] No year for '{search_title}', skipping")
                    continue
                query = f"{search_title} {year}"
            queries.append((search_title, query))

        if not queries:
            scraper_logger.debug(f"[Webshare] No {content_name} queries to run for '{title}'")
            return []

        scraper_logger.debug(f"[Webshare] Searching {len(queries)} title variants in parallel")
        search_results = await asyncio.gather(*[self._search_all_pages(q) for _, q in queries])

        all_results = []
        for (search_title, query), files in zip(queries, search_results):
            if not files:
                scraper_logger.debug(f"[Webshare] No results for '{query}'")
                continue

            title_words = re.split(r"[^\w]+", search_title.lower())
            title_words = [w for w in title_words if w]

            results = self._format_results(files, title, year, title_words,
                                           season if is_series else None, episode if is_series else None)

            if results:
                scraper_logger.debug(f"[Webshare] {len(results)} verified results for '{query}'")
                all_results.extend(results)
            else:
                scraper_logger.debug(f"[Webshare] No verified results for '{query}'")

        if all_results:
            all_results = deduplicate_and_sort_results(all_results, quality_sort_key)
            scraper_logger.debug(f"[Webshare] Found {len(all_results)} total {content_name} results for '{title}'")
            return all_results

        scraper_logger.debug(f"[Webshare] No {content_name} found for any title variant of '{title}'")
        return []
