"""
library/track_extractor.py — M3

Section 4c is explicit that language, default-flag, and forced-flag all
come off mpv's `track-list` property (backed by Matroska's own track
headers) — "no per-container special-casing needed" — so the scanner uses
the exact same data source the player (M2's MpvPlayer) and the future A/S
cycling picker (Section 8) use, instead of a separate probing library
that could disagree with what actually plays.

This is a *headless* mpv instance (vo=null, ao=null, idle, no GL context
needed) — separate from player.mpv_player.MpvPlayer, which owns the one
render-API instance actually used for playback. Scanning thousands of
files means this needs to be cheap: each file is opened just long enough
for mpv to demux the header and populate track-list, then torn down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import mpv


@dataclass
class MkvTrack:
    id: int
    type: str                    # "audio" | "sub"
    lang: Optional[str]
    title: Optional[str]
    default: bool
    forced: bool

    @property
    def display_label(self) -> str:
        # Section 4c: language name alone, or "Unknown" — same rule the
        # player's TrackInfo.display_label uses, kept in sync deliberately.
        if self.lang:
            return self.lang
        return "Unknown"


@dataclass
class MkvTrackInfo:
    audio: list[MkvTrack]
    subtitles: list[MkvTrack]

    def audio_languages(self) -> list[str]:
        return _unique_labels(self.audio)

    def subtitle_languages(self) -> list[str]:
        return _unique_labels(self.subtitles)


def _unique_labels(tracks: list[MkvTrack]) -> list[str]:
    seen: list[str] = []
    for t in tracks:
        label = t.display_label
        if label not in seen:
            seen.append(label)
    return seen


class TrackExtractionError(RuntimeError):
    """Raised when a file can't be opened/demuxed at all (corrupt file,
    unreadable path, etc.) — the scanner catches this per-file so one bad
    file doesn't abort the whole library scan (Section 12 M3 note: a
    library scan has to be resilient to one-off bad files)."""


def extract_tracks(file_path: str, timeout_s: float = 10.0) -> MkvTrackInfo:
    """Opens `file_path` in a throwaway headless mpv instance just long
    enough to read its track-list, then shuts it down.

    `mpv` is imported lazily (not at module load) so that importing
    library.scanner in a context that never actually extracts tracks
    (e.g. ScannerConfig(extract_tracks=False), or unit tests for the pure
    parsing logic) doesn't require libmpv to be installed at all.
    """
    import mpv as mpv_module

    player = None
    try:
        player = mpv_module.MPV(
            vid="no",   # no video decode/output needed to read tracks
            ao="null",  # no audio output either — this never makes sound
            idle=True,
            pause=True,
        )
        player.play(file_path)
        _wait_for_tracks(player, timeout_s)

        audio: list[MkvTrack] = []
        subs: list[MkvTrack] = []
        for t in player.track_list:
            track_type = t.get("type")
            if track_type not in ("audio", "sub"):
                continue
            track = MkvTrack(
                id=t["id"],
                type=track_type,
                lang=t.get("lang"),
                title=t.get("title"),
                default=bool(t.get("default")),
                forced=bool(t.get("forced")),
            )
            (audio if track_type == "audio" else subs).append(track)
        return MkvTrackInfo(audio=audio, subtitles=subs)
    except Exception as exc:  # noqa: BLE001 — any mpv/ctypes failure -> our own error type
        raise TrackExtractionError(f"Could not read tracks from {file_path!r}: {exc}") from exc
    finally:
        if player is not None:
            try:
                player.terminate()
            except Exception:  # noqa: BLE001 — best-effort cleanup, never mask the real error
                pass


def _wait_for_tracks(player: "mpv.MPV", timeout_s: float) -> None:
    """mpv populates track-list asynchronously right after `play()` starts
    demuxing — core-idle-active or the first non-empty track-list both
    signal "the header's been read", whichever fires first."""
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if player.track_list:
                return
            if player.core_idle is False:
                # Actively decoding already implies tracks are known.
                return
        except Exception:  # noqa: BLE001 — property not ready yet, keep polling
            pass
        time.sleep(0.02)
    # Timed out — leave it to the caller: track_list may still be empty,
    # which just means this file contributes no language data.
