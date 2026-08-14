"""
ui/views/detail.py — M5

Backdrop-led Detail View (Section 9 router target after a Home tile
click): synopsis/genre/year/rating, a Play button for Movies, and a
season selector + episode list for Shows/Anime. Play/episode clicks call
back out to the Router to hand off to the Player module (M2's MpvPlayer,
already built) — this view owns no playback state itself.

Segment editing, Fix Match, hover-preview override, etc. (Admin Mode,
M6a) are out of scope here — this is the viewer-facing read path only.
"""

from __future__ import annotations

from typing import Callable, Optional

from library.models import ContentType, Title
from ui.gl2d import Quad2D
from ui.layout import SHELF_SIDE_MARGIN, episode_row_rect
from ui.text import TextRenderer
from ui.textures import TextureCache

BG_COLOR = (0.05, 0.05, 0.07, 1.0)
SCRIM_COLOR = (0.02, 0.02, 0.03, 0.72)
POSTER_BG = (0.14, 0.14, 0.17, 1.0)
TEXT_COLOR = (0.95, 0.95, 0.97, 1.0)
MUTED_COLOR = (0.7, 0.7, 0.75, 1.0)
BUTTON_BG = (0.85, 0.15, 0.2, 1.0)
BUTTON_HOVER_BG = (0.95, 0.2, 0.26, 1.0)
BACK_BUTTON_BG = (0.2, 0.2, 0.24, 1.0)
SEASON_TAB_BG = (0.16, 0.16, 0.2, 1.0)
SEASON_TAB_ACTIVE_BG = (0.85, 0.15, 0.2, 1.0)
EPISODE_ROW_BG = (0.12, 0.12, 0.15, 1.0)
EPISODE_ROW_HOVER_BG = (0.18, 0.18, 0.22, 1.0)

BACK_BUTTON_RECT = (24, 24, 100, 40)
PLAY_BUTTON_RECT = (SHELF_SIDE_MARGIN, 380, 160, 48)


