import asyncio
import re
import time
from typing import List, Dict, Optional, Tuple

from fastapi.responses import FileResponse, RedirectResponse

from wastream.config.settings import settings, DEBRID_ABBREVIATIONS, SOURCE_DISPLAY_NAMES
from wastream.debrid.alldebrid import alldebrid_service
from wastream.debrid.torbox import torbox_service
from wastream.debrid.premiumize import premiumize_service
from wastream.debrid.onefichier import onefichier_service
from wastream.debrid.nzbdav import nzbdav_service
from wastream.scrapers.darki_api.anime import anime_scraper as darki_api_anime_scraper
from wastream.scrapers.darki_api.base import BaseDarkiAPI
from wastream.scrapers.darki_api.movie import movie_scraper as darki_api_movie_scraper
from wastream.scrapers.darki_api.series import series_scraper as darki_api_series_scraper
from wastream.scrapers.wawacity.anime import anime_scraper
from wastream.scrapers.wawacity.movie import movie_scraper
from wastream.scrapers.wawacity.series import series_scraper
from wastream.scrapers.free_telecharger.anime import anime_scraper as free_telecharger_anime_scraper
from wastream.scrapers.free_telecharger.movie import movie_scraper as free_telecharger_movie_scraper
from wastream.scrapers.free_telecharger.series import series_scraper as free_telecharger_series_scraper
from wastream.scrapers.wasource.anime import anime_scraper as wasource_anime_scraper
from wastream.scrapers.wasource.movie import movie_scraper as wasource_movie_scraper
from wastream.scrapers.wasource.series import series_scraper as wasource_series_scraper
from wastream.scrapers.movix.anime import anime_scraper as movix_anime_scraper
from wastream.scrapers.movix.base import BaseMovix
from wastream.scrapers.movix.movie import movie_scraper as movix_movie_scraper
from wastream.scrapers.movix.series import series_scraper as movix_series_scraper
from wastream.scrapers.webshare.anime import anime_scraper as webshare_anime_scraper
from wastream.scrapers.webshare.movie import movie_scraper as webshare_movie_scraper
from wastream.scrapers.webshare.series import series_scraper as webshare_series_scraper
from wastream.scrapers.torznab.trackers import yggreborn_scraper, tr4ker_scraper, torr9_scraper, c411_scraper, gemini_scraper, generationfree_scraper
from wastream.scrapers.zilean.base import zilean_scraper
from wastream.scrapers.nyaa.base import nyaa_scraper
from wastream.services.kitsu import kitsu_service
from wastream.services.tmdb import tmdb_service
from wastream.utils.cache import get_cache, get_cache_with_status, get_cache_parallel, set_cache, set_cache_if_not_exists
from wastream.services.remote import fetch_remote_cache
from wastream.services.health import is_source_online
from wastream.utils.database import SearchLock, mark_dead_link, check_dead_links_batch, database
from wastream.utils.filters import apply_all_filters, filter_excluded_keywords, filter_archive_files
from wastream.utils.helpers import (
    encode_config_to_base64, encode_playback_token, quote_path_segment,
    deduplicate_and_sort_results, get_debrid_api_key, get_debrid_services,
    should_enable_full_season, parse_size_to_bytes
)
from wastream.utils.languages import MULTI_LANGUAGE_PREFIX, MULTI_PREFIX_LENGTH
from wastream.utils.logger import stream_logger, metadata_logger
from wastream.utils.quality import quality_sort_key, extract_resolution
from wastream.utils.validators import extract_media_info


