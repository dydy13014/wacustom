import asyncio
from typing import List, Dict, Optional

from wastream.config.settings import settings
from wastream.services.wasource import get_links_by_imdb as get_wasource_links_by_imdb, get_links_by_title as get_wasource_links_by_title, add_wasource_links_from_remote, update_wasource_release_sizes
from wastream.services.remote import fetch_remote_wasource
from wastream.utils.helpers import build_display_name, normalize_size, get_debrid_api_key
from wastream.utils.http_client import http_client
from wastream.utils.logger import scraper_logger
from wastream.utils.quality import quality_sort_key


async def _store_remote_links(remote_results: List[Dict], local_urls: set):
    grouped = {}
    new_urls_count = 0
    for link in remote_results:
        if link.get("url") in local_urls:
            continue
        new_urls_count += 1
        key = (
            link.get("imdb_id"),
            link.get("title"),
            link.get("year"),
            link.get("season"),
            link.get("episode"),
            link.get("release_name"),
            link.get("quality"),
            link.get("language"),
            link.get("size")
        )
        if key not in grouped:
            grouped[key] = []
        grouped[key].append({"url": link.get("url"), "host": link.get("host", "Unknown")})

    if not grouped:
        return

    for key, urls in grouped.items():
        imdb_id, title, year, season, episode, release_name, quality, language, size = key
        if imdb_id and title:
            await add_wasource_links_from_remote(
                imdb_id=imdb_id,
                title=title,
                release_name=release_name,
                quality=quality,
                language=language,
                size=size,
                season=season,
                episode=episode,
                urls=urls,
                year=year
            )

    scraper_logger.debug(f"[WASource] Stored {new_urls_count} remote links locally ({len(grouped)} releases)")


# ===========================
# AllDebrid Size Enrichment
# ===========================
ALLDEBRID_LINK_PREFIX = "https://alldebrid.com/f/"


async def _fetch_alldebrid_sizes(links: List[Dict], api_key: str) -> Dict[str, int]:
    alldebrid_urls = [
        link.get("url") for link in links
        if link.get("host") == "alldebrid"
        and link.get("url", "").startswith(ALLDEBRID_LINK_PREFIX)
        and not link.get("size")
    ]

    if not alldebrid_urls:
        return {}

    try:
        body = "&".join([f"link[]={url}" for url in alldebrid_urls])
        response = await http_client.post(
            f"{settings.ALLDEBRID_API_URL}/link/infos?agent=wastream&apikey={api_key}",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=settings.HTTP_TIMEOUT
        )

        if response.status_code != 200:
            scraper_logger.debug(f"[WASource] AllDebrid link/infos HTTP {response.status_code}")
            return {}

        data = response.json()
        if data.get("status") != "success":
            return {}

        url_to_size = {}
        for info in data.get("data", {}).get("infos", []):
            link_url = info.get("link")
            size = info.get("size")
            if link_url and size and size > 0:
                url_to_size[link_url] = size

        scraper_logger.debug(f"[WASource] AllDebrid sizes fetched: {len(url_to_size)}/{len(alldebrid_urls)} links")
        return url_to_size

    except Exception as e:
        scraper_logger.error(f"[WASource] AllDebrid size fetch error: {type(e).__name__}: {e}")
        return {}


