"""
Fetch extension for GNOME Files (Nautilus, nautilus-python 4.x).

- List view columns "Sound", "Brightness", "Tail" and "Folder" (choose them: right-click a column
  header, or View > Visible Columns). The values come from the Fetch index.
- Audio file context menu: "Similar sounds…" (opens samplehaku_similar.py) and "Add to Liked".

Install: ~/.local/share/nautilus-python/extensions/  (needs the python-nautilus package)
This file runs inside Files with the system Python, so it reads the index directly from
SQLite and leaves heavy work to the search service (D-Bus).
"""
from __future__ import annotations

import json
import os
import shutil
import time
import sqlite3
import subprocess
from bisect import bisect_left
from pathlib import Path

from gi.repository import Gio, GLib, GObject, Nautilus

DATA_DIR = Path(os.environ.get("SAMPLEHAKU_DIR", Path.home() / ".local/share/samplehaku"))
DB_PATH = DATA_DIR / "index.db"
TAGS_PATH = Path(os.environ.get("SAMPLEHAKU_TAGS", Path.home() / ".config/samplehaku/tagit.json"))
BUS_NAME = "fi.samplehaku.Search"
OBJ_PATH = "/fi/samplehaku/SearchProvider"
FEATURES = ("centroid", "low", "flat", "attack", "tail")
DEFAULT_TAGS = [(["dark", "tumma"], "centroid", -1), (["bright", "kirkas"], "centroid", 1),
                (["bass", "basso"], "low", 1), (["noisy", "kohina"], "flat", 1),
                (["tonal", "tonaalinen"], "flat", -1), (["punchy", "iskevä"], "attack", -1),
                (["soft", "pehmeä"], "attack", 1), (["tight", "lyhyt"], "tail", -1), (["long", "pitkä"], "tail", 1)]
# Menu: "Similar sounds…" opens a small window (samplehaku_similar.py) for choosing
# Timbre / Key / BPM. Nautilus menus can't have check boxes, and the menu closes
# on every click, so the choices are made in the window.
SIMILAR_CMD = os.environ.get("SAMPLEHAKU_SIMILAR", str(Path.home() / ".local/bin/samplehaku-similar"))
# Liked: hard links in the collections folder (samplehaku_kokoelmat.py, shared with the results window).
# The extension is a symlink into the project (asenna_gnome.sh), so the project folder is found
# from the link target. Older copy installs: SAMPLEHAKU_HOME or ~/.local/bin/samplehaku-similar.
import sys  # noqa: E402


def _project_dir() -> str:
    cands = [os.environ.get("SAMPLEHAKU_HOME", ""), str(Path(__file__).resolve().parent)]
    try:   # launcher: exec /usr/bin/python3 "<project>/samplehaku_similar.py" "$@"
        txt = Path(SIMILAR_CMD).read_text()
        cands.append(str(Path(txt.split('"')[1]).parent))
    except (OSError, IndexError):
        pass
    for d in cands:
        if d and (Path(d) / "samplehaku_kokoelmat.py").is_file():
            return d
    return cands[1]


sys.path.insert(0, _project_dir())
from samplehaku_kokoelmat import LIKED, original_path  # noqa: E402,F401
LIKED_EMBLEM = "samplehaku-liked"   # ~/.local/share/icons/hicolor/scalable/emblems/


def library_root(paths) -> str:
    """Common parent folder of the indexed files, e.g. ~/Samples."""
    paths = list(paths)
    if not paths:
        return ""
    try:
        root = os.path.commonpath(paths)
    except ValueError:
        return ""
    return os.path.dirname(root) if root in paths else root


def load_tags():
    try:
        cfg = json.loads(TAGS_PATH.read_text())
        defs = [(t.get("names", t.get("nimet")), t.get("feature", t.get("piirre")),
                 int(t.get("direction", t.get("suunta")))) for t in cfg.get("tags", cfg.get("tagit"))]
        return [d for d in defs if d[1] in FEATURES], float(cfg.get("share", cfg.get("osuus", 1 / 3)))
    except Exception:
        return DEFAULT_TAGS, 1 / 3


