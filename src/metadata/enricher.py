"""
metadata/enricher.py — M4

Takes the `Library` M3's scanner already built (paths, parsed names,
audio/subtitle languages — no TMDB fields set) and fills in everything
TMDB touches: poster/backdrop/synopsis/genres/rating/runtime/
original_language on every Title, title/synopsis/thumbnail on every
Episode where TMDB has episode-level data, anime-movie reclassification
(Section 4b), and offline fallback art (Section 4) wherever there's no
match or no internet at all.

Resilience is the whole point here (same M3 principle, extended):
  - One title's TMDB failure/no-match must not abort the rest of the
    library — caught per-title, logged, and the title falls back to
    on-disk frame art instead of TMDB art.
  - No internet at all must not crash enrichment — every TmdbClient call
    is wrapped, so a fully offline run just produces an entirely
    fallback-art library instead of failing outright.
  - Already-cached titles (MetadataStore, "cached locally forever") never
    re-hit TMDB or re-download/re-generate art that's already on disk.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from library.models import ContentType, Episode, Library, Season, Title
from metadata.classifier import is_anime_movie
from metadata.image_cache import (
    ImageCacheError,
    cache_path,
    download_image,
    episode_still_cache_path,
    generate_fallback_art,
)
from metadata.matcher import best_match, clean_query
from metadata.metadata_store import CachedEpisodeMetadata, CachedTitleMetadata, MetadataStore
from metadata.tmdb_client import TmdbClient, TmdbError, backdrop_url, build_retrying_session, poster_url, still_url

logger = logging.getLogger(__name__)

# Progress callback: (current_index, total_count, current_display_name)
ProgressCallback = Callable[[int, int, str], None]


@dataclass
class EnricherConfig:
    tmdb_api_key: Optional[str]  # None/empty = fully offline: fallback art only, no TMDB calls
    image_cache_dir: str
    metadata_cache_path: str
    # Off by default: per-episode TMDB fetch + fallback art (which opens
    # the actual video file via mpv when TMDB has no still) is slow across
    # a library with hundreds of episodes, and episode metadata rarely
    # changes once matched — better as a deliberate, opt-in pass (enable
    # with --episode-enrich once) than something every --enrich run pays
    # for by default. Title-level art/synopsis/genres (what Home/Detail
    # actually needs first) stays fast either way.
    enrich_episodes: bool = False


class MetadataEnricher:
    def __init__(self, config: EnricherConfig, client: Optional[TmdbClient] = None) -> None:
        self._config = config
        self._store = MetadataStore(config.metadata_cache_path)
        self._client = client
        if self._client is None and config.tmdb_api_key:
            self._client = TmdbClient(config.tmdb_api_key)

    # -- public entry point --------------------------------------------

    def enrich(self, library: Library, progress: Optional[ProgressCallback] = None) -> Library:
        all_movies = list(library.movies)
        reclassified: list[Title] = []
        kept_movies: list[Title] = []

        total = len(all_movies) + len(library.shows) + len(library.anime)
        idx = 0

        for title in all_movies:
            if progress:
                progress(idx, total, title.display_name)
            idx += 1
            self._enrich_title(title, is_series=False)
            if self._is_reclassified_anime(title):
                title.type = ContentType.ANIME
                reclassified.append(title)
            else:
                kept_movies.append(title)

        for title in library.shows:
            if progress:
                progress(idx, total, title.display_name)
            idx += 1
            self._enrich_title(title, is_series=True)

        for title in library.anime:
            if progress:
                progress(idx, total, title.display_name)
            idx += 1
            self._enrich_title(title, is_series=True)

        self._store.save()

        library.movies = kept_movies
        # Section 4b: reclassified anime movies join the Anime bucket
        # alongside folder-based anime series, sorted back in by name so
        # the shelf doesn't just dump them at the end.
        library.anime = sorted(
            [*library.anime, *reclassified], key=lambda t: t.sort_name
        )
        return library

    # -- one title ------------------------------------------------------------

    def _enrich_title(self, title: Title, is_series: bool) -> None:
        cached = self._store.get_title(title.id)
        if cached is None:
            cached = self._match_and_fetch(title, is_series=is_series)
            if cached is not None:
                self._store.set_title(title.id, cached)
            else:
                # Transient failure (network reset, TMDB hiccup) — NOT the
                # same as "TMDB genuinely has no match for this title."
                # Caching this would permanently poison the title as
                # unmatched forever, so re-running --enrich could never
                # fix a one-off network blip. Use an ephemeral, uncached
                # result for this run only; next run tries TMDB again.
                print(
                    f"[enrich] TMDB lookup for {title.display_name!r} failed due to a "
                    "transient error — will retry on the next --enrich run instead of "
                    "caching it as unmatched.",
                    file=sys.stderr,
                )
                cached = CachedTitleMetadata(matched=False)

        self._apply_title_metadata(title, cached)
        self._ensure_art(title, cached)

        if is_series and self._config.enrich_episodes:
            self._enrich_episodes(title, cached)

    def _match_and_fetch(self, title: Title, is_series: bool) -> Optional[CachedTitleMetadata]:
        """Returns None for a transient TMDB failure (caller must not
        cache that) — CachedTitleMetadata(matched=False) is reserved for
        a confirmed "TMDB has nothing confident for this title," which IS
        safe to cache forever."""
        if self._client is None:
            return CachedTitleMetadata(matched=False)

        query = clean_query(title.display_name)
        try:
            candidates = self._search(query, title.year, is_series)
        except TmdbError as exc:
            logger.warning("TMDB search failed for %r: %s", title.display_name, exc)
            return None

        match = best_match(title.display_name, title.year, candidates)
        if match is None:
            logger.info("No confident TMDB match for %r", title.display_name)
            return CachedTitleMetadata(matched=False)

        try:
            details = (
                self._client.tv_details(match.candidate.tmdb_id)
                if is_series
                else self._client.movie_details(match.candidate.tmdb_id)
            )
        except TmdbError as exc:
            logger.warning("TMDB details fetch failed for %r: %s", title.display_name, exc)
            return None

        anime_movie = (
            not is_series
            and is_anime_movie(details.genres, details.original_language, details.keywords)
        )

        return CachedTitleMetadata(
            matched=True,
            tmdb_id=details.tmdb_id,
            tmdb_media_type="tv" if is_series else "movie",
            synopsis=details.synopsis,
            genres=details.genres,
            keywords=details.keywords,
            rating=details.rating,
            runtime=details.runtime,
            original_language=details.original_language,
            poster_path=details.poster_path,   # still a raw TMDB path here; _ensure_art downloads + rewrites it
            backdrop_path=details.backdrop_path,
            is_anime_movie=anime_movie,
        )

    def _search(self, query: str, year: Optional[int], is_series: bool) -> list:
        """Searches with the parsed year first (helps disambiguate common
        titles), but TMDB's `year` parameter is a hard filter, not a
        hint — a wrong or placeholder year (unreleased/future test data,
        or just a parsing mistake) can zero out an otherwise-perfect
        title match. So an empty year-filtered result always gets one
        unfiltered retry before giving up, rather than treating "TMDB
        found nothing for this exact year" as "TMDB has no match"."""
        search_fn = self._client.search_tv if is_series else self._client.search_movie
        candidates = search_fn(query, year)
        if not candidates and year is not None:
            candidates = search_fn(query, None)
        return candidates

    def _apply_title_metadata(self, title: Title, cached: CachedTitleMetadata) -> None:
        if not cached.matched:
            return
        title.synopsis = cached.synopsis
        title.genres = cached.genres
        title.rating = cached.rating
        title.runtime = cached.runtime
        title.original_language = cached.original_language

    def _is_reclassified_anime(self, title: Title) -> bool:
        cached = self._store.get_title(title.id)
        return bool(cached and cached.matched and cached.is_anime_movie)

    # -- art ------------------------------------------------------------------

    def _ensure_art(self, title: Title, cached: CachedTitleMetadata) -> None:
        cache_dir = self._config.image_cache_dir
        poster_dest = cache_path(cache_dir, title.id, "poster")
        backdrop_dest = cache_path(cache_dir, title.id, "backdrop")

        if cached.matched and cached.poster_path and cached.poster_path.startswith("/"):
            # Raw TMDB path (starts with "/", e.g. "/abc123.jpg") means we
            # haven't downloaded it into the cache yet this run — download
            # once, then rewrite the cached entry to the local path so
            # future runs treat it as already-cached art.
            try:
                local_poster = download_image(poster_url(cached.poster_path), poster_dest, session=self._image_session())
                cached.poster_path = local_poster
            except ImageCacheError as exc:
                logger.warning("Poster download failed for %r: %s", title.display_name, exc)
                cached.poster_path = None
        if cached.matched and cached.backdrop_path and cached.backdrop_path.startswith("/"):
            try:
                local_backdrop = download_image(backdrop_url(cached.backdrop_path), backdrop_dest, session=self._image_session())
                cached.backdrop_path = local_backdrop
            except ImageCacheError as exc:
                logger.warning("Backdrop download failed for %r: %s", title.display_name, exc)
                cached.backdrop_path = None

        # No match, or a match whose art failed to download — fall back
        # to a frame pulled straight from the video (Section 4).
        source_video = title.file_path or _first_episode_video(title)
        if not cached.poster_path and source_video:
            cached.poster_path = self._safe_fallback_art(source_video, poster_dest, title.display_name)
        if not cached.backdrop_path and source_video:
            cached.backdrop_path = self._safe_fallback_art(source_video, backdrop_dest, title.display_name)

        title.poster_path = cached.poster_path
        title.backdrop_path = cached.backdrop_path

    def _image_session(self):
        """Reuses the TMDB client's retry-configured session for image
        downloads when a client exists (shared connection pool + retry/
        backoff behavior); falls back to a fresh retrying session when
        running fully offline-configured (no client) so download_image's
        own default doesn't silently drop back to zero-retry behavior."""
        if self._client is not None:
            return self._client.session
        return build_retrying_session()

    def _safe_fallback_art(self, video_path: str, dest_path: Path, display_name: str) -> Optional[str]:
        try:
            return generate_fallback_art(video_path, dest_path)
        except ImageCacheError as exc:
            logger.warning("Fallback art generation failed for %r: %s", display_name, exc)
            return None

    # -- episodes ---------------------------------------------------------

    def _enrich_episodes(self, title: Title, cached_title: CachedTitleMetadata) -> None:
        for season in title.seasons:
            for episode in season.episodes:
                self._enrich_episode(title, cached_title, season, episode)

    def _enrich_episode(
        self, title: Title, cached_title: CachedTitleMetadata, season: Season, episode: Episode
    ) -> None:
        if episode.episode_number is None:
            return  # nothing to key a TMDB episode lookup on (Section 3: absolute-only anime skip this)

        cached_ep = self._store.get_episode(title.id, season.season_number, episode.episode_number)
        if cached_ep is None:
            cached_ep = self._fetch_episode(
                title.id, cached_title, season.season_number, episode.episode_number
            )
            self._store.set_episode(title.id, season.season_number, episode.episode_number, cached_ep)

        episode.title = cached_ep.title
        episode.synopsis = cached_ep.synopsis

        still_dest = episode_still_cache_path(
            self._config.image_cache_dir, title.id, season.season_number, episode.episode_number
        )
        if not cached_ep.thumbnail_path:
            cached_ep.thumbnail_path = self._safe_fallback_art(
                episode.file_path, still_dest, f"{title.display_name} S{season.season_number:02d}E{episode.episode_number:02d}"
            )
        episode.thumbnail_path = cached_ep.thumbnail_path

    def _fetch_episode(
        self, title_id: str, cached_title: CachedTitleMetadata, season_number: int, episode_number: int
    ) -> CachedEpisodeMetadata:
        if self._client is None or not cached_title.matched or cached_title.tmdb_id is None:
            return CachedEpisodeMetadata()

        try:
            data = self._client.tv_episode_details(cached_title.tmdb_id, season_number, episode_number)
        except TmdbError as exc:
            logger.info("TMDB episode fetch failed (S%02dE%02d): %s", season_number, episode_number, exc)
            return CachedEpisodeMetadata()
        if data is None:
            return CachedEpisodeMetadata()

        thumbnail_path = None
        still_path = data.get("still_path")
        if still_path:
            dest = episode_still_cache_path(
                self._config.image_cache_dir, title_id, season_number, episode_number
            )
            try:
                thumbnail_path = download_image(still_url(still_path), dest, session=self._image_session())
            except ImageCacheError as exc:
                logger.info("Episode still download failed: %s", exc)

        return CachedEpisodeMetadata(
            title=data.get("title"),
            synopsis=data.get("synopsis"),
            thumbnail_path=thumbnail_path,
        )


def _first_episode_video(title: Title) -> Optional[str]:
    for season in title.seasons:
        for episode in season.episodes:
            return episode.file_path
    return None