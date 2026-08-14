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

BG_COLOR = (0.04, 0.04, 0.055, 1.0)
SCRIM_TOP = (0.0, 0.0, 0.0, 0.05)
SCRIM_MID = (0.02, 0.02, 0.03, 0.55)
SCRIM_BOTTOM = (0.04, 0.04, 0.055, 1.0)
TEXT_COLOR = (0.97, 0.97, 0.98, 1.0)
MUTED_COLOR = (0.78, 0.78, 0.82, 1.0)
GOLD = (0.95, 0.72, 0.20, 1.0)  # star rating + Play button, matches the reference screenshots' accent
BUTTON_BG = GOLD
BUTTON_TEXT_COLOR = (0.12, 0.09, 0.02, 1.0)
BUTTON_HOVER_BG = (1.0, 0.79, 0.28, 1.0)
CLOSE_BUTTON_BG = (0.0, 0.0, 0.0, 0.45)
CLOSE_BUTTON_HOVER_BG = (0.0, 0.0, 0.0, 0.7)
SEASON_TAB_BG = (0.14, 0.14, 0.18, 1.0)
SEASON_TAB_ACTIVE_BG = GOLD
EPISODE_ROW_BG = (0.10, 0.10, 0.13, 1.0)
EPISODE_ROW_HOVER_BG = (0.16, 0.16, 0.20, 1.0)
SHADOW_COLOR = (0.0, 0.0, 0.0, 0.5)