class Data:
    """Sound features from the index, in memory. Reloaded when the index or the tags change."""

    def __init__(self):
        self.stamp = None
        self.rows: dict[str, tuple] = {}
        self.sorted: dict[str, list[float]] = {}
        self.tags, self.share = DEFAULT_TAGS, 1 / 3
        self.checked = 0.0
        self.roots: list[str] = []
        self.manifests: dict[str, tuple[float, dict]] = {}

    def _stamp(self):
        out = []
        for p in (DB_PATH, TAGS_PATH):
            try:
                st = p.stat()
                out.append((st.st_mtime, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    def refresh(self) -> None:
        # while indexing, the database changes constantly: re-read at most every 30 s
        now = time.monotonic()
        if self.rows and now - self.checked < 30:
            return
        self.checked = now
        stamp = self._stamp()
        if stamp == self.stamp:
            return
        self.stamp = stamp
        self.tags, self.share = load_tags()
        self.rows, self.sorted = {}, {}
        if stamp[0] is None:
            return
        try:
            con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1)
            q = f"""SELECT f.path, {', '.join('f.' + c for c in FEATURES)} FROM features f
                    JOIN files x ON f.path = x.path AND f.mtime = x.mtime AND f.size = x.size
                    WHERE f.centroid IS NOT NULL"""
            for r in con.execute(q):
                self.rows[r[0]] = r[1:]
            con.close()
        except sqlite3.Error:
            return
        for j, c in enumerate(FEATURES):
            self.sorted[c] = sorted(v[j] for v in self.rows.values())
        try:
            con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1)
            row = con.execute("SELECT value FROM meta WHERE key='roots'").fetchone()
            roots = json.loads(row[0]) if row else []
            self.roots = roots or [library_root(r[0] for r in con.execute("SELECT path FROM files"))]
            con.close()
        except (sqlite3.Error, ValueError):
            pass

    def rel(self, feat: str, value: float) -> float:
        s = self.sorted.get(feat) or [value]
        return bisect_left(s, value) / max(len(s) - 1, 1)

    def original(self, path: str) -> str:
        """Hard link in a result folder -> the original sample (the service's .samplehaku.json)."""
        d, name = os.path.split(path)
        mf = os.path.join(d, ".samplehaku.json")
        try:
            mt = os.stat(mf).st_mtime
        except OSError:
            return os.path.realpath(path)
        cached = self.manifests.get(d)
        if not cached or cached[0] != mt:
            try:
                cached = (mt, json.loads(Path(mf).read_text()))
            except (OSError, ValueError):
                cached = (mt, {})
            self.manifests[d] = cached
        return cached[1].get(name, os.path.realpath(path))

    def folder(self, path: str) -> str:
        """'Samples / Pack Vol 5 / Instruments' – from the library root (common folder of everything indexed)."""
        self.refresh()
        orig = self.original(path)
        roots = [r for r in self.roots if r and orig.startswith(r + os.sep)]
        if not roots:
            return ""
        root = max(roots, key=len)     # several libraries: the deepest root that contains the file
        rel = os.path.dirname(orig[len(root) + 1:])
        return " / ".join([os.path.basename(root)] + (rel.split(os.sep) if rel else []))

    def get(self, path: str):
        self.refresh()
        return self.rows.get(self.original(path))

    def words(self, vals) -> str:
        out = []
        for names, feat, sign in self.tags:
            r = self.rel(feat, vals[FEATURES.index(feat)])
            if (r if sign > 0 else 1 - r) >= 1 - self.share:
                out.append(names[0])
        return ", ".join(out)


DATA = Data()


def notify(text: str) -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-i", "fi.samplehaku.Results", "Fetch", text])


