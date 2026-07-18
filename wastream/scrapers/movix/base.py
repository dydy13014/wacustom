import asyncio
from typing import List, Dict, Optional, Tuple

from wastream.config.settings import settings
from wastream.services.tmdb import tmdb_service
from wastream.utils.helpers import normalize_text, normalize_size, build_display_name
from wastream.utils.http_client import http_client
from wastream.utils.languages import normalize_language
from wastream.utils.logger import scraper_logger
from wastream.utils.quality import quality_sort_key, normalize_quality


# ===========================
# Base Movix Client Class
# ===========================
class BaseMovix:

    def _get_headers(self) -> Dict[str, str]:
        if not settings.MOVIX_URL:
            return {}
        return {
            "Origin": settings.MOVIX_URL,
            "Referer": f"{settings.MOVIX_URL}/"
        }

    def _normalize_language_string(self, raw_language: str) -> str:
        if not raw_language or raw_language in ["Unknown", "N/A", ""]:
            return "Unknown"

        parts = [p.strip() for p in raw_language.split(",")]
        normalized_langs = []

        for part in parts:
            normalized = normalize_language(part)
            if normalized and normalized not in normalized_langs:
                normalized_langs.append(normalized)

        normalized_langs = [lang for lang in normalized_langs if lang != "Unknown"]

        if len(normalized_langs) == 0:
            return "Unknown"
        elif len(normalized_langs) == 1:
            return normalized_langs[0]
        else:
            return f"Multi ({', '.join(normalized_langs)})"

    async def search_by_titles(self, titles: List[str], metadata: Optional[Dict] = None) -> Optional[Dict]:
        if not settings.MOVIX_API_URL:
            scraper_logger.error("[Movix] settings.MOVIX_API_URL not configured")
            return None

        if not metadata:
            scraper_logger.error("[Movix] Metadata required for matching")
            return None

        has_imdb_id = metadata.get("imdb_id")

        if has_imdb_id:
            return await self._search_by_imdb_id(titles, metadata)
        else:
            return await self._search_by_name(titles, metadata)

    async def _search_by_imdb_id(self, titles: List[str], metadata: Dict) -> Optional[Dict]:
        target_imdb_id = metadata["imdb_id"]
        headers = self._get_headers()

        for search_title in titles:
            try:
                scraper_logger.debug(f"[Movix] Searching with title: '{search_title}'")

                search_url = f"{settings.MOVIX_API_URL}/api/search"
                params = {"title": search_title}

                response = await http_client.get(search_url, params=params, headers=headers)

                if response.status_code != 200:
                    scraper_logger.debug(f"[Movix] Search failed: {response.status_code}")
                    continue

                data = response.json()
                results = data.get("results", [])

                if not results:
                    scraper_logger.debug(f"[Movix] No results for '{search_title}'")
                    continue

                scraper_logger.debug(f"[Movix] Found {len(results)} results for '{search_title}'")

                for result in results:
                    result_imdb_id = result.get("imdb_id")

                    if result_imdb_id and result_imdb_id == target_imdb_id:
                        scraper_logger.debug(f"[Movix] Found match by IMDB ID: {result.get('name')} (ID: {result.get('id')})")
                        return result

                scraper_logger.debug(f"[Movix] No IMDB match in {len(results)} results for '{search_title}'")

            except Exception as e:
                scraper_logger.error(f"[Movix] Title '{search_title}' search error: {type(e).__name__}: {e}")
                continue

        scraper_logger.debug("[Movix] No match found for any title variant")
        return None

    async def _search_by_name(self, titles: List[str], metadata: Dict) -> Optional[Dict]:
        if metadata.get("all_titles"):
            normalized_targets = [normalize_text(t) for t in metadata["all_titles"]]
        else:
            normalized_targets = [normalize_text(t) for t in titles]
        headers = self._get_headers()

        kitsu_year = metadata.get("year")

        for search_title in titles:
            try:
                scraper_logger.debug(f"[Movix] Searching: '{search_title}'")

                search_url = f"{settings.MOVIX_API_URL}/api/search"
                params = {"title": search_title}

                response = await http_client.get(search_url, params=params, headers=headers)

                if response.status_code != 200:
                    scraper_logger.debug(f"[Movix] Search failed: {response.status_code}")
                    continue

                data = response.json()
                all_results = data.get("results", [])

                content_type = metadata.get("content_type")
                if content_type == "movies":
                    results = [r for r in all_results if r.get("type") in ["animes", "movie"]]
                else:
                    results = [r for r in all_results if r.get("type") == "animes"]

                if not results:
                    scraper_logger.debug(f"[Movix] No results for '{search_title}'")
                    continue

                scraper_logger.debug(f"[Movix] Found {len(results)} anime results for '{search_title}'")

                for result in results:
                    result_name = result.get("name", "")
                    result_name_normalized = normalize_text(result_name)

                    if result_name_normalized not in normalized_targets:
                        continue

                    movix_year = result.get("year")

                    if not movix_year:
                        scraper_logger.debug(f"[Movix] Year missing for '{result_name}', skipping")
                        continue

                    if not kitsu_year:
                        scraper_logger.debug("[Movix] Kitsu year missing, skipping")
                        continue

                    if str(kitsu_year) != str(movix_year):
                        scraper_logger.debug(f"[Movix] Year mismatch: Kitsu {kitsu_year} vs Movix {movix_year}")
                        continue

                    scraper_logger.debug(f"[Movix] Match by name + year: {result_name} ({movix_year}) [ID: {result.get('id')}]")
                    return result

                scraper_logger.debug(f"[Movix] No name + year match for '{search_title}'")

            except Exception as e:
                scraper_logger.error(f"[Movix] '{search_title}' search error: {type(e).__name__}: {e}")
                continue

        scraper_logger.debug("[Movix] No match found for any title variant")
        return None

    async def get_all_links(self, title_id: int, content_type: str = "movie", season: Optional[str] = None, episode: Optional[str] = None) -> List[Dict]:
        if not settings.MOVIX_API_URL:
            scraper_logger.error("[Movix] settings.MOVIX_API_URL not configured")
            return []

        headers = self._get_headers()

        try:
            api_type = "tv" if content_type in ["series", "anime"] else "movie"
            links_url = f"{settings.MOVIX_API_URL}/api/darkiworld/download/{api_type}/{title_id}"

            params = {}
            if season:
                params["season"] = season
            if episode:
                params["episode"] = episode

            scraper_logger.debug(f"[Movix] Fetching links for {api_type} {title_id}")

            response = await http_client.get(links_url, params=params, headers=headers)

            if response.status_code != 200:
                scraper_logger.debug(f"[Movix] Links request failed: {response.status_code}")
                return []

            data = response.json()

            if not data.get("success"):
                scraper_logger.debug("[Movix] Links request not successful")
                return []

            links = data.get("all", [])

            scraper_logger.debug(f"[Movix] Found {len(links)} links")
            return links

        except Exception as e:
            scraper_logger.error(f"[Movix] Links fetch error: {type(e).__name__}: {e}")
            return []

    async def _decode_link_request(self, link_id: int) -> Optional[str]:
        decode_url = f"{settings.MOVIX_API_URL}/api/darkiworld/decode/{link_id}"
        headers = self._get_headers()

        response = await http_client.get(decode_url, headers=headers)

        if response.status_code != 200:
            scraper_logger.debug(f"[Movix] Decode failed: {response.status_code} for ID {link_id}")
            return None

        data = response.json()

        if not data.get("success"):
            scraper_logger.debug(f"[Movix] Decode not successful for ID {link_id}")
            return None

        embed_url = data.get("embed_url", {})
        download_url = embed_url.get("lien") if isinstance(embed_url, dict) else None

        if not download_url:
            scraper_logger.debug(f"[Movix] No download URL found for link {link_id}")
            return None

        return download_url

    async def decode_link(self, link_id: int) -> Optional[str]:
        if not settings.MOVIX_API_URL:
            scraper_logger.error("[Movix] settings.MOVIX_API_URL not configured")
            return None

        try:
            task = asyncio.create_task(self._decode_link_request(link_id))
            soft_timeout = settings.DARKIBOX_LINK_TIMEOUT or 2

            try:
                result = await asyncio.wait_for(asyncio.shield(task), timeout=soft_timeout)
                return result
            except asyncio.TimeoutError:
                scraper_logger.debug(f"[Movix] Link {link_id} soft timeout ({soft_timeout}s), continuing in background")

                async def background_task():
                    try:
                        await task
                    except Exception:
                        pass

                asyncio.create_task(background_task())
                return None

        except Exception as e:
            scraper_logger.error(f"[Movix] Link {link_id} decode error: {type(e).__name__}: {e}")
            return None

    async def search_content(self, title: str, year: Optional[str] = None,
                             metadata: Optional[Dict] = None, content_type: str = "movie",
                             season: Optional[str] = None, episode: Optional[str] = None, config: Optional[Dict] = None) -> List[Dict]:
        content_names = {"movie": "movie", "series": "series", "anime": "anime"}
        content_name = content_names.get(content_type, "content")

        if season and episode:
            scraper_logger.debug(f"[Movix] Searching {content_name}: '{title}' S{season}E{episode}")
        else:
            scraper_logger.debug(f"[Movix] Searching {content_name}: '{title}' ({year})")

        try:
            search_titles = []

            if metadata and metadata.get("original_titles"):
                for t in metadata["original_titles"]:
                    if t not in search_titles:
                        search_titles.append(t)
                    if t.lower() not in search_titles:
                        search_titles.append(t.lower())
            elif metadata and metadata.get("titles"):
                search_titles.extend(metadata["titles"])

            if title not in search_titles:
                search_titles.append(title)
            if title.lower() not in search_titles:
                search_titles.append(title.lower())

            content = await self.search_by_titles(search_titles, metadata)

            if not content:
                scraper_logger.debug(f"[Movix] {content_name.title()} not found")
                return []

            title_id = content.get("id")
            content_title = content.get("name", title)

            if not title_id:
                scraper_logger.error("[Movix] No title ID found")
                return []

            scraper_logger.debug(f"[Movix] Found {content_name}: {content_title} (ID: {title_id})")

            links = await self.get_all_links(title_id, content_type=content_type, season=season, episode=episode)

            if not links:
                scraper_logger.debug(f"[Movix] No links found for {content_name}")
                return []

            is_series = season is not None and episode is not None

            results = await self.format_links(links, content_title, year=year, is_series=is_series, season=season, episode=episode)

            scraper_logger.debug(f"[Movix] {content_name.title()} links found: {len(results)}")
            return results

        except Exception as e:
            scraper_logger.error(f"[Movix] {content_name.title()} search error: {type(e).__name__}: {e}")
            return []

    async def format_links(self, links: List[Dict], content_title: str, year: Optional[str] = None, is_series: bool = False, season: Optional[str] = None, episode: Optional[str] = None) -> List[Dict]:
        if not links:
            return []

        if is_series:
            original_count = len(links)
            links = [link for link in links if not link.get("full_saison", 0)]
            if original_count != len(links):
                scraper_logger.debug(f"[Movix] Filtered {original_count - len(links)} season packs")

        if not links:
            return []

        decode_tasks = [self.decode_link(link.get("id")) for link in links]
        decoded_urls = await asyncio.gather(*decode_tasks, return_exceptions=True)

        formatted_results = []

        for i, link in enumerate(links):
            try:
                download_url = decoded_urls[i]

                if isinstance(download_url, Exception) or not download_url:
                    continue

                host_name = link.get("host_name", "Unknown")
                raw_quality = link.get("quality", "Unknown")
                quality = normalize_quality(raw_quality)
                raw_language = link.get("language", "Unknown")
                language = self._normalize_language_string(raw_language)
                size = link.get("size", 0)
                if size and size > 0:
                    size_gb = size / (1024 ** 3)
                    size_str = f"{size_gb:.2f} GB"
                else:
                    size_str = "Unknown"

                size_str = normalize_size(size_str)

                display_name = build_display_name(
                    title=content_title,
                    year=year,
                    language=language,
                    quality=quality,
                    season=season if is_series else None,
                    episode=episode if is_series else None,
                    raw_language=raw_language
                )

                result = {
                    "link": download_url,
                    "quality": quality,
                    "language": language,
                    "raw_language": raw_language,
                    "source": "Movix",
                    "hoster": host_name.title() if host_name else "Unknown",
                    "size": size_str,
                    "display_name": display_name,
                    "model_type": "link"
                }

                if is_series:
                    result["season"] = season
                    result["episode"] = episode

                formatted_results.append(result)

            except Exception as e:
                scraper_logger.error(f"[Movix] Link format error: {type(e).__name__}: {e}")
                continue

        formatted_results.sort(key=quality_sort_key)

        scraper_logger.debug(f"[Movix] Formatted {len(formatted_results)} valid links")
        return formatted_results

    async def get_all_seasons(self, title_id: int) -> List[Dict]:
        if not settings.MOVIX_API_URL:
            scraper_logger.error("[Movix] settings.MOVIX_API_URL not configured")
            return []

        headers = self._get_headers()
        all_seasons = []
        page = 1

        while True:
            try:
                url = f"{settings.MOVIX_API_URL}/api/darkiworld/seasons/{title_id}"
                params = {"page": page, "perPage": 8}

                response = await http_client.get(url, params=params, headers=headers)

                if response.status_code != 200:
                    scraper_logger.debug(f"[Movix] Seasons fetch failed: {response.status_code}")
                    break

                data = response.json()

                if not data.get("success"):
                    break

                pagination = data.get("pagination", {})
                seasons = pagination.get("data", [])

                if not seasons:
                    break

                all_seasons.extend(seasons)

                last_page = pagination.get("last_page", 1)
                if page >= last_page:
                    break

                page += 1

            except Exception as e:
                scraper_logger.error(f"[Movix] Seasons fetch error: {type(e).__name__}: {e}")
                break

        scraper_logger.debug(f"[Movix] Fetched {len(all_seasons)} seasons for title {title_id}")
        return all_seasons

    async def map_kitsu_absolute_to_movix_season(self, title_id: int, absolute_episode: int, tmdb_api_token: Optional[str] = None, imdb_id: Optional[str] = None) -> Optional[Tuple[str, str]]:
        if imdb_id and imdb_id in settings.DARKIMOVIX_KITSU_TMDB_MAPPING and tmdb_api_token:
            scraper_logger.debug(f"[Movix] Using TMDB mapping for {imdb_id}")

            tmdb_seasons = await tmdb_service.get_seasons_episode_count(imdb_id, tmdb_api_token)

            if tmdb_seasons:
                episode_counter = 0

                for season in tmdb_seasons:
                    season_number = season.get("number")
                    episode_count = season.get("episode_count", 0)

                    if episode_counter + episode_count >= absolute_episode:
                        episode_in_season = absolute_episode - episode_counter

                        scraper_logger.debug(f"[Movix] Kitsu episode {absolute_episode} (absolute) → TMDB S{season_number}E{episode_in_season}")
                        return (str(season_number), str(episode_in_season))

                    episode_counter += episode_count

                scraper_logger.debug(f"[Movix] Episode {absolute_episode} exceeds TMDB total ({episode_counter})")
                return None

        seasons = await self.get_all_seasons(title_id)

        if not seasons:
            scraper_logger.debug("[Movix] No seasons data for mapping")
            return None

        regular_seasons = [s for s in seasons if s.get("number", 0) > 0]
        regular_seasons.sort(key=lambda s: s.get("number", 0))

        episode_counter = 0

        for season in regular_seasons:
            season_number = season.get("number")
            episode_count = season.get("episodes_count", 0)

            if episode_counter + episode_count >= absolute_episode:
                episode_in_season = absolute_episode - episode_counter

                scraper_logger.debug(f"[Movix] Kitsu episode {absolute_episode} (absolute) → S{season_number}E{episode_in_season}")
                return (str(season_number), str(episode_in_season))

            episode_counter += episode_count

        scraper_logger.debug(f"[Movix] Episode {absolute_episode} exceeds total ({episode_counter})")
        return None