# Top-right circular close ("×"), Netflix-modal style, replacing the old
# top-left "< Back" box — matches the reference screenshot instead of
# looking like a generic app toolbar button.
CLOSE_BUTTON_SIZE = 40
CLOSE_BUTTON_MARGIN = 24


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

    def on_mouse_move(self, x: float, y: float, viewport_w: float = 1280, viewport_h: float = 720) -> None:
        self._hover = self._hit_test(x, y, viewport_w, viewport_h)

    def on_click(self, x: float, y: float, viewport_w: float = 1280, viewport_h: float = 720) -> None:
        hit = self._hit_test(x, y, viewport_w, viewport_h)
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

    def _close_button_rect(self, viewport_w: float) -> tuple[float, float, float, float]:
        s = CLOSE_BUTTON_SIZE
        return (viewport_w - s - CLOSE_BUTTON_MARGIN, CLOSE_BUTTON_MARGIN, s, s)

    def _play_button_rect(self, viewport_w: float, viewport_h: float) -> tuple[float, float, float, float]:
        backdrop_h = min(viewport_h * 0.62, viewport_h - 220)
        return (SHELF_SIDE_MARGIN, backdrop_h - 76, 150, 48)

    def _hit_test(self, x: float, y: float, viewport_w: float = 1280,
                   viewport_h: float = 720) -> Optional[str]:
        cx, cy, cw, ch = self._close_button_rect(viewport_w)
        if cx <= x <= cx + cw and cy <= y <= cy + ch:
            return "back"

        if not self._is_series:
            px, py, pw, ph = self._play_button_rect(viewport_w, viewport_h)
            if px <= x <= px + pw and py <= y <= py + ph:
                return "play"
            return None

        # Season tabs.
        tab_y, tab_h, tab_w, gap = self._season_tabs_y(viewport_w, viewport_h), 32, 90, 8
        for i, _season in enumerate(self._seasons):
            tx = SHELF_SIDE_MARGIN + i * (tab_w + gap)
            if tx <= x <= tx + tab_w and tab_y <= y <= tab_y + tab_h:
                return f"season:{i}"

        # Episode rows.
        list_top = self._season_tabs_y(viewport_w, viewport_h) + 56
        for i, _ep in enumerate(self._current_episodes()):
            rect = episode_row_rect(i, list_top=list_top)
            if rect.x <= x <= rect.x + rect.w and rect.y <= y <= rect.y + rect.h:
                return f"episode:{i}"
        return None

    def _season_tabs_y(self, viewport_w: float, viewport_h: float) -> float:
        backdrop_h = min(viewport_h * 0.62, viewport_h - 220)
        return backdrop_h + 20

    # -- render ------------------------------------------------------------

    def render(self, viewport_w: float, viewport_h: float) -> None:
        self._quad.rect(0, 0, viewport_w, viewport_h, BG_COLOR)

        backdrop_h = min(viewport_h * 0.62, viewport_h - 220)
        backdrop_tex = self._textures.get(self.title.backdrop_path)
        self._quad.textured_rect_cover(0, 0, viewport_w, backdrop_h, backdrop_tex,
                                        self._textures.get_aspect(self.title.backdrop_path))
        # Three-stop gradient: near-clear at the very top (so the image
        # reads before anything else), a mid darkening through the
        # middle so text is legible over any art, solid at the bottom
        # where it meets the page background — the "hero dissolves into
        # the page" look from the reference screenshots.
        self._quad.vertical_gradient(0, 0, viewport_w, backdrop_h * 0.5, SCRIM_TOP, SCRIM_MID, steps=10)
        self._quad.vertical_gradient(0, backdrop_h * 0.5, viewport_w, backdrop_h * 0.5,
                                      SCRIM_MID, SCRIM_BOTTOM, steps=10)

        self._draw_close_button(viewport_w)

        # Title/meta/synopsis/Play sit directly over the backdrop's dark
        # lower half — Netflix's actual layout (no separate poster
        # thumbnail competing for attention; the backdrop *is* the hero).
        info_x = SHELF_SIDE_MARGIN
        info_w = min(viewport_w - 2 * SHELF_SIDE_MARGIN, 820.0)
        y = backdrop_h - 210
        if self._text:
            y = self._text_line(self.title.display_name, info_x, y, 34, TEXT_COLOR, bold=True) + 14
            meta = self._meta_line()
            if meta:
                y = self._draw_meta_line(info_x, y) + 14
            if self.title.synopsis:
                lines = self._text.wrap(self.title.synopsis, 15, info_w, max_lines=3)
                for line in lines:
                    y = self._text_line(line, info_x, y, 15, MUTED_COLOR) + 5

        if not self._is_series:
            self._draw_play_button(viewport_w, viewport_h)
        else:
            self._draw_season_tabs(viewport_w, viewport_h)
            self._draw_episode_list(viewport_w, viewport_h)

    def _text_line(self, text: str, x: float, y: float, size: float, color, bold: bool = False) -> float:
        # TextRenderer has no bold weight of its own (single font asset,
        # Section 6) — a 1px double-draw offset fakes a heavier stroke
        # for the title without needing a second font file.
        if bold:
            self._text.draw(text, x + 0.6, y, size, color)
        self._text.draw(text, x, y, size, color)
        return y + size * 1.25

    def _meta_line(self) -> str:
        parts = []
        if self.title.year:
            parts.append(str(self.title.year))
        if self.title.genres:
            parts.append(", ".join(self.title.genres[:3]))
        if self.title.runtime:
            parts.append(f"{self.title.runtime} min")
        return "  •  ".join(parts)

    def _draw_meta_line(self, x: float, y: float) -> float:
        cursor = x
        size = 16
        if self.title.year:
            self._text.draw(str(self.title.year), cursor, y, size, TEXT_COLOR)
            cursor += self._text.measure(str(self.title.year), size) + 14
        if self.title.rating:
            star = f"★ {self.title.rating:.1f}"
            self._text.draw(star, cursor, y, size, GOLD)
            cursor += self._text.measure(star, size) + 14
        if self.title.genres:
            genre_str = ", ".join(self.title.genres[:3])
            self._text.draw(genre_str, cursor, y, size, MUTED_COLOR)
        return y + size * 1.3

    def _draw_close_button(self, viewport_w: float) -> None:
        cx, cy, cw, ch = self._close_button_rect(viewport_w)
        color = CLOSE_BUTTON_HOVER_BG if self._hover == "back" else CLOSE_BUTTON_BG
        self._quad.rect(cx, cy, cw, ch, color)
        if self._text:
            label = "×"
            lw = self._text.measure(label, 22)
            self._text.draw(label, cx + (cw - lw) / 2.0, cy + 6, 22, TEXT_COLOR)

    def _draw_play_button(self, viewport_w: float, viewport_h: float) -> None:
        px, py, pw, ph = self._play_button_rect(viewport_w, viewport_h)
        color = BUTTON_HOVER_BG if self._hover == "play" else BUTTON_BG
        self._quad.rect(px, py, pw, ph, color)
        if self._text:
            self._text.draw("▶  Play", px + 26, py + 14, 17, BUTTON_TEXT_COLOR)

    def _draw_season_tabs(self, viewport_w: float, viewport_h: float) -> None:
        tab_y, tab_h, tab_w, gap = self._season_tabs_y(viewport_w, viewport_h), 32, 90, 8
        for i, season in enumerate(self._seasons):
            tx = SHELF_SIDE_MARGIN + i * (tab_w + gap)
            active = i == self._season_idx
            color = SEASON_TAB_ACTIVE_BG if active else SEASON_TAB_BG
            self._quad.rect(tx, tab_y, tab_w, tab_h, color)
            if self._text:
                label = f"Season {season.season_number}"
                text_color = BUTTON_TEXT_COLOR if active else TEXT_COLOR
                self._text.draw(label, tx + 10, tab_y + 8, 13, text_color, max_width=tab_w - 12)

    def _draw_episode_list(self, viewport_w: float, viewport_h: float) -> None:
        episodes = self._current_episodes()
        list_top = self._season_tabs_y(viewport_w, viewport_h) + 56
        for i, ep in enumerate(episodes):
            rect = episode_row_rect(i, list_top=list_top)
            hovered = self._hover == f"episode:{i}"
            self._quad.rect(rect.x, rect.y, rect.w, rect.h,
                             EPISODE_ROW_HOVER_BG if hovered else EPISODE_ROW_BG)
            if hovered:
                # Thin accent bar on the leading edge instead of a full
                # border/box glow — a quieter "this row is interactive"
                # cue that matches the season-tab accent color.
                self._quad.rect(rect.x, rect.y, 3, rect.h, SEASON_TAB_ACTIVE_BG)

            thumb_w, thumb_h = 140, rect.h - 16
            thumb_tex = self._textures.get(ep.thumbnail_path)
            self._quad.textured_rect_cover(rect.x + 8, rect.y + 8, thumb_w, thumb_h, thumb_tex,
                                            self._textures.get_aspect(ep.thumbnail_path))

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
