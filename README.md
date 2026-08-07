# LocalStream

Native Windows streaming-site app for local Movies/TV Shows/Anime.
Full contract: `docs/PROJECT_SPEC.md`.

## Status

**M2 — Playback core** (see spec Section 12). Currently working:
- (M1) GLFW window + OpenGL 3.3 core context, fullscreen boot + `F11` toggle
- libmpv embedded via the render API (draws into our GL context, not its
  own native window)
- Exact seek: ±5s (arrows, hold to repeat), ±10s (J/L), click/drag scrub
  bar (Section 8)
- Audio/subtitle track cycling (A/S), subtitle delay ±50ms ([/])
- Volume (Up/Down) and brightness (Shift+Up/Down) via mpv's video-equalizer
- No transcoding — plays files at full original quality

Still not wired up: library scan, metadata, profiles, segment engine/Skip
Mode, series auto-advance, hover preview. Those are M3+ (Section 12).

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

Once playback's confirmed solid, tell me and I'll move on to **M3 —
Library + parsing** (scan Movies/TV Shows/Anime roots, season/episode/
absolute-number parsing, MKV track extraction).
