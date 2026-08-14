"""
ui/gl2d.py — M5

A single, shared 2D quad primitive that every M5 view (Home shelves,
Detail backdrop/poster, text glyphs) draws through, so there's exactly one
shader/VBO to reason about instead of one per view. Pixel-space in, GL
clip-space out — callers never touch NDC directly.

Deliberately *not* the persistent-VBO / texture-atlas batching Section 10
calls out for perf — that's an M8/M10 concern ("if a specific view still
can't hit target frame time"). This is the straightforward, correct
version: one small dynamic VBO, re-filled with `glBufferSubData` per quad.
Good enough for M5's shelves/detail page; batching can replace the guts of
`Quad2D.draw()` later without any view code changing.
"""

from __future__ import annotations

import ctypes

import numpy as np
from OpenGL import GL as gl

_VERTEX_SRC = """
#version 330 core
layout(location = 0) in vec2 a_pos;   // pixel space, origin top-left
layout(location = 1) in vec2 a_uv;
uniform vec2 u_screen_size;
out vec2 v_uv;
void main() {
    vec2 ndc = (a_pos / u_screen_size) * 2.0 - 1.0;
    ndc.y = -ndc.y;
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_uv = a_uv;
}
"""

_FRAGMENT_SRC = """
#version 330 core
in vec2 v_uv;
out vec4 frag_color;
uniform vec4 u_color;
uniform sampler2D u_tex;
uniform int u_mode; // 0 = solid color, 1 = textured (RGBA, tinted), 2 = text glyph (R channel = alpha)
void main() {
    if (u_mode == 0) {
        frag_color = u_color;
    } else if (u_mode == 1) {
        frag_color = texture(u_tex, v_uv) * u_color;
    } else {
        float a = texture(u_tex, v_uv).r;
        frag_color = vec4(u_color.rgb, u_color.a * a);
    }
}
"""


def _compile_shader(src: str, shader_type) -> int:
    shader = gl.glCreateShader(shader_type)
    gl.glShaderSource(shader, src)
    gl.glCompileShader(shader)
    if not gl.glGetShaderiv(shader, gl.GL_COMPILE_STATUS):
        raise RuntimeError(gl.glGetShaderInfoLog(shader).decode())
    return shader


class Quad2D:
    """Owns the one shader program + one dynamic VBO used for every
    rect/textured-rect/glyph draw in the M5 UI."""

    MODE_SOLID = 0
    MODE_TEXTURED = 1
    MODE_TEXT = 2

    def __init__(self) -> None:
        vs = _compile_shader(_VERTEX_SRC, gl.GL_VERTEX_SHADER)
        fs = _compile_shader(_FRAGMENT_SRC, gl.GL_FRAGMENT_SHADER)
        self.program = gl.glCreateProgram()
        gl.glAttachShader(self.program, vs)
        gl.glAttachShader(self.program, fs)
        gl.glLinkProgram(self.program)
        if not gl.glGetProgramiv(self.program, gl.GL_LINK_STATUS):
            raise RuntimeError(gl.glGetProgramInfoLog(self.program).decode())
        gl.glDeleteShader(vs)
        gl.glDeleteShader(fs)

        self._u_screen_size = gl.glGetUniformLocation(self.program, "u_screen_size")
        self._u_color = gl.glGetUniformLocation(self.program, "u_color")
        self._u_mode = gl.glGetUniformLocation(self.program, "u_mode")
        self._u_tex = gl.glGetUniformLocation(self.program, "u_tex")

        self.vao = gl.glGenVertexArrays(1)
        self.vbo = gl.glGenBuffers(1)
        gl.glBindVertexArray(self.vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.vbo)
        # 6 verts * (pos.xy + uv.xy) floats, re-filled every draw call.
        gl.glBufferData(gl.GL_ARRAY_BUFFER, 6 * 4 * 4, None, gl.GL_DYNAMIC_DRAW)
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 2, gl.GL_FLOAT, gl.GL_FALSE, 16, ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(1)
        gl.glVertexAttribPointer(1, 2, gl.GL_FLOAT, gl.GL_FALSE, 16, ctypes.c_void_p(8))
        gl.glBindVertexArray(0)

        self._screen_w = 0
        self._screen_h = 0

    def begin_frame(self, screen_w: int, screen_h: int) -> None:
        self._screen_w = screen_w
        self._screen_h = screen_h
        gl.glUseProgram(self.program)
        gl.glUniform2f(self._u_screen_size, float(screen_w), float(screen_h))
        gl.glBindVertexArray(self.vao)

    def _upload_quad(self, x: float, y: float, w: float, h: float,
                      uv: tuple[float, float, float, float]) -> None:
        u0, v0, u1, v1 = uv
        verts = np.array([
            x,     y,     u0, v0,
            x + w, y,     u1, v0,
            x,     y + h, u0, v1,
            x + w, y,     u1, v0,
            x + w, y + h, u1, v1,
            x,     y + h, u0, v1,
        ], dtype=np.float32)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.vbo)
        gl.glBufferSubData(gl.GL_ARRAY_BUFFER, 0, verts.nbytes, verts)

    def rect(self, x: float, y: float, w: float, h: float,
             color: tuple[float, float, float, float]) -> None:
        gl.glUseProgram(self.program)
        gl.glUniform4f(self._u_color, *color)
        gl.glUniform1i(self._u_mode, self.MODE_SOLID)
        self._upload_quad(x, y, w, h, (0.0, 0.0, 1.0, 1.0))
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

    def textured_rect(self, x: float, y: float, w: float, h: float, texture_id: int,
                       tint: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
                       uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)) -> None:
        gl.glUseProgram(self.program)
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, texture_id)
        gl.glUniform1i(self._u_tex, 0)
        gl.glUniform4f(self._u_color, *tint)
        gl.glUniform1i(self._u_mode, self.MODE_TEXTURED)
        self._upload_quad(x, y, w, h, uv)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

    def glyph(self, x: float, y: float, w: float, h: float, texture_id: int,
              color: tuple[float, float, float, float],
              uv: tuple[float, float, float, float]) -> None:
        gl.glUseProgram(self.program)
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, texture_id)
        gl.glUniform1i(self._u_tex, 0)
        gl.glUniform4f(self._u_color, *color)
        gl.glUniform1i(self._u_mode, self.MODE_TEXT)
        self._upload_quad(x, y, w, h, uv)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
