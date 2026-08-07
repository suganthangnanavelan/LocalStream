# LocalStream — Project Spec (v6)

A native Windows desktop app that turns local **Movies / TV Shows / Anime**
folders into a real streaming-site experience — poster walls, backdrops,
detail pages, hover previews, multi-profile continue-watching, segment-aware
skip controls — with playback as reliable as VLC. No web framework, no
Qt/Electron. Custom-rendered UI over OpenGL, playback powered by libmpv.

This spec is the contract for the build. Anything not in here is out of scope
until we add it here first.

---

## 1. Goals / Non-Goals

**Goals**
- Full streaming-site experience for **Movies, TV Shows, Anime** — unified
  library, poster/backdrop/synopsis metadata, season/episode browsing.
- **Multi-user local profiles** — separate watch progress and personal
  playback preferences per person on the same machine. No lock/PIN.
- **Segment-aware playback** governed by one global **Skip Mode** setting:
  - **Automatic**: every skip point (Intro, Outro, Recap, Next Episode) shows
    a countdown alongside its button/shortcut. Clicking or pressing the
    shortcut fires it immediately; doing nothing fires it when the countdown
    hits zero (Netflix "Next Episode in 8s" pattern, applied uniformly).
  - **Manual**: no countdown, nothing fires on its own. Button/shortcut is
    present but the segment just plays through unless actively triggered.
  - **Custom segments are exempt from this setting entirely** — always
    hard-automatic, no button, no countdown, no exceptions (see Section 5.2, Section 5.5).
- Playback as reliable as VLC: exact seek (drag + click-to-position),
  fast-forward/rewind, audio track switching, subtitle track switching, full
  original quality, no transcoding.
- In-player brightness and volume control, same reliability as everything else.
- **Netflix-style hover preview**: hovering a poster on Home plays a short
  preview clip (with audio) from the actual file.
- **Browse/filter by language** (original language + embedded audio
  languages), and a lightweight **recommendation system** ("More Like This,"
  "Because you watched X") built from data already being cached, so it stays
  fast regardless of library size — see Section 4c, Section 4d.
- Boots straight into fullscreen on machine startup.
- Fast, smooth, fully custom-drawn UI — no toolkit defaults.

**Non-Goals (v1)**
- No network streaming to other devices — single machine only.
- No transcoding pipeline.
- No profile account security — profiles are convenience/personalization
  only (Admin Mode's password, Section 7f, is a separate, deliberate
  exception for shared configuration access, not a per-profile security
  system).
- No automatic audio-fingerprint intro/outro detection (manual marking +
  season-wide propagation instead — see Section 5.4).
- No live TV / torrent / download features.

---

## 2. Content Model

```
Profile
 ├─ id, name, avatar_color
 ├─ per-title watch_state: unwatched | in_progress(position) | watched
 └─ preferences: preferred audio/subtitle language (overrides the file's
                 default-flag pick when a matching track exists, Section 4c),
                 default volume, default brightness
                 (Skip Mode is NOT here — it's a global app setting, Section 5)

Title
 ├─ type: Movie | Show | Anime   (Anime-movie classified via Section 4b, not folder)
 ├─ id, display_name, sort_name, year
 ├─ poster_path, backdrop_path, synopsis, genres[], rating, runtime (Movie)
 ├─ original_language, audio_languages[], subtitle_languages[]   (Section 4c)
 ├─ similar_title_ids[]   (cached TMDB similar/recommendations, Section 4d)
 ├─ added_at   (first-seen file date modified — "NEW" badge, Section 7b)
 └─ Movie → single file_path + segment data (see below)
    Show/Anime → Season[]
                  └─ Episode[]
                       ├─ episode_number, absolute_number (anime), title
                       ├─ thumbnail_path, synopsis, file_path, added_at
                       ├─ preview_start (Home hover preview, Section 7)
                       └─ segments: Segment[]   (season-level by default,
                                                  episode overrides allowed)

Segment
 ├─ type: Intro | Recap | Outro | PostCredit | Custom(label)
 ├─ start, end   (seconds, relative to episode/movie start)
 └─ scope: season (applies to every episode in the season) | this-episode-only

RequestedTitle   (Section 7a)
 ├─ tmdb_id, type, display_name, poster_path (cached)
 └─ requested_at
```

