"""
library/scanner.py — M3

Scans the three separate top-level roots (Section 3) and builds the
content model (Section 2 / models.py) out of what's on disk: folder
structure + filename parsing (parser.py) + embedded MKV track languages
(track_extractor.py). No TMDB, no posters/backdrops/synopses, no
anime-movie reclassification — that's all M4. No segments, no watch
state — those are M6/M7. This module's only job is: what titles exist,
what files back them, what languages their tracks carry.

Folder rules (Section 3), reaffirmed here:
  - Movies root: flat, one subfolder per movie, anime movies included
    (classified as Movie for now — M4 reclassifies into Anime later).
  - TV Shows root: Show/Season NN/S01E02 files.
  - Anime root: same Season NN/S01E02 layout *or* flat files using
    absolute numbering (no season folder) — both parsed here.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from library.models import ContentType, Episode, Library, Season, Title
from library.parser import (
    is_video_file,
    make_sort_name,
    parse_episode_filename,
    parse_season_folder,
    parse_year,
)
from library.track_extractor import TrackExtractionError, extract_tracks

logger = logging.getLogger(__name__)

# Progress callback: (current_index, total_count, current_display_name)
ProgressCallback = Callable[[int, int, str], None]


def make_id(path: str) -> str:
    """Stable id derived from the path, so re-scans keep the same id for
    the same on-disk item (profiles/watch_state key against this id in
    later milestones, so it can't be re-derived from mutable display data
    like a display_name that metadata matching might later tweak)."""
    normalized = os.path.normcase(os.path.normpath(path))
    return hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]


def _first_video_file(folder: Path) -> Optional[Path]:
    """A movie folder should contain exactly one video file; if there are
    several (extras, sample clips, etc.) we deterministically pick the
    largest — almost always the actual movie, never the sample/trailer."""
    candidates = [
        p for p in folder.iterdir() if p.is_file() and is_video_file(p.name)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


@dataclass
class ScannerConfig:
    movies_root: Optional[str] = None
    tv_shows_root: Optional[str] = None
    anime_root: Optional[str] = None
    extract_tracks: bool = True  # disable in tests to skip spinning up mpv per file


class LibraryScanner:
    def __init__(self, config: ScannerConfig) -> None:
        self._config = config

    # -- public entry point --------------------------------------------

    def scan(self, progress: Optional[ProgressCallback] = None) -> Library:
        library = Library()

        if self._config.movies_root:
            library.movies = self._scan_movies(self._config.movies_root, progress)
        if self._config.tv_shows_root:
            library.shows = self._scan_series_root(
                self._config.tv_shows_root, ContentType.SHOW, progress
            )
        if self._config.anime_root:
            library.anime = self._scan_series_root(
                self._config.anime_root, ContentType.ANIME, progress
            )
        return library

    # -- Movies -----------------------------------------------------------

    def _scan_movies(self, root: str, progress: Optional[ProgressCallback]) -> list[Title]:
        root_path = Path(root)
        if not root_path.is_dir():
            logger.warning("Movies root does not exist: %s", root)
            return []

        # A movie can live either as its own subfolder (Section 3's
        # canonical example) or as a bare video file directly in the
        # Movies root (common in flatter real-world libraries) — both are
        # valid, so both are scanned.
        entries = sorted(root_path.iterdir())
        titles: list[Title] = []
        for idx, entry in enumerate(entries):
            if entry.is_dir():
                video = _first_video_file(entry)
                name_for_parsing = entry.name
            elif entry.is_file() and is_video_file(entry.name):
                video = entry
                name_for_parsing = entry.stem
            else:
                continue
            if video is None:
                continue
            if progress:
                progress(idx, len(entries), name_for_parsing)

            parsed = parse_year(name_for_parsing)
            audio_langs: list[str] = []
            sub_langs: list[str] = []
            if self._config.extract_tracks:
                audio_langs, sub_langs = self._safe_extract_languages(str(video))

            titles.append(
                Title(
                    type=ContentType.MOVIE,
                    id=make_id(str(video)),
                    display_name=parsed.display_name,
                    sort_name=make_sort_name(parsed.display_name),
                    year=parsed.year,
                    added_at=_mtime(video),
                    audio_languages=audio_langs,
                    subtitle_languages=sub_langs,
                    file_path=str(video),
                )
            )
        return titles

    # -- TV Shows / Anime ---------------------------------------------------

    def _scan_series_root(
        self, root: str, content_type: ContentType, progress: Optional[ProgressCallback]
    ) -> list[Title]:
        root_path = Path(root)
        if not root_path.is_dir():
            logger.warning("%s root does not exist: %s", content_type.value, root)
            return []

        show_folders = sorted(p for p in root_path.iterdir() if p.is_dir())
        titles: list[Title] = []
        for idx, show_folder in enumerate(show_folders):
            if progress:
                progress(idx, len(show_folders), show_folder.name)
            title = self._scan_one_series(show_folder, content_type)
            if title is not None:
                titles.append(title)
        return titles

    # Season bucket used for any subfolder that carries video files but
    # doesn't match the "Season N" naming pattern — movie folders, OVA/
    # specials folders, "EP 001-148" range folders, or a show's own
    # differently-named alternate season (e.g. "Steins;Gate 0"). Numbered
    # 0 by the common "specials" convention rather than being dropped.
    UNNUMBERED_SEASON = 0

    def _scan_one_series(self, show_folder: Path, content_type: ContentType) -> Optional[Title]:
        parsed = parse_year(show_folder.name)
        subfolders = [p for p in show_folder.iterdir() if p.is_dir()]

        seasons: list[Season] = []
        if subfolders:
            # Real season folders keep their parsed number; everything
            # else (movies, specials, oddly-named folders) is pooled into
            # one Season 0 bucket instead of being silently dropped —
            # this is the fix for titles like Hunter X Hunter, Steins;Gate,
            # Trigun, and Tengen Toppa Gurren Lagann, whose movie/OVA/
            # specials folders previously caused the *entire* title to
            # vanish because not every subfolder matched "Season N".
            numbered: list[tuple[int, Path]] = []
            unnumbered: list[Path] = []
            for folder in subfolders:
                season_number = parse_season_folder(folder.name)
                if season_number is not None:
                    numbered.append((season_number, folder))
                else:
                    unnumbered.append(folder)

            for season_number, folder in sorted(numbered, key=lambda pair: pair[0]):
                episodes = self._scan_episode_files(folder, fallback_season=season_number)
                if episodes:
                    seasons.append(Season(season_number=season_number, episodes=episodes))

            pooled_episodes: list[Episode] = []
            for folder in sorted(unnumbered):
                pooled_episodes.extend(
                    self._scan_episode_files(folder, fallback_season=self.UNNUMBERED_SEASON)
                )
            if pooled_episodes:
                seasons.append(Season(season_number=self.UNNUMBERED_SEASON, episodes=pooled_episodes))
        else:
            # No subfolders at all — every video file sits directly in
            # the show's own folder (flat layout, Section 3's "One Piece"
            # absolute-numbering example). Everything goes in a single
            # synthetic Season 1 so the content model doesn't need a
            # separate "seasonless" case downstream.
            episodes = self._scan_episode_files(show_folder, fallback_season=1)
            if episodes:
                seasons.append(Season(season_number=1, episodes=episodes))

        if not seasons:
            return None

        oldest_episode_mtime = min(
            ep.added_at for season in seasons for ep in season.episodes
        )

        return Title(
            type=content_type,
            id=make_id(str(show_folder)),
            display_name=parsed.display_name,
            sort_name=make_sort_name(parsed.display_name),
            year=parsed.year,
            added_at=oldest_episode_mtime,
            seasons=seasons,
        )

    def _scan_episode_files(self, folder: Path, fallback_season: Optional[int]) -> list[Episode]:
        video_files = sorted(
            p for p in folder.iterdir() if p.is_file() and is_video_file(p.name)
        )
        episodes: list[Episode] = []
        for video in video_files:
            parsed = parse_episode_filename(video.name, fallback_season=fallback_season)
            audio_langs: list[str] = []
            sub_langs: list[str] = []
            if self._config.extract_tracks:
                audio_langs, sub_langs = self._safe_extract_languages(str(video))

            episodes.append(
                Episode(
                    episode_number=parsed.episode_number,
                    absolute_number=parsed.absolute_number,
                    title=None,  # TMDB, M4
                    file_path=str(video),
                    added_at=_mtime(video),
                    audio_languages=audio_langs,
                    subtitle_languages=sub_langs,
                )
            )
        return episodes

    # -- shared -------------------------------------------------------------

    def _safe_extract_languages(self, file_path: str) -> tuple[list[str], list[str]]:
        try:
            info = extract_tracks(file_path)
        except TrackExtractionError as exc:
            # One bad/corrupt file must not abort the whole scan (Section
            # 12 M3 intent) — log and continue with empty language lists;
            # the file still shows up in the library, just without
            # language-browse/filter data until re-scanned successfully.
            logger.warning("%s", exc)
            return [], []
        return info.audio_languages(), info.subtitle_languages()


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