class SamplehakuExtension(GObject.GObject, Nautilus.ColumnProvider, Nautilus.InfoProvider,
                          Nautilus.MenuProvider):

    # ------------------------------------------------------------ columns
    def get_columns(self):
        return [
            Nautilus.Column(name="Samplehaku::sointi", attribute="samplehaku_sointi",
                            label="Sound", description="Fetch sound tags"),
            Nautilus.Column(name="Samplehaku::kirkkaus", attribute="samplehaku_kirkkaus",
                            label="Brightness", description="Spectral centroid (Hz)"),
            Nautilus.Column(name="Samplehaku::hanta", attribute="samplehaku_hanta",
                            label="Tail", description="Time from peak to -40 dB (s)"),
            Nautilus.Column(name="Samplehaku::kansio", attribute="samplehaku_kansio",
                            label="Folder", description="Sample's folder in your library "
                            "(also for search results)"),
        ]

    def update_file_info(self, file):
        if file.get_uri_scheme() != "file" or file.is_directory():
            return
        path = file.get_location().get_path()
        if not path:
            return
        if (file.get_mime_type() or "").startswith("audio/") and LIKED.has(path):
            file.add_emblem(LIKED_EMBLEM)
        folder = DATA.folder(path)
        if folder:
            file.add_string_attribute("samplehaku_kansio", folder)
        vals = DATA.get(path)
        if not vals:
            return
        c, t = vals[0], vals[4]
        # spaces pad the width so that sorting also works on the text
        file.add_string_attribute("samplehaku_kirkkaus", f"{c:7.0f} Hz")
        file.add_string_attribute("samplehaku_hanta", f"{t:5.2f} s")
        file.add_string_attribute("samplehaku_sointi", DATA.words(vals))

    # ------------------------------------------------------------ menu
    def get_file_items(self, *args):
        files = [f for f in args[-1] if f.get_uri_scheme() == "file"
                 and (f.get_mime_type() or "").startswith("audio/") and f.get_location().get_path()]
        if not files or len(files) != len(args[-1]):
            return []
        items = []
        if len(files) == 1:
            item = Nautilus.MenuItem(name="Samplehaku::similar", label="Similar sounds…")
            item.connect("activate", self._open_similar, files[0].get_location().get_path())
            items.append(item)
        liked = [LIKED.has(f.get_location().get_path()) for f in files]
        many = f" ({len(files)})" if len(files) > 1 else ""
        if all(liked):
            item = Nautilus.MenuItem(name="Samplehaku::unlike", label=f"Remove from Liked{many}")
            item.connect("activate", self._like, files, False)
        else:
            item = Nautilus.MenuItem(name="Samplehaku::like", label=f"Add to Liked{many}")
            item.connect("activate", self._like, files, True)
        items.append(item)
        return items

    def _like(self, _item, files, add: bool) -> None:
        try:
            for f in files:
                (LIKED.add if add else LIKED.remove)(f.get_location().get_path())
        except OSError as e:
            notify(f"Liked: {e}")
        for f in files:
            f.invalidate_extension_info()      # the emblem updates immediately

    def _open_similar(self, _item, path: str) -> None:
        try:
            subprocess.Popen([SIMILAR_CMD, path], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            notify(f"Could not open the Similar window ({SIMILAR_CMD}): {e}")

    def get_background_items(self, *args):
        return []

    def _similar(self, _item, path: str, mode: str) -> None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as e:
            notify(f"D-Bus not responding: {e.message}")
            return
        bus.call(BUS_NAME, OBJ_PATH, "fi.samplehaku.Search", "Similar",
                 GLib.Variant("(ss)", (path, mode)), GLib.VariantType("(s)"),
                 Gio.DBusCallFlags.NONE, 120_000, None, self._done)

    def _done(self, bus, res) -> None:
        try:
            out = bus.call_finish(res).unpack()[0]
        except GLib.Error as e:
            notify(f"Fetch service did not respond: {e.message}")
            return
        if out.startswith(("ERROR:", "VIRHE:")):   # VIRHE: = older service
            notify(out[6:].strip())
            return
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(out).get_uri(), None)