Behavior by `type`:
- `Intro` → follows global **Skip Mode** (Section 5.2), except a season's
  first episode, where it's always button-only, never auto-fires.
- `Recap` → always button-only, never auto-fires, in either Skip Mode.
- `Outro` → always follows global **Skip Mode** (Section 5.2), no exceptions.
- `Custom` → always hard-automatic, unconditionally, regardless of Skip Mode
  (Section 5.2, Section 5.5).
- `PostCredit` → not itself skippable; it's the landing target for Skip Outro
  when marked right after an Outro segment.

`Segment` data is a property of the **content**, shared across all profiles —
mark it once, every profile benefits. `watch_state` and playback preferences
are the only per-profile data; **Skip Mode is global, same for everyone**.

---

## 3. Folder Convention

**Three separate top-level roots** — Anime is its own root, not nested
inside TV Shows:

```
Movies/
  Inception (2010)/Inception (2010).mkv
  Spirited Away (2001)/Spirited Away (2001).mkv   ← anime movie, stays in
                                                      Movies, no subfolder

TV Shows/
  Breaking Bad/Season 01/Breaking Bad S01E01.mkv

Anime/
  Attack on Titan/Season 01/Attack on Titan S01E01.mkv
  One Piece/One Piece - 1085.mkv                  ← absolute numbering
```

- **Movies root**: every movie lives here flat, anime movies included —
  there's no dedicated anime-movie subfolder, so anime movies are
  **classified by metadata, not folder location** (see Section 4b).
- **TV Shows root**: regular (non-anime) shows only.
- **Anime root**: a fully separate top-level root, own configurable path —
  anime series here are classified by folder location (cheap, reliable),
  same `S01E02` pattern parsing / absolute numbering / `(YYYY)` hints as
  everywhere else.

So: Show vs Anime-show is folder-based (which root it's under); Movie vs
Anime-movie is metadata-based (needs the TMDB match to land first, Section 4b).

---

## 4. Metadata (posters, backdrops, synopses)

Unchanged — TMDB-matched, cached locally forever, offline fallback art
generated from the video itself if no match/no internet.

---

## 4a. Subtitles (VLC parity, embedded-only)

Confirmed: **no external/sidecar subtitle files** — every file in the
library carries its subtitles embedded, so the scanner never needs to look
for `.srt`/`.ass` companions. Rendering is via libass (same engine VLC uses),
so behavior matches VLC directly:

- Formats: SRT, ASS/SSA (full styling — fonts, colors, positioning, karaoke
  effects preserved), VobSub, PGS (Blu-ray bitmap subs), DVB subtitles.
- All embedded tracks per file are detected and selectable via the `S`
  shortcut (cycles tracks, "Off" included). The label shown is the
  **language name alone** — "English," "Tamil," "Japanese," etc., or
  "Unknown" if unflagged — never the track's own name/number (Section 4c).
- Styling: uses each file's own embedded ASS styling as-is by default
  (VLC's default behavior) — a Settings-level font/size/color override is a
  possible later addition, not built for v1 unless you want it prioritized.
- Non-UTF8 encoded legacy tracks auto-detected rather than shown garbled.
- Subtitle delay/sync adjustment added to Player Controls (Section 8) for tracks
  that drift out of sync.

---

## 4b. Content Classification (Anime-movie detection)

Since anime movies aren't in a separate folder, the app decides `Movie` vs
`Anime` **after** the TMDB match lands, using signal already present in the
metadata response — no extra API calls needed:

- `genre` includes "Animation" **and** `original_language` is Japanese, or
- TMDB keywords include "anime"

Either condition classifies it as Anime for Home-shelf grouping purposes.
Until a TMDB match lands (or if one never does), it's shown under Movies by
default and re-classified automatically once metadata arrives — so nothing
requires the user to manually sort anime movies out.

---

## 4c. Language Grouping & Multi-Audio/Subtitle Support

Not limited to anime dub/sub pairs — **any title in any of the three roots
can carry multiple audio and subtitle language tracks** (e.g. a movie with
Tamil, English, and Hindi audio tracks embedded; an anime with Japanese,
English, and Tamil). The track system is fully generic, not hardcoded to a
2-language case:

- **`original_language`** comes from TMDB metadata (e.g. "ja", "ta", "en",
  "hi", "ko") — used for a "Browse by Language" shelf on Home and as a
  Search filter.
- **`audio_languages[]`** — every embedded audio track's language, detected
  per file at scan time from Matroska track metadata (the same data source
  used for the `A` audio-track cycling picker, Section 8) — an arbitrary-length
  list, not a fixed pair.
- **`subtitle_languages[]`** — same idea for embedded subtitle tracks (Section 4a).
- **The displayed label is the language name — nothing else.** Not the
  track's "Name" field, not a track number, not "Audio 1" / "Track 2", not
  the codec — just the language itself: **"English," "Tamil," "Japanese,"**
  etc., read from the track's language flag. If a track has no language
  flag set, the label is **"Unknown"** — that's the only fallback, never a
  blank, a number, or a guess. Same rule for subtitle tracks (Section 4a).
- **Default track selection comes from the file itself, not TMDB.** Every
  file in the library is MKV, and Matroska tracks carry their own
  **default flag** per track — on first play, LocalStream selects whatever
  audio/subtitle track the file already marks as default, full stop. No
  attempt to infer a "correct" default from TMDB's `original_language` or
  any other external metadata — the file's own flag is authoritative.
  `A`/`S` cycling (Section 8) still works through every track regardless. A
  per-profile "preferred language" setting can still override this
  starting pick going forward, but the file's default flag is always the
  fallback, not a metadata guess.
- Since the entire library is MKV, extraction is straightforward and
  consistent: language, default-flag, and forced-flag all come cleanly off
  mpv's `track-list` property (backed by Matroska's own track headers) —
  no per-container special-casing needed.
- `audio_languages[]`/`subtitle_languages[]` still live on `Title`/`Episode`
  in the Content Model (Section 2), populated during the same scan pass.
  `original_language` (TMDB) is kept only for the "Browse by Language"
  shelf/Search filter — a separate, unrelated use of language data from
  default-track selection.

**Reaffirmed source-of-truth rule**: this applies identically to anime,
which is expected to carry the widest language spread (Japanese, English,
Tamil, others). Whatever tracks a file actually contains, in whatever
languages, are read straight off the file — TMDB is never consulted to
decide what languages a specific file has or which one plays by default.
TMDB's role is strictly limited to presentation data (poster, backdrop,
synopsis, genre, rating, similar titles) — never language/track behavior,
for any content type.

