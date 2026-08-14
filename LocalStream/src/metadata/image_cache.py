"""
metadata/image_cache.py — M4

Section 4: "TMDB-matched, cached locally forever, offline fallback art
generated from the video itself if no match/no internet."

Two jobs:
  1. Download a TMDB poster/backdrop/still to a local cache dir, keyed by
     title id so re-runs never re-download something already on disk.
  2. When there's no match (or no internet at all), grab a frame straight
     out of the video file itself via a headless mpv instance and use
     that as the poster/backdrop stand-in, so every title always has
     *some* art instead of a blank tile.

Cache layout (under a caller-supplied cache_dir, normally
%APPDATA%/LocalStream/image_cache per Section 9's app-data layout):
    <cache_dir>/<title_id>_poster.jpg
    <cache_dir>/<title_id>_backdrop.jpg
    <cache_dir>/<title_id>_s<season>e<episode>_still.jpg
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Optional

import requests

from metadata.tmdb_client import build_retrying_session

logger = logging.getLogger(__name__)

# How far into the file to grab a fallback frame — skips opening titles/
# black frames/studio logos that a 0:00 grab would usually catch, without
# needing to know the file's actual runtime up front.
FALLBACK_FRAME_OFFSET_S = 90.0

# Hard ceiling on the whole mpv frame-grab. python-mpv's wait_until_playing()
# has no timeout of its own and can block forever on a slow network drive,
# an unsupported codec, or a file mpv otherwise can't get moving on — a
# native blocking call Ctrl+C can't interrupt either. Everything here runs
# on a daemon thread specifically so a hang can be abandoned (thread left
# running in the background, process moves on and can still exit normally)
# instead of freezing the whole enrichment run on one bad file.
FALLBACK_ART_TIMEOUT_S = 20.0


class ImageCacheError(RuntimeError):
    """Download or frame-grab failed — callers treat this as "no art for
    this title/episode right now," never as fatal to the enrichment pass
    (Section 4's whole point is that a missing-art title still works)."""


def cache_path(cache_dir: str, title_id: str, kind: str) -> Path:
    suffix = {"poster": "_poster.jpg", "backdrop": "_backdrop.jpg"}[kind]
    return Path(cache_dir) / f"{title_id}{suffix}"


def episode_still_cache_path(cache_dir: str, title_id: str, season_number: int, episode_number: int) -> Path:
    return Path(cache_dir) / f"{title_id}_s{season_number:02d}e{episode_number:03d}_still.jpg"


def download_image(
    url: str,
    dest_path: Path,
    session: Optional[requests.Session] = None,
    timeout_s: float = 15.0,
) -> str:
    """Downloads `url` to `dest_path` unless it's already cached there —
    "cached locally forever" means a re-run is a no-op, not a re-fetch.
    Returns the (string) path either way."""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        return str(dest_path)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    sess = session or build_retrying_session()
    try:
        resp = sess.get(url, timeout=(5.0, timeout_s), stream=True)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — curl_cffi and requests raise different
                                # exception hierarchies; both just mean "download failed."
        raise ImageCacheError(f"Failed to download {url}: {exc}") from exc

    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    try:
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        tmp_path.replace(dest_path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise ImageCacheError(f"Failed to write {dest_path}: {exc}") from exc
    return str(dest_path)


def generate_fallback_art(
    video_path: str,
    dest_path: Path,
    offset_s: float = FALLBACK_FRAME_OFFSET_S,
    timeout_s: float = FALLBACK_ART_TIMEOUT_S,
) -> str:
    """Grabs a frame from `video_path` via a headless mpv instance and
    writes it to `dest_path` as a JPEG, for use as both poster and
    backdrop stand-in when there's no TMDB match / no internet (Section
    4). Same "throwaway headless mpv, lazy `import mpv`" pattern as
    library/track_extractor.py, so tests that never call this don't need
    libmpv installed.

    Reused as fallback art regardless of whether it's already cached —
    same skip-if-exists behavior as download_image, so a re-scan doesn't
    re-decode video it already has a frame for.

    Runs the actual mpv work on a daemon thread with a hard timeout: a
    file mpv can't get moving on (slow network share, bad codec, etc.)
    would otherwise hang this call forever with zero output and no way
    to Ctrl+C out of it. On timeout we abandon that thread (it's a
    daemon, so it won't block process exit) and raise ImageCacheError so
    the caller just treats this title as having no fallback art,
    instead of the whole run stalling on one bad file."""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        return str(dest_path)

    print(f"[art] Grabbing fallback frame from {video_path!r}...", file=sys.stderr)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    result: dict = {}

    def worker() -> None:
        try:
            _grab_frame(video_path, dest_path, offset_s)
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001 — captured and re-raised on the calling thread
            result["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)

    if thread.is_alive():
        print(
            f"[art] Timed out after {timeout_s:.0f}s grabbing a frame from {video_path!r}; "
            "skipping fallback art for this title (mpv thread abandoned in the background).",
            file=sys.stderr,
        )
        raise ImageCacheError(f"Timed out grabbing fallback frame from {video_path!r}")

    if "error" in result:
        raise ImageCacheError(f"Failed to grab fallback frame from {video_path!r}: {result['error']}") from result["error"]

    return str(dest_path)


def _grab_frame(video_path: str, dest_path: Path, offset_s: float) -> None:
    """The actual mpv work, run on a background thread by
    generate_fallback_art so it can be abandoned on timeout."""
    import mpv as mpv_module

    player = None
    try:
        player = mpv_module.MPV(vo="null", ao="null", idle=True, pause=True)
        player.play(video_path)
        player.wait_until_playing()
        try:
            player.seek(offset_s, reference="absolute")
        except Exception:  # noqa: BLE001 — short file, offset past EOF: fall back to whatever frame we're on
            pass
        _wait_for_seek(player)  # let the seek land before grabbing the frame
        player.screenshot_to_file(str(dest_path), includes="video")
        if not dest_path.exists():
            raise ImageCacheError(f"mpv did not produce a screenshot for {video_path}")
    finally:
        if player is not None:
            try:
                player.terminate()
            except Exception:  # noqa: BLE001 — best-effort cleanup, never mask the real error
                pass


def _wait_for_seek(player, timeout_s: float = 10.0) -> None:
    """Polls until mpv's `seeking` flag drops back to False (or the frame
    is otherwise decoding again) — same "poll a live-updating property"
    approach as track_extractor._wait_for_tracks, since python-mpv has no
    blocking "seek finished" call to await directly."""
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if player.seeking is False and player.core_idle is False:
                return
        except Exception:  # noqa: BLE001 — property not ready yet, keep polling
            pass
        time.sleep(0.02)