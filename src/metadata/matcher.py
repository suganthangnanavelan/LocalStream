"""
metadata/matcher.py — M4

Picks the best TMDB search candidate for a locally-parsed title, or
decides nothing is a good enough match at all. Pure scoring logic, no
network — takes candidates already fetched by tmdb_client.py so this can
be unit-tested without hitting the real API.

Score = title-similarity (dominant factor) with a year bonus/penalty —
folder/filename parsing (Section 3, library/parser.py) is decent but not
perfect, so this has to tolerate small naming differences ("The Boys" vs
"The Boys (TV Series)" style noise, punctuation, case) while still
refusing to match something wildly different just because it's the only
result TMDB returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from metadata.tmdb_client import TmdbCandidate

# Below this score, we'd rather leave a title unmatched (Section 4:
# "offline fallback art ... if no match") than attach clearly-wrong
# metadata to it.
MIN_MATCH_SCORE = 0.55

_PUNCT_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")
# Local organizational tags in square brackets — "[A]", "[Remake]",
# "[Uncut]", etc — that library/parser.py leaves in display_name for the
# UI to show, but that are never part of the real TMDB title and actively
# hurt both the search query and the fuzzy match if left in (a short
# title like "Se7en [A]" gets dragged down hard by 4 extra characters
# that don't exist on TMDB at all).
_BRACKET_TAG_RE = re.compile(r"\s*\[[^\]]*\]")


def clean_query(display_name: str) -> str:
    """Strips bracketed local tags from a title before it's sent to TMDB
    as a search query. display_name itself is untouched — this is only
    for what gets searched, not what the UI shows."""
    cleaned = _BRACKET_TAG_RE.sub("", display_name)
    return _WHITESPACE_RE.sub(" ", cleaned).strip() or display_name


def _normalize(name: str) -> str:
    name = name.lower()
    name = _BRACKET_TAG_RE.sub(" ", name)
    name = _PUNCT_RE.sub(" ", name)
    name = _WHITESPACE_RE.sub(" ", name).strip()
    return name


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


@dataclass
class MatchResult:
    candidate: TmdbCandidate
    score: float


def best_match(
    display_name: str,
    year: Optional[int],
    candidates: list[TmdbCandidate],
) -> Optional[MatchResult]:
    """Returns the highest-scoring candidate, or None if nothing clears
    MIN_MATCH_SCORE. `candidates` is expected in TMDB's own relevance
    order (its `popularity`-influenced search ranking) — used as a
    tie-breaker only, title/year match always wins on score first."""
    best: Optional[MatchResult] = None
    for idx, candidate in enumerate(candidates):
        score = _score(display_name, year, candidate, rank=idx)
        if best is None or score > best.score:
            best = MatchResult(candidate=candidate, score=score)
    if best is None or best.score < MIN_MATCH_SCORE:
        return None
    return best


def _score(display_name: str, year: Optional[int], candidate: TmdbCandidate, rank: int) -> float:
    title_score = _title_similarity(display_name, candidate.title)

    year_score = 0.5  # neutral when we don't have a parsed year to check against
    if year is not None and candidate.year is not None:
        diff = abs(year - candidate.year)
        if diff == 0:
            year_score = 1.0
        elif diff == 1:
            # Release-year vs premiere-year off-by-one is common for TV
            # (a season can straddle a New Year) and for regional release
            # dates on movies — don't punish this as hard as a real
            # mismatch.
            year_score = 0.7
        else:
            year_score = 0.0

    # TMDB's own search ranking as a small tie-breaker only — never
    # enough to overcome a real title/year difference.
    rank_bonus = max(0.0, 0.03 - rank * 0.01)

    return (title_score * 0.75) + (year_score * 0.25) + rank_bonus