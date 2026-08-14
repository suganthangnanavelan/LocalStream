"""
ui/text.py — M5

Real text rendering, replacing player/osd.py's 7-segment-glyph stopgap
(that module explicitly deferred this: "There's no freetype here ... real
text rendering lands [in] M5+"). Rasterizes glyphs with freetype-py into
one grayscale GL texture atlas per font size, then draws strings as a run
of textured quads through ui/gl2d.py's MODE_TEXT path (texture's R channel
= coverage, tinted by the caller's color).

Font resolution: LocalStream ships no bundled font (no font asset was
provided in this repo yet — see assets/fonts/README below). At runtime
this looks for, in order:
  1. assets/fonts/*.ttf next to the project root (drop one in to ship
     a consistent look across machines),
  2. common system fonts (Segoe UI on Windows, since this is a Windows
     app per Section 1, with a couple of Linux/macOS fallbacks so dev
     machines othe than Windows can still run/test the UI).
If none are found, `FontAtlas` raises `FontNotFoundError` with a clear
message rather than silently drawing nothing — callers (Router) catch
this once at startup and disable text draw calls with a printed warning
instead of crashing the whole app, so the poster grid/layout is still
visible and testable before a font is installed.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

import freetype
import numpy as np
from OpenGL import GL as gl

from ui.gl2d import Quad2D

_CANDIDATE_FONTS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets", "fonts", "Inter-Regular.ttf"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets", "fonts", "Roboto-Regular.ttf"),
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

# Printable ASCII is enough for M5's UI copy (titles, synopses, labels);
# Section 4c language names / non-Latin titles falling outside this set
# will show as "Unknown"/tofu-free blanks rather than crash — a wider
# glyph set (CJK etc., for native-script titles) is a straightforward but
# larger follow-up, not required for M5's Home/Detail scope.
_CHARSET = "".join(chr(c) for c in range(32, 127))


class FontNotFoundError(RuntimeError):
    pass


def find_font_path() -> str:
    for candidate in _CANDIDATE_FONTS:
        if os.path.isfile(candidate):
            return candidate
    raise FontNotFoundError(
        "No usable font found. Drop a .ttf into assets/fonts/ "
        "(e.g. Inter-Regular.ttf) or install one of the system fonts "
        "text.py looks for."
    )


@dataclass
class Glyph:
    tex_x0: float
    tex_y0: float
    tex_x1: float
    tex_y1: float
    width: int
    height: int
    bearing_x: int
    bearing_y: int
    advance: float


class FontAtlas:
    """One rasterized glyph set at a fixed pixel size, packed into a
    single texture in a simple left-to-right/row-wrap layout (fine at
    ASCII-only, ~95-glyph scale — no need for a bin-packer here)."""

    def __init__(self, font_path: str, pixel_size: int) -> None:
        self.pixel_size = pixel_size
        face = freetype.Face(font_path)
        face.set_pixel_sizes(0, pixel_size)

        self.glyphs: dict[str, Glyph] = {}
        self.line_height = pixel_size * 1.3

        rendered = []
        max_row_h = 0
        pad = 2
        atlas_w = 512
        pen_x = pad
        pen_y = pad
        row_h = 0
        for ch in _CHARSET:
            face.load_char(ch, freetype.FT_LOAD_RENDER)
            bmp = face.glyph.bitmap
            w, h = bmp.width, bmp.rows
            if pen_x + w + pad > atlas_w:
                pen_x = pad
                pen_y += row_h + pad
                row_h = 0
            rendered.append((ch, pen_x, pen_y, w, h,
                              face.glyph.bitmap_left, face.glyph.bitmap_top,
                              face.glyph.advance.x / 64.0,
                              bytes(bmp.buffer) if bmp.buffer else b""))
            row_h = max(row_h, h)
            max_row_h = max(max_row_h, pen_y + row_h)
            pen_x += w + pad
        atlas_h = max(1, max_row_h + pad)
        # Round up to a friendlier texture height.
        atlas_h = 1 << max(4, math.ceil(math.log2(atlas_h)))

        buf = np.zeros((atlas_h, atlas_w), dtype=np.uint8)
        for ch, gx, gy, w, h, bl, bt, adv, data in rendered:
            if w and h and data:
                arr = np.frombuffer(data, dtype=np.uint8).reshape(h, w)
                buf[gy:gy + h, gx:gx + w] = arr
            self.glyphs[ch] = Glyph(
                tex_x0=gx / atlas_w, tex_y0=gy / atlas_h,
                tex_x1=(gx + w) / atlas_w, tex_y1=(gy + h) / atlas_h,
                width=w, height=h, bearing_x=bl, bearing_y=bt, advance=adv,
            )

        self.texture_id = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture_id)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RED, atlas_w, atlas_h, 0,
                         gl.GL_RED, gl.GL_UNSIGNED_BYTE, buf.tobytes())
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)

    def measure(self, text: str) -> float:
        return sum(self.glyphs[c].advance for c in text if c in self.glyphs)


class TextRenderer:
    """Owns one FontAtlas per requested pixel size (built lazily) and
    draws through the shared Quad2D primitive."""

    def __init__(self, quad: Quad2D, font_path: Optional[str] = None) -> None:
        self._quad = quad
        self._font_path = font_path or find_font_path()
        self._atlases: dict[int, FontAtlas] = {}

    def _atlas(self, size: int) -> FontAtlas:
        atlas = self._atlases.get(size)
        if atlas is None:
            atlas = FontAtlas(self._font_path, size)
            self._atlases[size] = atlas
        return atlas

    def measure(self, text: str, size: int) -> float:
        return self._atlas(size).measure(text)

    def draw(self, text: str, x: float, y: float, size: int,
              color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
              max_width: Optional[float] = None) -> float:
        """Draws `text` with its top-left baseline anchor at (x, y).
        Returns the x position after the last glyph drawn. If max_width is
        given, characters beyond it are truncated with '…'."""
        atlas = self._atlas(size)
        pen_x = x
        if max_width is not None and atlas.measure(text) > max_width:
            ellipsis_w = atlas.measure("...")
            truncated = ""
            for ch in text:
                w = atlas.glyphs.get(ch, atlas.glyphs.get("?")).advance if ch in atlas.glyphs else 0
                if atlas.measure(truncated) + w + ellipsis_w > max_width:
                    break
                truncated += ch
            text = truncated + "..."
        for ch in text:
            glyph = atlas.glyphs.get(ch)
            if glyph is None:
                continue
            if glyph.width and glyph.height:
                gx = pen_x + glyph.bearing_x
                gy = y + (atlas.pixel_size * 0.8) - glyph.bearing_y
                self._quad.glyph(
                    gx, gy, glyph.width, glyph.height, atlas.texture_id, color,
                    (glyph.tex_x0, glyph.tex_y0, glyph.tex_x1, glyph.tex_y1),
                )
            pen_x += glyph.advance
        return pen_x

    def wrap(self, text: str, size: int, max_width: float, max_lines: Optional[int] = None) -> list[str]:
        """Greedy word-wrap for synopsis/paragraph text (Detail View)."""
        atlas = self._atlas(size)
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if atlas.measure(candidate) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
                if max_lines is not None and len(lines) >= max_lines:
                    break
        if current and (max_lines is None or len(lines) < max_lines):
            lines.append(current)
        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip() + "…"
        return lines
