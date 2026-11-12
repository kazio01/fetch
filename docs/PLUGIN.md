# Fetch VST3 plugin

Fetch inside your DAW: search the library, preview samples **through the DAW** in sync with the
project, and drag them into the arrangement already matched to the project's tempo and key.

The plugin is a VST3 **instrument** (put it on an instrument track; its output is the preview).
It is a client of the Fetch search service: the library, the index and the search engine are the
same as in the Fetch window.

## Host sync

| Feature | What it does |
|---|---|
| **Sync to host tempo** | Loops with a known tempo are time-stretched to the host tempo. A 70 BPM loop in a 140 BPM project is treated as 140 (half/double), so the stretch stays small. While the transport runs, the loop is **phase-locked** to the host's beat position (it plays as if it had started at bar 1), so it lines up with the arrangement wherever you start it. Tempo changes are followed (re-rendered once the tempo is stable for 0.3 s). |
| **Project key** | Tonal samples with a known key are transposed to the project key, the shortest way (at most 6 semitones). Minor samples in a major project go to the relative minor and vice versa (an Am loop in C is not shifted). VST3 hosts don't report the key, so you pick it in the plugin. The Key column shows the shift (`Dm  +2`). |
| **Only host tempo** | Filters results to the host tempo: `bpm:<host>` (±3 %, half/double). Re-runs when the host tempo changes. |
| **Only matching keys** | Filters results to the project key and its Camelot neighbours (`key:Am~`). |
| **Drag matched audio** | Dragging rows into the DAW drops a copy rendered at the host tempo and project key (24-bit WAV in `Documents/Fetch Collections/Rendered`, never deleted automatically because the project refers to it). Off: the original file. Samples that need no change are always dropped as originals. |

