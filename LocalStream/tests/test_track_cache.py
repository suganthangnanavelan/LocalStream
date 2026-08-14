"""Unit tests for library/track_cache.py and its wiring into the scanner:
a re-scan of unchanged files must not call extract_tracks again (that's
the actual headless-mpv-per-file cost the cache exists to avoid), while a
modified file (mtime/size changed) must be re-extracted."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from library.scanner import LibraryScanner, ScannerConfig
from library.track_cache import TrackCache
from library.track_extractor import MkvTrack, MkvTrackInfo


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


FAKE_INFO = MkvTrackInfo(
    audio=[MkvTrack(id=0, type="audio", lang="eng", title=None, default=True, forced=False)],
    subtitles=[],
)


def test_track_cache_get_set_roundtrip(tmp_path: Path):
    cache_path = tmp_path / "track_cache.json"
    cache = TrackCache(str(cache_path))
    assert cache.get("abc123", mtime=1.0, size=100) is None

    cache.set("abc123", mtime=1.0, size=100, audio=["eng"], sub=["spa"])
    cache.save()

    reloaded = TrackCache(str(cache_path))
    hit = reloaded.get("abc123", mtime=1.0, size=100)
    assert hit is not None
    assert hit.audio_languages == ["eng"]
    assert hit.subtitle_languages == ["spa"]

    # Changed mtime/size (file modified on disk) must miss, not return
    # the stale entry.
    assert reloaded.get("abc123", mtime=2.0, size=100) is None
    assert reloaded.get("abc123", mtime=1.0, size=999) is None


def test_track_cache_corrupt_file_starts_fresh(tmp_path: Path):
    cache_path = tmp_path / "track_cache.json"
    cache_path.write_text("{not valid json", encoding="utf-8")
    cache = TrackCache(str(cache_path))  # must not raise
    assert cache.get("anything", 1.0, 1) is None


def test_rescan_of_unchanged_library_skips_extraction(tmp_path: Path):
    movie = tmp_path / "Movies" / "Inception (2010)" / "Inception (2010).mkv"
    _touch(movie)
    movie.write_bytes(b"fake video bytes")  # give it a real, stable size

    track_cache_path = str(tmp_path / "track_cache.json")
    config = ScannerConfig(
        movies_root=str(tmp_path / "Movies"),
        extract_tracks=True,
        track_cache_path=track_cache_path,
    )

    with patch("library.scanner.extract_tracks", return_value=FAKE_INFO) as mocked:
        library1 = LibraryScanner(config).scan()
        assert mocked.call_count == 1
        assert library1.movies[0].audio_languages == ["eng"]

    # Second scan, same on-disk file (untouched) — must reuse the cache
    # and never call extract_tracks (i.e. never spin up headless mpv).
    with patch("library.scanner.extract_tracks", return_value=FAKE_INFO) as mocked:
        library2 = LibraryScanner(config).scan()
        assert mocked.call_count == 0
        assert library2.movies[0].audio_languages == ["eng"]


def test_rescan_after_file_modified_reextracts(tmp_path: Path):
    movie = tmp_path / "Movies" / "Inception (2010)" / "Inception (2010).mkv"
    _touch(movie)
    movie.write_bytes(b"fake video bytes")

    track_cache_path = str(tmp_path / "track_cache.json")
    config = ScannerConfig(
        movies_root=str(tmp_path / "Movies"),
        extract_tracks=True,
        track_cache_path=track_cache_path,
    )

    with patch("library.scanner.extract_tracks", return_value=FAKE_INFO):
        LibraryScanner(config).scan()

    # Simulate the file changing (re-encoded/replaced) — different size.
    movie.write_bytes(b"different, larger fake video bytes than before")

    with patch("library.scanner.extract_tracks", return_value=FAKE_INFO) as mocked:
        LibraryScanner(config).scan()
        assert mocked.call_count == 1