---

## 4d. Recommendation Engine

Kept deliberately lightweight so it stays fast at any library size — no ML
pipeline, no external recommendation service beyond what TMDB already gives
us for free:

- **"More Like This"** (on Detail View): TMDB's own `/similar` and
  `/recommendations` endpoints per title, fetched once and cached alongside
  the rest of that title's metadata — reuses data we're already pulling,
  filtered down to only titles that actually exist in your local library.
- **"Because you watched X"** (Home row): a local heuristic, computed
  in-memory from already-cached metadata — scores every other library title
  by genre overlap + shared keywords + same-franchise match, weighted toward
  recently-finished titles. This is a plain scoring pass over cached data
  (not a database, not a model), so it stays fast even at large libraries —
  computed on a background thread on library load and re-scored only when
  watch_state or the library actually changes, not on every Home render.
- **Continue Watching** row (already spec'd) sits above these, always first.

**Scalability note**: because everything here reads from data we're already
caching (TMDB responses, local watch_state) rather than fetching or
computing anything new, this scales with library size the same way the
rest of the app does — the in-memory scoring pass is O(library size), which
stays well under a frame budget even for a few thousand titles.

---

## 5. Segment System

### 5.1 The timeline
```
[Recap?] → [Intro] → [Main Content] → [Outro/Credits] → [Post-credit?] → [End]
```
Intro, Outro, and Next Episode are the "assistive" skip points driven by the
global Skip Mode setting below — but two exceptions carve out real viewing
behavior that a blanket auto-fire rule would get wrong:

- **Recap is never auto-fired, in either mode.** A "previously on..." recap
  is genuinely useful content, not filler — the button is always there to
  skip it, but it never counts down or fires on its own, even when Skip
  Mode is Automatic. Recap behaves like Manual mode unconditionally.
- **Intro on a season's first episode is never auto-fired either** — same
  reasoning: the first episode of a season is exactly when you actually
  want to see the opening/titles, not skip past them by default. From the
  second episode of that season onward, Intro follows the global Skip Mode
  normally. This applies per season (a show's S02E01 gets the same
  first-episode treatment as S01E01), and per Movie/Anime-movie there's no
  "episode" concept so it doesn't apply there — Movie Intro segments always
  follow the global Skip Mode.

### 5.2 Global Skip Mode setting

One setting in the app's global Settings screen — `skip_mode: automatic |
manual`, **defaulting to Automatic** — governs Outro and Next Episode
uniformly, and Intro everywhere except a season's first episode (Section 5.1):

| | Automatic (default) | Manual |
|---|---|---|
| Countdown shown | Yes (e.g. "Skip Intro · 6" / "Next Episode in 6s") | No |
| Click button / shortcut | Fires immediately | Fires immediately |
| No action taken | Fires automatically at countdown end | Never fires — segment plays through / episode doesn't advance |

Both modes always show the button and honor the shortcut the instant it's
used — the only difference is what happens if the user does **nothing**.

**Recap ignores this setting** — always button-only, never auto-fires
(Section 5.1). **A season's first-episode Intro ignores this setting too**
for the same reason — always button-only there specifically.

**Custom segments ignore this setting completely.** They're always silent,
always automatic, no button, no countdown, no way to opt out — because the
entire point of a Custom segment is a guaranteed skip, not a convenience.
This was explicitly confirmed: Custom always auto-skips, no exceptions.

### 5.3 Marking segments (editor)
- From Detail View or Player View: "Mark Segment" → drag a range on the
  scrub bar (or set in/out points live while watching) → pick type
  (Intro/Recap/Outro/PostCredit/Custom — Custom prompts for a label) →
  choose scope (this episode / whole season).
- Manage/edit/delete existing segments from the Detail View.

**Recommendation:** manual marking + season-wide propagation over
audio-fingerprint auto-detection for v1 — mark Intro/Outro once on episode 1
of a season, it applies to the rest automatically, with per-episode override
for the finale/premiere if it differs.

### 5.4 Custom segments (always silent, always automatic)
The mechanism for your NSFW-scene example and anything else you want
silently excised, regardless of global Skip Mode: the engine skips the
instant playback would enter the range, with a small corner toast ("Skipped
45s") that fades after a couple seconds.

### 5.5 Red Zones — Custom segments are truly unreachable
A `Custom` segment is treated as if it doesn't exist in the seekable
timeline at all — this is independent of, and stronger than, the Skip Mode
setting above.

- **Scrub bar**: Custom ranges render as a distinct red-shaded band,
  visually separate from Intro/Outro markers.
- **Every seek path is checked before the seek is issued** — drag-release,
  click-to-position, ←/→, J/L, and natural playback advance all funnel
  through the same check: *would this land inside a Custom range?* If yes,
  the target is corrected **before** the underlying mpv seek command runs —
  no frame from inside the range is ever decoded or displayed.
- **Direction-aware landing**: forward seeks into a red zone land at its
  end, backward seeks land at its start — preserving intent while
  guaranteeing zero exposure.
- Scrub-bar hover thumbnails (if enabled) suppress/substitute previews for
  timestamps inside a red zone too.

---

## 6. Multi-User Profiles

- Profile picker screen right after fullscreen boot, before Home — named
  avatars (color swatch + initials), "Add Profile" tile.
- **No PIN, no lock** — profiles exist purely to separate watch history and
  personal preferences (audio/subtitle language, volume, brightness) per
  person, not to restrict access. (Admin Mode is the one deliberate
  exception to this app's lock-free design — see Section 7f — because it
  edits shared configuration, not personal viewing preferences.)
- **Skip Mode is global**, not per-profile — one setting for the whole app,
  changed from the main Settings screen, not per-profile preferences.
- Switch profile from a corner menu in Home View without relaunching.

---

## 7. Home Hover Preview

Same as before with one change: **preview audio plays, it is not muted.**
Hovering (mouse) or focusing a poster tile starts a looping preview clip,
single reused secondary mpv instance decoding at reduced resolution so cost
stays flat regardless of grid size.

**Configurable, not hardcoded** — three knobs live in Settings:
- **Hover delay** before the preview starts (default ~600–800ms).
- **Preview duration** — how long the loop window runs before holding/
  restarting (default ~20–30s).
- **`preview_start` per title** — the timestamp the preview clip begins at.
  Defaults to just after the marked Intro, or 15% into runtime if no Intro
  is marked, but is **directly overridable per title**: from the Detail View
  or while watching in Player View, "Set as Preview Start" captures the
  current playback position and saves it — same mouse-driven pattern as
  segment marking (Section 5.3), just a single point instead of a range.

Rest unchanged: short crossfade (~150–200ms) on audio/video when switching
hovered tiles, only one preview audible at a time, built after core Home/
Detail UI (M5) is solid.

---

## 7a. Search — Local + "Request This Title"

Search is mouse-first (see Section 7c) and does two things at once: finds
what's already in your library, and tells you clearly when something isn't
there — with a way to flag it for later instead of a dead end.

- **On-screen keyboard**: clicking the search icon opens a search field plus
  a full clickable QWERTY overlay — typing on a physical keyboard still
  works if one's attached, but nothing in Search requires it.
- **Local fuzzy search**: as you type, results rank against cached library
  metadata (title, alt titles) using a lightweight fuzzy/typo-tolerant
  match (simple edit-distance + substring scoring — no heavy search
  dependency needed at this library scale), so near-misses and partial
  words still surface the right title.
- **If it's not in your library**: LocalStream also checks TMDB for a match
  on what you typed.
  - **TMDB has it, you don't**: shows the real poster + title with a clear
    "Not in your library" tag and a **Request** button.
  - **Request** stores it in a local Requested list (title, poster, TMDB id,
    requested date) — reviewable anytime from a "Requests" screen — so you
    know what to go add to the Movies/TV Shows/Anime folder later.
  - **Nothing found anywhere** (not local, not on TMDB): plain "We don't
    have this" message, no poster/request option since there's nothing to
    request against.
- **Auto-resolution**: once a requested title's file actually shows up in
  the scanned folders, the scanner's normal metadata match links it, it's
  removed from the Requested list automatically, and it enters the library
  flagged as newly added (Section 7b) — no manual bookkeeping needed.

---

## 7b. New / Recently Added

Netflix-style "just added" surfacing, driven by the filesystem, not a
manual flag:

- Every `Title`/`Episode` gets an `added_at` timestamp captured **the first
  time the scanner sees that file's path** — sourced from the file's OS
  "date modified," which is reliable for this purpose since files are
  dropped into the library folders once and not touched again.
- A small **"NEW"** badge shows on the poster tile for a configurable window
  after `added_at` (default 14 days).
- A **"Recently Added"** shelf on Home, sorted by `added_at` descending,
  same shape as the Continue Watching / Movies / TV Shows / Anime shelves.
- Applies at the **episode level** too — a new episode dropped into an
  existing show's folder flags that specific episode as new (and gives the
  show's Detail View a "New Episode" indicator), even though the show
  itself isn't a new addition to the library.
- No separate tracking system needed: this reuses the same path+mtime diff
  the scanner already does on every launch (Section 3) — `added_at` is just
  the timestamp captured the first time a given path is seen.

---

## 7c. Mouse-Only Operability

Every feature in this spec must be fully usable with a mouse alone — the
keyboard shortcuts in Section 8 are power-user accelerators, never the only
way to do something:

- Every shortcut in Section 8 has a clickable on-screen equivalent (visible
  buttons for play/pause/skip/track-cycling/next-episode, draggable sliders
  for volume/brightness/scrub bar, clickable menus for audio/subtitle
  track selection).
- Any text entry anywhere in the app — search, custom segment labels,
  profile names — is achievable through an on-screen virtual keyboard
  (Section 7a), so a physical keyboard is never required to fully use the
  app.
- Segment marking (Section 5.3) is already mouse-native (drag a range on
  the scrub bar); profile switching, folder setup, and Settings are all
  plain point-and-click.

---

## 7d. File Info Panel

A technical-details view (VLC's "Media Information" equivalent), accessible
from Detail View or Player View — read straight off the file itself, not
guessed or inferred:

- Filename (full, as it exists on disk).
- Container, resolution, video codec, duration.
- **Every audio track**: language (or "Unknown," Section 4c), codec,
  channel layout, which one is the file's default.
- **Every subtitle track**: language (or "Unknown"), format (SRT/ASS/PGS/
  etc.), which one is the file's default/forced.
- File size, path (useful for locating it manually, e.g. to fix a bad
  metadata match or check what's actually there before requesting a
  re-scan).

This reuses the same mpv `track-list` probe already run at scan time
(Section 4c) — no separate extraction pass, just a UI surface for data
that's already being read.

---

## 7e. Year-Based Categorization ("Classics" / Era Shelves)

A configurable browsing dimension on top of Movie/Show/Anime type — groups
titles by release year into user-defined eras (e.g. "Old but Gold" for pre-
1990, "2000s," "Modern"), shown as additional Home shelves.

- **Fully user-configured, not a hardcoded threshold** — in Settings, define
  one or more Era buckets, each with a label and a year range (start year,
  end year — end can be left open for "and newer"). Add, edit, remove,
  reorder freely.
- Each Title's existing `year` field (Section 2) slots it into whichever
  bucket its year falls into; a title outside all defined ranges simply
  doesn't appear in an Era shelf (no shelf breaks, no "uncategorized"
  clutter).
- Ships with no eras defined by default — this is opt-in configuration, not
  a shelf that appears uninvited. You set up whatever buckets make sense
  for your library ("Old but Gold," "Modern," or however you want to split
  it) the same way you'll configure hover-preview timing and segments —
  through Settings, at your own pace, not something LocalStream guesses at.

---

## 7f. Admin Mode

**Password-protected — distinct from the Profile no-PIN rule in Section 6.**
Profiles have no lock because they're pure personalization; Admin Mode is
different — it edits shared, app-wide configuration data every profile
sees, so it gets an actual password. Set on first use (Settings → "Go
Admin"), stored hashed locally, prompted every time before entry. This is
the one deliberate exception to the app's otherwise lock-free design.

Entry point: **Settings → Admin** — not scattered across Detail/Player
views implicitly, but a real destination you navigate to and unlock. From
inside Admin Mode, editing still happens contextually (open a title's
Detail View or play it to mark segments), but the mode itself is switched
on/off from Settings, gated by the password.

**Everything the app uses for viewer convenience lives here, all in one
place:**
- **Segments** — Intro, Recap, Outro, PostCredit, Custom (including your
  skip-specific-scene use case) — mark via drag-on-scrub-bar (Section 5.3)
  or live **Mark Start**/**Mark End** while watching, per title or per
  episode, with season-wide propagation (Section 5.4).
- **Hover preview** — where it starts (`preview_start`) and how long it
  runs, per title, plus the global hover-delay/duration defaults (Section 7).
- **Track info** — audio/subtitle languages are read from the file
  (Section 4c) and shown here for review; if a track's language flag is
  genuinely wrong or missing in the file itself, Admin Mode lets you set a
  **display-only override** (doesn't touch the file, just corrects what
  LocalStream shows/uses for that track going forward).
- **Metadata** — display name, synopsis, year, genre, Movie-vs-Anime
  classification override (Section 4b), Era bucket override (Section 7e).
- **Fix Match** — manually re-search and re-link TMDB if the automatic
  match picked the wrong title.
- **Test Skip** — jump to just before any marked segment and trigger its
  skip button exactly as it'd appear live, to confirm it lands correctly
  (Skip Outro's post-credit landing, Custom's red-zone snap) without
  sitting through the segment for real every time you tweak a boundary.

**TMDB usage stays cheap and deliberate.** Fix Match only calls TMDB when
you explicitly search — never automatically, never re-polled in the
background — and whatever you confirm is cached indefinitely, same as the
initial scan-time matching (Section 4). Admin Mode gives you full control
over metadata without turning every edit into a network round-trip.

All edits write to the same `Title`/`Episode`/`Segment` records used
everywhere else in the app (Section 2) — Admin Mode is a UI + access-control
layer over existing data, not a separate system to keep in sync.

---

## 8. Player Controls (consolidated)

| Action | Shortcut | Mouse Equivalent |
|---|---|---|
| Play/Pause | Space | Click the play/pause button |
| Seek ±5s | ←/→ (hold to repeat) | Click ±5s buttons on the OSD |
| Big seek ±10s | J/L | Click ±10s buttons on the OSD |
| Exact seek to position | — | Click/drag scrub bar (red-zone corrected, Section 5.5) |
| Volume | ↑/↓ | Drag the volume slider |
| Brightness | Shift+↑/Shift+↓ | Drag the brightness slider |
| Cycle audio track | A | Click the audio menu, pick a language (or "Unknown") |
| Cycle subtitle track | S | Click the subtitle menu, pick a language (or "Unknown"/"Off") |
| Subtitle delay/sync ±50ms | [ / ] | Click ±50ms buttons in the subtitle menu |
| Skip Intro/Recap/Outro | shortcut shown on-screen | Click the on-screen Skip button (auto-fire rules: Section 5.1, Section 5.2) |
| Custom segment skip | — | Always automatic, corner toast, no button/shortcut at all |
| Next Episode | N | Click the Next Episode countdown card (Section 5.2) |
| Back to Detail View | Esc / Backspace | Click the on-screen Back button (position saved) |

Brightness via mpv's video-equalizer `brightness` property (-100 to 100) —
no custom shader work, same libmpv reliability guarantee as everything else.

---

## 9. Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                             LocalStream.exe                             │
│                                                                           │
│  Window/Input   UI Renderer   Library Scanner   Metadata Engine          │
│                      │              │                  │                 │
│                      │        Profile Manager (Section 6)      │                │
│                      │        Recommendation Engine      │                │
│                      │        (Section 4d, in-memory scoring)    │               │
│                      │        Search Engine + Requests    │               │
│                      │        (Section 7a, local fuzzy + TMDB)    │              │
│                      │              │                  │                 │
│               ┌──────▼──────────────▼──────────────────▼──────┐         │
│               │            App State / Router                  │        │
│               │  Profile Picker ⇄ Home ⇄ Detail ⇄ Player        │        │
│               └──────┬───────────────────────────┬─────────────┘        │
│                       │                            │                     │
│                Player Module                 Preview Player (Section 7)         │
│           (libmpv render API, segment         (secondary lightweight     │
│            engine + red-zone seek guard,        mpv instance, audible)   │
│            global Skip Mode, brightness)                                 │
└────────────────────────────────────────────────────────────────────────┘
        │                          │                        │
        ▼                          ▼                        ▼
 %APPDATA%/LocalStream/    Movie/Show/Anime folders     TMDB (metadata,
 profiles.json, library    on disk (read-only)          first fetch only)
 .json, config.json         (skip_mode lives here),
 image cache
```

---

## 10. Tech Stack

Ported to Python in v6 (previously C++ in v3–v5): Python 3.12+, `pyproject.toml`
+ pip/venv, `glfw` (GLFW bindings), `PyOpenGL` + `PyOpenGL-accelerate`
(OpenGL 3.3 core), `python-mpv` (libmpv render API, video-equalizer for
brightness — same underlying libmpv, just via Python bindings), `freetype-py`
+ `Pillow` (glyph rasterization + image decode, replacing stb_truetype/
stb_image), stdlib `json` (replacing nlohmann/json), `requests` (TMDB,
replacing WinHTTP/libcurl), PyInstaller (packages to LocalStream.exe),
Task Scheduler autostart (unchanged).

**Performance note**: Python + PyOpenGL carries real per-call overhead vs.
C++. To hit the perf goals already in M8/M10 (hover-preview, 1000+ title
grids) — batch draw calls (persistent VBOs updated per-frame, not
immediate-mode-style calls), use one texture atlas per poster shelf instead
of one texture per draw, and lean on `PyOpenGL-accelerate`'s numpy-backed
paths. If a specific view still can't hit target frame time after that, it's
the one component worth dropping to a small C extension — everything else
should be fine in pure Python.

---

## 11. Project Structure

```
LocalStream/
├── pyproject.toml
├── LocalStream.spec              (PyInstaller — packages to LocalStream.exe)
├── src/
│   ├── main.py
│   ├── window/
│   ├── ui/            (renderer, Profile/Home/Detail/Player/Settings views)
│   ├── library/         (scanner, filename parsing, content model)
│   ├── metadata/          (TMDB client, matcher, image cache)
│   ├── profiles/            (profile CRUD, watch_state, preferences)
│   ├── recommendations/       (in-memory scoring, TMDB similar/recs cache)
│   ├── search/                  (local fuzzy search, TMDB fallback lookup,
│   │                              requests.json CRUD, on-screen keyboard)
│   ├── player/                (python-mpv wrapper, segment engine + red-zone
│   │                            seek guard, global Skip Mode, brightness,
│   │                            series auto-advance)
│   ├── preview/                 (hover-preview secondary player)
│   ├── config/                    (JSON load/save, paths, skip_mode, era
│   │                                buckets, preview timing settings,
│   │                                admin password hash)
│   └── autostart/                  (Task Scheduler registration)
├── assets/
│   └── fonts/, icons/, fallback-art/
└── docs/
    └── PROJECT_SPEC.md   (this file)
```

---

## 12. Build Milestones

1. **M1 — Skeleton**: window + GL context + custom UI loop, fullscreen toggle.
2. **M2 — Playback core**: libmpv embed, exact seek, FF/RW speed-cycle,
   audio/subtitle track cycling, brightness/volume control.
3. **M3 — Library + parsing**: scan Movies/TV Shows/Anime roots (three
   separate top-level folders, Section 3), season/episode/absolute-number parsing,
   MKV track extraction (language + default flags, Section 4c).
4. **M4 — Metadata + classification**: TMDB matching, poster/backdrop/
   synopsis/language fetch + cache, offline fallback art, anime-movie
   classification (Section 4b) once metadata lands.
5. **M5 — Home + Detail UI**: shelves (incl. Language browse, Section 4c,
   and Recently Added, Section 7b), poster tiles with progress + NEW badge,
   backdrop detail page, season/episode list.
5a. **M5a — Recommendations**: "More Like This" (TMDB similar/recs, cached
    + filtered to local library), "Because you watched X" in-memory scoring
    pass, wired into Home below Continue Watching (Section 4d).
5b. **M5b — Search + Requests**: on-screen keyboard, local fuzzy search,
    TMDB fallback lookup for "not in your library" results, Request button
    + Requests screen, auto-resolution when a requested file later appears
    in the scan (Section 7a).
5c. **M5c — File Info + Era Shelves + Preview Config**: File Info panel
    (Section 7d), user-configurable Era buckets and shelves (Section 7e),
    Settings UI for hover-preview delay/duration + per-title "Set as
    Preview Start" (Section 7).
6. **M6 — Segment engine + series flow**: Intro/Outro skip buttons with
   global Skip Mode (countdown vs manual), Recap and season-first-episode
   Intro as always-manual exceptions (Section 5.1), Custom hard-auto-skip +
   red-zone seek guard (Section 5.5), Next Episode under the same Skip Mode,
   Continue Watching wiring.
6a. **M6a — Admin Mode**: password setup/gate, Settings→Admin entry point,
    metadata edit fields + Fix Match search + track-language override,
    in-player Mark Start/Mark End segment editor, hover-preview per-title
    override, Test Skip flow (Section 7f).
7. **M7 — Profiles**: picker screen, per-profile watch_state/preferences,
   profile switch flow (no PIN).
8. **M8 — Hover Preview**: secondary mpv instance, preview_start defaulting,
   perf profiling against a large grid.
9. **M9 — Config + Autostart**: first-run setup (folders + TMDB key),
   Settings screen (Skip Mode toggle among others), Task Scheduler,
   `--fullscreen` boot end-to-end.
10. **M10 — Polish**: transitions, error states, segment-marking editor UX
    pass, mouse-only operability audit (every Section 8 shortcut has a
    working clickable equivalent, Section 7c), perf pass at 1000+ title
    libraries.

---

## 13. Open Decisions — All Resolved

- ~~**TMDB API key**~~ — confirmed, you're generating one at themoviedb.org.
- ~~**Folder convention**~~ — confirmed, Section 3: three separate top-level roots
  (Movies, TV Shows, Anime). Anime movies stay in Movies, classified via
  metadata (Section 4b).
- ~~**Folder picker**~~ — `tkinter.filedialog.askdirectory()`, ships with
  Python stdlib, native system dialog, no extra dependency.
- ~~**Multi-language audio/subtitles**~~ — confirmed generic, not limited to
  a dub/sub pair; any number of embedded audio/subtitle tracks in any
  language (including Tamil, alongside Japanese/English/etc.) supported per
  Section 4c and Section 4a.
- ~~**Default Skip Mode**~~ — Automatic.
- ~~**Default countdown duration**~~ — 6 seconds.

Spec is complete — nothing blocking M1 anymore.
