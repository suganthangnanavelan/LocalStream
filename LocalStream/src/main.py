"""
main.py — M2 entry point

M1 gave us window + GL context + fullscreen toggle. M2 adds the playback
core on top: libmpv embed (render API), exact seek, audio/subtitle track
switching, volume/brightness, and a minimal scrub bar so seeking is
actually testable. Still no profiles, no segment engine — those are
later milestones (Section 12).

Run (after `pip install -e .` from the project root):
    python src/main.py --file "C:\\path\\to\\video.mkv"
    python src/main.py --file "...\\video.mkv" --fullscreen

`--file` is a temporary M2 testing hook — real playback is triggered from
the Detail View / library (M5+), not a CLI flag. It exists so playback can
be verified standalone before any UI is built on top of it.

Testing-hook config file (--scan / --enrich):
    Repeating --movies/--tv-shows/--anime/--tmdb-key on every run is
    tedious, so both hooks will also read a JSON config file if one is
    present, and use it to fill in whatever flags weren't passed on the
    command line explicitly (CLI flags always win when both are given).
    Defaults to ./localstream.config.json next to pyproject.toml; override
    with --config. See localstream.config.example.json for the shape.
    This is a dev-testing convenience only, NOT the real first-run
    config/Settings system (folders + TMDB key entry via UI) — that's
    M9, and will live in config/ once it's built.

Controls (Section 8 subset relevant to M2 — segment skip/Next Episode/Back
land with M6):
    Space          Play/Pause
    Left/Right     Seek -5s / +5s (hold to repeat)
    J / L          Seek -10s / +10s
    Up/Down        Volume +/-
    Shift+Up/Down  Brightness +/-
    A              Cycle audio track
    S              Cycle subtitle track
    [ / ]          Subtitle delay -50ms / +50ms
    Click/drag scrub bar   Exact seek to position
    F              Toggle fullscreen (M1)
    Esc            Exit fullscreen (does not close the app)
    Alt+F4         Quit (native OS handling)
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Callable, Optional

import glfw

from library.scanner import LibraryScanner, ScannerConfig
from metadata.enricher import EnricherConfig, MetadataEnricher
from player.mpv_player import MpvPlayer
from player.osd import ScrubBarOSD
from player.scrub_input import ScrubBarInput
from ui.gl2d import Quad2D
from ui.text import FontNotFoundError, TextRenderer
from ui.renderer import Renderer
from window.window import Window

# Repo-root-relative default — main.py lives in src/, config sits next to
# pyproject.toml one level up.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "localstream.config.json"

# Which CLI dest names a config file's keys map onto, and which of those
# are boolean "skip" flags (JSON `true`/`false`) vs plain values.
_CONFIG_KEYS = {
    "movies": "movies",
    "tv_shows": "tv_shows",
    "anime": "anime",
    "tmdb_key": "tmdb_key",
    "image_cache": "image_cache",
    "metadata_cache": "metadata_cache",
    "no_tracks": "no_tracks",
    "episode_enrich": "episode_enrich",
}


def load_dev_config(path: Path) -> dict:
    """Loads the optional testing-hook config file. Missing file is not
    an error — CLI flags/argparse defaults just apply as-is, same as
    before this existed. A present-but-malformed file *is* reported, so a
    typo in the JSON doesn't silently fall back to "nothing configured"
    and leave someone wondering why their paths didn't take."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[config] Could not read {path}: {exc}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"[config] {path} must contain a JSON object, ignoring.", file=sys.stderr)
        return {}
    return data


