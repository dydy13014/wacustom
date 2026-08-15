import asyncio
import html
import re
from typing import List, Dict, Optional
from urllib.parse import parse_qs, urlparse

from selectolax.parser import HTMLParser, Node

from wastream.config.settings import settings
from wastream.utils.helpers import normalize_text, normalize_size, format_url, build_display_name
from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger
from wastream.utils.quality import quality_sort_key
from wastream.utils.release_parser import parse_release_info
from wastream.utils.urls import canonicalize_url

# ===========================
# Constants
# ===========================
CONTENT_NAME_MAPPING = {"movies": "movie", "films": "movie", "series": "series", "anime": "anime"}


# ===========================
# Base Zone-Telechargement Scraper Class
# ===========================
class BaseZoneTelechargement:
    _zoneurs_semaphores: Dict = {}

    async def search_content_by_titles(self, title: str, year: Optional[str], metadata: Optional[Dict],
                                       content_type: str, page_cache: Dict) -> Optional[Dict]:
        titles = metadata["titles"] if metadata and metadata.get("titles") else [title]
        titles_to_try = list(dict.fromkeys(t for t in titles if t))
        expected_year = metadata.get("year") if metadata and metadata.get("year") else year
        fallback_results: Dict[str, Dict] = {}

        for search_title in titles_to_try:
            result = await self.try_search_with_title(
                search_title, expected_year, metadata, content_type, page_cache, fallback_results
            )
            if result:
                return result

        if fallback_results:
            fallback = next(iter(fallback_results.values()))
            scraper_logger.debug(
                f"[Zone-Telechargement] Exact title fallback without matching year: {fallback['link']}"
            )
            return fallback

        content_name = CONTENT_NAME_MAPPING.get(content_type, "content")
        scraper_logger.debug(f"[Zone-Telechargement] No {content_name} found for any title variants of '{title}'")
        return None

    async def try_search_with_title(self, search_title: str, year: Optional[str], metadata: Optional[Dict],
                                    content_type: str, page_cache: Dict,
                                    fallback_results: Dict[str, Dict]) -> Optional[Dict]:
        result_urls = await self._run_filter(search_title, content_type, 0)
        if not result_urls:
            scraper_logger.debug(f"[Zone-Telechargement] No results for '{search_title}'")
            return None
        result = await self.verify_content_results(
            result_urls, metadata, search_title, year, content_type, page_cache, fallback_results
        )
        if result:
            return result

        for page in range(1, max(1, settings.ZONE_TELECHARGEMENT_MAX_SEARCH_PAGES)):
            result = await self.try_page_verification(
                search_title, year, metadata, page, content_type, page_cache, fallback_results
            )
            if result:
                return result
        return None

    async def verify_content_results(self, result_urls: List[str], metadata: Optional[Dict],
                                     search_title: str, year: Optional[str], content_type: str,
                                     page_cache: Dict,
                                     fallback_results: Optional[Dict[str, Dict]] = None) -> Optional[Dict]:
        if metadata and metadata.get("all_titles"):
            tmdb_titles = [normalize_text(t) for t in metadata["all_titles"]]
        elif metadata and metadata.get("titles"):
            tmdb_titles = [normalize_text(t) for t in metadata["titles"]]
        else:
            tmdb_titles = [normalize_text(search_title)]
        tmdb_titles = [t for t in tmdb_titles if t]

        for url in result_urls:
            page_html = await self._fetch(url, page_cache)
            if not page_html:
                continue
            content_data = self._extract_content_identity(page_html, content_type)
            if not content_data or content_data.get("title") not in tmdb_titles:
                continue
            candidate = {"link": url, "text": content_data["title"]}
            if self.progressive_verification_from_search(
                content_data, tmdb_titles, str(year) if year else None
            ):
                scraper_logger.debug(f"[Zone-Telechargement] Anchor (année {year} confirmée): {url}")
                return candidate
            if fallback_results is not None and url not in fallback_results:
                fallback_results[url] = candidate

        return None

    async def try_page_verification(self, search_title: str, year: Optional[str], metadata: Optional[Dict],
                                    page: int, content_type: str, page_cache: Dict,
                                    fallback_results: Dict[str, Dict]) -> Optional[Dict]:
        result_urls = await self._run_filter(search_title, content_type, page)
        if not result_urls:
            return None
        return await self.verify_content_results(
            result_urls, metadata, search_title, year, content_type, page_cache, fallback_results
        )

    @staticmethod
    def _extract_content_identity(page_html: str, content_type: str) -> Optional[Dict]:
        parser = HTMLParser(page_html)
        page_title = None
        for node in parser.css("div[style]"):
            style = re.sub(r"\s+", "", node.attributes.get("style", "").lower())
            if "font-size:24px" in style:
                page_title = node.text(strip=True)
                if page_title:
                    break
        if not page_title:
            node = parser.css_first("[data-title]")
            page_title = node.attributes.get("data-title", "").strip() if node else ""
        if not page_title:
            return None

        normalized_title = normalize_text(page_title)
        if CONTENT_NAME_MAPPING.get(content_type) != "movie":
            normalized_title = re.sub(r"\s+saison\s+\d+\b.*$", "", normalized_title, flags=re.IGNORECASE).strip()

        page_text = parser.text(separator=" ", strip=True)
        release_date = re.search(
            r"Date de sortie\s*:?\s*(\d{4})-\d{2}-\d{2}",
            page_text,
            re.IGNORECASE,
        )
        return {
            "title": normalized_title,
            "year": release_date.group(1) if release_date else None,
        }

    @staticmethod
    def progressive_verification_from_search(content_data: Optional[Dict], tmdb_titles: List[str],
                                             tmdb_year: Optional[str]) -> bool:
        if not content_data or content_data.get("title") not in tmdb_titles:
            return False
        content_year = content_data.get("year")
        if not content_year or not tmdb_year:
            return False
        return content_year == str(tmdb_year)

    async def search_content(self, title: str, year: Optional[str] = None,
                             metadata: Optional[Dict] = None, content_type: str = "films",
                             season: Optional[str] = None,
                             episode: Optional[str] = None) -> List[Dict]:
        if not settings.ZONE_TELECHARGEMENT_URL:
            scraper_logger.error("[Zone-Telechargement] settings.ZONE_TELECHARGEMENT_URL not configured")
            return []

        content_type = metadata.get("content_type", content_type) if metadata else content_type
        content_name = CONTENT_NAME_MAPPING.get(content_type, "content")

        try:
            page_cache: Dict = {}
            search_result = await self.search_content_by_titles(title, year, metadata, content_type, page_cache)
            if not search_result:
                scraper_logger.debug(f"[Zone-Telechargement] {content_name.title()} not found")
                return []

            if content_name == "movie":
                results = await self._extract_movie_content(search_result, page_cache)
            else:
                results = await self._extract_series_content(
                    search_result, title, year, page_cache, season, episode
                )

            scraper_logger.debug(f"[Zone-Telechargement] {content_name.title()} links found: {len(results)}")
            return results

        except Exception as e:
            scraper_logger.error(f"[Zone-Telechargement] {content_name.title()} search error: {type(e).__name__}: {e}")
            return []

    async def _run_filter(self, query: str, content_type: str, page: int) -> List[str]:
        base = settings.ZONE_TELECHARGEMENT_URL
        zone_telechargement_category_mapping = {
            "movies": "2", "films": "2", "series": "15", "anime": "32"
        }
        category_id = zone_telechargement_category_mapping.get(content_type)
        if not category_id:
            return []

        params = {"mod": "filter", "catid": "0", "q": query, "categorie[]": category_id,
                  "art": "0", "AiffchageMode": "0", "inputTirePar": "0", "cstart": str(page)}
        try:
            response = await http_client.get(
                f"{base}/engine/ajax/controller.php",
                params=params,
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{base}/"},
            )
            if response.status_code != 200:
                scraper_logger.debug(f"[Zone-Telechargement] Search HTTP {response.status_code} (page {page})")
                return []
        except Exception as e:
            scraper_logger.error(f"[Zone-Telechargement] Search failed (page {page}): {type(e).__name__}: {e}")
            return []

        response_url = str(getattr(response, "url", "") or base)
        urls = []
        for node in HTMLParser(response.text).css("a[href]"):
            href = html.unescape(node.attributes.get("href", ""))
            full = self._internal_url(href, response_url, base)
            if full and re.search(r"/\d+-[^/]+\.html$", urlparse(full).path, re.IGNORECASE):
                urls.append(full)
        return list(dict.fromkeys(urls))

    @staticmethod
    def _internal_url(href: str, page_url: str, alternate_url: Optional[str] = None) -> Optional[str]:
        if not href:
            return None
        page = urlparse(page_url)
        if not page.scheme or not page.netloc:
            return None
        origin = f"{page.scheme}://{page.netloc}"
        if href.startswith("//"):
            full = f"{page.scheme}:{href}"
        else:
            full = format_url(href, origin)
        parsed = urlparse(full)
        allowed_hosts = {page.hostname}
        if alternate_url:
            allowed_hosts.add(urlparse(alternate_url).hostname)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in allowed_hosts:
            return None
        return full

    @staticmethod
    async def _fetch(url: str, page_cache: Dict) -> Optional[str]:
        if url in page_cache:
            return page_cache[url]
        html_text = None
        try:
            response = await http_client.get(url)
            if response.status_code == 200:
                html_text = response.text
        except Exception as e:
            scraper_logger.error(f"[Zone-Telechargement] Fetch {url} failed: {type(e).__name__}: {e}")
        page_cache[url] = html_text
        return html_text

    async def _collect_quality_pages(self, anchor: str, page_cache: Dict) -> List[str]:
        pages = [anchor]
        html_text = await self._fetch(anchor, page_cache)
        if not html_text:
            return pages
        for node in HTMLParser(html_text).css("span.otherquality"):
            parent = node.parent
            href = parent.attributes.get("href") if parent and parent.tag == "a" else None
            if href:
                full = self._internal_url(href, anchor)
                if full and full not in pages:
                    pages.append(full)
        return pages

    async def _crawl_related_pages(self, anchors: List[str], page_cache: Dict) -> List[str]:
        seen = set()
        ordered: List[str] = []
        frontier = list(anchors)
        while frontier:
            current = []
            for u in frontier:
                if u not in seen:
                    seen.add(u)
                    ordered.append(u)
                    current.append(u)
            htmls = await asyncio.gather(*[self._fetch(u, page_cache) for u in current], return_exceptions=True)
            next_frontier = []
            queued = set()
            for page_url, html_text in zip(current, htmls):
                if not isinstance(html_text, str) or not html_text:
                    continue
                for node in HTMLParser(html_text).css("span.otherquality"):
                    parent = node.parent
                    href = parent.attributes.get("href") if parent and parent.tag == "a" else None
                    if not href:
                        continue
                    full = self._internal_url(href, page_url)
                    if full and full not in seen and full not in queued:
                        queued.add(full)
                        next_frontier.append(full)
            frontier = next_frontier
        return ordered

    async def _extract_movie_content(self, search_result: Dict, page_cache: Dict) -> List[Dict]:
        quality_pages = await self._collect_quality_pages(search_result["link"], page_cache)

        tasks = [self._extract_movie_links_for_quality({"page_path": p}, page_cache) for p in quality_pages]
        results_lists = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: List[Dict] = []
        for result in results_lists:
            if isinstance(result, list):
                all_results.extend(result)
            elif isinstance(result, Exception):
                scraper_logger.error(f"[Zone-Telechargement] Movie links extraction error: {type(result).__name__}: {result}")

        all_results.sort(key=quality_sort_key)
        return all_results

    async def _extract_series_content(self, search_result: Dict, title: str, year: Optional[str],
                                      page_cache: Dict, season: Optional[str] = None,
                                      episode: Optional[str] = None) -> List[Dict]:
        related_pages = await self._crawl_related_pages([search_result["link"]], page_cache)

        tasks = [
            self._extract_episodes_from_page(
                {"page_path": page_path}, title, year, page_cache, season, episode
            )
            for page_path in related_pages
        ]
        results_lists = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: List[Dict] = []
        for result in results_lists:
            if isinstance(result, list):
                all_results.extend(result)
            elif isinstance(result, Exception):
                scraper_logger.error(f"[Zone-Telechargement] Episodes extraction error: {type(result).__name__}: {result}")

        all_results.sort(key=lambda x: (int(x.get("season", "0")), int(x.get("episode", "0")), quality_sort_key(x)))
        return all_results

    async def _extract_movie_links_for_quality(self, quality_page: Dict, page_cache: Dict) -> List[Dict]:
        html_text = await self._fetch(quality_page["page_path"], page_cache)
        if not html_text:
            return []
        return await self._resolve_pairs(self._collect_movie_pairs(html_text, quality_page["page_path"]))

    async def _extract_episodes_from_page(self, page: Dict, title: str, year: Optional[str],
                                          page_cache: Dict, season: Optional[str] = None,
                                          episode: Optional[str] = None) -> List[Dict]:
        html_text = await self._fetch(page["page_path"], page_cache)
        if not html_text:
            return []
        pairs = self._collect_series_pairs(
            html_text, page["page_path"], title, year, season, episode
        )
        return await self._resolve_pairs(pairs)

    async def _resolve_pairs(self, pairs: List[Dict]) -> List[Dict]:
        resolved_urls = await asyncio.gather(
            *[self._resolve_zoneurs(pair["_link_url"], pair["hoster"]) for pair in pairs],
            return_exceptions=True,
        )
        results: List[Dict] = []
        for pair, resolved_url in zip(pairs, resolved_urls):
            if not resolved_url or isinstance(resolved_url, Exception):
                continue
            item = {key: value for key, value in pair.items() if key != "_link_url"}
            item["link"] = resolved_url
            item["hoster"] = item["hoster"].title()
            results.append(item)
        return results

    @classmethod
    def _get_zoneurs_semaphore(cls) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        limit = max(1, settings.ZONE_TELECHARGEMENT_MAX_CONCURRENCY)
        entry = cls._zoneurs_semaphores.get(loop)
        if entry is None or entry[0] != limit:
            entry = (limit, asyncio.Semaphore(limit))
            cls._zoneurs_semaphores[loop] = entry
        return entry[1]

    async def _resolve_zoneurs(self, link_url: str, expected_host: str) -> Optional[str]:
        if link_url.startswith("//"):
            link_url = "https:" + link_url
        try:
            async with self._get_zoneurs_semaphore():
                response = await http_client.get(link_url)
            if response.status_code != 200:
                return None
            final_url = canonicalize_url(str(getattr(response, "url", "") or ""))
            if final_url and self._url_matches_host(final_url, expected_host):
                return final_url
            parser = HTMLParser(response.text)
            for selector in ("a.btn-success[href]", "a.btn-action[href]"):
                node = parser.css_first(selector)
                if node:
                    href = html.unescape(node.attributes.get("href", ""))
                    if href.startswith("http") and not re.search(r"(zoneurs|/assets/)", href, re.IGNORECASE):
                        return canonicalize_url(href)
            for a in parser.css("a[href]"):
                href = html.unescape(a.attributes.get("href", ""))
                if href.startswith("http"):
                    canonical = canonicalize_url(href)
                    if self._url_matches_host(canonical, expected_host):
                        return canonical
        except Exception:
            return None
        return None

    @staticmethod
    def _url_matches_host(url: str, expected_host: str) -> bool:
        hostname = urlparse(url).hostname or ""
        host_key = re.sub(r"[^a-z0-9]", "", expected_host.lower())
        domain_key = re.sub(r"[^a-z0-9]", "", hostname.lower())
        return bool(host_key and host_key in domain_key)

    @staticmethod
    def _is_ignored_release_label(value: str) -> bool:
        normalized = normalize_text(value).rstrip(":").strip()
        return normalized in {"lien premium", "liens premium", "lien premiums", "liens premiums"}

    @staticmethod
    def _extract_content_markers(block: Node, content_type: str) -> List:
        markers = []
        for node in block.traverse():
            if node.tag == "font" and node.attributes.get("color", "").strip().lower() == "red":
                release_text = node.text(strip=True)
                if release_text and not BaseZoneTelechargement._is_ignored_release_label(release_text):
                    markers.append(("release", release_text))
                continue

            if node.tag == "img":
                image_path = urlparse(node.attributes.get("src", "")).path
                image_name = image_path.rsplit("/", 1)[-1]
                if image_path.lower().startswith("/img/") and image_name.lower().endswith(".png"):
                    hoster = image_name[:-4].strip().lower()
                    if hoster:
                        markers.append(("host", hoster))
                continue

            if node.tag == "div":
                style = "".join(node.attributes.get("style", "").lower().split())
                if "color:#1e88e5" in style:
                    hoster = node.text(strip=True).lower()
                    if hoster:
                        markers.append(("host", hoster))
                continue

            if node.tag != "a":
                continue
            classes = node.attributes.get("class", "").lower().split()
            href = html.unescape(node.attributes.get("href", ""))
            parsed_href = urlparse(href)
            if "btntolink" not in classes or not parsed_href.netloc:
                continue
            if "url" not in parse_qs(parsed_href.query, keep_blank_values=True):
                continue

            label = node.text(strip=True)
            if content_type == "movie":
                markers.append(("link", (href, label)))
            else:
                episode_match = re.search(r"\b[EÉ]pisode\s*(\d+)\b", label, re.IGNORECASE)
                if episode_match:
                    markers.append(("link", (href, episode_match.group(1))))
        return markers

    @staticmethod
    def _parse_release_info(raw: str, page_url: str, content_title: Optional[str] = None) -> Dict:
        size_pattern = r'(?:\(\s*)?~?\s*([\d.,]+\s*[KMGT]o)(?:\s*par\s+[eé]pisode)?\s*\)?'
        size_match = re.search(size_pattern, raw, re.IGNORECASE)
        file_size = normalize_size(size_match.group(1)) if size_match else "Unknown"
        release_name = re.sub(size_pattern, "", raw, flags=re.IGNORECASE).strip(" -~")
        release_text = BaseZoneTelechargement._technical_release_text(release_name, content_title)
        page_text = BaseZoneTelechargement._technical_release_text(page_url, content_title)
        release_info = parse_release_info(release_text, page_text)
        return {
            "name": release_name,
            "quality": release_info["quality"],
            "language": release_info["language"],
            "raw_language": release_info["raw_language"],
            "size": file_size,
        }

    def _collect_movie_pairs(self, page_html: str, page_url: str) -> List[Dict]:
        parser = HTMLParser(page_html)
        identity = self._extract_content_identity(page_html, "films")
        content_title = identity.get("title") if identity else None
        pairs: List[Dict] = []

        for block in parser.css("div.postinfo"):
            try:
                release = None
                current_host = None
                for kind, value in self._extract_content_markers(block, "movie"):
                    if kind == "release":
                        release = self._parse_release_info(value, page_url, content_title)
                        current_host = None
                        continue
                    if kind == "host":
                        current_host = value
                        continue
                    if not release or not current_host:
                        continue
                    link_url, label = value
                    if "partie" in label.lower():
                        continue
                    pairs.append({
                        "quality": release["quality"],
                        "language": release["language"],
                        "raw_language": release["raw_language"],
                        "source": "Zone-Telechargement",
                        "size": release["size"],
                        "display_name": release["name"],
                        "model_type": "link",
                        "hoster": current_host,
                        "_link_url": link_url,
                    })
            except Exception as e:
                scraper_logger.error(f"[Zone-Telechargement] Movie block parse error: {type(e).__name__}: {e}")
                continue

        return pairs

    def _collect_series_pairs(self, page_html: str, page_url: str,
                              title: str, year: Optional[str], requested_season: Optional[str] = None,
                              requested_episode: Optional[str] = None) -> List[Dict]:
        season_match = re.search(r"saison[ -](\d+)", page_url, re.IGNORECASE)
        season = season_match.group(1) if season_match else "1"
        if not self._same_number(season, requested_season):
            return []

        parser = HTMLParser(page_html)
        identity = self._extract_content_identity(page_html, "series")
        content_title = identity.get("title") if identity else normalize_text(title)
        postinfos = parser.css("div.postinfo")
        pairs: List[Dict] = []
        for postinfo in postinfos:
            release = None
            current_host = None
            for kind, value in self._extract_content_markers(postinfo, "series"):
                if kind == "release":
                    release = self._parse_release_info(value, page_url, content_title)
                    current_host = None
                    continue
                if kind == "host":
                    current_host = value
                    continue
                if release and current_host:
                    link_url, epnum = value
                    if not self._same_number(epnum, requested_episode):
                        continue
                    display_name = build_display_name(
                        title=title, year=year, language=release["language"], quality=release["quality"],
                        season=season, episode=epnum, raw_language=release["raw_language"],
                    )
                    pairs.append({
                        "season": season,
                        "episode": epnum,
                        "quality": release["quality"],
                        "language": release["language"],
                        "raw_language": release["raw_language"],
                        "source": "Zone-Telechargement",
                        "size": release["size"],
                        "display_name": display_name,
                        "model_type": "link",
                        "hoster": current_host,
                        "_link_url": link_url,
                    })
        return pairs

    @staticmethod
    def _same_number(value: str, expected: Optional[str]) -> bool:
        if expected is None:
            return True
        try:
            return int(value) == int(expected)
        except (TypeError, ValueError):
            return str(value) == str(expected)

    @staticmethod
    def _technical_release_text(value: str, content_title: Optional[str]) -> str:
        if not value or not content_title:
            return value

        title_tokens = normalize_text(content_title).split()
        words = list(re.finditer(r"[^\W_]+", value, re.UNICODE))
        value_tokens = [normalize_text(word.group(0)) for word in words]
        count = len(title_tokens)
        if not count or count > len(value_tokens):
            return value

        for index in range(len(value_tokens) - count + 1):
            if value_tokens[index:index + count] == title_tokens:
                return value[words[index + count - 1].end():]
        return value