# ===========================
# Base WASource Scraper
# ===========================
class BaseWASource:

    async def get_links_by_imdb(self, imdb_id: str, season: Optional[str] = None, episode: Optional[str] = None) -> List[Dict]:
        try:
            season_int = int(season) if season else None
            episode_int = int(episode) if episode else None

            local_task = get_wasource_links_by_imdb(imdb_id, season_int, episode_int)
            remote_task = fetch_remote_wasource(imdb_id=imdb_id, season=season_int, episode=episode_int)

            local_results, remote_result = await asyncio.gather(local_task, remote_task, return_exceptions=True)

            all_links = []
            local_urls = set()
            if isinstance(local_results, list):
                all_links.extend(local_results)
                local_urls = {link.get("url") for link in local_results if link.get("url")}

            remote_results = []
            should_store = False
            if isinstance(remote_result, tuple):
                remote_results, should_store = remote_result
                all_links.extend(remote_results)

            if should_store and remote_results:
                asyncio.create_task(_store_remote_links(remote_results, local_urls))

            seen_urls = set()
            unique_links = []
            for link in all_links:
                url = link.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_links.append(link)

            return unique_links

        except Exception as e:
            scraper_logger.error(f"[WASource] DB query error: {type(e).__name__}: {e}")
            return []

    async def get_links_by_title(self, title: str, year: Optional[str] = None, season: Optional[str] = None, episode: Optional[str] = None) -> List[Dict]:
        try:
            year_int = int(year) if year else None
            season_int = int(season) if season else None
            episode_int = int(episode) if episode else None

            local_task = get_wasource_links_by_title(title, year_int, season_int, episode_int)
            remote_task = fetch_remote_wasource(title=title, year=year_int, season=season_int, episode=episode_int)

            local_results, remote_result = await asyncio.gather(local_task, remote_task, return_exceptions=True)

            all_links = []
            local_urls = set()
            if isinstance(local_results, list):
                all_links.extend(local_results)
                local_urls = {link.get("url") for link in local_results if link.get("url")}

            remote_results = []
            should_store = False
            if isinstance(remote_result, tuple):
                remote_results, should_store = remote_result
                all_links.extend(remote_results)

            if should_store and remote_results:
                asyncio.create_task(_store_remote_links(remote_results, local_urls))

            seen_urls = set()
            unique_links = []
            for link in all_links:
                url = link.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_links.append(link)

            return unique_links

        except Exception as e:
            scraper_logger.error(f"[WASource] DB title query error: {type(e).__name__}: {e}")
            return []

    async def search_content(self, title: str, year: Optional[str] = None,
                             metadata: Optional[Dict] = None, content_type: str = "movie",
                             season: Optional[str] = None, episode: Optional[str] = None,
                             config: Optional[Dict] = None) -> List[Dict]:
        content_names = {"movie": "movie", "series": "series", "anime": "anime"}
        content_name = content_names.get(content_type, "content")

        if season and episode:
            scraper_logger.debug(f"[WASource] Searching {content_name}: '{title}' S{season}E{episode}")
        else:
            scraper_logger.debug(f"[WASource] Searching {content_name}: '{title}' ({year})")

        try:
            imdb_id = metadata.get("imdb_id") if metadata else None
            links = []

            if imdb_id:
                links = await self.get_links_by_imdb(imdb_id, season, episode)

            if not links:
                scraper_logger.debug(f"[WASource] No IMDB links, trying title search for '{title}'")
                links = await self.get_links_by_title(title, year, season, episode)

            if not links:
                scraper_logger.debug(f"[WASource] No custom links found for '{title}'")
                return []

            alldebrid_api_key = get_debrid_api_key(config, "alldebrid") if config else ""
            if alldebrid_api_key:
                url_to_size = await _fetch_alldebrid_sizes(links, alldebrid_api_key)
                if url_to_size:
                    affected_row_ids = []
                    for link in links:
                        if not link.get("size") and link.get("url") in url_to_size:
                            link["size"] = url_to_size[link["url"]]
                            if link.get("id"):
                                affected_row_ids.append(link["id"])
                    if affected_row_ids:
                        asyncio.create_task(update_wasource_release_sizes(affected_row_ids, url_to_size))

            is_series = season is not None and episode is not None

            formatted_results = await self.format_links(links, title, year=year, is_series=is_series)

            scraper_logger.debug(f"[WASource] {content_name.title()} links found: {len(formatted_results)}")
            return formatted_results

        except Exception as e:
            scraper_logger.error(f"[WASource] {content_name.title()} search error: {type(e).__name__}: {e}")
            return []

    async def format_links(self, links: List[Dict], content_title: str, year: Optional[str] = None, is_series: bool = False) -> List[Dict]:
        if not links:
            return []

        formatted_results = []

        for link in links:
            try:
                download_url = link["url"]
                quality = link.get("quality") or "Unknown"
                language = link.get("language") or "Unknown"
                host = link.get("host") or "Unknown"
                custom_title = link.get("release_name")

                size = link.get("size")
                if size and size > 0:
                    size_gb = size / (1024 ** 3)
                    size_str = f"{size_gb:.2f} GB"
                else:
                    size_str = "Unknown"

                size_str = normalize_size(size_str)

                season_val = None
                episode_val = None
                if is_series:
                    season_val = str(link.get("season", "1")) if link.get("season") else "1"
                    episode_val = str(link.get("episode", "1")) if link.get("episode") else "1"

                raw_language = link.get("raw_language") or language

                if custom_title:
                    display_name = custom_title
                else:
                    display_name = build_display_name(
                        title=content_title,
                        year=year,
                        language=language,
                        quality=quality,
                        season=season_val,
                        episode=episode_val,
                        raw_language=raw_language
                    )

                result = {
                    "link": download_url,
                    "quality": quality,
                    "language": language,
                    "raw_language": raw_language,
                    "source": "WASource",
                    "hoster": host.title(),
                    "size": size_str,
                    "display_name": display_name,
                    "model_type": "link"
                }

                if is_series:
                    result["season"] = season_val
                    result["episode"] = episode_val

                formatted_results.append(result)

            except Exception as e:
                scraper_logger.error(f"[WASource] Link format error: {type(e).__name__}: {e}")
                continue

        formatted_results.sort(key=quality_sort_key)

        scraper_logger.debug(f"[WASource] Formatted {len(formatted_results)} links")
        return formatted_results
