"""Unit tests for metadata/classifier.py — Section 4b's anime-movie rule."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from metadata.classifier import is_anime_movie


def test_animation_genre_and_japanese_language_classifies_as_anime():
    assert is_anime_movie(["Animation", "Family"], "ja", []) is True


def test_anime_keyword_alone_classifies_as_anime():
    assert is_anime_movie(["Drama"], "en", ["anime", "coming of age"]) is True


def test_animation_without_japanese_is_not_anime():
    # e.g. a Pixar movie: Animation genre, English original language.
    assert is_anime_movie(["Animation", "Comedy"], "en", []) is False


def test_japanese_without_animation_genre_is_not_anime():
    # A live-action Japanese film shouldn't be swept in.
    assert is_anime_movie(["Drama"], "ja", []) is False


def test_no_signal_at_all_is_not_anime():
    assert is_anime_movie(["Action"], "en", []) is False


def test_keyword_match_is_case_and_whitespace_insensitive():
    assert is_anime_movie([], "en", [" Anime ", "shounen"]) is True
