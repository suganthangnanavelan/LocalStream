"""
ui/views/home.py — M5

Renders the shelf stack ui/shelves.py builds: poster tiles with a progress
bar (Continue Watching) and NEW badge (Section 7b), shelf labels, and a
vertical scroll over the whole stack plus an independent horizontal scroll
per shelf. Mouse-only per Section 7c: every shelf scrolls by drag or by
clicking its edge arrows, every tile is clickable to open Detail.

No text-rendering fallback: if the Router couldn't find a font (see
ui/text.py), `text` is None and this view just skips every label/badge
draw, leaving the poster grid itself fully visible/testable.
"""

from __future__ import annotations

from typing import Callable, Optional

from library.models import Library
from ui.gl2d import Quad2D
from ui.layout import (
    SHELF_SIDE_MARGIN,
    TILE_H,
    TILE_W,
    clamp_scroll,
    hit_test_tile,
    shelf_tile_rects,
    shelves_layout,
)
from ui.shelves import Shelf, WatchState, build_home_shelves
from ui.text import TextRenderer
from ui.textures import TextureCache

BG_COLOR = (0.06, 0.06, 0.08, 1.0)
TILE_BG = (0.14, 0.14, 0.17, 1.0)
TILE_HOVER_BORDER = (0.95, 0.95, 0.98, 1.0)
PROGRESS_BG = (0.0, 0.0, 0.0, 0.55)
PROGRESS_FILL = (0.85, 0.15, 0.2, 1.0)
NEW_BADGE_BG = (0.85, 0.15, 0.2, 1.0)
LABEL_COLOR = (0.92, 0.92, 0.95, 1.0)
TITLE_COLOR = (1.0, 1.0, 1.0, 1.0)