# ===========================
# Stream Service Class
# ===========================
class StreamService:
    """Core service that orchestrates content search, cache, debrid enrichment, and stream formatting."""

    def _parse_kitsu_override_entry(self, entry: str) -> Optional[Tuple[str, int, Optional[int]]]:
        if "=" not in entry:
            return None
        kitsu_id, season_part = entry.split("=", 1)
        kitsu_id = kitsu_id.strip()
        if "-" in season_part:
            season_str, part_str = season_part.split("-", 1)
            return (kitsu_id, int(season_str), int(part_str))
        else:
            return (kitsu_id, int(season_part), None)

    def _check_kitsu_imdb_override(self, kitsu_id: str) -> Optional[Dict]:
        for mapping in settings.KITSU_IMDB_OVERRIDE:
            if ":" not in mapping:
                continue
            kitsu_ids_part, imdb_id = mapping.rsplit(":", 1)
            entries = [e.strip() for e in kitsu_ids_part.split(",")]

            all_parsed = []
            found_entry = None

            for entry in entries:
                parsed = self._parse_kitsu_override_entry(entry)
                if parsed:
                    all_parsed.append(parsed)
                    if parsed[0] == kitsu_id:
                        found_entry = parsed

            if found_entry:
                kid, season, part = found_entry
                previous_parts = []
                if part and part > 1:
                    for p_kid, p_season, p_part in all_parsed:
                        if p_season == season and p_part and p_part < part:
                            previous_parts.append(p_kid)
                    previous_parts.sort(key=lambda x: next((p[2] for p in all_parsed if p[0] == x), 0))

                stream_logger.debug(f"Kitsu override: {kitsu_id} → {imdb_id} S{season}" + (f"-P{part}" if part else ""))
                return {
                    "imdb_id": imdb_id,
                    "season": season,
                    "part": part,
                    "previous_parts_kitsu_ids": previous_parts
                }
        return None

    async def _get_episode_offset_from_previous_parts(self, previous_parts_kitsu_ids: List[str]) -> int:
        total_episodes = 0
        for kitsu_id in previous_parts_kitsu_ids:
            try:
                season_info = await kitsu_service._get_season_info(kitsu_id)
                if season_info and season_info.get("episodes"):
                    episodes = season_info["episodes"]
                    total_episodes += episodes
                    stream_logger.debug(f"Kitsu {kitsu_id}: {episodes} episodes")
            except Exception as e:
                stream_logger.error(f"Failed to get episodes for {kitsu_id}: {type(e).__name__}: {e}")
        return total_episodes

    def _get_debrid_service(self, service_name: str):
        if service_name == "torbox":
            return torbox_service
        elif service_name == "premiumize":
            return premiumize_service
        elif service_name == "1fichier":
            return onefichier_service
        elif service_name == "nzbdav":
            return nzbdav_service
        else:
            return alldebrid_service

    def _get_default_sources_for_service(self, service_name: str) -> List[str]:
        if service_name == "torbox":
            return settings.TORBOX_SUPPORTED_SOURCES
        elif service_name == "premiumize":
            return settings.PREMIUMIZE_SUPPORTED_SOURCES
        elif service_name == "1fichier":
            return settings.ONEFICHIER_SUPPORTED_SOURCES
        elif service_name == "nzbdav":
            return settings.NZBDAV_SUPPORTED_SOURCES
        else:
            return settings.ALLDEBRID_SUPPORTED_SOURCES

    def _get_sources_for_service(self, service_name: str, service_entry: Dict = None) -> List[str]:
        if service_entry and "sources" in service_entry and service_entry["sources"]:
            return service_entry["sources"]
        return self._get_default_sources_for_service(service_name)

    def _get_default_hosts_for_service(self, service_name: str) -> List[str]:
        if service_name == "torbox":
            return settings.TORBOX_SUPPORTED_HOSTS
        elif service_name == "premiumize":
            return settings.PREMIUMIZE_SUPPORTED_HOSTS
        elif service_name == "1fichier":
            return settings.ONEFICHIER_SUPPORTED_HOSTS
        else:
            return settings.ALLDEBRID_SUPPORTED_HOSTS

    def _get_hosts_for_service(self, service_name: str, service_entry: Dict = None) -> List[str]:
        if service_entry and "hosts" in service_entry and service_entry["hosts"]:
            return service_entry["hosts"]
        return self._get_default_hosts_for_service(service_name)

    def _get_supported_sources(self, config: Dict) -> List[str]:
        debrid_services = get_debrid_services(config)
        if not debrid_services:
            return settings.ALLDEBRID_SUPPORTED_SOURCES

        all_sources = set()
        for service_entry in debrid_services:
            service_name = service_entry.get("service", "alldebrid")
            all_sources.update(self._get_sources_for_service(service_name, service_entry))

        return list(all_sources)

    async def _check_cache_and_enrich(
        self,
        results: List[Dict],
        debrid_services: List[Dict],
        config: Dict,
        timeout_remaining: float,
        season: Optional[str] = None,
        episode: Optional[str] = None
    ) -> List[Dict]:
        if not debrid_services:
            return []

        source_mapping = SOURCE_DISPLAY_NAMES

        async def check_single(service_entry):
            service_name = service_entry.get("service", "alldebrid")
            api_key = service_entry.get("api_key", "")
            debrid_service = self._get_debrid_service(service_name)

            supported_sources = self._get_sources_for_service(service_name, service_entry)
            allowed_sources = [source_mapping.get(s, s) for s in supported_sources]

            filtered_results = [
                r.copy() for r in results
                if r.get("source") in allowed_sources
            ]

            if not filtered_results:
                return []

            user_hosts = self._get_hosts_for_service(service_name, service_entry)

            service_config = {
                **config,
                "enable_nzb": service_entry.get("enable_nzb", False),
                "enable_full_season": service_entry.get("enable_full_season", False)
            }

            enriched = await debrid_service.check_cache_and_enrich(
                filtered_results, api_key, service_config, timeout_remaining, season, episode, user_hosts
            )

            for r in enriched:
                r["debrid_service"] = service_name

            return enriched

        tasks = [check_single(entry) for entry in debrid_services]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        merged_results = []
        for idx, result in enumerate(all_results):
            if isinstance(result, list):
                merged_results.extend(result)
            elif isinstance(result, Exception):
                service_name = debrid_services[idx].get("service", "unknown")
                stream_logger.error(f"Cache check failed for {service_name}: {type(result).__name__}: {result}")

        merged_results.sort(key=quality_sort_key)

        return merged_results

    async def _format_streams(
        self,
        results: List[Dict],
        config: Dict,
        base_url: str,
        season: Optional[str],
        episode: Optional[str],
        year: Optional[str],
        content_type: Optional[str] = None
    ) -> List[Dict]:
        streams = []
        dead_links_count = 0

        all_links = [r.get("link") for r in results if r.get("link")]
        dead_links_map = await check_dead_links_batch(all_links)

        for result in results:
            link = result.get("link")
            if not link:
                continue

            if dead_links_map.get(link):
                dead_links_count += 1
                continue

            if result.get("model_type") == "nzb" and not is_source_online(result.get("source", "")):
                continue

            quality = result.get("quality", "Unknown")
            language = result.get("language", "Unknown")
            hoster = result.get("hoster", "Unknown")
            size = result.get("size", "Unknown")
            display_name = result.get("display_name", "Unknown")
            episode_num = result.get("episode") if result.get("episode") is not None else episode
            season_num = result.get("season") if result.get("season") is not None else season

            service_name = result.get("debrid_service", "alldebrid")
            service_abbr = DEBRID_ABBREVIATIONS.get(service_name, "AD")

            debrid_filename = result.get("debrid_filename")
            if debrid_filename and debrid_filename.strip():
                if not (debrid_filename.startswith("Unknown") and debrid_filename.endswith("Link")):
                    display_name = debrid_filename

            user_languages = config.get("languages", [])
            if user_languages and language.startswith(MULTI_LANGUAGE_PREFIX.title()) and language.endswith(")"):
                multi_langs = language[MULTI_PREFIX_LENGTH:-1]
                multi_langs_list = [lang.strip() for lang in multi_langs.split(",")]

                filtered_langs = [lang for lang in multi_langs_list if lang in user_languages]

                if filtered_langs:
                    filtered_multi = f"Multi ({', '.join(filtered_langs)})"
                    language = filtered_multi

                    if "Multi (" in display_name and ")" in display_name:
                        pattern = r"Multi \([^)]+\)"
                        display_name = re.sub(pattern, filtered_multi, display_name)

            cache_status = result.get("cache_status", "uncached")
            cache_emoji = "⚡" if cache_status == "cached" else "⏳"

            token_data = {"l": link, "s": service_name}

            user_uuid = config.get("_user_uuid")
            enc_password = config.get("_enc_password")

            if user_uuid and enc_password:
                token_data["u"] = user_uuid
                token_data["p"] = enc_password
            else:
                token_data["c"] = encode_config_to_base64(config)

            source = result.get("source", "")
            if source:
                token_data["so"] = source
            if season_num:
                token_data["se"] = season_num
            if episode_num:
                token_data["ep"] = episode_num
            if content_type:
                token_data["ct"] = content_type
            hoster = result.get("hoster", "")
            if hoster:
                token_data["h"] = hoster
            if service_name == "nzbdav" and display_name and display_name != "Unknown":
                token_data["t"] = display_name

            token = encode_playback_token(token_data)
            filename = quote_path_segment(display_name) if display_name and display_name != "Unknown" else "stream"
            playback_url = f"{base_url}/playback/{token}/{filename}"

            resolution = extract_resolution(quality)
            resolution_line = f"\n{resolution}" if resolution and resolution != "Unknown" else ""
            stream_name = f"[{service_abbr} {cache_emoji}]\n{settings.ADDON_NAME}{resolution_line}"

            description_parts = []
            if language and language != "Unknown":
                description_parts.append(f"🌍 {language}")
            if quality and quality != "Unknown":
                description_parts.append(f"🎞️ {quality}")

            size_year_parts = []
            if size and size != "Unknown":
                size_year_parts.append(f"📦 {size}")
            if year:
                size_year_parts.append(f"📅 {year}")
            if size_year_parts:
                description_parts.append(" ".join(size_year_parts))

            source = result.get("source", "Wawacity")
            source_line = f"🌐 {source}"

            if hoster and hoster != "Unknown":
                source_line += f" ☁️ {hoster}"

            if source_line:
                description_parts.append(source_line)

            if display_name and display_name != "Unknown":
                description_parts.append(f"📁 {display_name}")

            q_key = quality_sort_key(result)
            size_bytes = parse_size_to_bytes(size)
            lang_priority = 0 if language != "Unknown" else 1

            # Sub-priority for French Audio vs VOSTFR
            french_sub_priority = 0
            if config.get("prioritize_vff_multi", False) and language == "French":
                display_name_upper = display_name.upper() if display_name else ""
                is_vostfr = any(tag in display_name_upper for tag in ["VOSTFR", "SUBFRENCH", "SUBFR", ".SUB."])
                is_audio = any(tag in display_name_upper for tag in ["VFF", "MULTI", "VF", "TRUEFRENCH"])
                if is_audio and not is_vostfr:
                    french_sub_priority = 0
                elif is_vostfr:
                    french_sub_priority = 1

            # Stream type priority (DDL/NZB vs Torrent)
            is_torrent = (result.get("model_type") == "torrent")
            pref = config.get("stream_type_preference", "ddl_first")
            if pref == "torrent_first":
                stream_type_priority = 0 if is_torrent else 1
            else:  # ddl_first
                stream_type_priority = 1 if is_torrent else 0

            streams.append({
                "name": stream_name,
                "description": "\r\n".join(description_parts),
                "behaviorHints": {
                    "filename": display_name
                },
                "url": playback_url,
                "_sort_values": {
                    "cached": 0 if cache_status == "cached" else 1,
                    "resolution": q_key[0],
                    "size": -size_bytes,
                    "release_type": q_key[1],
                    "language": (lang_priority, french_sub_priority, language),
                    "stream_type": stream_type_priority,
                },
            })

        default_sort_order = ["cached", "resolution", "size", "release_type", "language", "stream_type"]
        valid_keys = set(default_sort_order)
        sort_order = config.get("sort_order") or default_sort_order
        sort_order = [k for k in sort_order if k in valid_keys]
        for k in default_sort_order:
            if k not in sort_order:
                sort_order.append(k)

        # If user has a non-default stream_type_preference and stream_type is at the
        # tail of the order (i.e. wasn't manually moved up), promote it to right
        # after "cached" so the preference is the dominant secondary criterion.
        stream_type_pref = config.get("stream_type_preference", "ddl_first")
        if stream_type_pref != "ddl_first" and "stream_type" in sort_order:
            st_idx = sort_order.index("stream_type")
            cached_idx = sort_order.index("cached") if "cached" in sort_order else -1
            # Only auto-promote if the user left stream_type near the end
            # (i.e. they haven't manually dragged it up above language/resolution)
            if st_idx > (cached_idx + 1):
                sort_order.pop(st_idx)
                sort_order.insert(cached_idx + 1, "stream_type")

        streams.sort(key=lambda s: tuple(s["_sort_values"][k] for k in sort_order))

        for s in streams:
            del s["_sort_values"]

        stream_logger.debug(f"Skipped {dead_links_count} dead links")
        stream_logger.debug(f"Returning {len(streams)} stream(s)")
        return streams

    async def get_streams(self, content_type: str, content_id: str,
                          config: Dict, base_url: str) -> List[Dict]:
        start_time = time.time()

        media_info = extract_media_info(content_id, content_type)

        if media_info.get("kitsu_id"):
            override = self._check_kitsu_imdb_override(media_info["kitsu_id"])
            if override:
                media_info["imdb_id"] = override["imdb_id"]
                media_info["season"] = str(override["season"])

                episode = int(media_info.get("episode", 1))
                if override["previous_parts_kitsu_ids"]:
                    offset = await self._get_episode_offset_from_previous_parts(override["previous_parts_kitsu_ids"])
                    episode = offset + episode
                    stream_logger.debug(f"Episode with offset: {offset} + {media_info.get('episode')} = {episode}")
                media_info["episode"] = str(episode)

                content_type = "series"
            else:
                return await self._handle_kitsu_request(media_info, config, base_url, start_time)

        metadata = await self._get_metadata(
            media_info["imdb_id"],
            config.get("tmdb_api_token", "")
        )

        if not metadata:
            stream_logger.error(f"TMDB metadata failed for {media_info['imdb_id']}")
            return []

        search_config = {
            **config,
            "enable_full_season": should_enable_full_season(config)
        }

        results = await self._search_content(
            metadata["title"],
            metadata.get("year"),
            content_type,
            media_info.get("season"),
            media_info.get("episode"),
            metadata.get("enhanced"),
            search_config
        )

        if not results:
            stream_logger.debug(f"No content: '{metadata['title']}' ({metadata.get('year', 'Unknown')})")
            return []

        results = deduplicate_and_sort_results(results, quality_sort_key)

        results = apply_all_filters(results, config)

        elapsed = time.time() - start_time
        timeout = config.get("stream_request_timeout", settings.STREAM_REQUEST_TIMEOUT)
        remaining_time = max(0, timeout - elapsed)

        debrid_services = get_debrid_services(config)
        enriched_results = await self._check_cache_and_enrich(
            results, debrid_services, config, remaining_time,
            media_info.get("season"), media_info.get("episode")
        )

        streams = await self._format_streams(
            enriched_results,
            config,
            base_url,
            media_info.get("season"),
            media_info.get("episode"),
            metadata.get("year"),
            content_type
        )

        streams = filter_archive_files(streams)

        excluded_keywords = config.get("excluded_keywords", [])
        if excluded_keywords:
            filtered_streams = filter_excluded_keywords(streams, excluded_keywords)
            excluded_count = len(streams) - len(filtered_streams)
            if excluded_count > 0:
                stream_logger.debug(f"Excluded {excluded_count} streams")
            streams = filtered_streams

        return streams

    async def _get_metadata(self, imdb_id: str, tmdb_api_token: str) -> Optional[Dict]:
        if not tmdb_api_token or not tmdb_api_token.strip():
            stream_logger.error("No TMDB token")
            return None

        try:
            enhanced_metadata = await tmdb_service.get_enhanced_metadata(imdb_id, tmdb_api_token)
            if enhanced_metadata:
                return {
                    "title": enhanced_metadata["titles"][0] if enhanced_metadata["titles"] else "",
                    "year": enhanced_metadata["year"],
                    "type": enhanced_metadata["type"],
                    "enhanced": enhanced_metadata
                }

            return await tmdb_service.get_metadata(imdb_id, tmdb_api_token)

        except Exception as e:
            stream_logger.error(f"Metadata fetch error: {type(e).__name__}: {e}")
            return None

    async def _search_content(self, title: str, year: Optional[str],
                              content_type: str, season: Optional[str],
                              episode: Optional[str], metadata: Optional[Dict] = None, config: Dict = None) -> List[Dict]:
        if metadata and metadata.get("content_type") == "anime":
            return await self._search_anime(title, year, season, episode, metadata, config)
        elif content_type == "series":
            return await self._search_series(title, year, season, episode, metadata, config)
        else:
            return await self._search_movie(title, year, metadata, config)

    async def _search_source_with_cache(
        self,
        source_name: str,
        content_type: str,
        do_search,
        title: str,
        year: Optional[str],
        season: Optional[str] = None,
        episode: Optional[str] = None,
        metadata: Optional[Dict] = None,
        use_episode_key: bool = False,
        filter_episodes: bool = True
    ) -> List[Dict]:
        """Generic cached search with lock, remote fallback, and background refresh."""
        if use_episode_key and season and episode:
            base_types = {"series": "series", "anime": "anime"}
            base_type = base_types.get(content_type, content_type)
            cache_type = f"{source_name}_{base_type}_s{season}e{episode}"
        else:
            type_map = {"movies": "movie", "series": "series", "anime": "anime"}
            mapped_type = type_map.get(content_type, content_type)
            cache_type = f"{source_name}_{mapped_type}"

        # Build lock type (always at the content_type level, not per-episode)
        lock_map = {"movies": "movie", "series": "series", "anime": "anime"}
        lock_type = f"{source_name}_{lock_map.get(content_type, content_type)}"

        if use_episode_key and (not season or not episode):
            try:
                return await do_search()
            except Exception as e:
                stream_logger.error(f"{source_name} search failed: {type(e).__name__}: {e}")
                return []

        def _maybe_filter(results):
            if filter_episodes and results:
                return self._filter_episode_results(results, season, episode, content_type, metadata)
            return results

        async def _background_refresh():
            try:
                stream_logger.debug(f"Background refresh: {cache_type} {title} ({year})")
                results = await do_search()
                if results:
                    await set_cache(database, cache_type, title, year, results, settings.CONTENT_CACHE_TTL)
                    stream_logger.debug(f"Background refresh done: {len(results)} results")
            except Exception as e:
                stream_logger.error(f"Background refresh failed: {type(e).__name__}: {e}")

        if settings.CONTENT_CACHE_MODE == "live":
            cached_results, is_valid = await get_cache_with_status(database, cache_type, title, year)

            if cached_results is not None and is_valid:
                asyncio.create_task(_background_refresh())
                return _maybe_filter(cached_results)

            if cached_results is None:
                remote_result = await fetch_remote_cache(cache_type, title, year)
                if isinstance(remote_result, tuple):
                    remote_data, should_store = remote_result
                    if remote_data:
                        if should_store:
                            await set_cache_if_not_exists(database, cache_type, title, year, remote_data, settings.CONTENT_CACHE_TTL)
                        asyncio.create_task(_background_refresh())
                        return _maybe_filter(remote_data)

            async with SearchLock(lock_type, title, year):
                cached_results = await get_cache(database, cache_type, title, year)
                if cached_results is not None:
                    return _maybe_filter(cached_results)

                results = await do_search()

                if results:
                    await set_cache(database, cache_type, title, year, results, settings.CONTENT_CACHE_TTL)

                return _maybe_filter(results)

        # Background cache mode (default)
        async with SearchLock(lock_type, title, year):
            cached_results, should_store = await get_cache_parallel(database, cache_type, title, year)
            if cached_results is not None:
                stream_logger.debug(f"Using cached results for {content_type}")
                if should_store:
                    await set_cache_if_not_exists(database, cache_type, title, year, cached_results, settings.CONTENT_CACHE_TTL)
                return _maybe_filter(cached_results)

            results = await do_search()

            if results:
                await set_cache(database, cache_type, title, year, results, settings.CONTENT_CACHE_TTL)

            return _maybe_filter(results)

    async def _search_darki_api_with_kitsu_direct_mapping(
        self,
        title: str,
        year: Optional[str],
        metadata: Dict,
        absolute_episode: int,
        config: Optional[Dict] = None
    ) -> List[Dict]:
        """Map Kitsu absolute episode to Darki-API season/episode and search."""
        try:
            darki_api_kitsu_metadata = {
                "titles": metadata.get("titles", [title]),
                "year": year
            }

            metadata_logger.debug("Kitsu→Darki: searching anime")

            darki_api_scraper = BaseDarkiAPI()
            search_titles = darki_api_kitsu_metadata.get("titles", [title])
            darki_api_result = await darki_api_scraper.search_by_titles(search_titles, darki_api_kitsu_metadata)

            if not darki_api_result:
                metadata_logger.debug("Kitsu→Darki: anime not found")
                return []

            title_id = darki_api_result.get("id")

            if not title_id:
                metadata_logger.error("Kitsu→Darki: no title ID")
                return []

            metadata_logger.debug(f"Kitsu→Darki: mapping episode {absolute_episode}")

            tmdb_api_token = config.get("tmdb_api_token") if config else None

            darki_mapping = await darki_api_scraper.map_kitsu_absolute_to_darki_season(
                title_id, absolute_episode, tmdb_api_token
            )

            if not darki_mapping:
                metadata_logger.error("Kitsu→Darki: mapping failed")
                return []

            darki_season, darki_episode = darki_mapping

            metadata_logger.debug(f"Kitsu→Darki: S{darki_season}E{darki_episode}")

            return await self._search_source_with_cache(
                "darki_api", "anime",
                lambda: darki_api_anime_scraper.search(title, year, darki_api_kitsu_metadata, darki_season, darki_episode, config),
                title, year, darki_season, darki_episode,
                darki_api_kitsu_metadata, use_episode_key=True, filter_episodes=False
            )

        except Exception as e:
            metadata_logger.error(f"Kitsu→Darki error: {type(e).__name__}: {e}")
            return []

    async def _search_movix_with_kitsu_direct_mapping(
        self,
        title: str,
        year: Optional[str],
        metadata: Dict,
        absolute_episode: int,
        config: Optional[Dict] = None
    ) -> List[Dict]:
        """Map Kitsu absolute episode to Movix season/episode and search."""
        try:
            search_titles = metadata.get("titles", [title])
            movix_kitsu_metadata = {
                "titles": search_titles,
                "original_titles": search_titles,
                "all_titles": metadata.get("all_titles", search_titles),
                "year": year
            }

            metadata_logger.debug("Kitsu→Movix: searching anime")

            movix_scraper = BaseMovix()
            movix_result = await movix_scraper.search_by_titles(search_titles, movix_kitsu_metadata)

            if not movix_result:
                metadata_logger.debug("Kitsu→Movix: anime not found")
                return []

            title_id = movix_result.get("id")

            if not title_id:
                metadata_logger.error("Kitsu→Movix: no title ID")
                return []

            metadata_logger.debug(f"Kitsu→Movix: mapping episode {absolute_episode}")

            tmdb_api_token = config.get("tmdb_api_token") if config else None
            imdb_id = movix_result.get("imdb_id")

            movix_mapping = await movix_scraper.map_kitsu_absolute_to_movix_season(
                title_id, absolute_episode, tmdb_api_token, imdb_id
            )

            if not movix_mapping:
                metadata_logger.error("Kitsu→Movix: mapping failed")
                return []

            movix_season, movix_episode = movix_mapping

            metadata_logger.debug(f"Kitsu→Movix: S{movix_season}E{movix_episode}")

            return await self._search_source_with_cache(
                "movix", "anime",
                lambda: movix_anime_scraper.search(title, year, movix_kitsu_metadata, movix_season, movix_episode, config),
                title, year, movix_season, movix_episode,
                movix_kitsu_metadata, use_episode_key=True, filter_episodes=False
            )

        except Exception as e:
            metadata_logger.error(f"Kitsu→Movix error: {type(e).__name__}: {e}")
            return []

    def _season_episode_to_absolute(self, season: int, episode: int, seasons_data: List[Dict]) -> Optional[int]:
        if not seasons_data:
            return None

        absolute = 0
        for s in seasons_data:
            s_num = s.get("number", 0)
            ep_count = s.get("episode_count", 0)

            if s_num < season:
                absolute += ep_count
            elif s_num == season:
                return absolute + episode

        return None

    def _filter_episode_results(self, results: List[Dict], season: Optional[str],
                                episode: Optional[str], content_type: str,
                                metadata: Optional[Dict] = None) -> List[Dict]:
        if not results or not season or not episode:
            return results

        seasons_data = None
        if metadata:
            seasons_data = metadata.get("seasons")

        if seasons_data:
            try:
                target_absolute = self._season_episode_to_absolute(
                    int(season), int(episode), seasons_data
                )

                if target_absolute:
                    filtered = []
                    for r in results:
                        r_season = r.get("season")
                        r_episode = r.get("episode")

                        if r_season and r_episode:
                            r_absolute = self._season_episode_to_absolute(
                                int(r_season), int(r_episode), seasons_data
                            )
                            if r_absolute == target_absolute:
                                filtered.append(r)

                    stream_logger.debug(f"Absolute filtered S{season}E{episode} (abs:{target_absolute}): {len(filtered)} results")
                    return filtered

            except (ValueError, TypeError):
                pass

        if content_type == "anime":
            season_str = str(season) if season is not None else None
            episode_str = str(episode) if episode is not None else None
            filtered = [
                r for r in results
                if r.get("season") == season_str and r.get("episode") == episode_str
            ]
        else:
            filtered = [
                r for r in results
                if r.get("season") == season and r.get("episode") == episode
            ]

        stream_logger.debug(f"Filtered S{season}E{episode}: {len(filtered)} results")
        return filtered

    def _get_early_stop_config(self, config: Dict) -> Dict:
        return {
            "enabled": config.get("early_stop", False),
            "min_streams": max(0, config.get("early_stop_min_streams", 0)),
            "min_hosts": max(0, config.get("early_stop_min_hosts", 0)),
            "sources": config.get("early_stop_sources", []),
            "hosts": config.get("early_stop_hosts", []),
            "resolutions": config.get("early_stop_resolutions", []),
            "include_nzb": config.get("early_stop_include_nzb", False)
        }

    def _is_source_allowed_for_content(self, source_name: str, content_name: str, config: Dict) -> bool:
        if not config or not config.get("source_content_types", False):
            return True
        sct_config = config.get("source_content_types_config", {})
        if not sct_config or source_name not in sct_config:
            return True
        allowed = content_name in sct_config[source_name]
        if not allowed:
            stream_logger.debug(f"[Content-Filter] {source_name} skipped for '{content_name}' (allowed: {sct_config[source_name]})")
        return allowed

    def _check_early_stop_conditions(self, results: List[Dict], early_stop_config: Dict, source_name: str) -> bool:
        stream_logger.debug(f"[Early-Stop] Check: source={source_name}, config={early_stop_config}")

        if not early_stop_config["enabled"]:
            stream_logger.debug("[Early-Stop] Disabled")
            return False

        has_any_condition = (
            early_stop_config["min_streams"] > 0
            or early_stop_config["min_hosts"] > 0
            or early_stop_config["resolutions"]
            or early_stop_config["sources"]
        )
        if not has_any_condition:
            stream_logger.debug("[Early-Stop] No conditions configured")
            return False

        if early_stop_config["sources"] and source_name not in early_stop_config["sources"]:
            stream_logger.debug(f"[Early-Stop] Source '{source_name}' not in allowed sources {early_stop_config['sources']}")
            return False

        if early_stop_config["include_nzb"]:
            filtered_results = results
        else:
            filtered_results = [r for r in results if "/nzb/" not in r.get("link", "")]

        unique_links = set(r.get("link", "") for r in filtered_results if r.get("link"))
        unique_count = len(unique_links)
        stream_logger.debug(f"[Early-Stop] {unique_count} unique streams (min required: {early_stop_config['min_streams']})")

        if early_stop_config["min_streams"] > 0 and unique_count < early_stop_config["min_streams"]:
            stream_logger.debug(f"[Early-Stop] FAILED - not enough streams ({unique_count} < {early_stop_config['min_streams']})")
            return False

        if early_stop_config["min_hosts"] > 0:
            hosts_in_results = set()
            for r in filtered_results:
                host = r.get("hoster", "").lower()
                if host:
                    hosts_in_results.add(host)

            stream_logger.debug(f"[Early-Stop] {len(hosts_in_results)} unique hosts found: {hosts_in_results} (min required: {early_stop_config['min_hosts']})")

            if early_stop_config["hosts"]:
                matching_hosts = hosts_in_results.intersection(set(h.lower() for h in early_stop_config["hosts"]))
                if len(matching_hosts) < early_stop_config["min_hosts"]:
                    stream_logger.debug(f"[Early-Stop] FAILED - not enough matching hosts ({len(matching_hosts)} < {early_stop_config['min_hosts']})")
                    return False
            else:
                if len(hosts_in_results) < early_stop_config["min_hosts"]:
                    stream_logger.debug(f"[Early-Stop] FAILED - not enough hosts ({len(hosts_in_results)} < {early_stop_config['min_hosts']})")
                    return False

        if early_stop_config["resolutions"]:
            required_resolutions = set(r.lower() for r in early_stop_config["resolutions"])
            has_required_resolution = False
            qualities_found = [r.get("quality", "") for r in filtered_results]
            stream_logger.debug(f"[Early-Stop] Checking resolutions, required={required_resolutions}, found qualities={qualities_found}")

            for r in filtered_results:
                quality = r.get("quality", "")
                if quality:
                    quality_upper = quality.upper()
                    if "2160" in quality_upper or "4K" in quality_upper:
                        if "2160p" in required_resolutions:
                            has_required_resolution = True
                            break
                    elif "1080" in quality_upper:
                        if "1080p" in required_resolutions:
                            has_required_resolution = True
                            break
                    elif "720" in quality_upper:
                        if "720p" in required_resolutions:
                            has_required_resolution = True
                            break
                    elif "480" in quality_upper:
                        if "480p" in required_resolutions:
                            has_required_resolution = True
                            break
            if not has_required_resolution:
                stream_logger.debug("[Early-Stop] FAILED - no matching resolution found")
                return False

        stream_logger.debug("[Early-Stop] ALL CONDITIONS MET - triggering")
        return True

    async def _search_content_common(self, content_type: str, content_name: str,
                                     wawacity_scraper, darki_scraper, free_telecharger_scraper,
                                     wasource_scraper, movix_scraper, webshare_scraper,
                                     title: str, year: Optional[str],
                                     metadata: Optional[Dict] = None,
                                     season: Optional[str] = None, episode: Optional[str] = None,
                                     config: Dict = None, use_episode_cache: bool = False) -> List[Dict]:
        supported_sources = self._get_supported_sources(config) if config else settings.ALLDEBRID_SUPPORTED_SOURCES
        early_stop_config = self._get_early_stop_config(config) if config else {"enabled": False, "min_streams": 3, "min_hosts": 2, "sources": [], "hosts": []}

        tasks_with_sources = []

        if "wasource" in supported_sources and self._is_source_allowed_for_content("wasource", content_name, config):
            if use_episode_cache:
                coro = wasource_scraper.search(title, year, metadata, season, episode, config)
            else:
                coro = wasource_scraper.search(title, year, metadata, config)
            tasks_with_sources.append(("wasource", coro))

        if "wawacity" in supported_sources and self._is_source_allowed_for_content("wawacity", content_name, config):
            coro = self._search_source_with_cache(
                "wawacity", content_type, lambda: wawacity_scraper.search(title, year, metadata),
                title, year, season if use_episode_cache else None, episode if use_episode_cache else None,
                metadata, use_episode_key=False, filter_episodes=True)
            tasks_with_sources.append(("wawacity", coro))

        if "free-telecharger" in supported_sources and self._is_source_allowed_for_content("free-telecharger", content_name, config):
            coro = self._search_source_with_cache(
                "free_telecharger", content_type, lambda: free_telecharger_scraper.search(title, year, metadata),
                title, year, season if use_episode_cache else None, episode if use_episode_cache else None,
                metadata, use_episode_key=False, filter_episodes=True)
            tasks_with_sources.append(("free-telecharger", coro))

        if "darki-api" in supported_sources and self._is_source_allowed_for_content("darki-api", content_name, config):
            if use_episode_cache:
                coro = self._search_source_with_cache(
                    "darki_api", content_type, lambda: darki_scraper.search(title, year, metadata, season, episode, config),
                    title, year, season, episode, metadata, use_episode_key=True, filter_episodes=False)
            else:
                coro = self._search_source_with_cache(
                    "darki_api", content_type, lambda: darki_scraper.search(title, year, metadata, config),
                    title, year, metadata=metadata, use_episode_key=False, filter_episodes=False)
            tasks_with_sources.append(("darki-api", coro))

        if "movix" in supported_sources and self._is_source_allowed_for_content("movix", content_name, config):
            if use_episode_cache:
                coro = self._search_source_with_cache(
                    "movix", content_type, lambda: movix_scraper.search(title, year, metadata, season, episode, config),
                    title, year, season, episode, metadata, use_episode_key=True, filter_episodes=False)
            else:
                coro = self._search_source_with_cache(
                    "movix", content_type, lambda: movix_scraper.search(title, year, metadata, config),
                    title, year, metadata=metadata, use_episode_key=False, filter_episodes=False)
            tasks_with_sources.append(("movix", coro))

        if "webshare" in supported_sources and self._is_source_allowed_for_content("webshare", content_name, config):
            if use_episode_cache:
                coro = self._search_source_with_cache(
                    "webshare", content_type, lambda: webshare_scraper.search(title, year, metadata, season, episode, config),
                    title, year, season, episode, metadata, use_episode_key=True, filter_episodes=False)
            else:
                coro = self._search_source_with_cache(
                    "webshare", content_type, lambda: webshare_scraper.search(title, year, metadata, config),
                    title, year, metadata=metadata, use_episode_key=False, filter_episodes=False)
            tasks_with_sources.append(("webshare", coro))

        if "yggreborn" in supported_sources and self._is_source_allowed_for_content("yggreborn", content_name, config):
            if use_episode_cache:
                coro = self._search_source_with_cache(
                    "yggreborn", content_type, lambda: yggreborn_scraper.search(title, year, metadata, season, episode, config),
                    title, year, season, episode, metadata, use_episode_key=True, filter_episodes=False)
            else:
                coro = self._search_source_with_cache(
                    "yggreborn", content_type, lambda: yggreborn_scraper.search(title, year, metadata, config=config),
                    title, year, metadata=metadata, use_episode_key=False, filter_episodes=False)
            tasks_with_sources.append(("yggreborn", coro))

        if "tr4ker" in supported_sources and self._is_source_allowed_for_content("tr4ker", content_name, config):
            if use_episode_cache:
                coro = self._search_source_with_cache(
                    "tr4ker", content_type, lambda: tr4ker_scraper.search(title, year, metadata, season, episode, config),
                    title, year, season, episode, metadata, use_episode_key=True, filter_episodes=False)
            else:
                coro = self._search_source_with_cache(
                    "tr4ker", content_type, lambda: tr4ker_scraper.search(title, year, metadata, config=config),
                    title, year, metadata=metadata, use_episode_key=False, filter_episodes=False)
            tasks_with_sources.append(("tr4ker", coro))

        if "torr9" in supported_sources and self._is_source_allowed_for_content("torr9", content_name, config):
            if use_episode_cache:
                coro = self._search_source_with_cache(
                    "torr9", content_type, lambda: torr9_scraper.search(title, year, metadata, season, episode, config),
                    title, year, season, episode, metadata, use_episode_key=True, filter_episodes=False)
            else:
                coro = self._search_source_with_cache(
                    "torr9", content_type, lambda: torr9_scraper.search(title, year, metadata, config=config),
                    title, year, metadata=metadata, use_episode_key=False, filter_episodes=False)
            tasks_with_sources.append(("torr9", coro))

        if "c411" in supported_sources and self._is_source_allowed_for_content("c411", content_name, config):
            if use_episode_cache:
                coro = self._search_source_with_cache(
                    "c411", content_type, lambda: c411_scraper.search(title, year, metadata, season, episode, config),
                    title, year, season, episode, metadata, use_episode_key=True, filter_episodes=False)
            else:
                coro = self._search_source_with_cache(
                    "c411", content_type, lambda: c411_scraper.search(title, year, metadata, config=config),
                    title, year, metadata=metadata, use_episode_key=False, filter_episodes=False)
            tasks_with_sources.append(("c411", coro))

        if "gemini" in supported_sources and self._is_source_allowed_for_content("gemini", content_name, config):
            if use_episode_cache:
                coro = self._search_source_with_cache(
                    "gemini", content_type, lambda: gemini_scraper.search(title, year, metadata, season, episode, config),
                    title, year, season, episode, metadata, use_episode_key=True, filter_episodes=False)
            else:
                coro = self._search_source_with_cache(
                    "gemini", content_type, lambda: gemini_scraper.search(title, year, metadata, config=config),
                    title, year, metadata=metadata, use_episode_key=False, filter_episodes=False)
            tasks_with_sources.append(("gemini", coro))

        if "generation-free" in supported_sources and self._is_source_allowed_for_content("generation-free", content_name, config):
            if use_episode_cache:
                coro = self._search_source_with_cache(
                    "generation_free", content_type, lambda: generationfree_scraper.search(title, year, metadata, season, episode, config),
                    title, year, season, episode, metadata, use_episode_key=True, filter_episodes=False)
            else:
                coro = self._search_source_with_cache(
                    "generation_free", content_type, lambda: generationfree_scraper.search(title, year, metadata, config=config),
                    title, year, metadata=metadata, use_episode_key=False, filter_episodes=False)
            tasks_with_sources.append(("generation-free", coro))

        if "zilean" in supported_sources and self._is_source_allowed_for_content("zilean", content_name, config):
            if use_episode_cache:
                coro = self._search_source_with_cache(
                    "zilean", content_type, lambda: zilean_scraper.search(title, year, metadata, season, episode, config),
                    title, year, season, episode, metadata, use_episode_key=True, filter_episodes=False)
            else:
                coro = self._search_source_with_cache(
                    "zilean", content_type, lambda: zilean_scraper.search(title, year, metadata, config=config),
                    title, year, metadata=metadata, use_episode_key=False, filter_episodes=False)
            tasks_with_sources.append(("zilean", coro))

        if not tasks_with_sources:
            return []

        if not early_stop_config["enabled"]:
            tasks = [t[1] for t in tasks_with_sources]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)

            all_results = []
            for result in results_list:
                if isinstance(result, list):
                    all_results.extend(result)
                elif isinstance(result, Exception):
                    stream_logger.error(f"{content_name.title()} search failed: {type(result).__name__}: {result}")

            return all_results

        all_results = []
        pending_tasks = {}
        early_stop_triggered = False

        for source_name, coro in tasks_with_sources:
            task = asyncio.create_task(coro)
            pending_tasks[task] = source_name

        try:
            while pending_tasks:
                done, _ = await asyncio.wait(
                    pending_tasks.keys(),
                    return_when=asyncio.FIRST_COMPLETED
                )

                for task in done:
                    source_name = pending_tasks.pop(task)

                    try:
                        result = task.result()
                        if isinstance(result, list):
                            all_results.extend(result)
                            stream_logger.debug(f"[Early-Stop] {source_name} returned {len(result)} results")

                            if not early_stop_triggered and self._check_early_stop_conditions(all_results, early_stop_config, source_name):
                                early_stop_triggered = True
                                stream_logger.info(f"[Early-Stop] Triggered by {source_name} with {len(all_results)} results")

                    except Exception as e:
                        stream_logger.error(f"{content_name.title()} search failed ({source_name}): {type(e).__name__}: {e}")

                if early_stop_triggered and pending_tasks:
                    stream_logger.debug(f"[Early-Stop] Returning early, {len(pending_tasks)} sources still running in background")

                    async def background_collector(remaining_tasks: Dict, current_results: List[Dict]):
                        for remaining_task in remaining_tasks:
                            try:
                                result = await remaining_task
                                if isinstance(result, list):
                                    current_results.extend(result)
                                    stream_logger.debug(f"[Early-Stop] Background: collected {len(result)} additional results")
                            except Exception as e:
                                stream_logger.error(f"[Early-Stop] Background task failed: {type(e).__name__}: {e}")

                    asyncio.create_task(background_collector(list(pending_tasks.keys()), all_results))
                    break

        except Exception as e:
            stream_logger.error(f"[Early-Stop] Error: {type(e).__name__}: {e}")
            for task in pending_tasks:
                task.cancel()

        return all_results

    async def _search_movie(self, title: str, year: Optional[str], metadata: Optional[Dict] = None, config: Dict = None) -> List[Dict]:
        return await self._search_content_common("movies", "movie", movie_scraper, darki_api_movie_scraper,
                                                 free_telecharger_movie_scraper, wasource_movie_scraper, movix_movie_scraper, webshare_movie_scraper,
                                                 title, year, metadata, None, None, config, use_episode_cache=False)

    async def _search_anime(self, title: str, year: Optional[str],
                            season: Optional[str], episode: Optional[str], metadata: Optional[Dict] = None, config: Dict = None) -> List[Dict]:
        return await self._search_content_common("anime", "anime", anime_scraper, darki_api_anime_scraper,
                                                 free_telecharger_anime_scraper, wasource_anime_scraper, movix_anime_scraper, webshare_anime_scraper,
                                                 title, year, metadata, season, episode, config, use_episode_cache=True)

    async def _search_series(self, title: str, year: Optional[str],
                             season: Optional[str], episode: Optional[str], metadata: Optional[Dict] = None, config: Dict = None) -> List[Dict]:
        return await self._search_content_common("series", "series", series_scraper, darki_api_series_scraper,
                                                 free_telecharger_series_scraper, wasource_series_scraper, movix_series_scraper, webshare_series_scraper,
                                                 title, year, metadata, season, episode, config, use_episode_cache=True)

    async def resolve_link(self, link: str, config: Dict, season: Optional[str] = None, episode: Optional[str] = None, service: Optional[str] = None, content_type: Optional[str] = None, title: Optional[str] = None, source: Optional[str] = None, hoster: Optional[str] = None) -> Optional[str]:
        if service:
            debrid_service = self._get_debrid_service(service)
            debrid_api_key = get_debrid_api_key(config, service)
        else:
            debrid_services = get_debrid_services(config)
            if debrid_services:
                first_service = debrid_services[0]
                service = first_service.get("service", "alldebrid")
                debrid_service = self._get_debrid_service(service)
                debrid_api_key = first_service.get("api_key", "")
            else:
                debrid_service = alldebrid_service
                debrid_api_key = ""

        if service == "nzbdav":
            nzbdav_url = config.get("nzbdav_url", "")
            webdav_user = config.get("webdav_user", "")
            webdav_password = config.get("webdav_password", "")
            category = "Movies" if content_type == "movie" else "TV"
            result = await debrid_service.convert_link(link, debrid_api_key, season, episode, nzbdav_url, webdav_user, webdav_password, category, title, hoster=hoster)
        else:
            result = await debrid_service.convert_link(link, debrid_api_key, season, episode, hoster=hoster)

        if result == "LINK_DOWN":
            await mark_dead_link(link, settings.DEAD_LINK_TTL)

        return result

    async def resolve_link_with_response(self, link: str, config: Dict, season: Optional[str] = None, episode: Optional[str] = None, service: Optional[str] = None, content_type: Optional[str] = None, title: Optional[str] = None, source: Optional[str] = None, hoster: Optional[str] = None):
        direct_link = await self.resolve_link(link, config, season, episode, service, content_type, title, source, hoster)

        if direct_link and direct_link not in ["LINK_DOWN", "RETRY_ERROR", "FATAL_ERROR", "LINK_UNCACHED"]:
            return RedirectResponse(url=direct_link, status_code=302)
        elif direct_link == "LINK_DOWN":
            return FileResponse("wastream/public/link_down.mp4")
        elif direct_link == "LINK_UNCACHED":
            return FileResponse("wastream/public/uncached.mp4")
        elif direct_link == "RETRY_ERROR":
            return FileResponse("wastream/public/retry_error.mp4")
        else:
            return FileResponse("wastream/public/fatal_error.mp4")

    async def _handle_kitsu_request(self, media_info: Dict, config: Dict, base_url: str, start_time: float) -> List[Dict]:
        kitsu_id = media_info.get("kitsu_id")
        episode = media_info.get("episode")

        config = {
            **config,
            "enable_full_season": should_enable_full_season(config)
        }

        if not kitsu_id:
            stream_logger.error("Empty Kitsu ID")
            return []

        kitsu_metadata = await kitsu_service.get_metadata(kitsu_id)
        if not kitsu_metadata:
            stream_logger.error(f"Kitsu metadata failed: {kitsu_id}")
            return []

        if kitsu_metadata.get("subtype") == "movie":
            search_title = kitsu_metadata["title"]
            search_year = kitsu_metadata.get("year")
            kitsu_titles = kitsu_metadata.get("search_titles", [kitsu_metadata["title"]])

            enhanced_kitsu_metadata = {
                "titles": kitsu_titles,
                "original_titles": kitsu_titles,
                "all_titles": kitsu_metadata.get("all_titles", [kitsu_metadata["title"]]),
                "year": search_year,
                "type": "movie",
                "content_type": "movies"
            }

            results = await self._search_movie(search_title, search_year, enhanced_kitsu_metadata, config)

            if not results:
                stream_logger.debug(f"No content: Kitsu {kitsu_metadata['title']}")
                return []

            results = deduplicate_and_sort_results(results, quality_sort_key)

            results = apply_all_filters(results, config)

            elapsed = time.time() - start_time
            timeout = config.get("stream_request_timeout", settings.STREAM_REQUEST_TIMEOUT)
            remaining_time = max(0, timeout - elapsed)

            debrid_services = get_debrid_services(config)
            enriched_results = await self._check_cache_and_enrich(
                results, debrid_services, config, remaining_time, None, None
            )

            streams = await self._format_streams(
                enriched_results,
                config,
                base_url,
                None,
                None,
                kitsu_metadata.get("year"),
                "movie"
            )

            streams = filter_archive_files(streams)

        else:
            actual_season = None
            actual_episode = None
            base_metadata = None
            season_mapping = None
            search_title = kitsu_metadata["title"]
            search_year = kitsu_metadata.get("year")

            if episode:
                actual_season, actual_episode, season_mapping, base_title, base_year, base_metadata = await kitsu_service.get_season_chain_and_mapping(
                    kitsu_id,
                    int(episode)
                )
                if base_title:
                    search_title = base_title
                if base_year:
                    search_year = base_year

            if base_metadata:
                kitsu_titles = base_metadata.get("search_titles", [base_metadata["title"]])
                enhanced_kitsu_metadata = {
                    "titles": kitsu_titles,
                    "original_titles": kitsu_titles,
                    "all_titles": base_metadata.get("all_titles", [base_metadata["title"]]),
                    "year": search_year,
                    "type": "anime",
                    "content_type": "anime"
                }
            else:
                kitsu_titles = [search_title] + kitsu_metadata.get("aliases", [])
                enhanced_kitsu_metadata = {
                    "titles": kitsu_titles,
                    "original_titles": kitsu_titles,
                    "year": search_year,
                    "type": "anime",
                    "content_type": "anime"
                }

            supported_sources = self._get_supported_sources(config) if config else settings.ALLDEBRID_SUPPORTED_SOURCES

            tasks = []

            if "wasource" in supported_sources and self._is_source_allowed_for_content("wasource", "anime", config):
                tasks.append(wasource_anime_scraper.search(
                    search_title, search_year, enhanced_kitsu_metadata,
                    str(actual_season), str(actual_episode), config
                ))

            if "wawacity" in supported_sources and self._is_source_allowed_for_content("wawacity", "anime", config):
                tasks.append(self._search_source_with_cache(
                    "wawacity", "anime", lambda: anime_scraper.search(search_title, search_year, enhanced_kitsu_metadata),
                    search_title, search_year, str(actual_season), str(actual_episode),
                    enhanced_kitsu_metadata, use_episode_key=False, filter_episodes=True
                ))

            if "free-telecharger" in supported_sources and self._is_source_allowed_for_content("free-telecharger", "anime", config):
                tasks.append(self._search_source_with_cache(
                    "free_telecharger", "anime", lambda: free_telecharger_anime_scraper.search(search_title, search_year, enhanced_kitsu_metadata),
                    search_title, search_year, str(actual_season), str(actual_episode),
                    enhanced_kitsu_metadata, use_episode_key=False, filter_episodes=True
                ))

            if "darki-api" in supported_sources and self._is_source_allowed_for_content("darki-api", "anime", config):
                absolute_ep = season_mapping.get("absolute_episode", actual_episode) if season_mapping else actual_episode

                tasks.append(self._search_darki_api_with_kitsu_direct_mapping(
                    search_title, search_year, enhanced_kitsu_metadata,
                    absolute_ep, config
                ))

            if "movix" in supported_sources and self._is_source_allowed_for_content("movix", "anime", config):
                absolute_ep = season_mapping.get("absolute_episode", actual_episode) if season_mapping else actual_episode

                tasks.append(self._search_movix_with_kitsu_direct_mapping(
                    search_title, search_year, enhanced_kitsu_metadata,
                    absolute_ep, config
                ))

            if "webshare" in supported_sources and self._is_source_allowed_for_content("webshare", "anime", config):
                _s, _e = str(actual_season), str(actual_episode)
                tasks.append(self._search_source_with_cache(
                    "webshare", "anime", lambda: webshare_anime_scraper.search(search_title, search_year, enhanced_kitsu_metadata, _s, _e, config),
                    search_title, search_year, _s, _e,
                    enhanced_kitsu_metadata, use_episode_key=True, filter_episodes=False
                ))

            if "nyaa" in supported_sources and self._is_source_allowed_for_content("nyaa", "anime", config):
                _s, _e = str(actual_season), str(actual_episode)
                tasks.append(self._search_source_with_cache(
                    "nyaa", "anime", lambda: nyaa_scraper.search(search_title, search_year, enhanced_kitsu_metadata, _s, _e, config),
                    search_title, search_year, _s, _e,
                    enhanced_kitsu_metadata, use_episode_key=True, filter_episodes=False
                ))

            results_list = await asyncio.gather(*tasks, return_exceptions=True)

            results = []
            for result in results_list:
                if isinstance(result, list):
                    results.extend(result)
                elif isinstance(result, Exception):
                    stream_logger.error(f"Kitsu search failed: {type(result).__name__}: {result}")

            if not results:
                stream_logger.debug(f"No content: Kitsu {kitsu_metadata['title']}")
                return []

            results = deduplicate_and_sort_results(results, quality_sort_key)

            results = apply_all_filters(results, config)

            elapsed = time.time() - start_time
            timeout = config.get("stream_request_timeout", settings.STREAM_REQUEST_TIMEOUT)
            remaining_time = max(0, timeout - elapsed)

            debrid_services = get_debrid_services(config)
            enriched_results = await self._check_cache_and_enrich(
                results, debrid_services, config, remaining_time,
                str(actual_season) if actual_season else None,
                str(actual_episode) if actual_episode else None
            )

            streams = await self._format_streams(
                enriched_results,
                config,
                base_url,
                None,
                None,
                search_year,
                "series"
            )

            streams = filter_archive_files(streams)

        excluded_keywords = config.get("excluded_keywords", [])
        if excluded_keywords:
            filtered_streams = filter_excluded_keywords(streams, excluded_keywords)
            excluded_count = len(streams) - len(filtered_streams)
            if excluded_count > 0:
                stream_logger.debug(f"Excluded {excluded_count} streams")
            return filtered_streams

        return streams


# ===========================
# Singleton Instance
# ===========================
stream_service = StreamService()
