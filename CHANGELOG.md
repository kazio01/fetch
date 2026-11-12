# Changelog

## 0.5.0

- Installers. Windows: `Fetch-Setup-<version>-windows-x64.exe` (Inno Setup, per user, Start menu entries,
  optional VST3 copy with one administrator prompt, uninstaller that asks before deleting the index).
  Linux: `install.sh` in the release package `Fetch-<version>-linux-x64.tar.gz` for Arch, Debian/Ubuntu and
  Fedora (hash-locked Python packages, pinned uv, the plugin into ~/.vst3, the GNOME parts when available,
  `fetch-index` command). Both are built and install-tested in CI.
- Less memory: the search service takes ~1.3 GB instead of 2–2.5 GB for 200k samples. Audio and name vectors
  are kept as float16 and scored with torch (faster than before), the text model runs in bfloat16 from a
  text-only copy made once (`<data>/clap-text-bf16`), tag sets are shared and caches are bounded. Measured:
  Similar top-100 99.6 % the same, text search top-10 98.5 %, instruments differ for 10 of 200k samples.
- Settings > Library folders: each folder can be removed from the library (window and plugin). Only the index
  forgets it: files on disk, likes and tag corrections stay. Not the last folder, nor one being analysed.
- Packs are found from the folder tree (`pakkaukset.py`): a library can have vendor, genre or "misc" folders
  above its packs. Genres come from the real pack's name or the folder above it ("TECH HOUSE/<pack>"), and the
  genre list shows packs by their path.
- Instrument names: names of packs and vendors are titles – their instrument word is used only where the
  sound agrees and the named files below don't contradict it ("Dark Bass 2", "Techno Machine" no longer label
  everything inside). Fixed: "Bass Drum" was a bass, "Drum Kit" folders were full drum loops, "Kit Loop … Lead"
  a drum loop, "Urban"/"Industrial" a city ambience, "E-Piano" a piano. New: "Kickz"/"Snarez", "808's" (by
  itself an 808 is a bass), "oh07", "36kick2", folders called "BD"/"SD", "SNAREROLL11", "guitarlicks", generic
  "Drums" (refined to Kicks/Snares… by the sound when clear), folders that list several things
  ("Claps & Snares" = drums). Measured with pack series held out: sound-only guesses answered 65.3 % (was 63.2),
  main class right 90.4 % (89.8); tempo from audio shown for 83.4 % (82.1), right 93.7 % (93.2).
- Samples on a network share: the service no longer probes the share per sample (Similar 1.4 s -> 0.35 s), the
  window doesn't stat files on its main thread (select -> sound 143 -> 24 ms), the window and the plugin read
  the next rows ahead while one plays, and the plugin starts playing unchanged samples while they are still
  being read.
- Fixed: an empty index left every search waiting for ever; settings files (corrections, genres, window) are
  written atomically; two index reloads could start at once; data of replaced indexes stayed in memory; a type
  or tag correction made the next service start recompute all instruments (1.5 min for 200k samples); a pasted
  paragraph as a search scanned the library for minutes; a possible use of a freed preview in the plugin when
  the host pauses processing; a negative request length.
- Key from the audio: a small network trained on the library's named keys (shipped as `savellaji_malli.npz`,
  retrain with `testit/savellaji_kehitys.py train`) replaces the learned profiles. Measured on 24.7k named files,
  5-fold by pack series: at the same coverage (~35–40 %) 76 % right instead of 56 %, 90 % right or harmonically
  compatible. Drums and percussion no longer get a key from the audio. Known limit: the library is 92 % minor,
  so a major sample is shown as its relative minor (same notes, same Camelot slot).
- Fixed: the index cache saved values with user corrections applied, so an undone type correction stayed.
- Plugin: dragging a sample into REAPER did nothing for paths with spaces (the drag sent unescaped `file://`
  paths, REAPER cut them at the first space). Drags now send escaped URIs on Linux.
- Plugin tempo sync: half / double tempo only when it lands within ~25 % of the host. A 174 BPM break in a 120 project
  is slowed down beat for beat instead of played double-time as 87.
