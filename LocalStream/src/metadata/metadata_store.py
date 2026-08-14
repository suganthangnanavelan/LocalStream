"""
metadata/metadata_store.py — M4

Section 4: "TMDB-matched, cached locally forever." A Title/Episode's TMDB
match result is looked up once and never re-fetched on later scans —
this is the on-disk record of that, so re-running the scanner (M3) +
enricher (M4) on an unchanged library does zero TMDB requests.

One JSON file, keyed by the scanner's stable `Title.id` (library/scanner.
make_id — derived from the on-disk path, so it survives a title's
display_name changing). Episode-level entries are keyed
"<title_id>:s<season>e<episode>" alongside it in the same file, since
they're small and always looked up together with their parent title.

Deliberately not tied to any particular on-disk location — callers pass
the path (normally under %APPDATA%/LocalStream per Section 9's app-data
layout, wired up once config/ exists in a later milestone).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CachedTitleMetadata:
    matched: bool                              # False = looked up, TMDB had no good match
    tmdb_id: Optional[int] = None
    tmdb_media_type: Optional[str] = None      # "movie" | "tv"
    synopsis: Optional[str] = None
    genres: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    rating: Optional[float] = None
    runtime: Optional[int] = None
    original_language: Optional[str] = None
    poster_path: Optional[str] = None          # local cached file path
    backdrop_path: Optional[str] = None        # local cached file path
    is_anime_movie: bool = False               # Section 4b classification result


@dataclass
class CachedEpisodeMetadata:
    title: Optional[str] = None
    synopsis: Optional[str] = None
    thumbnail_path: Optional[str] = None       # local cached file path


class MetadataStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._titles: dict[str, CachedTitleMetadata] = {}
        self._episodes: dict[str, CachedEpisodeMetadata] = {}
        self._load()

    # -- titles -------------------------------------------------------------

    def get_title(self, title_id: str) -> Optional[CachedTitleMetadata]:
        return self._titles.get(title_id)

    def set_title(self, title_id: str, metadata: CachedTitleMetadata) -> None:
        self._titles[title_id] = metadata

    # -- episodes ---------------------------------------------------------

    def get_episode(self, title_id: str, season_number: int, episode_number: int) -> Optional[CachedEpisodeMetadata]:
        return self._episodes.get(_episode_key(title_id, season_number, episode_number))

    def set_episode(
        self, title_id: str, season_number: int, episode_number: int, metadata: CachedEpisodeMetadata
    ) -> None:
        self._episodes[_episode_key(title_id, season_number, episode_number)] = metadata

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A corrupt/unreadable cache must not crash startup — worst
            # case we re-fetch from TMDB, same as a fresh install.
            logger.warning("Could not read metadata cache at %s: %s", self._path, exc)
            return
        for title_id, entry in raw.get("titles", {}).items():
            self._titles[title_id] = CachedTitleMetadata(**entry)
        for key, entry in raw.get("episodes", {}).items():
            self._episodes[key] = CachedEpisodeMetadata(**entry)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "titles": {tid: asdict(meta) for tid, meta in self._titles.items()},
            "episodes": {key: asdict(meta) for key, meta in self._episodes.items()},
        }
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)


def _episode_key(title_id: str, season_number: int, episode_number: int) -> str:
    return f"{title_id}:s{season_number:02d}e{episode_number:03d}"
