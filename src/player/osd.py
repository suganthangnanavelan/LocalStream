"""
player/osd.py — M2

A deliberately tiny GL renderer for playback feedback: scrub bar, elapsed/
duration time readout, and the transient Netflix-style pulses that confirm
seek/volume/brightness key presses actually did something. This is NOT the
custom UI renderer — Section 9/10's real UI (shelves, buttons, freetype
text, texture atlases) is M5+. There's no freetype here; the time/seek
digits are drawn as simple 7-segment glyphs out of the same rect primitive
everything else uses, which is plenty for numbers and keeps this module
dependency-free until real text rendering lands.

Everything is normalized-device-coordinate geometry, one shader, drawn
per-frame. FeedbackOSD (bottom of file) owns the fade timing so callers
just call notify_*() on a key press and draw() every frame.
"""

from __future__ import annotations

import ctypes
import time

import glfw
import numpy as np
from OpenGL import GL as gl

_VERTEX_SRC = """
#version 330 core
layout(location = 0) in vec2 a_pos;
void main() {
    gl_Position = vec4(a_pos, 0.0, 1.0);
}
"""

_FRAGMENT_SRC = """
#version 330 core
uniform vec4 u_color;
out vec4 frag_color;
void main() {
    frag_color = u_color;
}
"""


def _compile_shader(src: str, shader_type) -> int:
    shader = gl.glCreateShader(shader_type)
    gl.glShaderSource(shader, src)
    gl.glCompileShader(shader)
    if not gl.glGetShaderiv(shader, gl.GL_COMPILE_STATUS):
        raise RuntimeError(gl.glGetShaderInfoLog(shader).decode())
    return shader


# -- 7-segment glyphs, used for the time readout and seek/volume pulses ----
#
# Each glyph is a list of (x, y, w, h) rects in a 0..1 unit cell (origin
# bottom-left), at a shared segment thickness T. Covers digits, and the
# handful of punctuation the time readout / pulses need. No freetype dep
# needed for "12:34 / 1:03:22" and "+10s".
_T = 0.16
_SEGMENTS = {
    "a": (_T, 1 - _T, 1 - 2 * _T, _T),                      # top
    "g": (_T, 0.5 - _T / 2, 1 - 2 * _T, _T),                # middle
    "d": (_T, 0.0, 1 - 2 * _T, _T),                          # bottom
    "f": (0.0, 0.5 + _T / 2, _T, 0.5 - 1.5 * _T),            # top-left
    "b": (1 - _T, 0.5 + _T / 2, _T, 0.5 - 1.5 * _T),         # top-right
    "e": (0.0, _T, _T, 0.5 - 1.5 * _T),                       # bottom-left
    "c": (1 - _T, _T, _T, 0.5 - 1.5 * _T),                    # bottom-right
}
_DIGIT_SEGMENTS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd",
    "4": "fgbc", "5": "afgcd", "6": "afgecd", "7": "abc",
    "8": "abcdefg", "9": "abcdfg",
}
_GLYPH_RECTS = {ch: [_SEGMENTS[s] for s in segs] for ch, segs in _DIGIT_SEGMENTS.items()}
_GLYPH_RECTS[":"] = [(0.35, 0.62, 0.3, 0.16), (0.35, 0.22, 0.3, 0.16)]
_GLYPH_RECTS["-"] = [_SEGMENTS["g"]]
_GLYPH_RECTS["+"] = [_SEGMENTS["g"], (0.42, 0.22, 0.16, 0.56)]
_GLYPH_RECTS[" "] = []
_GLYPH_RECTS["/"] = [(0.05, 0.05, 0.28, 0.28), (0.36, 0.36, 0.28, 0.28), (0.67, 0.67, 0.28, 0.28)]
_GLYPH_WIDTH_FRACTION = {":": 0.45, "-": 0.6, "+": 0.7, " ": 0.5, "/": 0.75}


