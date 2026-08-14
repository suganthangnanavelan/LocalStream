"""
ui/layout.py — M5

Geometry math for the Home poster grid / shelf rows, kept separate from
views/home.py's GL drawing so hit-testing and scroll-clamping can be unit
tested without a window/GL context. Everything here is pixel-space floats
in, rects out — views/home.py just draws whatever this says.
"""

from __future__ import annotations

from dataclasses import dataclass

TILE_W = 180
TILE_H = 270
TILE_GAP = 16
SHELF_LABEL_H = 36
SHELF_GAP = 28
SHELF_SIDE_MARGIN = 48

# Netflix-style row "peek" — a shelf-nav arrow click pages by most of a
# viewport width, not a single tile, so side-scrolling actually feels
# like paging through the row instead of nudging it.
SHELF_ARROW_W = 44
SHELF_PAGE_FRACTION = 0.85


@dataclass(frozen=True)
class TileRect:
    index: int
    x: float
    y: float
    w: float
    h: float


def shelf_tile_rects(tile_count: int, shelf_top: float, scroll_x: float = 0.0) -> list[TileRect]:
    """Positions for one horizontally-scrolling shelf row. `scroll_x` is
    how far the row has been scrolled right (pixels), so tile 0 sits at
    `SHELF_SIDE_MARGIN - scroll_x`."""
    y = shelf_top + SHELF_LABEL_H
    rects = []
    for i in range(tile_count):
        x = SHELF_SIDE_MARGIN - scroll_x + i * (TILE_W + TILE_GAP)
        rects.append(TileRect(i, x, y, TILE_W, TILE_H))
    return rects


def shelf_row_height() -> float:
    return SHELF_LABEL_H + TILE_H


def shelf_content_width(tile_count: int) -> float:
    if tile_count == 0:
        return 0.0
    return tile_count * TILE_W + (tile_count - 1) * TILE_GAP


def max_scroll_x(tile_count: int, viewport_w: float) -> float:
    """Furthest a shelf can be scrolled right before its last tile would
    leave the right edge — clamps both the wheel/drag scroll input and an
    arrow-button "scroll shelf" click."""
    content_w = shelf_content_width(tile_count) + SHELF_SIDE_MARGIN
    overflow = content_w - viewport_w + SHELF_SIDE_MARGIN
    return max(0.0, overflow)


def clamp_scroll(value: float, tile_count: int, viewport_w: float) -> float:
    return max(0.0, min(value, max_scroll_x(tile_count, viewport_w)))


def hit_test_tile(rects: list[TileRect], cursor_x: float, cursor_y: float,
                   viewport_left: float = 0.0, viewport_right: float | None = None) -> int | None:
    """Returns the index of the tile under the cursor, or None. Tiles
    partially/fully outside [viewport_left, viewport_right] (scrolled off
    the shelf's visible strip) never hit, so a tile clipped behind the
    left margin from scrolling can't be accidentally clicked."""
    for rect in rects:
        if rect.x + rect.w <= viewport_left:
            continue
        if viewport_right is not None and rect.x >= viewport_right:
            continue
        if rect.x <= cursor_x <= rect.x + rect.w and rect.y <= cursor_y <= rect.y + rect.h:
            return rect.index
    return None


def shelf_page_step(viewport_w: float) -> float:
    """How far a shelf-nav arrow click scrolls the row — most of the
    visible width, so one click pages to (roughly) the next screenful of
    posters instead of creeping by a tile or two."""
    return max(TILE_W, (viewport_w - SHELF_SIDE_MARGIN) * SHELF_PAGE_FRACTION)


def shelf_arrow_rects(row_top: float, row_h: float,
                       viewport_w: float) -> tuple[TileRect, TileRect]:
    """Left/right nav-arrow hit rects for a shelf row, vertically
    centered on the poster band (row_top is the tile top, not the
    shelf's label top)."""
    left = TileRect(-1, 4.0, row_top, SHELF_ARROW_W, row_h)
    right = TileRect(-2, viewport_w - SHELF_ARROW_W - 4.0, row_top, SHELF_ARROW_W, row_h)
    return left, right


def shelves_layout(shelf_tile_counts: list[int], top_margin: float = 24.0) -> list[float]:
    """Given the tile count of each shelf in display order, returns the
    top y-coordinate of each shelf, stacked with SHELF_GAP between them."""
    tops = []
    y = top_margin
    for _ in shelf_tile_counts:
        tops.append(y)
        y += shelf_row_height() + SHELF_GAP
    return tops


def episode_row_rect(index: int, list_top: float, row_h: float = 96.0,
                      x: float = SHELF_SIDE_MARGIN, w: float = 640.0) -> TileRect:
    """Detail View's season/episode list — a vertical stack of rows
    (thumbnail + title + synopsis snippet) rather than a poster grid."""
    return TileRect(index, x, list_top + index * (row_h + 8), w, row_h)
