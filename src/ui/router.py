"""
ui/router.py — M5

Section 9's "App State / Router": owns which view is active (Home / Detail
/ Player — Profile Picker joins this stack at M7) and forwards
input/render calls to it. main.py's event loop talks to this, not to
individual views, so adding the Profile Picker or the segment-aware
Player UI later is a router change, not a main.py rewrite.

Player hookup here is deliberately thin: M2's MpvPlayer already does the
actual playback (embed, seek, tracks, brightness/volume) and M2's
scrub_osd/scrub_input already handle its controls — the router just needs
to know "we're in Player mode" so Home/Detail stop receiving mouse input
and Esc/Back returns to Detail instead of exiting fullscreen. The segment
engine, Skip Mode UI, and Next Episode flow are M6, not built here.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Callable, Optional

from library.models import Library, Title
from player.mpv_player import MpvPlayer
from ui.gl2d import Quad2D
from ui.shelves import WatchState
from ui.text import FontNotFoundError, TextRenderer
from ui.textures import TextureCache
from ui.views.detail import DetailView
from ui.views.home import HomeView


class ViewState(Enum):
    HOME = auto()
    DETAIL = auto()
    PLAYER = auto()


class Router:
    def __init__(self, library: Library, player: MpvPlayer,
                 watch_states: Optional[dict[str, WatchState]] = None,
                 on_request_fullscreen_playback: Optional[Callable[[], None]] = None) -> None:
        self.library = library
        self.player = player
        self.watch_states = watch_states or {}
        self._on_request_fullscreen_playback = on_request_fullscreen_playback

        self.quad = Quad2D()
        self.textures = TextureCache()
        try:
            self.text: Optional[TextRenderer] = TextRenderer(self.quad)
        except FontNotFoundError as exc:
            # Section 7c-adjacent robustness: a missing font shouldn't take
            # the whole app down before Settings (M9) can help someone fix
            # it — degrade to a labelless (but still fully clickable, since
            # hit-testing doesn't depend on text) UI and say why.
            print(f"[ui] {exc}", file=__import__("sys").stderr)
            self.text = None

        self._titles_by_id: dict[str, Title] = {t.id: t for t in library.all_titles()}

        self.state = ViewState.HOME
        self.home = HomeView(self.quad, self.textures, self.text, library,
                              self.watch_states, on_open_title=self.open_detail)
        self.detail: Optional[DetailView] = None
        self._last_player_return_state = ViewState.HOME

    # -- navigation --------------------------------------------------------

    def open_detail(self, title_id: str) -> None:
        title = self._titles_by_id.get(title_id)
        if title is None:
            return
        self.detail = DetailView(self.quad, self.textures, self.text, title,
                                  on_back=self.open_home, on_play=self.open_player)
        self.state = ViewState.DETAIL

    def open_home(self) -> None:
        self.state = ViewState.HOME
        self.detail = None

    def open_player(self, file_path: str, start_position_s: float = 0.0) -> None:
        self.player.load(file_path, start_position_s=start_position_s)
        self._last_player_return_state = ViewState.DETAIL if self.detail else ViewState.HOME
        self.state = ViewState.PLAYER
        if self._on_request_fullscreen_playback:
            self._on_request_fullscreen_playback()

    def close_player(self) -> None:
        """Back/Esc out of the Player — M6 will replace this with the real
        'Back to Detail View' behavior (Section 8: position saved). For now
        this just returns to wherever we came from."""
        self.state = self._last_player_return_state

    # -- input dispatch ------------------------------------------------------

    def on_mouse_move(self, x: float, y: float, viewport_w: float, viewport_h: float) -> None:
        if self.state == ViewState.HOME:
            self.home.on_mouse_move(x, y, viewport_w)
        elif self.state == ViewState.DETAIL and self.detail:
            self.detail.on_mouse_move(x, y)

    def on_click(self, x: float, y: float, viewport_w: float, viewport_h: float) -> None:
        if self.state == ViewState.HOME:
            self.home.on_click(x, y, viewport_w)
        elif self.state == ViewState.DETAIL and self.detail:
            self.detail.on_click(x, y)

    def on_scroll(self, dx: float, dy: float, viewport_w: float, viewport_h: float) -> None:
        if self.state == ViewState.HOME:
            self.home.on_scroll(dx, dy, viewport_w, viewport_h)

    def on_escape(self) -> bool:
        """Returns True if this consumed the Esc press (so main.py's
        fullscreen-toggle handling knows not to also act on it)."""
        if self.state == ViewState.PLAYER:
            self.close_player()
            return True
        if self.state == ViewState.DETAIL:
            self.open_home()
            return True
        return False

    # -- render --------------------------------------------------------------

    def render(self, viewport_w: float, viewport_h: float) -> None:
        if self.state == ViewState.PLAYER:
            return  # main.py draws the player/scrub-OSD layer directly, as in M2
        self.quad.begin_frame(viewport_w, viewport_h)
        if self.state == ViewState.HOME:
            self.home.render(viewport_w, viewport_h)
        elif self.state == ViewState.DETAIL and self.detail:
            self.detail.render(viewport_w, viewport_h)
