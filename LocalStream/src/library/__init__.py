"""
library/ — M3: scan Movies/TV Shows/Anime roots, season/episode/absolute-
number parsing, MKV track extraction (Section 12 M3).

Public surface:
  - models: content model dataclasses (Title, Season, Episode, Segment, Library)
  - parser: pure filename/folder parsing helpers
  - track_extractor: MKV audio/subtitle track reading via headless mpv
  - scanner: LibraryScanner, ScannerConfig — walks disk, builds a Library
"""

from library.models import ContentType, Episode, Library, Season, Segment, SegmentScope, SegmentType, Title
from library.scanner import LibraryScanner, ScannerConfig, make_id

__all__ = [
    "ContentType",
    "Episode",
    "Library",
    "Season",
    "Segment",
    "SegmentScope",
    "SegmentType",
    "Title",
    "LibraryScanner",
    "ScannerConfig",
    "make_id",
]
