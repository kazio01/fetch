<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="fetch-mark-dark.png">
    <img src="fetch-mark.png" alt="Fetch logo" width="112">
  </picture>
</p>

# Fetch

Fetch is a search and sorting tool for existing sample library.
Describe a sound in plain English, filter by instrument, genre, key, tempo or timbre,
or search similar sounds, and drag the results into DAW.

Idea of Fetch is to give every sample a soft tag. 
That way if you have collected a bunch of random samples over the years(guilty) and some get dusty over the years. 
Fetch will give them a new life by subtly introducing them to the rotation with tags!
Ive had a pool of roughly 300k samples that ive used for fine tuning the detection.
Im very interested to hearing how it works with other systems.

It never modifies your sample files. All indexes, caches and settings live in your home directory.

> Fetch is built on Linux with GNOME and also runs as a VST3 plugin on Windows. The NPU part is optional.

## Features

- **Semantic search**: [CLAP](https://huggingface.co/laion/larger_clap_general) audio–text
  embeddings, so `dusty vinyl drum loop` finds sounds even when their file names don't say so.
  File and folder names are also used in ranking.
- **Similar sounds**: find samples that match a given sample by timbre, key (Camelot neighbours)
  and/or BPM (±3 %, plus half/double time).
- **Instruments, genres and types**: Comprehensive instrument tree (Drums › Hats › Open, Bass › 808 …),
  style tags, per-pack genres, loop / one-shot detection. Read from names and folders first and
  estimated from the audio otherwise (estimated values are shown with `~`).
- **Key and tempo**: read from file names (`124bpm`, `_Gm`, `(125, C#min)` …) or estimated from the audio.
- **Sound features**: brightness, bass, noisiness, attack and tail length, measured on the
  AMD XDNA NPU with [IRON / mlir-aie](https://github.com/Xilinx/mlir-aie) (CPU fallback).
- **GNOME integration**:
  - Fetch window (GTK 4 / libadwaita) with instant playback, filters, collections and drag-and-drop to the DAW
  - Search from the Super key (`fetch dark kick`)
  - Files (Nautilus) extension: *Similar sounds…* menu, and *Sound / Brightness / Tail / Folder* columns
- **VST3 plugin (Linux, Windows)**: search and preview inside the DAW. Loops are time-stretched
  to the host tempo and phase-locked to the transport, tonal samples transposed to the project key,
  and rows drag into the arrangement already matched. See [docs/PLUGIN.md](docs/PLUGIN.md).
- **Collections**: *Liked Samples* lives in `~/Documents/Fetch Collections/` as hard links, so you can
  point your DAW's browser at it for quick access. Hard links take no extra space.

## Search syntax

Works the same in the Fetch window, Super search and the command line.

| Example | Meaning |
|---|---|
| `warm rhodes chords` | free text (English), ranked by sound and name |
| `+dark +tight` | sound-feature tags (see [Sound-feature tags](#sound-feature-tags)) |
| `+loop`, `+oneshot` | sample type (every sample is one or the other) |
| `bpm:120`, `bpm:118-126` | tempo (±3 %, also half/double time) |
| `key:Am`, `key:A`, `key:Am~` | key; `~` adds compatible keys (Camelot neighbours) |
| `inst:drums/perc`, `tag:female`, `genre:house` | instrument, style tag, genre |
| `snare loop -kick` | `-word` excludes matches |

Instrument, genre, type and pack words in free text also act as filters. Other words only affect ranking.

## Requirements

- **Linux** x86_64: Arch / CachyOS, Debian 13 / Ubuntu, or Fedora. The Fetch window, Super search and the Files
  extension need GNOME-era libraries (libadwaita 1.6+: Debian 13, Ubuntu 25.04, Fedora 41, Arch); on older
  systems you get the search service, the command line and the VST3 plugin.
- **Windows** 10/11 x64: the VST3 plugin and the search service (no window).
- About 2–4 GB of disk for Python, PyTorch and the CLAP model (downloaded once by the installer).
- Optional: AMD Ryzen with an XDNA NPU (`amdxdna` driver, Linux ≥ 6.14, Arch) for the sound features.

## Installation

**Windows:** run `Fetch-Setup-<version>-windows-x64.exe`. It installs for your user (no administrator rights,
except one prompt to put the plugin in the standard VST3 folder), downloads Python, PyTorch and the model, and
adds Start menu entries (Index a sample folder, Repair, Uninstall). A portable zip is available too.

**Linux:** unpack `Fetch-<version>-linux-x64.tar.gz` and run the installer as your normal user (it asks for
sudo for the system packages only):

```bash
tar xzf Fetch-<version>-linux-x64.tar.gz && cd Fetch-<version>-linux-x64
bash install.sh                 # --no-desktop: service + plugin only; --npu: NPU sound features (Arch)
fetch-index ~/Samples           # analyse your library (again = only new or changed files)
```

Then open the Fetch window (GNOME: log out and back in once for Super search) or load the **Fetch** instrument
in your DAW. You can also add folders in Settings > Library folders. `bash install.sh --uninstall` removes
Fetch and keeps your index and settings.

From source (developers): `git clone https://github.com/kazio01/fetch.git && cd fetch && bash install.sh`
uses the checkout in place; build the plugin with `bash plugin/build.sh`. The older step-by-step scripts
(`asenna.sh`, `asenna_npu.sh`, `asenna_gnome.sh`; *asenna* = install) still work on Arch.

## Usage

```bash
samplehaku-results                      # open Fetch (also in the app menu)
samplehaku                              # interactive terminal search (? = help)
samplehaku search "kick +dark" --key Am --bpm 124
samplehaku similar ~/Samples/kick_07.wav -t tight
samplehaku stats
```

**Fetch window keys:** **↑/↓** play, **Space** play/stop, **←/→** seek 10 %, **L** like,
**S** similar, **E** edit tags, **/** or **Ctrl+F** search, **Ctrl+A** select all.
Rows can be dragged into a DAW; the DAW receives the original file.

**Super key:** type `fetch` (or `sample`) followed by a search, e.g. `fetch dark kick`.
A search containing a `+tag` or a filter works without the prefix. Clicking a result opens Fetch
with the whole search and that sample selected.

**Files:** right-click an audio file › **Similar sounds…** to pick Timbre / Key / BPM.
In list view, right-click the column header to add the **Sound**, **Brightness**, **Tail** and
**Folder** columns; they work in any folder, including result folders.

Re-run `samplehaku index …` and `samplehaku-piirteet` after adding samples. Only new or changed files are processed.

## Sound-feature tags

Tags are your own names for measured values, not trained models. Each tag picks the top or bottom
share of *your* library for one feature, so `+dark` means the darkest third of your samples.

| Tag | Feature |
|---|---|
| `+dark` / `+bright` | spectral centroid low / high |
| `+bass` | lots of energy below 150 Hz |
| `+noisy` / `+tonal` | noise-like / pitched |
| `+punchy` / `+soft` | fast / slow attack |
| `+tight` / `+long` | short / long tail (peak to −40 dB) |

Edit `~/.config/samplehaku/tagit.json` to rename, remove or add tags; changes apply immediately:

```json
{"names": ["boomy", "warm"], "feature": "low", "direction": 1}
```

`share` sets how large a part of the library each tag covers (0.33 = a third).

## How the NPU is used

On Hawk Point (XDNA1 / `npu1`) AMD's Ryzen AI software is not supported on Linux, so the NPU is
programmed directly with the open IRON toolchain. The frequency transform (DFT) is written as a matrix
multiplication: frames of 1024 samples × a 1024 × 1024 DFT matrix with the window function baked in,
run as IRON's `whole_array` GEMM (bf16 in, f32 out) on all 16 AI Engine cores.

The NPU computes a 4096-frame spectrum in about 13 ms, compared with 24 ms on the CPU, and the results
match the CPU within 0.23 %. On real files, decoding dominates, so the main benefit is a free CPU and
lower power use. `samplehaku-piirteet --selftest` measures both on your machine.

```bash
samplehaku-piirteet --selftest       # NPU vs. CPU: accuracy and speed
samplehaku-piirteet --backend cpu    # without the NPU
samplehaku-piirteet --show kick.wav  # one file's values
```

**Troubleshooting**

- **`/dev/accel/accel0` is missing:** check that the NPU (IPU) is enabled in the BIOS; `sudo dmesg | grep -i amdxdna`.
- **XRT packages not found:** they are in Arch's `extra-testing` repository; the installer can also build them from source.
- **Permission denied / memlock:** log out and back in (render group and memlock limit).
- **NPU test fails:** `samplehaku-piirteet --backend cpu` always works.

## Customising

All settings are in `~/.config/samplehaku/`:

| File | Purpose |
|---|---|
| `tagit.json` | sound-feature tags (see above) |
| `korjaukset.json` | your own instrument/tag corrections (*Edit tags…* in the window) |
| `genret.json` | per-pack genres you have confirmed (*Pack genres…* in the window) |
| `similar.json`, `ikkuna.json` | remembered window and Similar settings |

The search service runs in the background and stops after 10 idle minutes; change
`SAMPLEHAKU_IDLE_MIN` (and the Super-search prefixes, `SAMPLEHAKU_PREFIX`) in
`~/.config/systemd/user/samplehaku-haku.service`. Log: `journalctl --user -u samplehaku-haku -f`.

## Tests

```bash
testit/aja.sh                    # all tests + performance
testit/aja.sh tests              # tests only
testit/aja.sh performance        # performance only (compares with the previous run)
```

The tests run against your own indexed library and installation. Checks that need content your library
doesn't have are skipped with a warning. Settings files are backed up and restored around the run.

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the architecture.

## License

Fetch is free software under the [GNU General Public License v3.0](LICENSE).