- Player options (the old Play on select button, window and plugin): Play on select, Loop preview (off: a loop plays
  once). In the plugin also Loop length ÷2 / ×2 / Auto for the playing loop, remembered per sample (also for drags).
- Loop or one-shot from the sound: samples whose name and folder don't say were all loops; a model on the CLAP
  vector and sound features (shipped as `tyyppi_malli.npz`, `testit/tyyppi_kehitys.py`) now makes the ones it is
  sure about one-shots (13.5k of 26k: notes, chords, FX, cymbals, 808s, shouts). "LoopKit03" and "…_LPS" folders
  count as loops. The index cache now also follows the model files.
- Fewer single hits shown as loops: pack titles like "… - Samples and Loops", "Breakbeats-Loops-and-Drum-Cuts"
  and "Prime Loops" no longer make everything inside a loop, "Bloop"/"Kloop" are not loops, and a "loop" shorter
  than 0.75 s that sounds like a hit is a one-shot. Vocals: under 1.5 s (chants, shouts, single words) one-shot
  even in a loop folder, 2 s or longer a loop unless the name says shot. Against 2.9k hand-made type
  corrections the automatic type now agrees 98 % (was 77 %).
- `testit/rasitus.py`: fuzz and load test of the service. Accuracy tools work with several library folders.

## 0.4.0

- Folders: a folder tree under All samples (Fetch window and plugin) opens the library folder by folder; a folder
  shows all its samples (any depth) page by page with the usual filters and sorting. Loaded one level at a time
  (service: D-Bus `Folders`, HTTP `folders`, Browse/`browse` take `folder`), fast also for 360k samples.
- Settings (the gear in the sidebar header, which now also shows the version): UI scaling and Font size sliders
  (75–150 %, shared by the window and the plugin in `~/.config/samplehaku/appearance.json`; the plugin scales
  its whole editor), and Library folders with Add folder…: the folder is analysed in the background by its own
  process (`indeksoija.py`; CLAP on an NVIDIA/ROCm GPU if PyTorch sees one, sound features on the NPU if it
  answers, otherwise the CPU), with progress in Settings. Service: `AddFolder`, `IndexStatus` (HTTP `addfolder`,
  `indexstatus`).
- Windows: portable package (`windows/`, CUDA or CPU), several library roots in one index, `samplehaku merge`
  (bring in an index made on another computer) and `relocate`, content hashes and `--reuse` (copy the analysis
  of identical files), faster indexing (decoding and mel spectrograms in worker processes), ffmpeg fallback
  decoder, background index reload in the service, versioned cache files, the plugin waits while the library loads.
- VST3 plugin (`plugin/`, JUCE 8): search and preview inside the DAW with host sync – tempo-synced,
  phase-locked loop preview that follows tempo changes, key matching to a chosen project key, filters for
  the host tempo / project key, and drag-and-drop of tempo/key-matched renders. Linux build script
  (`plugin/build.sh`), Linux + Windows CI build (`.github/workflows/plugin.yml`), offline tests.
- Plugin UI laid out and styled like the Fetch window (libadwaita dark): sidebar with Collections, Host sync,
  collapsible Filters (type, instrument tree, genre, sound, tempo range) and Recent searches; heart column,
  sortable columns, shuffle, player bar with seek, context menu. Clicking the sidebar no longer takes the arrow
  keys from the results list; Host sync is a compact, collapsible group (between Tempo and Recent searches).
- Plugin follows the system light/dark scheme and GNOME accent colour (gsettings; the OS setting on Windows),
  with the Fetch window's measurements (40 px rows, chip grid, header bar). Search field fills the header bar.
- Plugin: Edit tags… (right-click or E), same dialog as the Fetch window. In sync mode the preview follows the
  host transport (Play starts the current sample in phase, Stop stops it). Fixed: typing into the search field
  went to the list when the host gave the window focus after the click.
- Plugin: the sidebar layout (open/closed sections) and the Host sync switches are remembered for new instances
  (`~/.config/samplehaku/plugin.json`, Windows `%APPDATA%\Fetch\plugin.json`). Down in the search field moves
  into the results, as in the Fetch window (it used to stay in the field). Type chips (Loop / One-shot) span the
  sidebar width in both the plugin and the window.
