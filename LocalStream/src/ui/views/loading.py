"""
ui/views/loading.py — M5 fix

The library scan + TMDB enrich pass (main.build_library) used to run
synchronously between window creation and the render loop starting —
GLFW never got to poll events or swap buffers for however long that
took, so the OS saw an unresponsive window with nothing painted (blank
white) for the whole scan on a real library. That's what this view
fixes: `run_browse` now kicks the scan+enrich off on a background
thread and keeps pumping the normal render loop (poll_events + swap)
while it runs, drawing *this* — a simple centered logo mark, a status
line naming what's being scanned/fetched right now, and an
indeterminate progress bar — instead of nothing.

Deliberately minimal: this exists for maybe a few seconds to a couple
of minutes on first run (subsequent runs are much faster once the
track/metadata caches are warm), not a view anyone lingers on, so it
doesn't need shelves/animation polish, just proof the app is alive and
working.
"""

from __future__ import annotations

from typing import Optional

from ui.gl2d import Quad2D
from ui.text import TextRenderer

BG_COLOR = (0.06, 0.06, 0.08, 1.0)
TITLE_COLOR = (0.92, 0.92, 0.95, 1.0)
STATUS_COLOR = (0.6, 0.6, 0.65, 1.0)
BAR_BG = (0.16, 0.16, 0.19, 1.0)
BAR_FILL = (0.95, 0.72, 0.20, 1.0)  # same gold accent as Play/progress elsewhere

BAR_W = 360.0
BAR_H = 6.0


class LoadingView:
    """Renders over a blank background: "LocalStream" wordmark, current
    status line (from build_library's `status` callback), and a small
    indeterminate bar that sweeps left-to-right so the window visibly
    isn't frozen even when progress fraction isn't meaningful (TMDB
    lookups don't have a reliable total ahead of time for a mixed
    movies+shows+anime run)."""

    def __init__(self, quad: Quad2D, text: Optional[TextRenderer]) -> None:
        self._quad = quad
        self._text = text
        self.status = "Starting up…"
        self._t = 0.0  # seconds elapsed, drives the sweep animation

    def tick(self, dt: float) -> None:
        self._t += dt

    def set_status(self, status: str) -> None:
        self.status = status

    def render(self, fb_w: int, fb_h: int) -> None:
        self._quad.rect(0, 0, fb_w, fb_h, BG_COLOR)

        cx, cy = fb_w / 2.0, fb_h / 2.0

        if self._text:
            title = "LocalStream"
            title_size = 40
            title_w = self._text.measure(title, title_size)
            self._text.draw(title, cx - title_w / 2.0, cy - 60, title_size, TITLE_COLOR)

            status_size = 16
            status_w = self._text.measure(self.status, status_size)
            max_w = min(fb_w - 80.0, 700.0)
            self._text.draw(
                self.status, cx - min(status_w, max_w) / 2.0, cy + 10, status_size,
                STATUS_COLOR, max_width=max_w,
            )

        bar_x = cx - BAR_W / 2.0
        bar_y = cy + 45
        self._quad.rect(bar_x, bar_y, BAR_W, BAR_H, BAR_BG)

        # Indeterminate sweep: a ~1/3-width highlight bounces back and
        # forth across the track once per ~1.6s.
        sweep_w = BAR_W / 3.0
        period = 1.6
        phase = (self._t % period) / period  # 0..1
        # triangle wave 0->1->0 so it bounces instead of snapping back
        travel = phase * 2.0 if phase < 0.5 else (1.0 - phase) * 2.0
        sweep_x = bar_x + travel * (BAR_W - sweep_w)
        self._quad.rect(sweep_x, bar_y, sweep_w, BAR_H, BAR_FILL)
