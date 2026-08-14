import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from library.models import ContentType, Library, Title
from ui.shelves import (
    NEW_BADGE_WINDOW_S,
    WatchState,
    build_continue_watching,
    build_home_shelves,
    build_language_shelves,
    build_recently_added,
)


def make_title(id_, name, added_at=0.0, lang=None, type_=ContentType.MOVIE):
    return Title(type=type_, id=id_, display_name=name, sort_name=name, added_at=added_at,
                 original_language=lang)


def test_recently_added_sorted_desc_and_new_badge():
    now = 1_000_000.0
    old = make_title("a", "Old Movie", added_at=now - NEW_BADGE_WINDOW_S - 10)
    new = make_title("b", "New Movie", added_at=now - 10)
    lib = Library(movies=[old, new])

    shelf = build_recently_added(lib, {}, now=now)
    assert [t.title.id for t in shelf.tiles] == ["b", "a"]
    assert shelf.tiles[0].is_new is True
    assert shelf.tiles[1].is_new is False


def test_continue_watching_only_in_progress_sorted_by_position():
    lib = Library(movies=[make_title("a", "A"), make_title("b", "B"), make_title("c", "C")])
    states = {
        "a": WatchState(status="in_progress", position_s=100, duration_s=6000),
        "b": WatchState(status="watched", position_s=6000, duration_s=6000),
        "c": WatchState(status="in_progress", position_s=4000, duration_s=6000),
    }
    shelf = build_continue_watching(lib, states)
    assert [t.title.id for t in shelf.tiles] == ["c", "a"]
    assert round(shelf.tiles[0].progress_fraction, 3) == round(4000 / 6000, 3)


def test_language_shelves_respect_min_titles_and_sort_by_size():
    titles = [make_title(f"ja{i}", f"J{i}", lang="ja") for i in range(4)]
    titles += [make_title(f"ta{i}", f"T{i}", lang="ta") for i in range(2)]
    titles += [make_title("solo", "Solo", lang="fr")]
    lib = Library(movies=titles)

    shelves = build_language_shelves(lib, {}, min_titles=3)
    keys = [s.key for s in shelves]
    assert "language_ja" in keys
    assert "language_ta" not in keys   # below min_titles
    assert "language_fr" not in keys   # below min_titles
    assert shelves[0].key == "language_ja"


def test_build_home_shelves_omits_empty_rows():
    lib = Library(movies=[make_title("a", "A")])
    shelves = build_home_shelves(lib, {})
    keys = {s.key for s in shelves}
    assert "continue_watching" not in keys   # nothing in progress
    assert "movies" in keys
    assert "shows" not in keys
    assert "anime" not in keys
