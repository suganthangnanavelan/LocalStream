"""
ui/shelves.py — M5

Pure-Python shelf construction for Home (Section 9: "Home" reads Library +
Profile Manager + Recommendation Engine and produces what gets drawn — this
module is the "produces what gets drawn" half, kept free of any GL/window
dependency so it's unit-testable and so M5a's Recommendation Engine and
M7's Profile Manager can extend it later without dragging rendering code
along).

Scope note: Continue Watching (needs per-profile watch_state, M7) and the
M5a/M5b/M5c shelves (recommendations, search results, Era buckets) aren't
built here — M5 itself only covers Section 12's explicit list: shelves,
poster tiles w/ progress + NEW badge, backdrop detail page, season/episode
list. `watch_states` is still accepted as a parameter (rather than added
later) so Continue Watching can be wired in at M7 without another pass
over this file's shape.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from library.models import Library, Title

NEW_BADGE_WINDOW_S = 14 * 24 * 60 * 60  # Section 7b: 14-day default window


@dataclass
class WatchState:
    """Placeholder shape for M7's per-profile watch_state (Section 2).
    Until profiles exist, callers simply pass an empty dict and every
    title renders unwatched/no-progress — this dataclass exists now so
    Continue Watching and progress-bar wiring don't need shelves.py
    touched again at M7."""
    status: str = "unwatched"     # unwatched | in_progress | watched
    position_s: float = 0.0
    duration_s: float = 0.0

    @property
    def progress_fraction(self) -> float:
        if self.status != "in_progress" or self.duration_s <= 0:
            return 0.0
        return max(0.0, min(1.0, self.position_s / self.duration_s))


@dataclass
class Tile:
    """One poster tile's worth of data a Home shelf or Detail's related-row
    needs to draw — resolved once here so views/home.py stays render-only."""
    title: Title
    is_new: bool = False
    progress_fraction: float = 0.0

    @property
    def poster_path(self) -> Optional[str]:
        return self.title.poster_path

    @property
    def display_name(self) -> str:
        return self.title.display_name


@dataclass
class Shelf:
    key: str
    label: str
    tiles: list[Tile] = field(default_factory=list)


def _is_new(added_at: float, now: float) -> bool:
    return added_at > 0 and (now - added_at) <= NEW_BADGE_WINDOW_S


def _make_tile(title: Title, watch_states: dict[str, WatchState], now: float) -> Tile:
    state = watch_states.get(title.id)
    return Tile(
        title=title,
        is_new=_is_new(title.added_at, now),
        progress_fraction=state.progress_fraction if state else 0.0,
    )


def _sorted_by_name(titles: list[Title]) -> list[Title]:
    return sorted(titles, key=lambda t: t.sort_name.lower())


def build_continue_watching(library: Library, watch_states: dict[str, WatchState],
                             now: Optional[float] = None) -> Shelf:
    now = now if now is not None else time.time()
    in_progress = [
        t for t in library.all_titles()
        if watch_states.get(t.id) and watch_states[t.id].status == "in_progress"
    ]
    in_progress.sort(key=lambda t: watch_states[t.id].position_s, reverse=True)
    return Shelf("continue_watching", "Continue Watching",
                 [_make_tile(t, watch_states, now) for t in in_progress])


def build_recently_added(library: Library, watch_states: dict[str, WatchState],
                          now: Optional[float] = None, limit: int = 30) -> Shelf:
    """Section 7b — sorted by added_at descending, across all three roots."""
    now = now if now is not None else time.time()
    titles = sorted(library.all_titles(), key=lambda t: t.added_at, reverse=True)[:limit]
    return Shelf("recently_added", "Recently Added",
                 [_make_tile(t, watch_states, now) for t in titles])


def build_type_shelf(key: str, label: str, titles: list[Title],
                      watch_states: dict[str, WatchState], now: Optional[float] = None) -> Shelf:
    now = now if now is not None else time.time()
    return Shelf(key, label, [_make_tile(t, watch_states, now) for t in _sorted_by_name(titles)])


def build_language_shelves(library: Library, watch_states: dict[str, WatchState],
                            now: Optional[float] = None, min_titles: int = 3) -> list[Shelf]:
    """Section 4c "Browse by Language" — one shelf per original_language
    that has enough titles to be worth a row, sorted by shelf size
    descending so the languages best represented in the library surface
    first. `original_language` only (audio/subtitle language browsing
    lives in Search filtering, Section 7a, not Home shelves)."""
    now = now if now is not None else time.time()
    by_lang: dict[str, list[Title]] = {}
    for t in library.all_titles():
        if not t.original_language:
            continue
        by_lang.setdefault(t.original_language, []).append(t)

    shelves = []
    for lang, titles in by_lang.items():
        if len(titles) < min_titles:
            continue
        shelves.append(build_type_shelf(f"language_{lang}", _language_label(lang),
                                         titles, watch_states, now))
    shelves.sort(key=lambda s: len(s.tiles), reverse=True)
    return shelves


_LANGUAGE_NAMES = {
    "en": "English", "ja": "Japanese", "ta": "Tamil", "hi": "Hindi",
    "ko": "Korean", "fr": "French", "es": "Spanish", "de": "German",
    "zh": "Chinese", "cn": "Chinese", "it": "Italian", "te": "Telugu",
    "ml": "Malayalam", "kn": "Kannada",
}


def _language_label(code: str) -> str:
    name = _LANGUAGE_NAMES.get(code.lower())
    return f"{name} Titles" if name else f"{code.upper()} Titles"


def build_home_shelves(library: Library, watch_states: Optional[dict[str, WatchState]] = None,
                        now: Optional[float] = None) -> list[Shelf]:
    """Full Home shelf stack in display order — Continue Watching always
    first (Section 4d), then Recently Added, then the three type shelves,
    then Language shelves. Empty shelves are omitted so a fresh/small
    library doesn't show dead rows."""
    watch_states = watch_states or {}
    now = now if now is not None else time.time()

    shelves = [
        build_continue_watching(library, watch_states, now),
        build_recently_added(library, watch_states, now),
        build_type_shelf("movies", "Movies", library.movies, watch_states, now),
        build_type_shelf("shows", "TV Shows", library.shows, watch_states, now),
        build_type_shelf("anime", "Anime", library.anime, watch_states, now),
    ]
    shelves.extend(build_language_shelves(library, watch_states, now))
    return [s for s in shelves if s.tiles]