def apply_dev_config(args: argparse.Namespace, config: dict) -> None:
    """Fills in any arg that's still at its argparse default (i.e. wasn't
    explicitly passed on the command line) from the config file. A flag
    typed on the command line always overrides the config file, never the
    other way around."""
    for arg_name, config_key in _CONFIG_KEYS.items():
        if config_key not in config:
            continue
        current = getattr(args, arg_name)
        is_unset = current is None or current is False
        if is_unset:
            setattr(args, arg_name, config[config_key])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="LocalStream")
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="Boot straight into fullscreen (used by autostart, Section 9/12).",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="M2 testing hook: a video file to load and play on launch.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help=(
            "M3 testing hook: scan --movies/--tv-shows/--anime, print a "
            "summary of what was found, and exit without opening a window."
        ),
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=(
            "Path to the testing-hook config JSON (--scan/--enrich defaults). "
            f"Defaults to {DEFAULT_CONFIG_PATH.name} next to pyproject.toml."
        ),
    )
    parser.add_argument("--movies", default=None, help="Movies root (Section 3).")
    parser.add_argument("--tv-shows", default=None, help="TV Shows root (Section 3).")
    parser.add_argument("--anime", default=None, help="Anime root (Section 3).")
    parser.add_argument(
        "--no-tracks",
        action="store_true",
        default=None,
        help="Skip MKV track extraction during --scan (faster, no language data).",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help=(
            "M4 testing hook: scan (as --scan does) then run TMDB matching + "
            "classification + art caching on top, print a summary, and exit "
            "without opening a window."
        ),
    )
    parser.add_argument(
        "--tmdb-key",
        default=None,
        help="TMDB API key for --enrich. Omit to enrich fully offline (fallback art only).",
    )
    parser.add_argument(
        "--image-cache",
        default=None,
        help="Directory for cached posters/backdrops/stills (--enrich). Default: ./localstream_cache/images",
    )
    parser.add_argument(
        "--metadata-cache",
        default=None,
        help="Path to the persistent TMDB match cache (--enrich).",
    )
    parser.add_argument(
        "--episode-enrich",
        action="store_true",
        default=None,
        help=(
            "Also fetch per-episode TMDB title/synopsis/still during --enrich, with fallback "
            "art (mpv frame-grab) per episode where TMDB has none. Off by default: this is slow "
            "across a library with many episodes and rarely needs re-running once done, so treat "
            "it as a deliberate one-time pass rather than part of every --enrich run."
        ),
    )
    return parser.parse_args(argv)


def run_scan(args: argparse.Namespace) -> int:
    """M3 testing hook — scans the given roots and prints a summary, no
    window/GL/mpv-render-API involved. Real library scanning is triggered
    from app startup / a Settings "Rescan" action in later milestones, not
    a CLI flag — this exists so the scanner can be verified standalone
    before the Home/Detail UI (M5) exists to browse the results in."""
    apply_dev_config(args, load_dev_config(Path(args.config)))
    config = ScannerConfig(
        movies_root=args.movies,
        tv_shows_root=args.tv_shows,
        anime_root=args.anime,
        extract_tracks=not args.no_tracks,
    )
    if not any([args.movies, args.tv_shows, args.anime]):
        print("[scan] Pass at least one of --movies/--tv-shows/--anime.", file=sys.stderr)
        return 1

    def progress(idx: int, total: int, name: str) -> None:
        print(f"[scan] ({idx + 1}/{total}) {name}", file=sys.stderr)

    library = LibraryScanner(config).scan(progress=progress)

    def describe(title) -> str:  # local import-free duck typing keeps this compact
        year = f" ({title.year})" if title.year else ""
        if title.file_path:
            langs = "/".join(title.audio_languages) or "no audio tracks read"
            return f"  - {title.display_name}{year} [{langs}]"
        ep_count = sum(len(s.episodes) for s in title.seasons)
        return f"  - {title.display_name}{year} — {len(title.seasons)} season(s), {ep_count} episode(s)"

    print(f"\nMovies ({len(library.movies)}):")
    for t in library.movies:
        print(describe(t))
    print(f"\nTV Shows ({len(library.shows)}):")
    for t in library.shows:
        print(describe(t))
    print(f"\nAnime ({len(library.anime)}):")
    for t in library.anime:
        print(describe(t))
    return 0