Stretching and transposing use [Signalsmith Stretch](https://github.com/Signalsmith-Audio/signalsmith-stretch)
(offline "exact" mode: a 4-beat loop is exactly 4 beats at the host tempo). Loops are rendered twice in a row and
the second pass is kept, so the loop point is seamless.

## Using it

The plugin window is laid out like the Fetch window:

- **Sidebar**: *Collections* (All samples, Liked Samples and your other folders in `Documents/Fetch Collections`),
  *Host sync* (the switches above, the project key and the host's tempo/transport), *Filters* (Type, Instrument tree,
  Genre, Sound, Tempo range; collapsible, with a summary of what is chosen and one *Clear*), and *Recent searches*
  (shared with the Fetch window).
- **Search** in the header bar: same syntax as everywhere in Fetch (`dark kick`, `warm pad +loop`, `bpm:120`,
  `key:Am~`, `-snare`). Results update as you type; Enter (or choosing a result) saves the search to Recent searches.
  ↓ in the field moves into the results.
- **List**: heart column (click = like/unlike), Name, Instrument, Genre, Key (with the transposition to the project
  key), BPM, Length, Type, Pack. Click a header to sort; the shuffle button gives a random order.
- **Player bar**: play/pause, position (click or drag to seek a free-running preview), time, play-on-select, volume.
- **Keys**: ↑/↓ browse and play, Space pause/resume, Enter play from the start, ←/→ jump 10 %, L like,
  S similar sounds, E edit tags, / or Ctrl+F search, Esc stop. Right-click: Like / Unlike, Similar sounds,
  Edit tags, Show in folder, Copy path.
- **Transport**: with *Sync to host tempo* on, the preview follows the host: Play starts the current sample
  (loops in phase with the arrangement, one-shots from their start), Stop stops it. While the host is stopped,
  samples preview freely.
- **Look**: light or dark like the system, with GNOME's accent colour (Windows: the app mode setting).
- Drag one or more rows into the arrangement or a sampler.
- *Preview volume* is an automatable parameter. All settings (filters, view, columns, sort) are saved with the project.
  The last-used settings (filters, open sections, Host sync switches, Play on select, columns, the last search)
  also carry over to new instances and win over a project's saved ones, as in the Fetch window; only the project
  key stays per project (`~/.config/samplehaku/plugin.json`; Windows `%APPDATA%\Fetch\plugin.json`).
- Loops play as soon as their first part is stretched; the rest renders while it plays.

## Building and installing (Linux)

Needs CMake ≥ 3.24, a C++20 compiler and the usual X11/ALSA/freetype headers (present on a normal
CachyOS/Arch desktop). JUCE 8 and Signalsmith Stretch are downloaded by CMake at pinned versions.

```bash
bash plugin/build.sh          # build (Release) and copy to ~/.vst3/Fetch.vst3
bash plugin/build.sh --test   # also run the offline tests
```

Rescan plugins in the DAW (Bitwig: *Settings › Locations › Rescan*). The search service starts by itself
when the plugin needs it (D-Bus activation, like the Fetch window), and it now also listens on
`http://127.0.0.1:47811/v1/` for the plugin.

## Windows

The plugin builds for Windows from the same sources (MSVC, static runtime: one self-contained `Fetch.vst3`).
The GitHub Actions workflow `.github/workflows/plugin.yml` builds and tests Linux and Windows and uploads both
`Fetch.vst3` bundles as artifacts. Locally, in a *Developer PowerShell for VS 2022*:

```powershell
cmake -S plugin -B plugin/build
cmake --build plugin/build --config Release
# copy plugin\build\Fetch_artefacts\Release\VST3\Fetch.vst3 to C:\Program Files\Common Files\VST3\
```

On Windows there is no D-Bus or GNOME, so the search service runs as a plain HTTP server:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\pip install transformers librosa soundfile numpy
.venv\Scripts\python samplehaku.py index D:\Samples      # index the library (once, takes a while)
.venv\Scripts\python fetchhttp.py                         # the service for the plugin
```

To let the plugin start the service itself, put the command on one line in
`%APPDATA%\Fetch\server-command.txt`, e.g.
`C:\fetch\.venv\Scripts\pythonw.exe C:\fetch\fetchhttp.py` (or set the `FETCH_SERVER_CMD` environment variable).
ffmpeg on `PATH` is needed for MP3/M4A files. NPU sound features (`samplehaku-piirteet`) are Linux-only; without
them search still works, only the brightness/tail/tempo-from-audio estimates are missing.

**Status:** the Windows plugin build is set up but has only been compiled in CI, not tried in a Windows DAW; the
Python service on Windows has not been tested.

## The HTTP API

`fetchhttp.py`, bound to 127.0.0.1 only. `POST /v1/<request>` with a JSON body; port `SAMPLEHAKU_HTTP_PORT`
(default 47811, `0` turns it off). Requests must carry an `X-Fetch-Client` header and no `Origin` header,
so web pages in a browser can't use it.

| Request | Body | Answer |
|---|---|---|
| `ping` | `{}` | `{ok, version, ready, error}` |
| `browse` | `{text, filters, offset, limit, order}` | `{total, paths, details}` (same as D-Bus `Browse`) |
| `similar` | `{path, conds: ["timbre","key","bpm"], n}` | `{paths, details, skipped}` |
| `details` | `{paths}` | `{path: details}` |
| `catalog` | `{}` | filter choices: types, sound tags, instrument tree, genres (with counts), collections |
| `folder` | `{path, filters, order}` | a collection folder, filtered like `browse` |
| `like` | `{paths, on}` | `{path: liked}` (`on` missing = toggle) |
| `recent` | `{add}` | `{recent: [...]}` (shared with the Fetch window's Recent searches) |
| `settags` | `{paths, tags}` | Edit tags: `{type?, inst?, tags?}`, `null` = back to automatic; answers `{path: details}` |

Every details entry carries `liked`.

## Tests

`build/FetchTests_artefacts/Release/FetchTests [path/to/Fetch.vst3]`:
key math, tempo plans, rendering (a 100 BPM click loop rendered at 120 BPM lands on the 120 BPM grid; transposition
and resampling keep the right pitch), host sync through the processor with a fake play head (a loop started at
ppq 9.5 plays its beats on host beats; still on the beat after a 120 → 90 BPM change), and the built bundle loaded
through JUCE's VST3 hosting (instantiate, process, state, editor).
`FETCH_TEST_SERVICE=1` adds checks against the running service and the real library;
`FETCH_SNAPSHOT=ui.png` renders the editor with real results to an image.
The service's HTTP API is tested in `testit/test_palvelu.py`.

## License note

JUCE 8 is licensed under AGPLv3 (or commercially). GPLv3 (Fetch) and AGPLv3 code may be combined
(GPLv3 §13); a distributed plugin binary is then also subject to the AGPLv3's terms.
