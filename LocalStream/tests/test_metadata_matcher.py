"""Unit tests for metadata/matcher.py — pure scoring, no network involved."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from metadata.matcher import best_match, clean_query
from metadata.tmdb_client import TmdbCandidate


def test_exact_title_and_year_wins():
    candidates = [
        TmdbCandidate(tmdb_id=1, title="Inception", year=2010, popularity=50.0),
        TmdbCandidate(tmdb_id=2, title="Insomnia", year=2002, popularity=10.0),
    ]
    result = best_match("Inception", 2010, candidates)
    assert result is not None
    assert result.candidate.tmdb_id == 1


def test_off_by_one_year_still_matches():
    # TV premiere year vs local folder year can legitimately differ by one.
    candidates = [TmdbCandidate(tmdb_id=1, title="Breaking Bad", year=2008, popularity=90.0)]
    result = best_match("Breaking Bad", 2009, candidates)
    assert result is not None
    assert result.candidate.tmdb_id == 1


def test_no_year_still_matches_on_title_alone():
    candidates = [TmdbCandidate(tmdb_id=1, title="One Piece", year=1999, popularity=99.0)]
    result = best_match("One Piece", None, candidates)
    assert result is not None
    assert result.candidate.tmdb_id == 1


def test_completely_unrelated_title_is_rejected():
    candidates = [TmdbCandidate(tmdb_id=1, title="The Silence of the Lambs", year=1991, popularity=80.0)]
    result = best_match("Spirited Away", 2001, candidates)
    assert result is None


def test_empty_candidates_returns_none():
    assert best_match("Anything", 2020, []) is None


def test_punctuation_and_case_are_ignored():
    candidates = [TmdbCandidate(tmdb_id=1, title="Mindhunter", year=2017, popularity=40.0)]
    result = best_match("MINDHUNTER!!", 2017, candidates)
    assert result is not None
    assert result.candidate.tmdb_id == 1


def test_prefers_better_title_match_over_search_rank():
    candidates = [
        TmdbCandidate(tmdb_id=1, title="Trigun Stampede", year=2023, popularity=95.0),
        TmdbCandidate(tmdb_id=2, title="Trigun", year=1998, popularity=20.0),
    ]
    result = best_match("Trigun", 1998, candidates)
    assert result is not None
    assert result.candidate.tmdb_id == 2


def test_clean_query_strips_bracketed_local_tags():
    assert clean_query("Hunter X Hunter [Remake]") == "Hunter X Hunter"
    assert clean_query("Se7en [A]") == "Se7en"
    assert clean_query("Wind River [A]") == "Wind River"


def test_clean_query_leaves_untagged_titles_alone():
    assert clean_query("Inception") == "Inception"


def test_clean_query_never_returns_empty_string():
    # A title that's *only* a bracket tag (unlikely, but shouldn't crash
    # or produce an empty TMDB query) falls back to the original text.
    assert clean_query("[A]") == "[A]"


def test_normalize_ignores_bracket_tags_when_scoring():
    # The scorer itself is already bracket-tolerant (this was fixed
    # alongside clean_query) — "Se7en [A]" scores the title match the
    # same as "Se7en" would. The real problem clean_query solves is
    # upstream of this: TMDB's search API does literal text matching, so
    # sending "Se7en [A]" as the query can return zero candidates in the
    # first place, before scoring ever gets a chance to run.
    candidates = [TmdbCandidate(tmdb_id=1, title="Se7en", year=1995, popularity=80.0)]
    with_tag = best_match("Se7en [A]", 1995, candidates)
    without_tag = best_match("Se7en", 1995, candidates)
    assert with_tag is not None
    assert without_tag is not None
    assert with_tag.score == without_tag.score