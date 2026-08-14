"""
player/scrub_input.py — M2

Click/drag handling for the scrub bar (Section 8: "Exact seek to
position — Click/drag scrub bar"). Kept separate from osd.py (drawing)
and mpv_player.py (playback) on purpose — this module only knows "where
is the mouse relative to the bar rect," and hands off a 0.0-1.0 fraction;
it doesn't know about mpv or GL.

Note on coordinates: GLFW cursor position callbacks report window-space
coordinates with origin top-left; the OSD's bar_rect_px() is computed in
framebuffer-space with origin bottom-left (matching OpenGL). This class
takes window size + cursor pos and does the flip itself so callers don't
have to juggle both spaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from player.osd import ScrubBarOSD


@dataclass
class ScrubBarInput:
    osd: ScrubBarOSD
    _dragging: bool = False
    _hover_padding_px: float = 6.0  # generous hit area above/below the thin bar

    def _rect_window_space(self, window_w: int, window_h: int) -> tuple[float, float, float, float]:
        """Same rect as osd.bar_rect_px, but in window-space (top-left origin,
        y not flipped) — for hit-testing against GLFW cursor coordinates."""
        x, y_bottom_origin, w, h = self.osd.bar_rect_px(window_w, window_h)
        y_top_origin = window_h - y_bottom_origin - h
        return x, y_top_origin, w, h

    def hit_test(self, window_w: int, window_h: int, cursor_x: float, cursor_y: float) -> bool:
        x, y, w, h = self._rect_window_space(window_w, window_h)
        pad = self._hover_padding_px
        return (x <= cursor_x <= x + w) and (y - pad <= cursor_y <= y + h + pad)

    def fraction_at(self, window_w: int, window_h: int, cursor_x: float) -> float:
        x, _y, w, _h = self._rect_window_space(window_w, window_h)
        if w <= 0:
            return 0.0
        return max(0.0, min(1.0, (cursor_x - x) / w))

    # -- drag state machine --------------------------------------------------

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    def on_mouse_down(self, window_w: int, window_h: int, cursor_x: float, cursor_y: float) -> Optional[float]:
        """Returns a seek fraction if the click landed on the bar (click-to-
        position, Section 8), and begins a drag."""
        if not self.hit_test(window_w, window_h, cursor_x, cursor_y):
            return None
        self._dragging = True
        return self.fraction_at(window_w, window_h, cursor_x)

    def on_mouse_up(self) -> None:
        self._dragging = False

    def on_mouse_move(self, window_w: int, window_h: int, cursor_x: float) -> Optional[float]:
        """Returns an updated seek fraction while actively dragging, else None."""
        if not self._dragging:
            return None
        return self.fraction_at(window_w, window_h, cursor_x)