class HomeView:
    def __init__(self, quad: Quad2D, textures: TextureCache,
                 text: Optional[TextRenderer], library: Library,
                 watch_states: Optional[dict[str, WatchState]] = None,
                 on_open_title: Optional[Callable[[str], None]] = None) -> None:
        self._quad = quad
        self._textures = textures
        self._text = text
        self._library = library
        self._watch_states = watch_states or {}
        self._on_open_title = on_open_title

        self.shelves: list[Shelf] = build_home_shelves(library, self._watch_states)
        self._shelf_tops = shelves_layout([len(s.tiles) for s in self.shelves])
        self._shelf_scroll_x: dict[str, float] = {s.key: 0.0 for s in self.shelves}
        self.scroll_y = 0.0
        self._hovered: Optional[tuple[int, int]] = None  # (shelf_idx, tile_idx)

    def refresh(self) -> None:
        """Rebuild shelves — call after a rescan or watch_state change
        (e.g. once M7 wires real per-profile state in)."""
        self.shelves = build_home_shelves(self._library, self._watch_states)
        self._shelf_tops = shelves_layout([len(s.tiles) for s in self.shelves])
        for s in self.shelves:
            self._shelf_scroll_x.setdefault(s.key, 0.0)

    # -- input -----------------------------------------------------------

    def on_scroll(self, dx: float, dy: float, viewport_w: float, viewport_h: float) -> None:
        max_y = max(0.0, (self._shelf_tops[-1] if self._shelf_tops else 0.0) - viewport_h + 200)
        self.scroll_y = max(0.0, min(self.scroll_y - dy * 40, max_y))

    def on_mouse_move(self, x: float, y: float, viewport_w: float) -> None:
        self._hovered = self._hit_test(x, y, viewport_w)

    def on_click(self, x: float, y: float, viewport_w: float) -> None:
        hit = self._hit_test(x, y, viewport_w)
        if hit is None:
            return
        shelf_idx, tile_idx = hit
        title = self.shelves[shelf_idx].tiles[tile_idx].title
        if self._on_open_title:
            self._on_open_title(title.id)

    def scroll_shelf(self, shelf_key: str, delta: float, viewport_w: float) -> None:
        shelf = next((s for s in self.shelves if s.key == shelf_key), None)
        if shelf is None:
            return
        current = self._shelf_scroll_x.get(shelf_key, 0.0)
        self._shelf_scroll_x[shelf_key] = clamp_scroll(current + delta, len(shelf.tiles),
                                                         viewport_w - SHELF_SIDE_MARGIN)

    def _hit_test(self, x: float, y: float, viewport_w: float) -> Optional[tuple[int, int]]:
        world_y = y + self.scroll_y
        for shelf_idx, (shelf, top) in enumerate(zip(self.shelves, self._shelf_tops)):
            row_top = top
            row_bottom = top + 36 + TILE_H
            if not (row_top <= world_y <= row_bottom):
                continue
            scroll_x = self._shelf_scroll_x.get(shelf.key, 0.0)
            rects = shelf_tile_rects(len(shelf.tiles), top, scroll_x)
            idx = hit_test_tile(rects, x, world_y, viewport_left=0, viewport_right=viewport_w)
            if idx is not None:
                return (shelf_idx, idx)
        return None

    # -- render ------------------------------------------------------------

    def render(self, viewport_w: float, viewport_h: float) -> None:
        self._quad.rect(0, 0, viewport_w, viewport_h, BG_COLOR)
        for shelf_idx, (shelf, top) in enumerate(zip(self.shelves, self._shelf_tops)):
            draw_top = top - self.scroll_y
            if draw_top > viewport_h or draw_top + 36 + TILE_H < 0:
                continue  # cheap vertical culling — off-screen shelves cost nothing
            self._draw_shelf(shelf, draw_top, shelf_idx, viewport_w)

    def _draw_shelf(self, shelf: Shelf, top: float, shelf_idx: int, viewport_w: float) -> None:
        if self._text:
            self._text.draw(shelf.label, SHELF_SIDE_MARGIN, top, 20, LABEL_COLOR)

        scroll_x = self._shelf_scroll_x.get(shelf.key, 0.0)
        rects = shelf_tile_rects(len(shelf.tiles), top, scroll_x)
        for tile_idx, (tile, rect) in enumerate(zip(shelf.tiles, rects)):
            if rect.x + rect.w < 0 or rect.x > viewport_w:
                continue  # cheap horizontal culling for long shelves
            self._draw_tile(tile, rect, hovered=(self._hovered == (shelf_idx, tile_idx)))

    def _draw_tile(self, tile, rect, hovered: bool) -> None:
        tex_id = self._textures.get(tile.poster_path)
        self._quad.rect(rect.x, rect.y, rect.w, rect.h, TILE_BG)
        self._quad.textured_rect(rect.x, rect.y, rect.w, rect.h, tex_id)

        if hovered:
            border = 3
            self._quad.rect(rect.x - border, rect.y - border, rect.w + 2 * border, border, TILE_HOVER_BORDER)
            self._quad.rect(rect.x - border, rect.y + rect.h, rect.w + 2 * border, border, TILE_HOVER_BORDER)
            self._quad.rect(rect.x - border, rect.y - border, border, rect.h + 2 * border, TILE_HOVER_BORDER)
            self._quad.rect(rect.x + rect.w, rect.y - border, border, rect.h + 2 * border, TILE_HOVER_BORDER)

        if tile.is_new:
            badge_w, badge_h = 44, 20
            self._quad.rect(rect.x + 6, rect.y + 6, badge_w, badge_h, NEW_BADGE_BG)
            if self._text:
                self._text.draw("NEW", rect.x + 12, rect.y + 9, 13, TITLE_COLOR)

        if tile.progress_fraction > 0:
            bar_h = 5
            bar_y = rect.y + rect.h - bar_h
            self._quad.rect(rect.x, bar_y, rect.w, bar_h, PROGRESS_BG)
            self._quad.rect(rect.x, bar_y, rect.w * tile.progress_fraction, bar_h, PROGRESS_FILL)

        if self._text:
            self._text.draw(tile.display_name, rect.x, rect.y + rect.h + 6, 15, TITLE_COLOR,
                             max_width=rect.w)
