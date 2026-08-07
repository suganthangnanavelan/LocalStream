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
from OpenGL import GL as gl


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
            hwdec="auto-safe",       # hardware decode with copy-back to system memory before upload.
                                       # The *zero-copy* hwdec interop (e.g. raw d3d11/opengl texture
                                       # sharing) isn't reliably supported across driver stacks, but the
                                       # "-copy" variants auto-safe resolves to (d3d11va-copy on Windows,
                                       # videotoolbox on macOS, vaapi-copy on Linux) decode on the GPU and
                                       # hand mpv a normal system-memory frame, so they work through the
                                       # same GL texture upload path as software decode — just fast enough
                                       # that the decoder doesn't fall behind on 4K/HEVC sources. Software
                                       # decode of a 4K file was too slow to keep up in real time, which
                                       # backed up the demuxer queue and starved audio for the first
                                       # ~30s (and periodically after) — that was the M2 playback bug.
            cache="yes",
            demuxer_max_bytes="400MiB",        # headroom for 4K/HEVC readahead; 150MiB was tight enough
            demuxer_max_back_bytes="75MiB",    # that any decode hiccup tripped the "too many packets"
                                                 # cap and forced a queue refresh/stall.
            demuxer_readahead_secs=20,          # read further ahead so brief decode hiccups don't starve
                                                 # audio or cause visible stutter.
            audio_buffer=0.5,        # mpv's default (~80ms) is thin enough that a seek or brief decode
                                       # stall empties it before the pipeline catches back up, which is
                                       # what the "Audio device underrun" warning was flagging.
            osc=False,               # no mpv's own on-screen controller — custom UI draws all of it
            input_default_bindings=False,
            input_vo_keyboard=False,
            keep_open="always",      # don't auto-close at EOF; series/auto-advance (M6) decides what's next
            hr_seek="yes",           # exact seek, not keyframe-snapped — VLC-parity requirement (Section 1)
            video_sync="display-resample",  # default video-sync=audio means: on resume, if the audio
                                       # device took a moment to reopen (Windows commonly suspends/closes
                                       # an idle WASAPI stream after a few seconds paused), mpv sees video
                                       # as "behind" the audio clock and catches up by dropping straight to
                                       # a later frame instead of playing the next one in sequence — that's
                                       # the "jumps to a new frame and continues from there" feel. Locking
                                       # video to the display's refresh instead (and letting audio resample
                                       # to match) removes that catch-up/frame-skip mechanism entirely, so
                                       # resuming just continues frame-by-frame regardless of how long the
                                       # audio device took to come back.
            vd_lavc_dr="no",        # disable direct rendering: with hwdec-copy (d3d11va-copy etc.), the
                                       # decoder's DR buffer pool gets reclaimed while the video sits idle
                                       # during a pause. Resuming after sitting paused for a while then has
                                       # to wait on the pool reallocating/resyncing before the next frame can
                                       # decode — that's the frame-stop-on-resume. Forcing a plain copy out of
                                       # the decoder instead of a pooled DR buffer costs a bit of memory
                                       # bandwidth (already paying that cost via hwdec-copy anyway) but keeps
                                       # every frame independent of pool state, so resuming after any pause
                                       # length decodes the next frame immediately instead of stalling on it.
            log_handler=self._on_mpv_log,
            loglevel="warn",
        )
        self._render_ctx: Optional[mpv.MpvRenderContext] = None
        self._needs_redraw = True
        self.has_rendered_first_frame = False
        # Keep a reference so the CFUNCTYPE wrapper isn't garbage-collected
        # out from under libmpv while it still holds the raw pointer.
        self._get_proc_address_c = _GetProcAddressFn(_get_proc_address)

        # -- offscreen target mpv actually paints into ------------------
        #
        # GLFW double-buffers (front/back). Early M2 called
        # self._render_ctx.render() straight into the window's default
        # framebuffer, and only when mpv.update() reported a *new* frame —
        # skipped entirely on frames where it didn't (which is most frames
        # while paused, and the gaps between frames during a seek). But
        # "skip drawing" doesn't mean "nothing visible changes": the buffer
        # that frame swaps in was last painted *two swaps ago*, i.e. an
        # older picture than the one still showing. That's exactly the
        # sub-second pause flicker and the "hitches backward before landing
        # on the seek target" feeling — both are the same double-buffer
        # staleness bug, not decode/seek problems.
        #
        # Fix: mpv always paints into this single persistent FBO/texture
        # (only *updated* when mpv has something new), and every frame we
        # blit that texture into whichever buffer GLFW handed us. Both
        # buffers then always carry the same, current picture, so swapping
        # between them is never a step backward in time.
        self._fbo: Optional[int] = None
        self._fbo_tex: Optional[int] = None
        self._fbo_w = 0
        self._fbo_h = 0

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
        self._free_fbo()
        self._mpv.terminate()

    # -- render -------------------------------------------------------------

    def _ensure_fbo(self, width: int, height: int) -> None:
        """(Re)creates the offscreen target mpv paints into, if it doesn't
        exist yet or the window size changed."""
        if self._fbo is not None and self._fbo_w == width and self._fbo_h == height:
            return
        self._free_fbo()

        tex = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, tex)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, width, height, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, None)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)

        fbo = gl.glGenFramebuffers(1)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
        gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, tex, 0)
        status = gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
        if status != gl.GL_FRAMEBUFFER_COMPLETE:
            print(f"[mpv_player] offscreen FBO incomplete: status={status}", file=sys.stderr)

        self._fbo = int(fbo)
        self._fbo_tex = int(tex)
        self._fbo_w, self._fbo_h = width, height
        # Size changed (e.g. window resize) — the old contents are gone,
        # so force one real mpv render before the next blit.
        self._needs_redraw = True

    def _free_fbo(self) -> None:
        if self._fbo is not None:
            gl.glDeleteFramebuffers(1, [self._fbo])
            self._fbo = None
        if self._fbo_tex is not None:
            gl.glDeleteTextures(1, [self._fbo_tex])
            self._fbo_tex = None

    def render(self, width: int, height: int, fbo: int = 0) -> None:
        """Call once per frame, after the GL context is current and the
        target FBO is bound.

        mpv only ever paints into our persistent offscreen FBO, and only
        when it actually has a new frame (`update()` is True). Every frame,
        regardless of that, we blit the offscreen texture into `fbo` (the
        window's current back buffer) — see the note in __init__ for why
        this unconditional blit is what actually fixes the pause flicker
        and the seek stutter (both were double-buffer staleness, not a
        decode/seek problem).
        """
        if self._render_ctx is None or width <= 0 or height <= 0:
            return

        self._ensure_fbo(width, height)

        if self._render_ctx.update():
            self._render_ctx.render(
                flip_y=True,
                opengl_fbo={"w": self._fbo_w, "h": self._fbo_h, "fbo": self._fbo},
            )
            self._needs_redraw = False
            self.has_rendered_first_frame = True

        if not self.has_rendered_first_frame:
            return

        # Blit offscreen texture -> target framebuffer, unconditionally.
        gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, self._fbo)
        gl.glBindFramebuffer(gl.GL_DRAW_FRAMEBUFFER, fbo)
        gl.glBlitFramebuffer(
            0, 0, self._fbo_w, self._fbo_h,
            0, 0, width, height,
            gl.GL_COLOR_BUFFER_BIT, gl.GL_LINEAR,
        )
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)

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
        # Keyframe ("keyframes") precision, not exact, for arrow/J-L taps —
        # see seek_small/seek_big below for why. Exact remains the default
        # here since other relative-seek callers may still want frame
        # accuracy; the fast path is opt-in via seek_relative_fast.
        self._mpv.seek(delta_s, reference="relative", precision="exact")

    def _seek_relative_fast(self, delta_s: float) -> None:
        # Keyframe-snapped seek: mpv jumps straight to the nearest keyframe
        # instead of decoding every intermediate frame from there to the
        # target. That decode-catch-up is exactly what caused the "stuck"
        # feeling on ±5s/±10s taps — VLC does the same fast/keyframe seek
        # for quick jumps and only goes frame-exact when you're precisely
        # scrubbing (that's what the scrub bar's seek_absolute is for).
        self._mpv.seek(delta_s, reference="relative", precision="keyframes")

    def seek_small(self, forward: bool) -> None:
        self._seek_relative_fast(self.SEEK_SMALL_S if forward else -self.SEEK_SMALL_S)

    def seek_big(self, forward: bool) -> None:
        self._seek_relative_fast(self.SEEK_BIG_S if forward else -self.SEEK_BIG_S)

    def seek_absolute(self, position_s: float) -> None:
        self._mpv.seek(position_s, reference="absolute", precision="exact")

    def _seek_absolute_fast(self, position_s: float) -> None:
        # Keyframe-snapped absolute seek — same rationale as
        # _seek_relative_fast. Used for scrub-bar *preview* while actively
        # dragging: exact-seeking on every mouse-move event (GLFW fires one
        # per pixel of movement) was flooding mpv with frame-accurate seeks
        # and causing the jerky/stuttery drag feel. Fast seeks keep the drag
        # responsive; seek_to_fraction(..., commit=True) on mouse-up still
        # lands on the exact frame.
        self._mpv.seek(position_s, reference="absolute", precision="keyframes")

    def seek_to_fraction(self, fraction: float, commit: bool = True) -> None:
        """Used by the scrub bar: click/drag reports 0.0-1.0 across the bar's
        width, translated to an absolute seek (Section 8, Section 5.5's
        red-zone guard hooks in later at the segment-engine layer, M6).

        commit=True (click, or mouse-up after a drag) does a frame-exact
        seek. commit=False (every intermediate mouse-move while dragging)
        does a fast keyframe seek so the drag itself stays smooth — see
        _seek_absolute_fast."""
        duration = self.duration_s
        if duration is None or duration <= 0:
            return
        fraction = max(0.0, min(1.0, fraction))
        target = fraction * duration
        if commit:
            self.seek_absolute(target)
        else:
            self._seek_absolute_fast(target)

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