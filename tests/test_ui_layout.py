import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ui.layout import (
    TILE_H,
    TILE_W,
    clamp_scroll,
    hit_test_tile,
    max_scroll_x,
    shelf_tile_rects,
    shelves_layout,
)


def test_shelf_tile_rects_spacing():
    rects = shelf_tile_rects(3, shelf_top=100.0)
    assert len(rects) == 3
    assert rects[0].y == rects[1].y == rects[2].y
    assert rects[1].x - rects[0].x == TILE_W + 16
    assert rects[0].w == TILE_W and rects[0].h == TILE_H


def test_scroll_clamps_to_content_width():
    # Few tiles, wide viewport -> can't scroll at all.
    assert max_scroll_x(3, viewport_w=2000) == 0.0
    assert clamp_scroll(500, 3, viewport_w=2000) == 0.0

    # Many tiles, narrow viewport -> positive max scroll, clamps above it.
    m = max_scroll_x(20, viewport_w=800)
    assert m > 0
    assert clamp_scroll(m + 1000, 20, viewport_w=800) == m
    assert clamp_scroll(-50, 20, viewport_w=800) == 0.0


def test_hit_test_tile_basic_and_viewport_clip():
    rects = shelf_tile_rects(3, shelf_top=0.0)
    # Click inside the first tile.
    hit = hit_test_tile(rects, rects[0].x + 5, rects[0].y + 5)
    assert hit == 0
    # Click in the gap between tiles -> no hit.
    gap_x = rects[0].x + TILE_W + 4
    assert hit_test_tile(rects, gap_x, rects[0].y + 5) is None
    # Tile scrolled left of the viewport should not register.
    assert hit_test_tile(rects, rects[0].x + 5, rects[0].y + 5,
                          viewport_left=rects[0].x + TILE_W + 1) is None


def test_shelves_layout_stacks_with_gap():
    tops = shelves_layout([5, 3])
    assert tops[0] == 24.0
    assert tops[1] > tops[0]