def run_enrich(args: argparse.Namespace) -> int:
    """M4 testing hook — scans then enriches, printing a summary of what
    matched/reclassified/fell back to on-disk art, no window/GL involved.
    Real enrichment runs automatically after a library scan in later
    milestones, not a CLI flag — this exists so TMDB matching can be
    verified standalone before the Home/Detail UI (M5) exists to browse
    the results in."""
    apply_dev_config(args, load_dev_config(Path(args.config)))
    image_cache_dir = args.image_cache or "./localstream_cache/images"
    metadata_cache_path = args.metadata_cache or "./localstream_cache/metadata.json"

    scanner_config = ScannerConfig(
        movies_root=args.movies,
        tv_shows_root=args.tv_shows,
        anime_root=args.anime,
        extract_tracks=not args.no_tracks,
    )
    if not any([args.movies, args.tv_shows, args.anime]):
        print("[enrich] Pass at least one of --movies/--tv-shows/--anime.", file=sys.stderr)
        return 1
    if not args.tmdb_key:
        print(
            "[enrich] No --tmdb-key given — running fully offline, every "
            "title will get frame-grabbed fallback art and no TMDB metadata.",
            file=sys.stderr,
        )

    def scan_progress(idx: int, total: int, name: str) -> None:
        print(f"[scan] ({idx + 1}/{total}) {name}", file=sys.stderr)

    library = LibraryScanner(scanner_config).scan(progress=scan_progress)

    enricher_config = EnricherConfig(
        tmdb_api_key=args.tmdb_key,
        image_cache_dir=image_cache_dir,
        metadata_cache_path=metadata_cache_path,
        enrich_episodes=bool(args.episode_enrich),
    )

    def enrich_progress(idx: int, total: int, name: str) -> None:
        print(f"[enrich] ({idx + 1}/{total}) {name}", file=sys.stderr)

    library = MetadataEnricher(enricher_config).enrich(library, progress=enrich_progress)

    def describe(title) -> str:
        year = f" ({title.year})" if title.year else ""
        art = "poster+backdrop" if (title.poster_path and title.backdrop_path) else "partial/no art"
        matched = "matched" if title.synopsis or title.genres else "unmatched (fallback art)"
        return f"  - {title.display_name}{year} [{title.type.value}, {matched}, {art}]"

    print(f"\nMovies ({len(library.movies)}):")
    for t in library.movies:
        print(describe(t))
    print(f"\nTV Shows ({len(library.shows)}):")
    for t in library.shows:
        print(describe(t))
    print(f"\nAnime ({len(library.anime)}) — includes any reclassified anime movies:")
    for t in library.anime:
        print(describe(t))
    return 0