class ScrubBarOSD:
    # Bar geometry, in pixels from the bottom edge — matches the
    # click/hover hit-testing rect used by ScrubBarInput below.
    HEIGHT_PX = 6
    MARGIN_BOTTOM_PX = 24
    MARGIN_SIDE_PX = 24

    BG_COLOR = (1.0, 1.0, 1.0, 0.25)
    FILL_COLOR = (0.90, 0.15, 0.20, 1.0)  # matches Skip/segment red-zone accent (Section 5.5)
    PLAYHEAD_COLOR = (1.0, 1.0, 1.0, 1.0)
    PLAYHEAD_WIDTH_PX = 3

    TIME_DIGIT_H_PX = 16
    TIME_DIGIT_W_PX = 9
    TIME_GAP_PX = 3
    TIME_MARGIN_ABOVE_BAR_PX = 14
    TIME_COLOR = (1.0, 1.0, 1.0, 0.9)

    def __init__(self) -> None:
        vs = _compile_shader(_VERTEX_SRC, gl.GL_VERTEX_SHADER)
        fs = _compile_shader(_FRAGMENT_SRC, gl.GL_FRAGMENT_SHADER)
        self._program = gl.glCreateProgram()
        gl.glAttachShader(self._program, vs)
        gl.glAttachShader(self._program, fs)
        gl.glLinkProgram(self._program)
        if not gl.glGetProgramiv(self._program, gl.GL_LINK_STATUS):
            raise RuntimeError(gl.glGetProgramInfoLog(self._program).decode())
        gl.glDeleteShader(vs)
        gl.glDeleteShader(fs)

        self._u_color = gl.glGetUniformLocation(self._program, "u_color")

        self._vao = gl.glGenVertexArrays(1)
        self._vbo = gl.glGenBuffers(1)
        gl.glBindVertexArray(self._vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, 8 * 4, None, gl.GL_DYNAMIC_DRAW)  # 4 vec2 verts
        gl.glVertexAttribPointer(0, 2, gl.GL_FLOAT, gl.GL_FALSE, 0, ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(0)
        gl.glBindVertexArray(0)

    def _draw_rect_px(self, x: float, y: float, w: float, h: float, fb_w: int, fb_h: int, color) -> None:
        """x,y = bottom-left corner in pixels (origin bottom-left of framebuffer)."""
        # to NDC
        x0 = (x / fb_w) * 2.0 - 1.0
        x1 = ((x + w) / fb_w) * 2.0 - 1.0
        y0 = (y / fb_h) * 2.0 - 1.0
        y1 = ((y + h) / fb_h) * 2.0 - 1.0
        verts = np.array(
            [x0, y0, x1, y0, x1, y1, x0, y1],
            dtype=np.float32,
        )
        gl.glBindVertexArray(self._vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._vbo)
        gl.glBufferSubData(gl.GL_ARRAY_BUFFER, 0, verts.nbytes, verts)
        gl.glUseProgram(self._program)
        gl.glUniform4f(self._u_color, *color)
        gl.glDrawArrays(gl.GL_TRIANGLE_FAN, 0, 4)
        gl.glBindVertexArray(0)

    def _draw_triangle_px(self, p1, p2, p3, fb_w: int, fb_h: int, color) -> None:
        """p1,p2,p3 = (x, y) pixel points, origin bottom-left. Used for the
        chevron seek pulse — the rect primitive alone can't make an arrow."""
        def to_ndc(p):
            return (p[0] / fb_w) * 2.0 - 1.0, (p[1] / fb_h) * 2.0 - 1.0

        (x0, y0), (x1, y1), (x2, y2) = to_ndc(p1), to_ndc(p2), to_ndc(p3)
        verts = np.array([x0, y0, x1, y1, x2, y2, x2, y2], dtype=np.float32)
        gl.glBindVertexArray(self._vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._vbo)
        gl.glBufferSubData(gl.GL_ARRAY_BUFFER, 0, verts.nbytes, verts)
        gl.glUseProgram(self._program)
        gl.glUniform4f(self._u_color, *color)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
        gl.glBindVertexArray(0)

    # -- glyph text (7-segment numerals) ---------------------------------

    def _text_width_px(self, text: str, digit_w: float, gap: float) -> float:
        total = 0.0
        for ch in text:
            total += digit_w * _GLYPH_WIDTH_FRACTION.get(ch, 1.0) + gap
        return total - gap if text else 0.0

    def _draw_text_px(
        self, text: str, x: float, y: float, digit_w: float, digit_h: float,
        gap: float, fb_w: int, fb_h: int, color,
    ) -> None:
        """Draws `text` (digits + : - + / space only) left-to-right starting
        at bottom-left corner (x, y), each glyph a set of 7-segment rects."""
        cursor = x
        for ch in text:
            cell_w = digit_w * _GLYPH_WIDTH_FRACTION.get(ch, 1.0)
            for gx, gy, gw, gh in _GLYPH_RECTS.get(ch, []):
                self._draw_rect_px(
                    cursor + gx * cell_w, y + gy * digit_h,
                    gw * cell_w, gh * digit_h,
                    fb_w, fb_h, color,
                )
            cursor += cell_w + gap

    def bar_rect_px(self, fb_w: int, fb_h: int) -> tuple[float, float, float, float]:
        """Returns (x, y, w, h) of the scrub bar's hit-test rectangle, in
        framebuffer pixels with origin bottom-left."""
        x = self.MARGIN_SIDE_PX
        w = fb_w - 2 * self.MARGIN_SIDE_PX
        y = self.MARGIN_BOTTOM_PX
        h = self.HEIGHT_PX
        return x, y, w, h

    @staticmethod
    def _format_time(seconds: float) -> str:
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def draw(
        self, fb_w: int, fb_h: int, progress_fraction: float,
        position_s: float | None = None, duration_s: float | None = None,
    ) -> None:
        x, y, w, h = self.bar_rect_px(fb_w, fb_h)
        gl.glDisable(gl.GL_DEPTH_TEST)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

        self._draw_rect_px(x, y, w, h, fb_w, fb_h, self.BG_COLOR)

        fill_w = w * max(0.0, min(1.0, progress_fraction))
        if fill_w > 0:
            self._draw_rect_px(x, y, fill_w, h, fb_w, fb_h, self.FILL_COLOR)

        playhead_x = x + fill_w - self.PLAYHEAD_WIDTH_PX / 2
        self._draw_rect_px(
            playhead_x, y - 3, self.PLAYHEAD_WIDTH_PX, h + 6, fb_w, fb_h, self.PLAYHEAD_COLOR
        )

        # -- time readout: "elapsed / total", left-aligned above the bar --
        if position_s is not None and duration_s:
            label = f"{self._format_time(position_s)} / {self._format_time(duration_s)}"
            self._draw_text_px(
                label, x, y + h + self.TIME_MARGIN_ABOVE_BAR_PX,
                self.TIME_DIGIT_W_PX, self.TIME_DIGIT_H_PX, self.TIME_GAP_PX,
                fb_w, fb_h, self.TIME_COLOR,
            )


# -- transient feedback pulses (seek / volume / brightness) ----------------
#
# Netflix-style: press an arrow / volume / brightness key and a small
# indicator flashes in, holds briefly, and fades out — the confirmation
# that up/down and left/right actually did something (previously there was
# no visual feedback at all for volume/brightness, which is what made them
# look "broken" even though the mpv calls were working).
class FeedbackOSD:
    HOLD_S = 0.65        # fully visible for this long after the last update
    FADE_S = 0.35        # then fades out over this long
    BURST_WINDOW_S = 0.8  # repeated seeks within this long keep accumulating
                          # into one growing indicator instead of resetting

    ICON_SIZE_PX = 34
    ICON_GAP_PX = 14
    DIGIT_W_PX = 15
    DIGIT_H_PX = 26
    DIGIT_GAP_PX = 4

    BAR_W_PX = 220
    BAR_H_PX = 10
    BAR_MARGIN_TOP_PX = 90

    def __init__(self, osd: ScrubBarOSD) -> None:
        self._osd = osd
        # seek pulse state
        self._seek_total_s = 0.0
        self._seek_forward = True
        self._seek_last_update = 0.0
        # volume / brightness pulse state (share one slot — Netflix only
        # ever shows one of these at a time, holding the last-touched one)
        self._level_kind: str | None = None  # "volume" | "brightness"
        self._level_value = 0.0
        self._level_last_update = 0.0

    # -- notifications, called from key handlers -------------------------

    def notify_seek(self, delta_s: float) -> None:
        now = time.monotonic()
        forward = delta_s >= 0
        if forward != self._seek_forward or (now - self._seek_last_update) > self.BURST_WINDOW_S:
            self._seek_total_s = 0.0
            self._seek_forward = forward
        self._seek_total_s += abs(delta_s)
        self._seek_last_update = now

    def notify_volume(self, value: float) -> None:
        self._level_kind = "volume"
        self._level_value = value
        self._level_last_update = time.monotonic()

    def notify_brightness(self, value: float) -> None:
        self._level_kind = "brightness"
        self._level_value = value
        self._level_last_update = time.monotonic()

    # -- fade math ---------------------------------------------------------

    @staticmethod
    def _alpha_for(last_update: float, now: float) -> float:
        age = now - last_update
        if age <= FeedbackOSD.HOLD_S:
            return 1.0
        if age <= FeedbackOSD.HOLD_S + FeedbackOSD.FADE_S:
            return 1.0 - (age - FeedbackOSD.HOLD_S) / FeedbackOSD.FADE_S
        return 0.0

    # -- drawing -----------------------------------------------------------

    def draw(self, fb_w: int, fb_h: int) -> None:
        now = time.monotonic()
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

        if self._seek_total_s > 0:
            alpha = self._alpha_for(self._seek_last_update, now)
            if alpha > 0:
                self._draw_seek_pulse(fb_w, fb_h, alpha)

        if self._level_kind is not None:
            alpha = self._alpha_for(self._level_last_update, now)
            if alpha > 0:
                self._draw_level_pulse(fb_w, fb_h, alpha)

    def _draw_seek_pulse(self, fb_w: int, fb_h: int, alpha: float) -> None:
        label = f"{int(self._seek_total_s)}s"
        text_w = self._osd._text_width_px(label, self.DIGIT_W_PX, self.DIGIT_GAP_PX)
        chevron_w = self.ICON_SIZE_PX * 0.55
        content_w = chevron_w * 2 + self.ICON_GAP_PX + text_w
        cx = fb_w / 2
        cy = fb_h / 2

        color = (1.0, 1.0, 1.0, 0.92 * alpha)
        chevron_h = self.ICON_SIZE_PX

        if self._seek_forward:
            start_x = cx + self.ICON_GAP_PX
            text_x = start_x + chevron_w * 2 + self.ICON_GAP_PX
            for i in range(2):
                base_x = start_x + i * (chevron_w + 6)
                self._osd._draw_triangle_px(
                    (base_x, cy + chevron_h / 2), (base_x, cy - chevron_h / 2),
                    (base_x + chevron_w, cy), fb_w, fb_h, color,
                )
        else:
            start_x = cx - self.ICON_GAP_PX
            text_x = start_x - chevron_w * 2 - self.ICON_GAP_PX - text_w
            for i in range(2):
                base_x = start_x - i * (chevron_w + 6)
                self._osd._draw_triangle_px(
                    (base_x, cy + chevron_h / 2), (base_x, cy - chevron_h / 2),
                    (base_x - chevron_w, cy), fb_w, fb_h, color,
                )

        self._osd._draw_text_px(
            label, text_x, cy - self.DIGIT_H_PX / 2,
            self.DIGIT_W_PX, self.DIGIT_H_PX, self.DIGIT_GAP_PX,
            fb_w, fb_h, color,
        )

    def _draw_level_pulse(self, fb_w: int, fb_h: int, alpha: float) -> None:
        if self._level_kind == "volume":
            fraction = max(0.0, min(1.0, self._level_value / 100.0))
            label = f"{int(round(self._level_value))}"
        else:
            fraction = max(0.0, min(1.0, (self._level_value + 100.0) / 200.0))
            label = f"{'+' if self._level_value >= 0 else '-'}{abs(int(round(self._level_value)))}"

        bar_x = fb_w / 2 - self.BAR_W_PX / 2
        bar_y = fb_h - self.BAR_MARGIN_TOP_PX
        bg = (1.0, 1.0, 1.0, 0.25 * alpha)
        fg = (1.0, 1.0, 1.0, 0.92 * alpha)

        self._osd._draw_rect_px(bar_x, bar_y, self.BAR_W_PX, self.BAR_H_PX, fb_w, fb_h, bg)
        fill_w = self.BAR_W_PX * fraction
        if fill_w > 0:
            self._osd._draw_rect_px(bar_x, bar_y, fill_w, self.BAR_H_PX, fb_w, fb_h, fg)

        label_w = self._osd._text_width_px(label, self.DIGIT_W_PX, self.DIGIT_GAP_PX)
        self._osd._draw_text_px(
            label, fb_w / 2 - label_w / 2, bar_y + self.BAR_H_PX + 10,
            self.DIGIT_W_PX, self.DIGIT_H_PX, self.DIGIT_GAP_PX,
            fb_w, fb_h, fg,
        )