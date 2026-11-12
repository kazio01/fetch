# Developing Fetch

## Architecture

```
samplehaku.py            Engine and CLI. CLAP embeddings (laion/larger_clap_general, CPU PyTorch),
                         SQLite index, search syntax, tempo/key from names, loop/one-shot rules.
aanipiirteet.py          Sound features: a DFT written as a matrix multiplication
                         (frames M×1024 @ windowed DFT matrix 1024×1024), run on the NPU with IRON's
                         whole_array GEMM (bf16 → f32), or on the CPU. Also tempo (onset autocorrelation)
                         and key (chromagram + Albrecht–Shanahan profiles).
soittimet.py             Instrument tree and style tags: rules from names and folders, stem families,
                         nearest-neighbour vote on CLAP audio vectors for the rest.
genret.py                Genres per pack (pack name, "By Genre" folders, user confirmations).
hakupalvelu.py           D-Bus service fi.samplehaku.Search (dbus-fast, runs in .venv):
                         org.gnome.Shell.SearchProvider2 + Browse / Query / Details / Info / Similar /
                         SetTags… / Packs / SetPackGenres. Loads only CLAP's text encoder; idles out.
samplehaku_tulokset.py   The Fetch window (GTK 4 + libadwaita, system Python, GStreamer playbin).
samplehaku_similar.py    The "Similar sounds…" dialog opened from Files.
samplehaku_nautilus.py   Files extension (nautilus-python 4.x): menu + columns, reads SQLite directly.
samplehaku_kokoelmat.py  Collections (Liked Samples) as hard links; standard library only.
tagit.json               Default sound-feature tags (copied to ~/.config/samplehaku/).
```

Two Python environments:

- `.venv` (Python 3.12, uv): torch (CPU), transformers, librosa, dbus-fast – engine and service.
- System Python: GTK 4, libadwaita, GStreamer, nautilus-python – the window, the dialog and the Files extension.
- Optional `~/.local/share/samplehaku/ironenv`: mlir_aie + llvm-aie (Peano) for the NPU, using the same Python
  version that pyxrt was built for.

Internal names (`samplehaku`, `fi.samplehaku.*`, the Finnish module and config file names) are kept for
compatibility with existing installations; the user-facing name is Fetch.

## Data locations

| Path | Contents |
|---|---|
| `~/.local/share/samplehaku/index.db` | SQLite index (tables `files`, `meta`, `features`) |
| `~/.local/share/samplehaku/indeksi-valimuisti/` | memory-mapped index cache (rebuilt when the DB changes) |
| `~/.cache/samplehaku/tulokset/` | Similar result folders (hard links, newest 20 kept) |
| `~/.cache/samplehaku/lyhyet/` | padded copies of very short samples (newest 500 kept) |
| `~/.config/samplehaku/` | user settings (tags, corrections, genres, window state) |
| `~/Documents/Fetch Collections/` | collections (`SAMPLEHAKU_COLLECTIONS` overrides) |

## Notes

- Never write to the user's sample files (tags, mtime): that would trigger a full re-index and
  break DAW analysis files (e.g. Ableton `.asd`).
- GNOME Shell skips a search provider whose `DesktopId` app is hidden (`NoDisplay=true`); the provider
  points at the visible Fetch entry.
- The Files extension is installed as a symlink into the project, and finds the project from the link target.
- PipeWire doesn't play extremely short streams; samples under 0.3 s are played from a copy padded with silence.
- The service's `Similar` returns a folder path or `ERROR: …` (older services: `VIRHE: …`); clients accept both.

## Tests

`testit/aja.sh` runs everything (`tests` or `performance` for one part):

1. `test_moottori.py` – search syntax, type/instrument/genre rules, index invariants (.venv)
2. `test_palvelu.py` – every D-Bus method, including the Super-search provider
3. `test_kokoelmat.py` – hard-link collections and the Files menu logic
4. `test_ikkuna.py` – a scripted session in the Fetch window (separate app id, temporary settings)
5. `test_asennus.py` – read-only installation check, from GNOME's and Files' point of view

Tests use the developer's own indexed library; checks that need specific content are skipped with a warning.
The test function names (`check`, `otsikko`, `yhteenveto`) are Finnish for historical reasons.