def build_library(args: argparse.Namespace, status: Optional[Callable[[str], None]] = None):
    """Shared scan(+enrich) path used by the browse UI (M5+) — same logic
    run_scan/run_enrich already exercise standalone, factored out so
    booting into Home doesn't duplicate it. Returns an empty Library (with
    a printed warning) if no roots are configured, rather than failing —
    an empty Home is a valid, testable state before folders are set up
    (Settings' first-run folder picker is M9).

    `status`, when given, is called with a short human-readable string on
    every progress tick (e.g. "Scanning Inception (12/340)") instead of
    only printing to stderr — this is what lets run_browse's loading
    screen (below) show live progress while this runs on a background
    thread. Intentionally just a string callback, not a Qt/GLFW-specific
    type, so it stays UI-framework-agnostic and callable from a worker
    thread without touching any GL/window state itself."""
    from library.models import Library  # local import: keeps this helper near its only caller

    apply_dev_config(args, load_dev_config(Path(args.config)))
    if not any([args.movies, args.tv_shows, args.anime]):
        print(
            "[main] No --movies/--tv-shows/--anime configured (CLI flags or "
            "localstream.config.json) — booting into Home with an empty "
            "library. Real first-run folder setup lands in M9.",
            file=sys.stderr,
        )
        if status:
            status("No library folders configured")
        return Library()

    # Track cache lives next to the metadata cache by default — same
    # ./localstream_cache app-data-ish location, one JSON file per cache
    # kind. This is the fix for "it re-enriches everything every launch":
    # unchanged files (same mtime+size as last scan) reuse their cached
    # audio/subtitle languages instead of spinning up headless mpv again.
    metadata_cache_path = args.metadata_cache or "./localstream_cache/metadata.json"
    track_cache_path = str(Path(metadata_cache_path).with_name("track_cache.json"))

    scanner_config = ScannerConfig(
        movies_root=args.movies,
        tv_shows_root=args.tv_shows,
        anime_root=args.anime,
        extract_tracks=not args.no_tracks,
        track_cache_path=track_cache_path,
    )

    def scan_progress(idx: int, total: int, name: str) -> None:
        print(f"[scan] ({idx + 1}/{total}) {name}", file=sys.stderr)
        if status:
            status(f"Scanning your library… {name} ({idx + 1}/{total})")

    library = LibraryScanner(scanner_config).scan(progress=scan_progress)

    if not args.tmdb_key:
        print(
            "[main] No --tmdb-key configured — titles will show with "
            "frame-grabbed fallback art and no synopsis/genre/rating "
            "until one is added (Section 4).",
            file=sys.stderr,
        )

    enricher_config = EnricherConfig(
        tmdb_api_key=args.tmdb_key,
        image_cache_dir=args.image_cache or "./localstream_cache/images",
        metadata_cache_path=metadata_cache_path,
        enrich_episodes=bool(args.episode_enrich),
    )

    def enrich_progress(idx: int, total: int, name: str) -> None:
        print(f"[enrich] ({idx + 1}/{total}) {name}", file=sys.stderr)
        if status:
            status(f"Fetching posters & info… {name} ({idx + 1}/{total})")

    library = MetadataEnricher(enricher_config).enrich(library, progress=enrich_progress)
    if status:
        status("Done")
    return library


