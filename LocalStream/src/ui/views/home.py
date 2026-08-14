"""
ui/views/home.py — M5

Renders the shelf stack ui/shelves.py builds: poster tiles with a progress
bar (Continue Watching) and NEW badge (Section 7b), shelf labels, and a
vertical scroll over the whole stack plus an independent horizontal scroll
per shelf. Mouse-only per Section 7c: every shelf scrolls by drag or by
clicking its edge arrows, every tile is clickable to open Detail.

Visual polish pass: tiles ease-scale up + lift a shadow on hover instead
of a hard border, fade in the first time they're drawn (staggered by
column so a shelf doesn't pop in as one flat block), and get a bottom
gradient scrim so the title/progress text stays legible over any poster
art. `tick(dt)` drives all of this — main.py calls it once per frame — so
the view is idle-animation-free (no wasted draws) unless the app itself
had a reason to touch it (hover changed, or a new tile hasn't finished
its fade-in), which is cheap even at library scale.

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

# Slightly deeper/warmer near-black than a flat gray — reads as
# "cinema," not "unstyled app." A subtle top-to-bottom gradient keeps the
# background from feeling like one dead flat slab behind the shelves.
BG_TOP = (0.05, 0.05, 0.065, 1.0)
BG_BOTTOM = (0.03, 0.03, 0.04, 1.0)
TILE_BG = (0.13, 0.13, 0.16, 1.0)
SCRIM_TOP = (0.0, 0.0, 0.0, 0.0)
SCRIM_BOTTOM = (0.0, 0.0, 0.0, 0.82)
PROGRESS_BG = (1.0, 1.0, 1.0, 0.22)
PROGRESS_FILL = (0.95, 0.72, 0.20, 1.0)  # matches Detail's gold accent — one consistent accent color app-wide
NEW_BADGE_BG = (0.95, 0.72, 0.20, 1.0)
LABEL_COLOR = (0.88, 0.88, 0.92, 0.95)
TITLE_COLOR = (1.0, 1.0, 1.0, 1.0)
SHADOW_COLOR = (0.0, 0.0, 0.0, 0.55)
BADGE_TEXT_COLOR = (0.12, 0.09, 0.02, 1.0)

HOVER_SCALE = 1.08
HOVER_ANIM_SPEED = 10.0   # higher = snappier ease toward the hover target
FADE_IN_DURATION = 0.35   # seconds for a tile to reach full opacity
FADE_IN_STAGGER = 0.03    # seconds of extra delay per column, left to right


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


class _TileAnim:
    """Per-tile animation state, created lazily the first time a tile is
    drawn (keyed by title id, so re-shuffling shelf order on refresh()
    doesn't reset animations for titles that were already visible)."""
    __slots__ = ("scale", "age")

    def __init__(self) -> None:
        self.scale = 1.0
        self.age = 0.0  # seconds since first drawn, drives fade-in


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
        self._anims: dict[str, _TileAnim] = {}  # title.id -> animation state
        self._dt = 1 / 60.0

    def refresh(self) -> None:
        """Rebuild shelves — call after a rescan or watch_state change
        (e.g. once M7 wires real per-profile state in)."""
        self.shelves = build_home_shelves(self._library, self._watch_states)
        self._shelf_tops = shelves_layout([len(s.tiles) for s in self.shelves])
        for s in self.shelves:
            self._shelf_scroll_x.setdefault(s.key, 0.0)

    # -- animation ---------------------------------------------------------

    def tick(self, dt: float) -> None:
        """Advances hover-scale easing and fade-in timers. Cheap even for
        a large library: only tiles that have actually been drawn at
        least once get an entry in `_anims`, and this just walks that
        dict — no per-frame allocation, no work for off-screen shelves."""
        self._dt = dt
        for anim in self._anims.values():
            if anim.age < FADE_IN_DURATION + FADE_IN_STAGGER * 8:
                anim.age += dt

    def _anim_for(self, title_id: str) -> _TileAnim:
        anim = self._anims.get(title_id)
        if anim is None:
            anim = _TileAnim()
            self._anims[title_id] = anim
        return anim

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
        self._quad.vertical_gradient(0, 0, viewport_w, viewport_h, BG_TOP, BG_BOTTOM, steps=8)
        for shelf_idx, (shelf, top) in enumerate(zip(self.shelves, self._shelf_tops)):
            draw_top = top - self.scroll_y
            if draw_top > viewport_h or draw_top + 36 + TILE_H < 0:
                continue  # cheap vertical culling — off-screen shelves cost nothing
            self._draw_shelf(shelf, draw_top, shelf_idx, viewport_w)

    def _draw_shelf(self, shelf: Shelf, top: float, shelf_idx: int, viewport_w: float) -> None:
        if self._text:
            self._text.draw(shelf.label, SHELF_SIDE_MARGIN, top, 21, LABEL_COLOR)

        scroll_x = self._shelf_scroll_x.get(shelf.key, 0.0)
        rects = shelf_tile_rects(len(shelf.tiles), top, scroll_x)
        for tile_idx, (tile, rect) in enumerate(zip(shelf.tiles, rects)):
            if rect.x + rect.w < 0 or rect.x > viewport_w:
                continue  # cheap horizontal culling for long shelves
            self._draw_tile(tile, rect, tile_idx, hovered=(self._hovered == (shelf_idx, tile_idx)))

    def _draw_tile(self, tile, rect, column: int, hovered: bool) -> None:
        anim = self._anim_for(tile.title.id)

        # Hover scale eases toward its target every frame rather than
        # snapping, so tiles visibly "lift" instead of jump-cutting —
        # exponential ease, frame-rate independent via the dt term.
        target_scale = HOVER_SCALE if hovered else 1.0
        ease = 1.0 - pow(2.71828, -HOVER_ANIM_SPEED * self._dt)
        anim.scale = _lerp(anim.scale, target_scale, ease)

        # Fade-in: staggered by column so a shelf's tiles bloom in left
        # to right instead of all popping at once.
        fade_t = (anim.age - column * FADE_IN_STAGGER) / FADE_IN_DURATION
        opacity = max(0.0, min(1.0, fade_t))

        # Scale from the tile's own center, not its top-left, so hover
        # growth reads as "coming toward you" rather than "sliding down-right".
        w = rect.w * anim.scale
        h = rect.h * anim.scale
        x = rect.x - (w - rect.w) / 2.0
        y = rect.y - (h - rect.h) / 2.0

        if hovered:
            # Soft drop shadow — a few translucent rects offset+padded
            # outward, cheap stand-in for a real blur without a second
            # shader pass.
            for pad, alpha in ((10, 0.10), (6, 0.16), (3, 0.22)):
                self._quad.rect(x - pad, y - pad + 4, w + 2 * pad, h + 2 * pad,
                                 (SHADOW_COLOR[0], SHADOW_COLOR[1], SHADOW_COLOR[2], alpha))

        tex_id = self._textures.get(tile.poster_path)
        tint = (1.0, 1.0, 1.0, opacity)
        self._quad.rect(x, y, w, h, (TILE_BG[0], TILE_BG[1], TILE_BG[2], TILE_BG[3] * opacity))
        self._quad.textured_rect_cover(x, y, w, h, tex_id, self._textures.get_aspect(tile.poster_path), tint=tint)

        # Bottom gradient scrim so title text / progress bar always read
        # against any poster art, bright or dark.
        scrim_h = h * 0.4
        self._quad.vertical_gradient(x, y + h - scrim_h, w, scrim_h, SCRIM_TOP, SCRIM_BOTTOM, steps=8)

        if tile.is_new:
            badge_w, badge_h = 46, 22
            self._quad.rect(x + 8, y + 8, badge_w, badge_h, NEW_BADGE_BG)
            if self._text:
                self._text.draw("NEW", x + 14, y + 12, 13, BADGE_TEXT_COLOR)

        if tile.progress_fraction > 0:
            bar_h = 4
            bar_margin = 8
            bar_y = y + h - bar_h - bar_margin
            bar_w = w - 2 * bar_margin
            self._quad.rect(x + bar_margin, bar_y, bar_w, bar_h, PROGRESS_BG)
            self._quad.rect(x + bar_margin, bar_y, bar_w * tile.progress_fraction, bar_h, PROGRESS_FILL)

        if self._text:
            label_color = (TITLE_COLOR[0], TITLE_COLOR[1], TITLE_COLOR[2], opacity)
            # When hovered, the title sits inside the scrim over the
            # (larger) tile instead of below it — Netflix-style "info
            # rides on the art" instead of pushing the grid apart.
            if hovered:
                self._text.draw(tile.display_name, x + 10, y + h - 30, 14, label_color, max_width=w - 20)
            else:
                self._text.draw(tile.display_name, rect.x, rect.y + rect.h + 6, 15, label_color,
                                 max_width=rect.w)
