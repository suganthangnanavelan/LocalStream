"""
library/track_cache.py

MKV audio/subtitle track reads (track_extractor.extract_tracks) spin up a
headless mpv process per file. That's fine once, but the scanner was
paying that cost on *every* app launch for *every* file, unchanged or
not — on a real library (hundreds/thousands of files) that's the actual
cause of "it re-enriches everything every time it runs" and turns
startup into a multi-minute freeze.

This is the on-disk cache that fixes that: one JSON file, keyed by the
scanner's stable `Title`/`Episode` id (library.scanner.make_id — path
derived), storing the extracted languages alongside the file's mtime +
size at read time. On the next scan, a file whose mtime/size haven't
changed reuses the cached languages instead of spinning up mpv again.
Only new or modified files pay the headless-mpv cost.

Mirrors metadata.metadata_store.MetadataStore's shape deliberately, so
the two caches behave the same way (same JSON-on-disk simplicity, same
"corrupt file? start fresh, don't crash" resilience).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CachedTracks:
    mtime: float
    size: int
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)


class TrackCache:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._entries: dict[str, CachedTracks] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._entries = {k: CachedTracks(**v) for k, v in raw.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            # A corrupt cache file must never take down the scan — just
            # start from empty (same policy as MetadataStore).
            logger.warning("Track cache unreadable (%s), starting fresh: %s", self._path, exc)
            self._entries = {}

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {k: asdict(v) for k, v in self._entries.items()}
            self._path.write_text(json.dumps(payload), encoding="utf-8")
            self._dirty = False
        except OSError as exc:
            logger.warning("Could not write track cache %s: %s", self._path, exc)

    def get(self, file_id: str, mtime: float, size: int) -> Optional[CachedTracks]:
        entry = self._entries.get(file_id)
        if entry is None:
            return None
        if entry.mtime != mtime or entry.size != size:
            return None  # file changed on disk since we last read it — re-extract
        return entry

    def set(self, file_id: str, mtime: float, size: int, audio: list[str], sub: list[str]) -> None:
        self._entries[file_id] = CachedTracks(
            mtime=mtime, size=size, audio_languages=list(audio), subtitle_languages=list(sub)
        )
        self._dirty = True
