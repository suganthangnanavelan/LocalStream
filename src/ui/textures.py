"""
ui/textures.py — M5

Loads poster/backdrop/still images (already downloaded to the local image
cache by M4's enricher, Section 4) into GL textures, and hands back a
solid-color placeholder texture for anything missing/broken instead of
crashing a render pass — a missing poster shouldn't take down the Home
grid. Keyed by path so the same poster (reused across shelves) only costs
one GPU upload.
"""

from __future__ import annotations

from typing import Optional

from OpenGL import GL as gl
from PIL import Image


class TextureCache:
    def __init__(self) -> None:
        self._by_path: dict[str, int] = {}
        self._placeholder: Optional[int] = None

    def _upload(self, rgba_bytes: bytes, width: int, height: int) -> int:
        tex_id = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, tex_id)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, width, height, 0,
                         gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, rgba_bytes)
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        return tex_id

    def placeholder(self) -> int:
        """A flat dark-slate swatch — used when a title has no poster/
        backdrop art at all (shouldn't normally happen once M4's offline
        fallback-art generation has run, but a render-time safety net
        costs nothing)."""
        if self._placeholder is None:
            pixel = bytes([32, 34, 40, 255])
            self._placeholder = self._upload(pixel, 1, 1)
        return self._placeholder

    def get(self, path: Optional[str]) -> int:
        """Returns a GL texture id for `path`, loading + caching it on
        first use. Falls back to the placeholder swatch on any missing
        file or decode error."""
        if not path:
            return self.placeholder()
        cached = self._by_path.get(path)
        if cached is not None:
            return cached
        try:
            with Image.open(path) as img:
                img = img.convert("RGBA")
                data = img.tobytes()
                tex_id = self._upload(data, img.width, img.height)
        except (OSError, ValueError):
            tex_id = self.placeholder()
        self._by_path[path] = tex_id
        return tex_id

    def invalidate(self, path: str) -> None:
        """Drops a cached texture (e.g. after Admin Mode's Fix Match
        replaces a title's art, M6a) so the next `get()` reloads it."""
        tex_id = self._by_path.pop(path, None)
        if tex_id is not None and tex_id != self._placeholder:
            gl.glDeleteTextures([tex_id])
