"""
player/osd.py — M2

A deliberately tiny GL quad renderer, used only to draw the scrub bar
(background track + filled progress + playhead) so click/drag seeking is
testable end-to-end in M2. This is NOT the custom UI renderer — Section
9/10's real UI (shelves, buttons, text via freetype, texture atlases) is
M5+. Building that here would be getting ahead of the spec's milestone
order for the sake of a progress bar.

Everything is normalized-device-coordinate rectangles, one shader, one
VBO, updated per-frame. No text, no icons — just enough to see and drag
the bar.
"""

from __future__ import annotations

import ctypes

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

    def bar_rect_px(self, fb_w: int, fb_h: int) -> tuple[float, float, float, float]:
        """Returns (x, y, w, h) of the scrub bar's hit-test rectangle, in
        framebuffer pixels with origin bottom-left."""
        x = self.MARGIN_SIDE_PX
        w = fb_w - 2 * self.MARGIN_SIDE_PX
        y = self.MARGIN_BOTTOM_PX
        h = self.HEIGHT_PX
        return x, y, w, h

    def draw(self, fb_w: int, fb_h: int, progress_fraction: float) -> None:
        x, y, w, h = self.bar_rect_px(fb_w, fb_h)
        gl.glDisable(gl.GL_DEPTH_TEST)

        self._draw_rect_px(x, y, w, h, fb_w, fb_h, self.BG_COLOR)

        fill_w = w * max(0.0, min(1.0, progress_fraction))
        if fill_w > 0:
            self._draw_rect_px(x, y, fill_w, h, fb_w, fb_h, self.FILL_COLOR)

        playhead_x = x + fill_w - self.PLAYHEAD_WIDTH_PX / 2
        self._draw_rect_px(
            playhead_x, y - 3, self.PLAYHEAD_WIDTH_PX, h + 6, fb_w, fb_h, self.PLAYHEAD_COLOR
        )