- Fetch window and plugin: the search field fills the header bar and starts where the title and the heart icons do.
- Plugin remembers the last-used settings like the Fetch window: filters, Host sync switches, open sections, Play on
  select, columns and the last search carry over to new instances and are put back when a host restores a project
  (the project key stays per project). Loops start playing right away while browsing (rendered in chunks;
  the first sound after a few milliseconds instead of up to a second for long loops at another sample rate).
- Sidebar header in both: the logo on a transparent background (black in the light scheme, white in the dark one:
  `fetch-mark.png`, `fetch-mark-dark.png`) with the name beside it, sized to fit the header so nothing scrolls under
  it, and a short fade at the bottom of the sidebar. The filter group names use the size of the sidebar titles.
- New app icon: white on black (`fetch-icon.png`); `fetch-icon-alt.png` (black on white) is an unused alternative.
- Sample type is now only Loop or One-shot: the "Other" type (`+other`, `+muu`, the Other filter chip) is gone.
  Samples whose name and length don't settle it count as loops; drones, ambiences, atmospheres, textures, vowels,
  tones, reverses, risers, sweeps and impacts count as one-shots unless the name says loop.
- Detection accuracy, measured with whole pack series held out (`testit/aja.sh accuracy`, `testit/tarkkuus.py`):
  - Tempo from the audio: candidate tempos from the loop length (whole beats), scored by the onset autocorrelation
    and decided between half/double by the library's named tempos of the same genre; only for samples named as
    loops and only when confident. 92.5 % right (was 57 %). New `features.onset_ac` column.
  - Key from the audio: pitch salience (spectral peaks credited to their fundamentals) against major/minor profiles
    learned from the library's named keys, shown for the most confident ~30 %: 64.5 % right + 19.5 % a compatible
    key (was 57 %). New `features.key_sal` column. Fixed: estimated major keys were stored as "root only".
    `aanipiirteet.py --onset` fills both columns for an existing library.
  - Instrument from the audio: stricter vote thresholds, main class 89 % right (was 82 %) for fewer samples.
  - File names: tempo + key in brackets "(170, A)", "120_BPM", "1254BPM" (= 125.4), tempos glued to a part word
    ("drm120", "pad125"), bare tempos in fills; track/sample numbers ("3.77-08", "Sax Loop 72 (128, Am)") are no
    longer read as tempos, "1 bar (B)" is no longer a key, keys keep the file name's spelling (D#m, not Ebm).
    Songstarter stems named "HH SS" are Hip Hop, not hi-hats; "cross stick" is a snare.
  - Every correction is logged with what was shown before (`~/.config/samplehaku/korjausloki.jsonl`).
  - Tools: spot check (`testit/tarkistus.py`, also `--blind`) and test benches for tempo, key and instruments.
- Search service: JSON API on 127.0.0.1:47811 (`fetchhttp.py`) for the plugin; runs standalone on systems
  without D-Bus (Windows). D-Bus is now an optional import. Requests: browse, similar, details, catalog,
  folder, like, recent, settags.

## 0.3.0 – first public release

- Fetch results window (GTK 4 / libadwaita): instant preview, filters (type, instrument, genre, sound, tempo),
  collections, recent searches, drag and drop to a DAW
- Semantic search with CLAP, plus filters for instrument, style tag, genre, key, tempo and loop / one-shot
- Similar sounds by timbre, key (Camelot neighbours) and/or BPM
- Sound features (brightness, bass, noisiness, attack, tail), key and tempo, measured on the AMD XDNA NPU
  via IRON / mlir-aie, with a CPU fallback
- GNOME integration: Super-key search provider, Files extension (Similar sounds… menu, Sound / Brightness /
  Tail / Folder columns)
- Liked Samples collection as hard links in `~/Documents/Fetch Collections/`
- Installers for Arch-based systems (`asenna.sh`, `asenna_npu.sh`, `asenna_gnome.sh`) and a test suite
  (`testit/aja.sh`), including an installation check
