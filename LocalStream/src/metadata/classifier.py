"""
metadata/classifier.py — M4

Section 4b: since anime movies aren't in a separate folder, whether a
Movies-root title is actually "Anime" (for Home-shelf grouping) is
decided *after* the TMDB match lands, from signal already present in the
details response — no extra API calls:

    genres includes "Animation" AND original_language is Japanese, OR
    keywords include "anime"

Either condition is enough. Only relevant for Movies-root titles — Show
vs Anime-show is folder-based (Section 3) and never runs through this.
"""

from __future__ import annotations

_JAPANESE = "ja"


def is_anime_movie(genres: list[str], original_language: str | None, keywords: list[str]) -> bool:
    animation_and_japanese = (
        _has_genre(genres, "animation") and original_language == _JAPANESE
    )
    anime_keyword = any(kw.strip().lower() == "anime" for kw in keywords)
    return animation_and_japanese or anime_keyword


def _has_genre(genres: list[str], name: str) -> bool:
    return any(g.strip().lower() == name for g in genres)
