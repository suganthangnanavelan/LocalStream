"""
window/window.py — M1

Owns the GLFW window, the OpenGL 3.3 core context, and raw input callback
wiring. Nothing here knows about app state, views, or routing — that lives
in the App State / Router layer (Section 9) added in later milestones.

Responsibilities (M1 scope only):
  - Create a GLFW window with an OpenGL 3.3 core profile context.
  - Boot straight into fullscreen when requested (Section 1 — "Boots
    straight into fullscreen on machine startup"), or windowed otherwise.
  - Toggle fullscreen <-> windowed at runtime (F11), remembering the
    windowed size/position to restore cleanly.
  - Expose a minimal, typed callback surface (resize, key, mouse) that
    later milestones (UI Renderer, Player Module) hook into, without this
    module needing to know who's listening.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

import glfw
from OpenGL import GL as gl

# Type aliases for the callback surface other modules hook into.
ResizeCallback = Callable[[int, int], None]
KeyCallback = Callable[[int, int, int, int], None]  # key, scancode, action, mods
CharCallback = Callable[[int], None]
MouseButtonCallback = Callable[[int, int, int], None]  # button, action, mods
CursorPosCallback = Callable[[float, float], None]
ScrollCallback = Callable[[float, float], None]


@dataclass
class WindowedGeometry:
    """Remembers windowed size/position so fullscreen toggle can restore it."""
    x: int = 100
    y: int = 100
    width: int = 1600
    height: int = 900


class Window:
    """
    Thin wrapper around a single GLFW window + GL context.

    Usage:
        win = Window(start_fullscreen=True)
        win.on_resize = my_renderer.handle_resize
        win.on_key = my_input_router.handle_key
        while win.should_close() is False:
            win.poll_events()
            ... render frame ...
            win.swap_buffers()
        win.terminate()
    """

    GL_VERSION_MAJOR = 3
    GL_VERSION_MINOR = 3

    def __init__(
        self,
        title: str = "LocalStream",
        start_fullscreen: bool = False,
        windowed_size: tuple[int, int] = (1600, 900),
        vsync: bool = True,
    ) -> None:
        self._title = title
        self._vsync = vsync
        self._geometry = WindowedGeometry(width=windowed_size[0], height=windowed_size[1])
        self._is_fullscreen = False

        # Public callback slots — later modules assign into these directly.
        # Kept as plain attributes (not an event bus) on purpose: M1 has
        # exactly one consumer per event type, and this stays easy to trace.
        self.on_resize: Optional[ResizeCallback] = None
        self.on_key: Optional[KeyCallback] = None
        self.on_char: Optional[CharCallback] = None
        self.on_mouse_button: Optional[MouseButtonCallback] = None
        self.on_cursor_pos: Optional[CursorPosCallback] = None
        self.on_scroll: Optional[ScrollCallback] = None

        self._handle = self._create_window(start_fullscreen)
        self._install_callbacks()

        glfw.make_context_current(self._handle)
        glfw.swap_interval(1 if vsync else 0)

        self._log_gl_context_info()

    # -- construction --------------------------------------------------

    def _create_window(self, start_fullscreen: bool):
        if not glfw.init():
            raise RuntimeError("GLFW failed to initialize")

        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, self.GL_VERSION_MAJOR)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, self.GL_VERSION_MINOR)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, gl.GL_TRUE)
        glfw.window_hint(glfw.DOUBLEBUFFER, gl.GL_TRUE)
        glfw.window_hint(glfw.SAMPLES, 4)  # 4x MSAA for smoother custom-drawn UI edges

        monitor = glfw.get_primary_monitor() if start_fullscreen else None
        mode = glfw.get_video_mode(glfw.get_primary_monitor())

        if start_fullscreen:
            width, height = mode.size.width, mode.size.height
        else:
            width, height = self._geometry.width, self._geometry.height

        handle = glfw.create_window(width, height, self._title, monitor, None)
        if not handle:
            glfw.terminate()
            raise RuntimeError(
                "GLFW failed to create a window. Ensure GPU drivers support "
                f"OpenGL {self.GL_VERSION_MAJOR}.{self.GL_VERSION_MINOR} core profile."
            )

        self._is_fullscreen = start_fullscreen
        if not start_fullscreen:
            glfw.set_window_pos(handle, self._geometry.x, self._geometry.y)

        return handle

    def _install_callbacks(self) -> None:
        glfw.set_window_size_callback(self._handle, self._on_glfw_resize)
        glfw.set_key_callback(self._handle, self._on_glfw_key)
        glfw.set_char_callback(self._handle, self._on_glfw_char)
        glfw.set_mouse_button_callback(self._handle, self._on_glfw_mouse_button)
        glfw.set_cursor_pos_callback(self._handle, self._on_glfw_cursor_pos)
        glfw.set_scroll_callback(self._handle, self._on_glfw_scroll)

    def _log_gl_context_info(self) -> None:
        vendor = gl.glGetString(gl.GL_VENDOR)
        renderer = gl.glGetString(gl.GL_RENDERER)
        version = gl.glGetString(gl.GL_VERSION)
        print(
            "[window] GL context ready — "
            f"vendor={vendor.decode() if vendor else '?'} "
            f"renderer={renderer.decode() if renderer else '?'} "
            f"version={version.decode() if version else '?'}",
            file=sys.stderr,
        )

    # -- GLFW callback trampolines --------------------------------------
    # Each trampoline forwards to the public callback slot if one is set.
    # Keeping these separate from the public API means swapping GLFW for
    # another windowing lib later only touches this file.

    def _on_glfw_resize(self, _handle, width: int, height: int) -> None:
        if not self._is_fullscreen:
            self._geometry.width, self._geometry.height = width, height
        if self.on_resize:
            self.on_resize(width, height)

    def _on_glfw_key(self, _handle, key: int, scancode: int, action: int, mods: int) -> None:
        if key == glfw.KEY_F11 and action == glfw.PRESS:
            self.toggle_fullscreen()
            return  # fullscreen toggle is handled here, not forwarded
        if self.on_key:
            self.on_key(key, scancode, action, mods)

    def _on_glfw_char(self, _handle, codepoint: int) -> None:
        if self.on_char:
            self.on_char(codepoint)

    def _on_glfw_mouse_button(self, _handle, button: int, action: int, mods: int) -> None:
        if self.on_mouse_button:
            self.on_mouse_button(button, action, mods)

    def _on_glfw_cursor_pos(self, _handle, x: float, y: float) -> None:
        if self.on_cursor_pos:
            self.on_cursor_pos(x, y)

    def _on_glfw_scroll(self, _handle, xoffset: float, yoffset: float) -> None:
        if self.on_scroll:
            self.on_scroll(xoffset, yoffset)

    # -- fullscreen toggle ------------------------------------------------

    def toggle_fullscreen(self) -> None:
        if self._is_fullscreen:
            self._enter_windowed()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self) -> None:
        # Remember current windowed geometry before leaving it.
        x, y = glfw.get_window_pos(self._handle)
        w, h = glfw.get_window_size(self._handle)
        self._geometry = WindowedGeometry(x=x, y=y, width=w, height=h)

        monitor = glfw.get_primary_monitor()
        mode = glfw.get_video_mode(monitor)
        glfw.set_window_monitor(
            self._handle, monitor, 0, 0, mode.size.width, mode.size.height, mode.refresh_rate
        )
        self._is_fullscreen = True

    def _enter_windowed(self) -> None:
        g = self._geometry
        glfw.set_window_monitor(self._handle, None, g.x, g.y, g.width, g.height, glfw.DONT_CARE)
        self._is_fullscreen = False

    @property
    def is_fullscreen(self) -> bool:
        return self._is_fullscreen

    @property
    def handle(self):
        """Raw GLFW window handle — exposed for modules (e.g. the player's
        mouse/cursor queries) that need to call glfw functions directly
        rather than adding a wrapper method here for every one of them."""
        return self._handle

    # -- main loop plumbing ------------------------------------------------

    def should_close(self) -> bool:
        return bool(glfw.window_should_close(self._handle))

    def request_close(self) -> None:
        glfw.set_window_should_close(self._handle, True)

    def poll_events(self) -> None:
        glfw.poll_events()

    def swap_buffers(self) -> None:
        glfw.swap_buffers(self._handle)

    def framebuffer_size(self) -> tuple[int, int]:
        return glfw.get_framebuffer_size(self._handle)

    def terminate(self) -> None:
        glfw.destroy_window(self._handle)
        glfw.terminate()
