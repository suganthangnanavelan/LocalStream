"""
ui/renderer.py — M1

The custom UI renderer's foundation. In M1 this only owns the GL viewport
and the clear/present each frame — no shaders, no draw batching, no text
yet (those land with the texture-atlas / persistent-VBO work called out in
Section 10's performance note, once there's actual UI content to draw in
M5+). What it does own from day one:

  - Viewport sync on resize, so `Window.on_resize` has somewhere to go.
  - A single per-frame `render()` hook that later milestones extend with
    the Profile Picker / Home / Detail / Player views (Section 9's
    App State / Router will call into this).
"""

from __future__ import annotations

from OpenGL import GL as gl

# Dark neutral background — matches the "streaming site," not a toolkit
# default gray. Placeholder value; Section 10 frontend styling refines this.
CLEAR_COLOR = (0.06, 0.06, 0.08, 1.0)


class Renderer:
    def __init__(self, framebuffer_size: tuple[int, int]) -> None:
        self._width, self._height = framebuffer_size
        gl.glViewport(0, 0, self._width, self._height)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glEnable(gl.GL_MULTISAMPLE)

    def handle_resize(self, width: int, height: int) -> None:
        self._width, self._height = width, height
        gl.glViewport(0, 0, width, height)

    def begin_frame(self) -> None:
        gl.glClearColor(*CLEAR_COLOR)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)

    def render(self) -> None:
        """
        Per-frame draw hook. M1 has nothing to draw yet — the loop exists
        so M5's view stack (Profile Picker / Home / Detail / Player) has a
        single, already-wired place to plug into rather than a rewrite of
        main.py later.
        """
        pass
