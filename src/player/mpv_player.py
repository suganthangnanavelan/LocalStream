"""
player/mpv_player.py — M2

Wraps libmpv's *render API* (not the simple `wid` window-embed path) so
mpv draws directly into our existing GLFW/OpenGL context instead of owning
its own native window. This is what lets playback live inside a
custom-drawn UI later (Section 9: Player Module sits inside the same App
State / Router as every other view) rather than punching a hole in it.

Scope for M2 (Section 12):
  - embed + render
  - exact seek: absolute (scrub bar), relative ±5s/±10s (Section 8)
  - audio/subtitle track cycling + subtitle delay
  - volume + brightness (mpv video-equalizer `brightness`, -100..100)
  - no transcoding, full original quality (mpv just plays the file as-is)

Explicitly NOT in M2: segment engine / skip buttons / Skip Mode (M6),
series auto-advance (M6), hover preview secondary instance (M8) — those
are separate modules that will use this class, not extend it.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Optional

import glfw
import mpv


def _get_proc_address(_ctx, name: bytes):
    """Bridges libmpv's GL loader to GLFW's, so mpv can find GL entry points
    inside *our* context instead of creating/loading its own."""
    address = glfw.get_proc_address(name.decode("utf-8"))
    return ctypes.cast(address, ctypes.c_void_p).value


# libmpv's render API expects a real C function pointer here, not a Python
# callable — ctypes won't implicitly convert one for us the way it does for
# some other callback slots. Signature is (opaque ctx, const char *name) -> void*.
_GetProcAddressFn = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)


@dataclass
class TrackInfo:
    """One audio or subtitle track, as exposed for UI selection (Section 4c:
    label shown is the language name alone, or 'Unknown' if unflagged)."""
    id: int
    lang: Optional[str]
    title: Optional[str]
    selected: bool

    @property
    def display_label(self) -> str:
        # Section 4c: language name alone, never the track's own name/number.
        if self.lang:
            return self.lang
        return "Unknown"


class MpvPlayer:
    """
    One embedded libmpv instance. M2 owns exactly one of these — the
    hover-preview secondary instance (Section 7/M8) is a separate class
    reusing this one's patterns, not a second responsibility bolted on here.
    """

    SEEK_SMALL_S = 5    # Section 8: Seek ±5s, arrows, hold to repeat
    SEEK_BIG_S = 10      # Section 8: Big seek ±10s, J/L
    VOLUME_STEP = 5       # 0-100 scale
    BRIGHTNESS_STEP = 5    # -100..100, mpv video-equalizer
    SUBTITLE_DELAY_STEP_S = 0.05  # Section 8: ±50ms

    def __init__(self) -> None:
        self._mpv = mpv.MPV(
            vid="auto",
            vo="libmpv",             # render-API output: draws into our GL context, not its own window
            hwdec="no",                # software decode — hw-decode interop with a raw opengl render
                                         # context isn't reliably supported across driver stacks and was
                                         # stalling the whole pipeline (both video AND audio) on some GPUs.
                                         # Revisit once M2 is otherwise stable; correctness over perf for now.
            cache="yes",
            demuxer_max_bytes="150MiB",       # matches the readahead this file actually needs — too low
            demuxer_max_back_bytes="75MiB",   # a cap and the demuxer stalls waiting to make room
            osc=False,               # no mpv's own on-screen controller — custom UI draws all of it
            input_default_bindings=False,
            input_vo_keyboard=False,
            keep_open="always",      # don't auto-close at EOF; series/auto-advance (M6) decides what's next
            hr_seek="yes",           # exact seek, not keyframe-snapped — VLC-parity requirement (Section 1)
            log_handler=self._on_mpv_log,
            loglevel="warn",
        )
        self._render_ctx: Optional[mpv.MpvRenderContext] = None
        self._needs_redraw = True
        self.has_rendered_first_frame = False
        # Keep a reference so the CFUNCTYPE wrapper isn't garbage-collected
        # out from under libmpv while it still holds the raw pointer.
        self._get_proc_address_c = _GetProcAddressFn(_get_proc_address)

    # -- lifecycle --------------------------------------------------------

    def init_render_context(self) -> None:
        """Must be called once, with the GL context already current, before
        the first render() call. Split from __init__ because mpv.MPV() must
        exist before we can hand it to MpvRenderContext, and the GL context
        must already be current when we do so."""
        self._render_ctx = mpv.MpvRenderContext(
            self._mpv,
            "opengl",
            opengl_init_params={"get_proc_address": self._get_proc_address_c},
        )
        self._render_ctx.update_cb = self._on_frame_ready

    def _on_frame_ready(self) -> None:
        self._needs_redraw = True

    def _on_mpv_log(self, level: str, prefix: str, text: str) -> None:
        print(f"[mpv:{level}] {prefix}: {text.rstrip()}", file=sys.stderr)

    def shutdown(self) -> None:
        if self._render_ctx is not None:
            self._render_ctx.free()
            self._render_ctx = None
        self._mpv.terminate()

    # -- render -------------------------------------------------------------

    def render(self, width: int, height: int, fbo: int = 0) -> None:
        """Call once per frame, after the GL context is current and the
        target FBO is bound. Skips work if mpv has no new frame — cheap to
        call unconditionally from the main loop.

        Deliberately does NOT clear the framebuffer itself — see
        `has_rendered_first_frame`: once video is up, the caller should stop
        clearing before this runs, so a frame where mpv has nothing new
        (e.g. mid-seek, still decoding) holds the last picture on screen
        instead of flashing to black every such frame.
        """
        if self._render_ctx is None:
            return
        if not self._render_ctx.update():
            return
        self._render_ctx.render(flip_y=True, opengl_fbo={"w": width, "h": height, "fbo": fbo})
        self._needs_redraw = False
        self.has_rendered_first_frame = True

    @property
    def has_pending_frame(self) -> bool:
        return self._needs_redraw

    # -- loading / transport --------------------------------------------------

    def load(self, file_path: str, start_position_s: float = 0.0) -> None:
        self._mpv.play(file_path)
        if start_position_s > 0:
            # set on file-loaded so mpv has duration/seek tables ready
            @self._mpv.event_callback("file-loaded")
            def _seek_on_load(_event, _once=[False]):
                if not _once[0]:
                    _once[0] = True
                    self._mpv.seek(start_position_s, reference="absolute", precision="exact")

    def toggle_pause(self) -> None:
        self._mpv.pause = not self._mpv.pause

    @property
    def is_paused(self) -> bool:
        return bool(self._mpv.pause)

    # -- seeking (Section 8) --------------------------------------------------

    def seek_relative(self, delta_s: float) -> None:
        # "exact" precision: VLC-parity, not keyframe-snapped (Section 1).
        self._mpv.seek(delta_s, reference="relative", precision="exact")

    def seek_small(self, forward: bool) -> None:
        self.seek_relative(self.SEEK_SMALL_S if forward else -self.SEEK_SMALL_S)

    def seek_big(self, forward: bool) -> None:
        self.seek_relative(self.SEEK_BIG_S if forward else -self.SEEK_BIG_S)

    def seek_absolute(self, position_s: float) -> None:
        self._mpv.seek(position_s, reference="absolute", precision="exact")

    def seek_to_fraction(self, fraction: float) -> None:
        """Used by the scrub bar: click/drag reports 0.0-1.0 across the bar's
        width, translated to an exact absolute seek (Section 8, Section 5.5's
        red-zone guard hooks in later at the segment-engine layer, M6)."""
        duration = self.duration_s
        if duration is None or duration <= 0:
            return
        fraction = max(0.0, min(1.0, fraction))
        self.seek_absolute(fraction * duration)

    @property
    def position_s(self) -> Optional[float]:
        return self._mpv.time_pos

    @property
    def duration_s(self) -> Optional[float]:
        return self._mpv.duration

    @property
    def progress_fraction(self) -> float:
        pos, dur = self.position_s, self.duration_s
        if not pos or not dur:
            return 0.0
        return max(0.0, min(1.0, pos / dur))

    # -- volume / brightness (Section 8) --------------------------------------

    @property
    def volume(self) -> float:
        return float(self._mpv.volume)

    def set_volume(self, value: float) -> None:
        self._mpv.volume = max(0.0, min(100.0, value))

    def adjust_volume(self, delta: float) -> None:
        self.set_volume(self.volume + delta)

    @property
    def brightness(self) -> int:
        return int(self._mpv.brightness)

    def set_brightness(self, value: int) -> None:
        self._mpv.brightness = max(-100, min(100, value))

    def adjust_brightness(self, delta: int) -> None:
        self.set_brightness(self.brightness + delta)

    # -- audio / subtitle tracks (Section 4a, 4c, Section 8) -------------------

    def _tracks(self, track_type: str) -> list[TrackInfo]:
        out: list[TrackInfo] = []
        for t in self._mpv.track_list:
            if t.get("type") != track_type:
                continue
            out.append(
                TrackInfo(
                    id=t["id"],
                    lang=t.get("lang"),
                    title=t.get("title"),
                    selected=bool(t.get("selected")),
                )
            )
        return out

    @property
    def audio_tracks(self) -> list[TrackInfo]:
        return self._tracks("audio")

    @property
    def subtitle_tracks(self) -> list[TrackInfo]:
        # "Off" is represented as track id 0 / no selection in mpv terms;
        # exposed as a synthetic entry so the UI (Section 4a) can list it
        # alongside real tracks without special-casing None.
        tracks = self._tracks("sub")
        return tracks

    def cycle_audio_track(self) -> None:
        self._mpv.command("cycle", "audio")

    def cycle_subtitle_track(self) -> None:
        # mpv's built-in "sub" cycle already includes an "off" step.
        self._mpv.command("cycle", "sub")

    def select_audio_track(self, track_id: int) -> None:
        self._mpv.aid = track_id

    def select_subtitle_track(self, track_id: Optional[int]) -> None:
        self._mpv.sid = track_id if track_id is not None else False  # False == Off

    @property
    def subtitle_delay_s(self) -> float:
        return float(self._mpv.sub_delay)

    def adjust_subtitle_delay(self, delta_s: float) -> None:
        self._mpv.sub_delay = self.subtitle_delay_s + delta_s