# LocalStream

Native Windows streaming-site app for local Movies/TV Shows/Anime.
Full contract: `docs/PROJECT_SPEC.md`.

## Status

**M3 — Library + parsing** (see spec Section 12). Currently working:
- (M1) GLFW window + OpenGL 3.3 core context, fullscreen boot + `F11` toggle
- (M2) libmpv embedded via the render API, exact seek, A/S track cycling,
  subtitle delay, volume/brightness — see previous status below
- (M3) `library/` scans the three separate top-level roots (Movies, TV
  Shows, Anime — Section 3): folder walking, `S01E02`/absolute-number/
  `(YYYY)` filename parsing, and embedded MKV audio/subtitle language
  extraction (language + default/forced flags, Section 4c) via a headless
  mpv instance per file. Builds the Section 2 content model (`Title` /
  `Season` / `Episode`) with everything TMDB/segments/watch-state
  touches left for M4/M6/M7 to fill in.

Still not wired up: metadata (TMDB), profiles, segment engine/Skip Mode,
series auto-advance, hover preview, and there's no UI browsing the scanned
library yet (that's M5). Those are M4+ (Section 12).

## Setup (Windows, Python 3.12+)

```powershell
cd LocalStream
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

**Required for M2:** `python-mpv` needs the `libmpv-2.dll` runtime — it
doesn't ship libmpv itself, just the bindings.
1. Grab a Windows libmpv build, e.g. the `mpv-dev-x86_64` (or `-x86_64-v3`)
   archive from https://sourceforge.net/projects/mpv-player-windows/files/libmpv/
2. Copy `libmpv-2.dll` from it into your venv's `Scripts\` folder (same
   folder as `python.exe` when the venv is active), or anywhere else on
   PATH.
3. If Windows complains about a missing `libmpv-2.dll` on launch, that's
   this step — the Python package alone isn't enough.

## Run

```powershell
python src\main.py --file "C:\path\to\a\video.mkv"
python src\main.py --file "C:\path\to\a\video.mkv" --fullscreen
```

`--file` is a temporary M2 testing hook (real playback triggers from the
library/Detail View starting M5) — pick any local video file you have, any
container mpv handles (mkv/mp4/etc.), doesn't need to be part of a
LocalStream-shaped library yet.

**Controls** (Section 8 subset relevant to M2 — skip buttons/Next
Episode/Back arrive with M6/M5):

| Action | Key |
|---|---|
| Play/Pause | Space |
| Seek -5s / +5s (hold to repeat) | Left / Right |
| Seek -10s / +10s | J / L |
| Volume +/- | Up / Down |
| Brightness +/- | Shift+Up / Shift+Down |
| Cycle audio track | A |
| Cycle subtitle track | S |
| Subtitle delay -50ms / +50ms | [ / ] |
| Exact seek to position | Click or drag the scrub bar at the bottom |
| Toggle fullscreen | F11 |
| Quit | Esc |

## What to check on your machine

I still can't run this myself — no GPU/display and no network in my
sandbox, so I can't `pip install`, fetch libmpv, or open a window. Please
verify before we move to M3:

1. `pip install -e .` succeeds, and `libmpv-2.dll` is in place (see above).
2. `python src\main.py --file <video>` opens a window and **plays video
   with audio** — this is the big one, confirms the render-API embed
   actually works end to end.
3. Space pauses/resumes. Left/Right nudge ±5s; holding a direction repeats.
   J/L jump ±10s.
4. Click somewhere on the scrub bar → playback jumps there. Click and drag
   it → playback scrubs live as you drag.
5. A cycles through audio tracks if the file has more than one (won't be
   audible/visible if it only has one — that's expected). S cycles
   subtitles the same way; if the file has subs, you should see them
   appear/disappear/change as you press it.
6. Up/Down changes volume; Shift+Up/Down visibly changes image brightness.
7. F11 and Esc from M1 still work exactly as before.

If step 2 fails (no picture, or audio-only, or an mpv/libmpv error in the
console), that's almost certainly the DLL step above — tell me the exact
error and I'll help track it down before we build anything on top of it.

## Fix — startup freeze + re-scanning everything every launch

Two real bugs, fixed:

1. **Blank white "(Not Responding)" window on launch.** `run_browse`
   (M5's real entry point) ran `build_library()` — the full scan + TMDB
   enrich pass — synchronously between creating the window and starting
   the render loop. GLFW never got to poll events or swap a buffer for
   however long that took, so Windows saw a window painting nothing and
   not responding to input for the whole scan on a real library. Fixed:
   the scan+enrich now runs on a background thread (`threading.Thread`)
   while the render loop keeps polling/painting a small loading screen
   (`ui/views/loading.py` — wordmark, live status text like "Scanning
   Inception (12/340)", indeterminate progress bar) so the window is
   always responsive and visibly alive, then swaps to the real Home once
   it's done.
2. **Every launch re-read every file's audio/subtitle tracks.** The
   scanner spun up a headless mpv instance per video file on *every* run
   to read embedded MKV track languages — TMDB metadata/art was already
   cached forever (`metadata/metadata_store.py`), but track reads weren't
   cached at all, so a big library paid that cost fresh every single
   launch. Fixed with a new `library/track_cache.py`: same JSON-cache
   pattern as the metadata store, keyed by file id + mtime + size, so an
   unchanged file reuses its cached languages and only new/modified files
   spin up mpv. First run after this fix is unchanged (still has to read
   everything once); every run after that should be close to instant for
   the scan step.

Both are covered by `tests/test_track_cache.py`.

Still open, and worth doing next if you want the Netflix feel from the
screenshots to go further: real poster-fade-in/skeleton loading per tile
instead of a blocking loading screen at all, hover-preview autoplay
(M8), and the transitions/polish pass (M10) — happy to scope whichever
of those you want first.

## M3 — Library scan (new)

Test the scanner standalone, no window needed, against your real folders
(or point it at any three test folders shaped per Section 3):

```powershell
python src\main.py --scan --movies "D:\Movies" --tv-shows "D:\TV Shows" --anime "D:\Anime"
```

Add `--no-tracks` to skip MKV track reads (faster, no language data) if
you just want to sanity-check the folder/filename parsing first. Drop
whichever of `--movies`/`--tv-shows`/`--anime` you don't have yet — any
subset works.

It prints every Title found under each of the three roots, with (for
Movies) the audio languages read off the file, or (for Shows/Anime) the
season/episode count. Sandbox note: I can run the pure parsing/scanning
logic myself (`pytest tests/` — 11 tests, all passing, covers folder
walking, `S01E02`/absolute-number/year parsing, stable IDs across
re-scans), but not the MKV track-reading path, since that needs the same
libmpv runtime as M2 and I have no video files or libmpv here. Please run
the command above against your actual library and confirmion:

1. Every movie folder shows up under **Movies**, with the right year
   parsed out of `(YYYY)` and the display name it in.
2. Anime movies (e.g. Spirited Away, sitting in the Movies folder, not a
   separate Anime subfolder — Section 3) show up in **Movies** too, not
   Anime — that's correct for M3; the Anime reclassification happens in M4
   once TMDB metadata lands.
3. Every show under TV Shows and Anime shows the right season/episode
   counts, and season-folder shows (`Season 01/…S01E02.mkv`) parse
   correctly.
4. Anime with **no** season folder (absolute numbering, e.g. `One Piece -
   1085.mkv`) still shows up, grouped under a single season.
5. Without `--no-tracks`: audio languages print for at least one movie
   with multiple embedded audio tracks, if you have one — confirms the
   headless-mpv track read actually works (this is the one thing I
   genuinely can't verify without libmpv in my sandbox).
6. A file that's corrupt/unreadable (if you have one to test with)
   doesn't crash the whole scan — it should just get skipped with a
   warning printed to the console.

Once that's confirmed, tell me and I'll move on to **M4 — Metadata +
classification** (TMDB matching, poster/backdrop/synopsis/language fetch +
cache, offline fallback art, anime-movie classification).
