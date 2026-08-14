"""
library/models.py — M3

Content model per Section 2 of the project spec. M3 only populates the
fields a filesystem scan + MKV track read can know about (paths, parsed
names/numbers, added_at, audio/subtitle language lists). Everything TMDB
touches (poster/backdrop/synopsis/genres/rating/original_language/
similar_title_ids) and everything segment/watch-state related is left at
its default and filled in by later milestones (M4, M6, M7) without needing
to touch this file again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ContentType(str, Enum):
    MOVIE = "movie"
    SHOW = "show"
    ANIME = "anime"


class SegmentType(str, Enum):
    INTRO = "intro"
    RECAP = "recap"
    OUTRO = "outro"
    POST_CREDIT = "post_credit"
    CUSTOM = "custom"


class SegmentScope(str, Enum):
    SEASON = "season"
    EPISODE = "episode"


@dataclass
class Segment:
    """Section 2 / Section 5. Empty on every Title/Episode until M6's
    marking editor exists — the shape is defined now so M6 doesn't need to
    touch the content model again."""
    type: SegmentType
    start: float
    end: float
    scope: SegmentScope = SegmentScope.EPISODE
    label: Optional[str] = None  # only meaningful for SegmentType.CUSTOM


@dataclass
class Episode:
    episode_number: Optional[int]        # None only if unparseable
    absolute_number: Optional[int]       # anime absolute numbering (Section 3)
    title: Optional[str]                 # from TMDB, M4
    file_path: str
    added_at: float                      # file mtime, epoch seconds (Section 2, "NEW" badge)
    thumbnail_path: Optional[str] = None
    synopsis: Optional[str] = None
    preview_start: Optional[float] = None
    audio_languages: list[str] = field(default_factory=list)     # Section 4c
    subtitle_languages: list[str] = field(default_factory=list)  # Section 4c
    segments: list[Segment] = field(default_factory=list)


@dataclass
class Season:
    season_number: int
    episodes: list[Episode] = field(default_factory=list)

    def sorted_episodes(self) -> list[Episode]:
        return sorted(
            self.episodes,
            key=lambda e: (
                e.episode_number if e.episode_number is not None else
                (e.absolute_number if e.absolute_number is not None else 0)
            ),
        )


@dataclass
class Title:
    type: ContentType
    id: str                    # stable id derived from path, see scanner.make_id
    display_name: str
    sort_name: str
    year: Optional[int] = None

    # M4 (TMDB) — left unset by the scanner
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    synopsis: Optional[str] = None
    genres: list[str] = field(default_factory=list)
    rating: Optional[float] = None
    runtime: Optional[int] = None                 # minutes, Movie only
    original_language: Optional[str] = None
    similar_title_ids: list[str] = field(default_factory=list)

    added_at: float = 0.0                          # file mtime (Section 2, "NEW" badge)

    # Section 4c — populated by the scanner from MKV track metadata for
    # Movies. Shows/Anime instead aggregate this per-episode (see
    # Title.all_audio_languages / all_subtitle_languages below), since a
    # show's tracks can legitimately vary episode to episode.
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)

    # Movie only
    file_path: Optional[str] = None
    # Show / Anime only
    seasons: list[Season] = field(default_factory=list)

    def sorted_seasons(self) -> list[Season]:
        return sorted(self.seasons, key=lambda s: s.season_number)

    def all_audio_languages(self) -> list[str]:
        """Union of every audio language present anywhere in this title —
        the single file's list for a Movie, or the union across all
        episodes for a Show/Anime. Used for Section 4c "Browse by
        Language" / Search filtering, where a whole title should surface
        if *any* of its content carries that language."""
        if self.type == ContentType.MOVIE:
            return list(self.audio_languages)
        seen: list[str] = []
        for season in self.seasons:
            for ep in season.episodes:
                for lang in ep.audio_languages:
                    if lang not in seen:
                        seen.append(lang)
        return seen

    def all_subtitle_languages(self) -> list[str]:
        if self.type == ContentType.MOVIE:
            return list(self.subtitle_languages)
        seen: list[str] = []
        for season in self.seasons:
            for ep in season.episodes:
                for lang in ep.subtitle_languages:
                    if lang not in seen:
                        seen.append(lang)
        return seen


@dataclass
class Library:
    """Everything a scan produced, grouped the way Home shelves group them
    (Section 3 — Movies/TV Shows/Anime are three separate top-level
    buckets; anime-movie reclassification into the Anime bucket happens in
    M4 once TMDB metadata lands, Section 4b — M3 always puts Movies-root
    content here regardless of what it'll turn out to be)."""
    movies: list[Title] = field(default_factory=list)
    shows: list[Title] = field(default_factory=list)
    anime: list[Title] = field(default_factory=list)

    def all_titles(self) -> list[Title]:
        return [*self.movies, *self.shows, *self.anime]
