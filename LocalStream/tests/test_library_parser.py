"""Unit tests for library/parser.py — pure parsing, no disk/mpv involved."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from library.parser import (
    is_video_file,
    make_sort_name,
    parse_episode_filename,
    parse_season_folder,
    parse_year,
)


def test_parse_year_present():
    result = parse_year("Inception (2010)")
    assert result.display_name == "Inception"
    assert result.year == 2010


def test_parse_year_square_bracket_prefix():
    # "[1991] The Silence of the Lambs.mkv" — bracket year at the front,
    # the format a flat Movies-root file naming convention actually uses.
    result = parse_year("[1991] The Silence of the Lambs")
    assert result.display_name == "The Silence of the Lambs"
    assert result.year == 1991


def test_parse_year_absent():
    result = parse_year("Breaking Bad")
    assert result.display_name == "Breaking Bad"
    assert result.year is None


def test_parse_season_folder():
    assert parse_season_folder("Season 01") == 1
    assert parse_season_folder("Season 1") == 1
    assert parse_season_folder("Extras") is None


def test_parse_episode_sxxexx():
    parsed = parse_episode_filename("Breaking Bad S01E02.mkv")
    assert parsed.season_number == 1
    assert parsed.episode_number == 2
    assert parsed.absolute_number is None


def test_parse_episode_season_ep_spaced():
    # "S01 EP01 Classroom of the Elite.mkv" / "S1 EP01 Solo Leveling.mkv" —
    # real-world spacing that the tight S01E01 regex alone doesn't catch.
    parsed = parse_episode_filename("S01 EP01 Classroom of the Elite.mkv")
    assert parsed.season_number == 1
    assert parsed.episode_number == 1

    parsed = parse_episode_filename("S4 EP01 ReZero - Starting Life in Another World.mkv")
    assert parsed.season_number == 4
    assert parsed.episode_number == 1


def test_parse_episode_ep_only_no_season_prefix():
    # "Hunter X Hunter EP001.mkv" / "EP01 Trigun.mkv" — no season marker
    # in the filename at all; season comes from the caller's fallback.
    parsed = parse_episode_filename("Hunter X Hunter EP001.mkv", fallback_season=0)
    assert parsed.season_number == 0
    assert parsed.episode_number == 1

    parsed = parse_episode_filename("EP01 Trigun.mkv", fallback_season=0)
    assert parsed.episode_number == 1


def test_parse_season_folder_case_insensitive():
    # Real libraries aren't consistent about casing — "SEASON 1" (all
    # caps) must parse the same as "Season 1".
    assert parse_season_folder("SEASON 1") == 1
    assert parse_season_folder("SEASON 2") == 2
    assert parse_season_folder("Season 01") == 1


def test_parse_episode_absolute_numbering():
    parsed = parse_episode_filename("One Piece - 1085.mkv", fallback_season=1)
    assert parsed.season_number == 1
    assert parsed.episode_number is None
    assert parsed.absolute_number == 1085


def test_parse_episode_unparseable_keeps_fallback_season():
    parsed = parse_episode_filename("random_clip.mkv", fallback_season=3)
    assert parsed.season_number == 3
    assert parsed.episode_number is None
    assert parsed.absolute_number is None


def test_make_sort_name_strips_leading_article():
    assert make_sort_name("The Boys") == "Boys"
    assert make_sort_name("Spirited Away") == "Spirited Away"


def test_is_video_file():
    assert is_video_file("movie.mkv")
    assert not is_video_file("movie.mp4")
    assert not is_video_file("poster.jpg")
