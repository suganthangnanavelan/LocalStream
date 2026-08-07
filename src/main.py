"""
main.py — M2 entry point

M1 gave us window + GL context + fullscreen toggle. M2 adds the playback
core on top: libmpv embed (render API), exact seek, audio/subtitle track
switching, volume/brightness, and a minimal scrub bar so seeking is
actually testable. Still no library scan, no metadata, no profiles, no
segment engine — those are later milestones (Section 12).

Run (after `pip install -e .` from the project root):
    python src/main.py --file "C:\\path\\to\\video.mkv"
    python src/main.py --file "...\\video.mkv" --fullscreen

`--file` is a temporary M2 testing hook — real playback is triggered from
the Detail View / library (M5+), not a CLI flag. It exists so playback can
be verified standalone before any UI is built on top of it.

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
import sys
import traceback
from typing import Optional

import glfw

from library.scanner import LibraryScanner, ScannerConfig
from player.mpv_player import MpvPlayer
from player.osd import ScrubBarOSD
from player.scrub_input import ScrubBarInput
from ui.renderer import Renderer
from window.window import Window


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
    parser.add_argument("--movies", default=None, help="Movies root (Section 3).")
    parser.add_argument("--tv-shows", default=None, help="TV Shows root (Section 3).")
    parser.add_argument("--anime", default=None, help="Anime root (Section 3).")
    parser.add_argument(
        "--no-tracks",
        action="store_true",
        help="Skip MKV track extraction during --scan (faster, no language data).",
    )
    return parser.parse_args(argv)


def run_scan(args: argparse.Namespace) -> int:
    """M3 testing hook — scans the given roots and prints a summary, no
    window/GL/mpv-render-API involved. Real library scanning is triggered
    from app startup / a Settings "Rescan" action in later milestones, not
    a CLI flag — this exists so the scanner can be verified standalone
    before the Home/Detail UI (M5) exists to browse the results in."""
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


def run(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.scan:
        return run_scan(args)

    window = Window(title="LocalStream", start_fullscreen=args.fullscreen)
    renderer = Renderer(window.framebuffer_size())

    player = MpvPlayer()
    player.init_render_context()  # needs the GL context current, which Window() already made so

    scrub_osd = ScrubBarOSD()
    scrub_input = ScrubBarInput(osd=scrub_osd)

    if args.file:
        player.load(args.file)
    else:
        print(
            "[main] No --file given — window will show a blank frame. "
            "Pass --file <path-to-video> to test playback.",
            file=sys.stderr,
        )

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