def run_browse(args: argparse.Namespace) -> int:
    """M5's real entry point: scan+enrich the configured library, then
    boot straight into the Home/Detail/Player router (Section 9) instead
    of M2's single-file `--file` test harness. `--file` (below, in `run`)
    stays available as a quicker playback-only smoke test that skips
    scanning entirely.

    build_library() (scan + TMDB enrich) used to run right here, on this
    same thread, before the window ever polled an event or swapped a
    buffer — on a real library that's a multi-second-to-minutes stall
    where GLFW has painted nothing and isn't responding to the OS, i.e.
    exactly the blank-white "(Not Responding)" window this was reported
    as. Fixed by running it on a background thread while the render loop
    below keeps polling/painting a small LoadingView (ui/views/loading.py)
    with live status text, then swapping in the real Router once it's
    done. The track/metadata caches (library/track_cache.py,
    metadata/metadata_store.py) mean this whole thing is only slow the
    *first* time or when files actually changed — a re-run against an
    unchanged library should reach Home in well under a second."""
    import threading
    import time

    from ui.router import Router
    from ui.views.loading import LoadingView

    window = Window(title="LocalStream", start_fullscreen=args.fullscreen)
    renderer = Renderer(window.framebuffer_size())

    player = MpvPlayer()
    player.init_render_context()

    scrub_osd = ScrubBarOSD()
    scrub_input = ScrubBarInput(osd=scrub_osd)

    window.on_resize = renderer.handle_resize

    # -- background scan+enrich, loading screen kept alive meanwhile ----
    loading_quad = Quad2D()
    try:
        loading_text: Optional[TextRenderer] = TextRenderer(loading_quad)
    except FontNotFoundError as exc:
        print(f"[ui] {exc}", file=sys.stderr)
        loading_text = None
    loading_view = LoadingView(loading_quad, loading_text)

    library_result: list = [None]
    error_result: list = [None]
    done = threading.Event()

    def load() -> None:
        try:
            library_result[0] = build_library(args, status=loading_view.set_status)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user below, not swallowed
            error_result[0] = exc
        finally:
            done.set()

    loader_thread = threading.Thread(target=load, name="library-load", daemon=True)
    loader_thread.start()

    last_t = time.perf_counter()
    while not done.is_set() and not window.should_close():
        window.poll_events()
        now = time.perf_counter()
        loading_view.tick(now - last_t)
        last_t = now

        renderer.begin_frame()
        fb_w, fb_h = window.framebuffer_size()
        loading_quad.begin_frame(fb_w, fb_h)
        loading_view.render(fb_w, fb_h)
        renderer.render()
        window.swap_buffers()

    if window.should_close():
        # User closed the window during the initial scan — don't wait on
        # the loader thread (it's a daemon; process exit reaps it) and
        # don't bother constructing a Router for a library nobody will see.
        player.shutdown()
        window.terminate()
        return 0

    loader_thread.join()
    if error_result[0] is not None:
        # Scan/enrich blew up somewhere main.py couldn't already catch
        # per-item (Section 12's per-title resilience only covers TMDB/
        # track-read failures, not e.g. a bad config path) — surface it
        # instead of silently hanging on a loading screen forever, but
        # still let people quit cleanly rather than a raw traceback-only
        # crash with an unresponsive window behind it.
        traceback.print_exception(error_result[0])
        loading_view.set_status("Something went wrong loading your library — see console. Press Esc to quit.")
        while not window.should_close():
            window.poll_events()
            if glfw.get_key(window.handle, glfw.KEY_ESCAPE) == glfw.PRESS:
                break
            renderer.begin_frame()
            fb_w, fb_h = window.framebuffer_size()
            loading_quad.begin_frame(fb_w, fb_h)
            loading_view.render(fb_w, fb_h)
            renderer.render()
            window.swap_buffers()
        player.shutdown()
        window.terminate()
        return 1

    library = library_result[0]
    router = Router(library, player)

    def handle_key(key: int, scancode: int, action: int, mods: int) -> None:
        if action == glfw.RELEASE:
            if key in (glfw.KEY_LEFT, glfw.KEY_RIGHT, glfw.KEY_J, glfw.KEY_L):
                player.flush_pending_seek()
            return
        if action not in (glfw.PRESS, glfw.REPEAT):
            return

        shift = bool(mods & glfw.MOD_SHIFT)

        if key in (glfw.KEY_ESCAPE, glfw.KEY_BACKSPACE) and action == glfw.PRESS:
            # Router gets first refusal (Player -> Detail -> Home), same
            # as Section 8's "Back to Detail View / Esc" (Backspace is the
            # same action, Section 8's table lists both) — only falls
            # through to the fullscreen-exit behavior (Esc only; Backspace
            # is a no-op once there's nowhere left to go back to) once the
            # router has nowhere left to go back to.
            went_back = router.on_escape()
            if not went_back and key == glfw.KEY_ESCAPE and window.is_fullscreen:
                window.toggle_fullscreen()
            return

        if router.state.name != "PLAYER":
            return  # Home/Detail don't use these playback shortcuts

        if key == glfw.KEY_SPACE and action == glfw.PRESS:
            player.toggle_pause()
        elif key == glfw.KEY_LEFT:
            if action == glfw.PRESS:
                player.seek_small(forward=False)
            else:
                player.queue_seek_repeat(-MpvPlayer.SEEK_SMALL_S)
        elif key == glfw.KEY_RIGHT:
            if action == glfw.PRESS:
                player.seek_small(forward=True)
            else:
                player.queue_seek_repeat(MpvPlayer.SEEK_SMALL_S)
        elif key == glfw.KEY_J:
            if action == glfw.PRESS:
                player.seek_big(forward=False)
            else:
                player.queue_seek_repeat(-MpvPlayer.SEEK_BIG_S)
        elif key == glfw.KEY_L:
            if action == glfw.PRESS:
                player.seek_big(forward=True)
            else:
                player.queue_seek_repeat(MpvPlayer.SEEK_BIG_S)
        elif key == glfw.KEY_UP and not shift:
            player.adjust_volume(MpvPlayer.VOLUME_STEP)
        elif key == glfw.KEY_DOWN and not shift:
            player.adjust_volume(-MpvPlayer.VOLUME_STEP)
        elif key == glfw.KEY_UP and shift:
            player.adjust_brightness(MpvPlayer.BRIGHTNESS_STEP)
        elif key == glfw.KEY_DOWN and shift:
            player.adjust_brightness(-MpvPlayer.BRIGHTNESS_STEP)
        elif key == glfw.KEY_A and action == glfw.PRESS:
            player.cycle_audio_track()
        elif key == glfw.KEY_S and action == glfw.PRESS:
            player.cycle_subtitle_track()
        elif key == glfw.KEY_LEFT_BRACKET and action == glfw.PRESS:
            player.adjust_subtitle_delay(-MpvPlayer.SUBTITLE_DELAY_STEP_S)
        elif key == glfw.KEY_RIGHT_BRACKET and action == glfw.PRESS:
            player.adjust_subtitle_delay(MpvPlayer.SUBTITLE_DELAY_STEP_S)

    window.on_key = handle_key

    _last_drag_fraction: list[Optional[float]] = [None]

    def handle_mouse_button(button: int, action: int, mods: int) -> None:
        win_w, win_h = glfw.get_window_size(window.handle)
        cx, cy = glfw.get_cursor_pos(window.handle)

        # Mouse "back" side-button (common on gaming/productivity mice) —
        # a free, discoverable way to back out of a Detail/Player view,
        # same destination as Esc/Backspace (Section 8).
        if button == glfw.MOUSE_BUTTON_4 and action == glfw.PRESS:
            if not router.on_escape() and window.is_fullscreen:
                pass  # mouse back button never touches fullscreen, unlike Esc
            return

        if button != glfw.MOUSE_BUTTON_LEFT:
            return

        if router.state.name != "PLAYER":
            # Home's shelves need real drag-to-pan (Section 7c), so a
            # click is only fired on release, and only if HomeView tells
            # us the press didn't turn into a shelf drag.
            if action == glfw.PRESS:
                router.on_mouse_down(cx, cy, win_w, win_h)
            elif action == glfw.RELEASE:
                was_click = router.on_mouse_up(cx, cy, win_w, win_h)
                if was_click:
                    router.on_click(cx, cy, win_w, win_h)
            return

        if action == glfw.PRESS:
            fraction = scrub_input.on_mouse_down(win_w, win_h, cx, cy)
            if fraction is not None:
                player.seek_to_fraction(fraction, commit=True)
        elif action == glfw.RELEASE:
            was_dragging = scrub_input.is_dragging
            scrub_input.on_mouse_up()
            if was_dragging and _last_drag_fraction[0] is not None:
                player.seek_to_fraction(_last_drag_fraction[0], commit=True)
            _last_drag_fraction[0] = None

    window.on_mouse_button = handle_mouse_button

    def handle_cursor_pos(x: float, y: float) -> None:
        win_w, win_h = glfw.get_window_size(window.handle)
        if router.state.name != "PLAYER":
            # Query the button live rather than tracking our own flag —
            # GLFW already knows, and this keeps a drag that started
            # inside a shelf tracking correctly even across frames.
            left_down = glfw.get_mouse_button(window.handle, glfw.MOUSE_BUTTON_LEFT) == glfw.PRESS
            if left_down:
                router.on_mouse_drag(x, y, win_w, win_h)
            else:
                router.on_mouse_move(x, y, win_w, win_h)
            return
        fraction = scrub_input.on_mouse_move(win_w, win_h, x)
        if fraction is not None:
            _last_drag_fraction[0] = fraction
            player.seek_to_fraction(fraction, commit=False)

    window.on_cursor_pos = handle_cursor_pos

    def handle_scroll(dx: float, dy: float) -> None:
        if router.state.name != "PLAYER":
            win_w, win_h = glfw.get_window_size(window.handle)
            router.on_scroll(dx, dy, win_w, win_h)

    window.on_scroll = handle_scroll

    try:
        last_frame_t = time.perf_counter()
        while not window.should_close():
            window.poll_events()
            player.flush_pending_seek()

            now = time.perf_counter()
            router.tick(now - last_frame_t)
            last_frame_t = now

            renderer.begin_frame()

            fb_w, fb_h = window.framebuffer_size()
            if router.state.name == "PLAYER":
                player.render(fb_w, fb_h)
                scrub_osd.draw(fb_w, fb_h, player.progress_fraction)
            router.render(fb_w, fb_h)

            renderer.render()
            window.swap_buffers()
    finally:
        player.shutdown()
        window.terminate()

    return 0


