"""
library/parser.py — M3

Pure filename/folder-name parsing, no filesystem access — kept separate
from scanner.py so the regexes can be unit-tested without touching disk.

Real libraries are messier than Section 3's clean examples, so this is
deliberately permissive about *how* a season/episode number is spelled,
while staying strict about not inventing numbers that aren't there:

  Movies/Inception (2010)/Inception (2010).mkv        ("(YYYY)" folder)
  Movies/[1991] The Silence of the Lambs.mkv           ("[YYYY]" flat file)
  TV Shows/Breaking Bad/Season 01/Breaking Bad S01E01.mkv
  TV Shows/Mindhunter/SEASON 1/S1 EP01 Mindhunter.mkv  (case-insensitive,
                                                          "S1 EP01" spacing)
  Anime/One Piece/One Piece - 1085.mkv                 (absolute numbering)
  Anime/Trigun/[1998] Trigun [Original]/EP01 Trigun.mkv (EP-only, no season
                                                          prefix at all)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# "(YYYY)" or "[YYYY]" hint, e.g. "Inception (2010)" / "[1991] The Silence
# of the Lambs" — either bracket style, either position in the name.
_YEAR_RE = re.compile(r"[\(\[](\d{4})[\)\]]")

# "S01E02", "S1E2" — classic tight pattern, tried first.
_SXXEXX_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})(?!\d)")

# "S01 EP01", "S1 EP01", "S4EP01" — season prefix + a spelled-out "EP",
# optionally separated by a space/dash/underscore. Covers real-world
# naming that isn't the tight S01E01 form above.
_SXX_EP_RE = re.compile(r"[Ss](\d{1,2})[\s_-]{0,3}EP\.?\s*(\d{1,4})", re.IGNORECASE)

# "EP001", "EP01", "EP1" with no season prefix at all — anime that puts
# every episode (including specials/movies mixed into the same folder)
# under a flat EP-numbered scheme. Season comes from context (the folder
# it's in), not the filename, in this case.
_EP_ONLY_RE = re.compile(r"\bEP\.?\s*(\d{1,4})\b", re.IGNORECASE)

# "Season 01", "SEASON 1", "Season01" folder names — case-insensitive,
# since real libraries aren't consistent about casing.
_SEASON_FOLDER_RE = re.compile(r"season\s*(\d{1,2})", re.IGNORECASE)

# Absolute numbering fallback for anime with no season folder and no
# EP-style marker at all, e.g. "One Piece - 1085.mkv". Requires a
# separator before the number so it doesn't grab stray digits out of the
# show name itself, and is only tried after every EP/season pattern above
# has already failed to match.
_ABSOLUTE_NUM_RE = re.compile(r"[-_ ](\d{2,4})(?:\s*\[.*\])?\.\w+$")

VIDEO_EXTENSIONS = {".mkv"}  # Section 4a: entire library is MKV


@dataclass
class ParsedYear:
    display_name: str   # with the "(YYYY)"/"[YYYY]" hint stripped
    year: Optional[int]


def parse_year(name: str) -> ParsedYear:
    """Splits a "Title (YYYY)" or "[YYYY] Title" folder/file stem into
    (title, year) — whichever bracket style and position is present."""
    match = _YEAR_RE.search(name)
    if not match:
        return ParsedYear(display_name=name.strip(), year=None)
    year = int(match.group(1))
    display_name = (name[: match.start()] + name[match.end():]).strip()
    return ParsedYear(display_name=display_name, year=year)


@dataclass
class ParsedEpisode:
    season_number: Optional[int]
    episode_number: Optional[int]
    absolute_number: Optional[int]


def parse_season_folder(folder_name: str) -> Optional[int]:
    match = _SEASON_FOLDER_RE.search(folder_name)
    if not match:
        return None
    return int(match.group(1))


def parse_episode_filename(filename: str, fallback_season: Optional[int] = None) -> ParsedEpisode:
    """Parses an episode file's name, trying patterns from most to least
    specific. `fallback_season` is what the *folder* already told us (a
    real Season N folder's number, or the caller's synthetic bucket for
    folders that aren't season-numbered at all) — used whenever the
    filename itself doesn't carry its own season number.
    """
    match = _SXXEXX_RE.search(filename)
    if match:
        return ParsedEpisode(
            season_number=int(match.group(1)), episode_number=int(match.group(2)), absolute_number=None
        )

    match = _SXX_EP_RE.search(filename)
    if match:
        return ParsedEpisode(
            season_number=int(match.group(1)), episode_number=int(match.group(2)), absolute_number=None
        )

    match = _EP_ONLY_RE.search(filename)
    if match:
        return ParsedEpisode(
            season_number=fallback_season, episode_number=int(match.group(1)), absolute_number=None
        )

    abs_match = _ABSOLUTE_NUM_RE.search(filename)
    if abs_match:
        absolute = int(abs_match.group(1))
        return ParsedEpisode(
            season_number=fallback_season, episode_number=None, absolute_number=absolute
        )

    return ParsedEpisode(season_number=fallback_season, episode_number=None, absolute_number=None)


_LEADING_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def make_sort_name(display_name: str) -> str:
    """Strips a leading article for alphabetical sort shelves ("The Boys"
    sorts under B, not T) — display_name itself is untouched."""
    return _LEADING_ARTICLE_RE.sub("", display_name).strip()


def is_video_file(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in VIDEO_EXTENSIONS)