class DetailView:
    def __init__(self, quad: Quad2D, textures: TextureCache, text: Optional[TextRenderer],
                 title: Title, on_back: Callable[[], None],
                 on_play: Callable[[str, float], None]) -> None:
        self._quad = quad
        self._textures = textures
        self._text = text
        self.title = title
        self._on_back = on_back
        self._on_play = on_play

        self._is_series = title.type in (ContentType.SHOW, ContentType.ANIME)
        self._seasons = title.sorted_seasons() if self._is_series else []
        self._season_idx = 0
        self._hover: Optional[str] = None  # "back" | "play" | f"season:{i}" | f"episode:{i}"

    # -- input -----------------------------------------------------------

    def on_mouse_move(self, x: float, y: float) -> None:
        self._hover = self._hit_test(x, y)

    def on_click(self, x: float, y: float) -> None:
        hit = self._hit_test(x, y)
        if hit is None:
            return
        if hit == "back":
            self._on_back()
        elif hit == "play":
            if not self._is_series and self.title.file_path:
                self._on_play(self.title.file_path, 0.0)
        elif hit.startswith("season:"):
            self._season_idx = int(hit.split(":")[1])
        elif hit.startswith("episode:"):
            ep_idx = int(hit.split(":")[1])
            episodes = self._current_episodes()
            if 0 <= ep_idx < len(episodes):
                ep = episodes[ep_idx]
                self._on_play(ep.file_path, 0.0)

    def _current_episodes(self) -> list:
        if not self._seasons:
            return []
        return self._seasons[self._season_idx].sorted_episodes()

    def _hit_test(self, x: float, y: float) -> Optional[str]:
        bx, by, bw, bh = BACK_BUTTON_RECT
        if bx <= x <= bx + bw and by <= y <= by + bh:
            return "back"

        if not self._is_series:
            px, py, pw, ph = PLAY_BUTTON_RECT
            if px <= x <= px + pw and py <= y <= py + ph:
                return "play"
            return None

        # Season tabs.
        tab_y, tab_h, tab_w, gap = 340, 32, 90, 8
        for i, _season in enumerate(self._seasons):
            tx = SHELF_SIDE_MARGIN + i * (tab_w + gap)
            if tx <= x <= tx + tab_w and tab_y <= y <= tab_y + tab_h:
                return f"season:{i}"

        # Episode rows.
        for i, _ep in enumerate(self._current_episodes()):
            rect = episode_row_rect(i, list_top=396)
            if rect.x <= x <= rect.x + rect.w and rect.y <= y <= rect.y + rect.h:
                return f"episode:{i}"
        return None

    # -- render ------------------------------------------------------------

    def render(self, viewport_w: float, viewport_h: float) -> None:
        self._quad.rect(0, 0, viewport_w, viewport_h, BG_COLOR)

        backdrop_h = min(480, viewport_h * 0.55)
        backdrop_tex = self._textures.get(self.title.backdrop_path)
        self._quad.textured_rect(0, 0, viewport_w, backdrop_h, backdrop_tex)
        self._quad.rect(0, 0, viewport_w, backdrop_h, SCRIM_COLOR)

        self._draw_back_button()

        poster_w, poster_h = 180, 270
        poster_x, poster_y = SHELF_SIDE_MARGIN, backdrop_h - poster_h - 24
        poster_tex = self._textures.get(self.title.poster_path)
        self._quad.rect(poster_x, poster_y, poster_w, poster_h, POSTER_BG)
        self._quad.textured_rect(poster_x, poster_y, poster_w, poster_h, poster_tex)

        info_x = poster_x + poster_w + 32
        info_w = viewport_w - info_x - SHELF_SIDE_MARGIN
        y = poster_y + 8
        if self._text:
            y = self._text_line(self.title.display_name, info_x, y, 28, TEXT_COLOR) + 12
            meta = self._meta_line()
            if meta:
                y = self._text_line(meta, info_x, y, 16, MUTED_COLOR) + 16
            if self.title.synopsis:
                lines = self._text.wrap(self.title.synopsis, 15, info_w, max_lines=4)
                for line in lines:
                    y = self._text_line(line, info_x, y, 15, MUTED_COLOR) + 4

        if not self._is_series:
            self._draw_play_button()
        else:
            self._draw_season_tabs()
            self._draw_episode_list()

    def _text_line(self, text: str, x: float, y: float, size: float, color) -> float:
        self._text.draw(text, x, y, size, color)
        return y + size * 1.3

    def _meta_line(self) -> str:
        parts = []
        if self.title.year:
            parts.append(str(self.title.year))
        if self.title.genres:
            parts.append(", ".join(self.title.genres[:3]))
        if self.title.rating:
            parts.append(f"★ {self.title.rating:.1f}")
        if self.title.runtime:
            parts.append(f"{self.title.runtime} min")
        return "  •  ".join(parts)

    def _draw_back_button(self) -> None:
        bx, by, bw, bh = BACK_BUTTON_RECT
        color = BACK_BUTTON_BG if self._hover != "back" else BUTTON_HOVER_BG
        self._quad.rect(bx, by, bw, bh, color)
        if self._text:
            self._text.draw("< Back", bx + 16, by + 10, 15, TEXT_COLOR)

    def _draw_play_button(self) -> None:
        px, py, pw, ph = PLAY_BUTTON_RECT
        color = BUTTON_HOVER_BG if self._hover == "play" else BUTTON_BG
        self._quad.rect(px, py, pw, ph, color)
        if self._text:
            self._text.draw("▶  Play", px + 24, py + 14, 17, TEXT_COLOR)

    def _draw_season_tabs(self) -> None:
        tab_y, tab_h, tab_w, gap = 340, 32, 90, 8
        for i, season in enumerate(self._seasons):
            tx = SHELF_SIDE_MARGIN + i * (tab_w + gap)
            active = i == self._season_idx
            color = SEASON_TAB_ACTIVE_BG if active else SEASON_TAB_BG
            self._quad.rect(tx, tab_y, tab_w, tab_h, color)
            if self._text:
                label = f"Season {season.season_number}"
                self._text.draw(label, tx + 10, tab_y + 8, 13, TEXT_COLOR, max_width=tab_w - 12)

    def _draw_episode_list(self) -> None:
        episodes = self._current_episodes()
        for i, ep in enumerate(episodes):
            rect = episode_row_rect(i, list_top=396)
            hovered = self._hover == f"episode:{i}"
            self._quad.rect(rect.x, rect.y, rect.w, rect.h,
                             EPISODE_ROW_HOVER_BG if hovered else EPISODE_ROW_BG)

            thumb_w, thumb_h = 140, rect.h - 16
            thumb_tex = self._textures.get(ep.thumbnail_path)
            self._quad.textured_rect(rect.x + 8, rect.y + 8, thumb_w, thumb_h, thumb_tex)

            if self._text:
                num = ep.episode_number if ep.episode_number is not None else ep.absolute_number
                label = f"{num}. {ep.title or 'Episode'}" if num is not None else (ep.title or "Episode")
                text_x = rect.x + thumb_w + 24
                self._text.draw(label, text_x, rect.y + 14, 16, TEXT_COLOR, max_width=rect.w - thumb_w - 40)
                if ep.synopsis:
                    wrapped = self._text.wrap(ep.synopsis, 13, rect.w - thumb_w - 40, max_lines=2)
                    yy = rect.y + 40
                    for line in wrapped:
                        self._text.draw(line, text_x, yy, 13, MUTED_COLOR)
                        yy += 18
