"""Unit tests for library/scanner.py against a throwaway fixture tree on
disk. extract_tracks=False throughout — track extraction needs real MKV
files + libmpv, and is track_extractor.py's own concern, not scanner
routing logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from library.scanner import LibraryScanner, ScannerConfig


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def build_fixture(root: Path) -> None:
    _touch(root / "Movies" / "Inception (2010)" / "Inception (2010).mkv")
    _touch(root / "Movies" / "Spirited Away (2001)" / "Spirited Away (2001).mkv")
    _touch(root / "TV Shows" / "Breaking Bad" / "Season 01" / "Breaking Bad S01E01.mkv")
    _touch(root / "TV Shows" / "Breaking Bad" / "Season 01" / "Breaking Bad S01E02.mkv")
    _touch(root / "TV Shows" / "Breaking Bad" / "Season 02" / "Breaking Bad S02E01.mkv")
    _touch(root / "Anime" / "Attack on Titan" / "Season 01" / "Attack on Titan S01E01.mkv")
    _touch(root / "Anime" / "One Piece" / "One Piece - 1085.mkv")
    _touch(root / "Anime" / "One Piece" / "One Piece - 1086.mkv")


def test_scan_movies_and_series(tmp_path: Path):
    build_fixture(tmp_path)
    config = ScannerConfig(
        movies_root=str(tmp_path / "Movies"),
        tv_shows_root=str(tmp_path / "TV Shows"),
        anime_root=str(tmp_path / "Anime"),
        extract_tracks=False,
    )
    library = LibraryScanner(config).scan()

    assert {t.display_name for t in library.movies} == {"Inception", "Spirited Away"}
    inception = next(t for t in library.movies if t.display_name == "Inception")
    assert inception.year == 2010
    assert inception.file_path is not None

    assert len(library.shows) == 1
    bb = library.shows[0]
    assert bb.display_name == "Breaking Bad"
    assert [s.season_number for s in bb.sorted_seasons()] == [1, 2]
    season1 = bb.sorted_seasons()[0]
    assert [e.episode_number for e in season1.sorted_episodes()] == [1, 2]

    assert {t.display_name for t in library.anime} == {"Attack on Titan", "One Piece"}
    one_piece = next(t for t in library.anime if t.display_name == "One Piece")
    # No season folder -> synthetic season 1, absolute numbering parsed
    assert len(one_piece.seasons) == 1
    abs_numbers = sorted(e.absolute_number for e in one_piece.seasons[0].episodes)
    assert abs_numbers == [1085, 1086]


def test_scan_missing_root_returns_empty(tmp_path: Path):
    config = ScannerConfig(movies_root=str(tmp_path / "does-not-exist"))
    library = LibraryScanner(config).scan()
    assert library.movies == []
    assert library.shows == []
    assert library.anime == []


def test_stable_ids_across_rescans(tmp_path: Path):
    build_fixture(tmp_path)
    config = ScannerConfig(movies_root=str(tmp_path / "Movies"), extract_tracks=False)
    first = LibraryScanner(config).scan()
    second = LibraryScanner(config).scan()
    first_ids = {t.display_name: t.id for t in first.movies}
    second_ids = {t.display_name: t.id for t in second.movies}
    assert first_ids == second_ids


def test_scan_flat_movie_files_no_subfolder(tmp_path: Path):
    # Real-world case from testing: movies sitting directly in the Movies
    # root as bare files ("[1991] The Silence of the Lambs.mkv"), not one
    # subfolder per movie. Previously these were invisible to the scanner.
    _touch(tmp_path / "Movies" / "[1991] The Silence of the Lambs.mkv")
    _touch(tmp_path / "Movies" / "[1995] Se7en [A].mkv")
    config = ScannerConfig(movies_root=str(tmp_path / "Movies"), extract_tracks=False)
    library = LibraryScanner(config).scan()

    names = {t.display_name: t.year for t in library.movies}
    assert names["The Silence of the Lambs"] == 1991
    assert names["Se7en [A]"] == 1995


def test_scan_series_with_mixed_season_and_movie_folders(tmp_path: Path):
    # Real-world case from testing: an anime folder containing both real
    # "Season N" subfolders AND non-season subfolders (movie/OVA/specials,
    # or a differently-named alternate season like "Steins;Gate 0").
    # Previously ANY non-matching subfolder caused the entire title to be
    # dropped instead of just that subfolder.
    root = tmp_path / "Anime" / "Hunter X Hunter [Remake]"
    _touch(root / "[2011] EP 001-148" / "Hunter X Hunter EP001.mkv")
    _touch(root / "[2011] EP 001-148" / "Hunter X Hunter EP002.mkv")
    _touch(root / "[2013] 1. Phantom Rouge Movie" / "Hunter X Hunter Phantom Rouge Movie.mkv")

    config = ScannerConfig(anime_root=str(tmp_path / "Anime"), extract_tracks=False)
    library = LibraryScanner(config).scan()

    assert len(library.anime) == 1
    title = library.anime[0]
    assert title.display_name == "Hunter X Hunter [Remake]"
    # Everything pools into the synthetic "Season 0" bucket since none of
    # the subfolders matched "Season N".
    assert len(title.seasons) == 1
    assert title.seasons[0].season_number == 0
    assert len(title.seasons[0].episodes) == 3


def test_scan_series_all_caps_season_folder(tmp_path: Path):
    # Real-world case from testing: "SEASON 1" (all caps) must parse the
    # same as "Season 1" — the old regex was case-sensitive and silently
    # dropped this entire show.
    root = tmp_path / "TV Series" / "Mindhunter"
    _touch(root / "SEASON 1" / "S1 EP01 Mindhunter.mkv")
    _touch(root / "SEASON 2" / "S2 EP01 Mindhunter.mkv")

    config = ScannerConfig(tv_shows_root=str(tmp_path / "TV Series"), extract_tracks=False)
    library = LibraryScanner(config).scan()

    assert len(library.shows) == 1
    assert {s.season_number for s in library.shows[0].seasons} == {1, 2}