def run(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.enrich:
        return run_enrich(args)

    if args.scan:
        return run_scan(args)

    if not args.file:
        return run_browse(args)

    window = Window(title="LocalStream", start_fullscreen=args.fullscreen)
    renderer = Renderer(window.framebuffer_size())

    player = MpvPlayer()
    player.init_render_context()  # needs the GL context current, which Window() already made so

    scrub_osd = ScrubBarOSD()
    scrub_input = ScrubBarInput(osd=scrub_osd)

    player.load(args.file)

    window.on_resize = renderer.handle_resize

    def handle_key(key: int, scancode: int, action: int, mods: int) -> None:
        if action == glfw.RELEASE:
            # Land whatever was accumulated by a held arrow/J/L key now,
            # rather than waiting on a repeat event that just stopped
            # arriving — see flush_pending_seek's docstring.
            if key in (glfw.KEY_LEFT, glfw.KEY_RIGHT, glfw.KEY_J, glfw.KEY_L):
                player.flush_pending_seek()
            return
        if action not in (glfw.PRESS, glfw.REPEAT):
            return

        shift = bool(mods & glfw.MOD_SHIFT)

        if key == glfw.KEY_ESCAPE and action == glfw.PRESS:
            # Esc never closes the app — only exits fullscreen if we're in
            # it. Closing is Alt+F4 (handled natively by the OS/window
            # manager; GLFW already turns that into window_should_close).
            if window.is_fullscreen:
                window.toggle_fullscreen()
        elif key == glfw.KEY_SPACE and action == glfw.PRESS:
            player.toggle_pause()
        elif key == glfw.KEY_LEFT:
            # First press: seek immediately for instant response. Held
            # (REPEAT): queue + throttle so holding doesn't flood mpv with
            # a seek per ~30-50ms repeat event — see queue_seek_repeat.
            if action == glfw.PRESS:
                player.seek_small(forward=False)
            else:
                player.queue_seek_repeat(-MpvPlayer.SEEK_SMALL_S)
        elif key == glfw.KEY_RIGHT:
            if action == glfw.PRESS:
                player.seek_small(forward=True)
            else:
                player.queue_seek_repeat(MpvPlayer.SEEK_SMALL_S)
        elif key == glfw.KEY_J:
            if action == glfw.PRESS:
                player.seek_big(forward=False)
            else:
                player.queue_seek_repeat(-MpvPlayer.SEEK_BIG_S)
        elif key == glfw.KEY_L:
            if action == glfw.PRESS:
                player.seek_big(forward=True)
            else:
                player.queue_seek_repeat(MpvPlayer.SEEK_BIG_S)
        elif key == glfw.KEY_UP and not shift:
            player.adjust_volume(MpvPlayer.VOLUME_STEP)
        elif key == glfw.KEY_DOWN and not shift:
            player.adjust_volume(-MpvPlayer.VOLUME_STEP)
        elif key == glfw.KEY_UP and shift:
            player.adjust_brightness(MpvPlayer.BRIGHTNESS_STEP)
        elif key == glfw.KEY_DOWN and shift:
            player.adjust_brightness(-MpvPlayer.BRIGHTNESS_STEP)
        elif key == glfw.KEY_A and action == glfw.PRESS:
            player.cycle_audio_track()
        elif key == glfw.KEY_S and action == glfw.PRESS:
            player.cycle_subtitle_track()
        elif key == glfw.KEY_LEFT_BRACKET and action == glfw.PRESS:
            player.adjust_subtitle_delay(-MpvPlayer.SUBTITLE_DELAY_STEP_S)
        elif key == glfw.KEY_RIGHT_BRACKET and action == glfw.PRESS:
            player.adjust_subtitle_delay(MpvPlayer.SUBTITLE_DELAY_STEP_S)

    window.on_key = handle_key

    _last_drag_fraction: list[Optional[float]] = [None]  # closure state for mouse-up commit

    def handle_mouse_button(button: int, action: int, mods: int) -> None:
        if button != glfw.MOUSE_BUTTON_LEFT:
            return
        win_w, win_h = glfw.get_window_size(window.handle)  # window-space, matches cursor callback
        cx, cy = glfw.get_cursor_pos(window.handle)
        if action == glfw.PRESS:
            fraction = scrub_input.on_mouse_down(win_w, win_h, cx, cy)
            if fraction is not None:
                player.seek_to_fraction(fraction, commit=True)  # single click: exact, no flood risk
        elif action == glfw.RELEASE:
            was_dragging = scrub_input.is_dragging
            scrub_input.on_mouse_up()
            # Drag just ended: land on the exact frame now that the flood
            # of fast preview seeks (see handle_cursor_pos) has stopped.
            if was_dragging and _last_drag_fraction[0] is not None:
                player.seek_to_fraction(_last_drag_fraction[0], commit=True)
            _last_drag_fraction[0] = None

    window.on_mouse_button = handle_mouse_button

    def handle_cursor_pos(x: float, y: float) -> None:
        win_w, win_h = glfw.get_window_size(window.handle)
        fraction = scrub_input.on_mouse_move(win_w, win_h, x)
        if fraction is not None:
            _last_drag_fraction[0] = fraction
            # Fast keyframe preview while actively dragging — exact-seeking
            # on every one of these (fired per pixel of mouse movement) is
            # what caused the jerky scrub-bar feel. Exact commit happens on
            # release, above.
            player.seek_to_fraction(fraction, commit=False)

    window.on_cursor_pos = handle_cursor_pos

    try:
        while not window.should_close():
            window.poll_events()

            # Safety net for queue_seek_repeat: normally a held key's own
            # REPEAT events flush it, but this catches the case where a
            # flush is due and no repeat event has arrived yet this tick.
            player.flush_pending_seek()

            # Always clear first. player.render() now unconditionally
            # blits mpv's last painted frame over this every frame (see
            # mpv_player.py) once video has started, so this only actually
            # shows through during the pre-video blank state — but clearing
            # unconditionally (instead of the old has_rendered_first_frame
            # special-case) keeps this loop simple and is effectively free.
            renderer.begin_frame()

            fb_w, fb_h = window.framebuffer_size()
            player.render(fb_w, fb_h)
            scrub_osd.draw(fb_w, fb_h, player.progress_fraction)

            renderer.render()
            window.swap_buffers()
    finally:
        player.shutdown()
        window.terminate()

    return 0


def main() -> None:
    try:
        sys.exit(run(sys.argv[1:]))
    except Exception:
        # Fail loud during M1 — no error-state UI yet (that's M10 polish).
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()