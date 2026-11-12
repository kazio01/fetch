#!/usr/bin/env python3
"""
samplehaku_tulokset – the Fetch results window (GTK4 + libadwaita, system Python).

    samplehaku-results [FOLDER] [--select PATH] [--query TEXT]

FOLDER is a result folder (~/.cache/samplehaku/tulokset/…) or a collection (Liked Samples).
Without arguments, All samples opens. There is a single window: a new call loads into the same window.

Why a window of its own: the Files previewer (Sushi) crashes on MP3s
(GTK's Gtk.MediaFile + a GStreamer 1.28 decodebin3 bug), and a Files extension can't add
keyboard shortcuts. Here playback goes straight through GStreamer's playbin, which also handles MP3.

Keys: ↑/↓ next/previous (plays at once) · Space pause/resume · Enter from the start ·
←/→ jump 10 % · L like · S similar · Ctrl+F or / search · Esc back to the list.
A row can be dragged straight into a DAW (the DAW gets the original file from the library).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gst, Gtk  # noqa: E402

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))
from samplehaku_kokoelmat import COLLECTIONS_DIR, LIKED, LIKED_DIR, MANIFEST, original_path  # noqa: E402
import soittimet  # noqa: E402
import genret  # noqa: E402

APP_ID = "fi.samplehaku.Results"
BUS_NAME = "fi.samplehaku.Search"
OBJ_PATH = "/fi/samplehaku/SearchProvider"
IFACE = "fi.samplehaku.Search"
RESULTS_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "samplehaku/tulokset"
SIMILAR_CMD = os.environ.get("SAMPLEHAKU_SIMILAR", str(Path.home() / ".local/bin/samplehaku-similar"))
AUDIO_EXT = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".opus", ".m4a"}
RANK_RE = re.compile(r"^\d{2,4} ")          # a search result's rank number "001 "
TAGS_PATH = Path(os.environ.get("SAMPLEHAKU_TAGS", Path.home() / ".config/samplehaku/tagit.json"))
# type: (search word, Details type, button). One at a time.
TYPE_TAGS = [("loop", "Loop", "Loop"), ("oneshot", "One-shot", "One-shot")]


def sound_tags() -> list[tuple[str, str]]:
    """Sound-feature tags from tagit.json: [(name, feature)]. Tags of the same feature are
    opposites (dark/bright), so only one of them can be chosen at a time."""
    try:
        cfg = json.loads(TAGS_PATH.read_text())
        return [(t.get("names", t.get("nimet"))[0].lower(), t.get("feature", t.get("piirre")))
                for t in cfg.get("tags", cfg.get("tagit"))]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return [("dark", "centroid"), ("bright", "centroid"), ("bass", "low"), ("noisy", "flat"),
                ("tonal", "flat"), ("punchy", "attack"), ("soft", "attack"), ("tight", "tail"),
                ("long", "tail")]


ALL = "ALL"                  # view: the whole library page by page
SEARCH = "SEARCH"            # view: a search, the whole library in relevance order page by page
RECENT_MAX = 20
PAGE = 100                   # All samples: rows loaded at a time
BPM_MIN, BPM_MAX = 60, 200   # tempo slider ends (the full range = no filtering)


class RangeSlider(Gtk.DrawingArea):
    """A two-handle slider (GTK has none built in). Double-click restores the full range."""
    PAD = 9

    def __init__(self, vmin: int, vmax: int, on_change):
        super().__init__(content_height=26, hexpand=True)
        self.vmin, self.vmax, self.lo, self.hi = vmin, vmax, vmin, vmax
        self.on_change, self.handle, self.x0 = on_change, None, 0.0
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", lambda _g, dx, _dy: self._move(self.x0 + dx))
        drag.connect("drag-end", lambda *_: setattr(self, "handle", None))
        self.add_controller(drag)
        dbl = Gtk.GestureClick()
        dbl.connect("pressed", lambda _g, n, *_: self.reset() if n == 2 else None)
        self.add_controller(dbl)

    @property
    def full(self) -> bool:
        return self.lo <= self.vmin and self.hi >= self.vmax

    def bounds(self) -> tuple[float, float]:
        """An end = no limit: left edge -> no lower limit, right edge -> no upper limit."""
        return (0 if self.lo <= self.vmin else self.lo, 999 if self.hi >= self.vmax else self.hi)

    def text(self) -> str:
        if self.full:
            return ""
        if self.hi >= self.vmax:
            return f"{self.lo}+"
        if self.lo <= self.vmin:
            return f"≤ {self.hi}"
        return f"{self.lo}–{self.hi}"

    def reset(self, notify: bool = True) -> None:
        self.lo, self.hi = self.vmin, self.vmax
        self.queue_draw()
        if notify:
            self.on_change()

    def _x(self, v: float) -> float:
        w = max(self.get_width() - 2 * self.PAD, 1)
        return self.PAD + (v - self.vmin) / (self.vmax - self.vmin) * w

    def _v(self, x: float) -> int:
        w = max(self.get_width() - 2 * self.PAD, 1)
        v = self.vmin + (x - self.PAD) / w * (self.vmax - self.vmin)
        return int(round(min(max(v, self.vmin), self.vmax)))

    def _begin(self, _g, x, _y) -> None:
        self.x0 = x
        dl, dh = abs(x - self._x(self.lo)), abs(x - self._x(self.hi))
        self.handle = "lo" if (dl < dh or (dl == dh and x < self._x(self.lo))) else "hi"
        self._move(x)

    def _move(self, x: float) -> None:
        v = self._v(x)
        if self.handle == "lo":
            v = min(v, self.hi)
            changed, self.lo = v != self.lo, v
        elif self.handle == "hi":
            v = max(v, self.lo)
            changed, self.hi = v != self.hi, v
        else:
            return
        if changed:
            self.queue_draw()
            self.on_change()

    def _draw(self, _area, cr, w, h) -> None:
        fg = self.get_color()
        acc = Adw.StyleManager.get_default().get_accent_color_rgba()
        y = h / 2
        cr.set_line_width(4)
        cr.set_line_cap(1)                                  # round end
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.18)
        cr.move_to(self.PAD, y)
        cr.line_to(w - self.PAD, y)
        cr.stroke()
        x1, x2 = self._x(self.lo), self._x(self.hi)
        if not self.full:
            cr.set_source_rgba(acc.red, acc.green, acc.blue, 1)
            cr.move_to(x1, y)
            cr.line_to(x2, y)
            cr.stroke()
        for x in (x1, x2):
            cr.arc(x, y, 7, 0, 6.2832)
            cr.set_source_rgba(1, 1, 1, 1)
            cr.fill_preserve()
            cr.set_line_width(1)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)
            cr.stroke()


BRAND_ICON = 52          # logo image size in the sidebar header (transparent background), 11 px from the top
                         # and left; the header stays 71 px
DEFAULT_COLLAPSED = ["Genre", "Sound", "Tempo"]   # default layout: Type and Instrument open
UI_CFG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "samplehaku/ikkuna.json"


# Appearance (Settings): UI scale and text size, shared with the plugin (Prefs.h reads the same file).
APPEARANCE = UI_CFG.parent / "appearance.json"
SCALE_MIN, SCALE_MAX = 0.75, 1.5


def load_appearance() -> dict:
    try:
        d = json.loads(APPEARANCE.read_text())
    except (OSError, ValueError):
        d = {}
    clamp = lambda v: min(SCALE_MAX, max(SCALE_MIN, float(v)))  # noqa: E731
    try:
        return {"ui_scale": clamp(d.get("ui_scale", 1.0)), "font_scale": clamp(d.get("font_scale", 1.0))}
    except (TypeError, ValueError):
        return {"ui_scale": 1.0, "font_scale": 1.0}


def save_appearance(a: dict) -> None:
    try:
        APPEARANCE.parent.mkdir(parents=True, exist_ok=True)
        APPEARANCE.write_text(json.dumps(a))
    except OSError:
        pass


def app_version() -> str:
    try:
        return (PROJECT / "VERSION").read_text().strip()
    except OSError:
        return ""


def scaled_css(css: str, ui: float) -> str:
    """The window's CSS with every px size multiplied by the UI scale (paddings, icons, the logo text)."""
    return re.sub(r"(-?\d+(?:\.\d+)?)px", lambda m: f"{float(m.group(1)) * ui:.2f}px", css)


def load_ui_cfg() -> dict:
    try:
        return json.loads(UI_CFG.read_text())
    except (OSError, ValueError):
        return {}


def save_ui_cfg(cfg: dict) -> None:
    try:
        UI_CFG.parent.mkdir(parents=True, exist_ok=True)
        tmp = UI_CFG.with_suffix(f".{os.getpid()}.tmp")   # never a half-written file
        tmp.write_text(json.dumps(cfg))
        os.replace(tmp, UI_CFG)
    except OSError:
        pass


HEART_ON = "emblem-samplehaku-liked"
HEART_OFF = "emblem-samplehaku-heart-outline"

CSS = """
.sh-heading { padding: 10px 14px 6px 14px; }
.sh-searchbar { padding: 6px 6px 6px 14px; }   /* field starts where the title and hearts do */
.sh-searchbar windowcontrols { margin-left: 6px; }
.sh-footer { padding: 6px 12px; border-top: 1px solid alpha(currentColor, 0.12); }
.sh-sidebar-title { padding: 12px 12px 4px 12px; font-size: 0.85em; font-weight: bold; opacity: 0.6; }
columnview.sh-list > listview > row:selected { background-color: alpha(var(--accent-bg-color), 0.35); }
.sh-heart { min-width: 24px; min-height: 24px; padding: 0; }
button.sh-chip { min-height: 22px; padding: 1px 4px; border-radius: 999px; font-size: 0.9em; font-weight: normal; }
button.sh-chip:checked { background-color: var(--accent-bg-color); color: var(--accent-fg-color); }
.sh-filter-label { font-size: 0.8em; opacity: 0.55; margin-top: 2px; font-weight: normal; }
/* the filter groups' names: full ink, normal weight, the size of the sidebar titles */
button.sh-section-head .sh-filter-label { font-size: 0.85em; opacity: 1; margin-top: 0; }   /* size of "Filters" */
button.sh-chip.sh-tight { font-size: 0.8em; padding: 1px 2px; }
button.sh-chip.sh-on { background-color: var(--accent-bg-color); color: var(--accent-fg-color); }
button.sh-section-head { padding: 2px 4px; min-height: 24px; font-weight: normal; }
.sh-summary { font-size: 0.8em; color: var(--accent-color); }
.sh-brand { padding: 11px 6px 8px 11px; }   /* the tile 14 px from the top and left (the row icons' edge),
                                     and 14 px above the header's bottom edge; the image has a 3 px margin */
.sh-brand-name { font-size: 30px; font-weight: 800; letter-spacing: -0.02em;   /* capitals = half the icon */
                 color: black; margin-bottom: 5px; }   /* text rows 28–49, as in the design reference */
.sh-dark .sh-brand-name { color: alpha(white, 0.92); }   /* off-white, matched to the icon's white */
.sh-fade { background: linear-gradient(to bottom, alpha(black, 0), alpha(black, 0.5)); }
button.sh-tree-arrow { min-width: 24px; min-height: 24px; padding: 0; }
.sh-ico { -gtk-icon-size: 16px; }   /* sidebar and heart icons (scaled with the UI) */
.sh-version { font-size: 0.75em; opacity: 0.55; margin-bottom: 11px; }   /* next to "Fetch" */
button.sh-gear { min-width: 28px; min-height: 28px; padding: 0; margin-top: 13px; }   /* centred on the name: y 38 */
.sh-recent-title { font-size: 0.85em; font-weight: bold; opacity: 0.6; }
.sh-sublevel { margin-left: 2px; padding-left: 5px; border-left: 2px solid alpha(var(--accent-bg-color), 0.5); }
"""


def fmt_time(s: float) -> str:
    if s is None or s < 0:
        return ""
    return f"{s:.1f} s" if s < 10 else f"{int(s // 60)}:{int(s % 60):02d}"


# ---------------------------------------------------------------- model
class Row(GObject.Object):
    __gtype_name__ = "SamplehakuRow"

    name = GObject.Property(type=str, default="")
    rank = GObject.Property(type=int, default=0)
    key = GObject.Property(type=str, default="")
    bpm = GObject.Property(type=float, default=-1.0)
    bpm_text = GObject.Property(type=str, default="")
    dur = GObject.Property(type=float, default=-1.0)
    dur_text = GObject.Property(type=str, default="")
    stype = GObject.Property(type=str, default="")
    inst = GObject.Property(type=str, default="")
    genre = GObject.Property(type=str, default="")
    pack = GObject.Property(type=str, default="")
    liked = GObject.Property(type=bool, default=False)

    def __init__(self, path: str, rank: int, name: str):
        super().__init__()
        self.path = path                 # file in the folder (may be a hard link)
        self.orig = original_path(path)  # the original in the library (Details refines it)
        self.props.rank = rank
        self.props.name = name
        self.props.liked = LIKED.has(path)
        self.tags: set[str] | None = None   # sound-feature tags, once Details has arrived
        self.inst_id = ""
        self.itags: set[str] = set()       # style tags (chords, stab, distorted, female …)
        self.itags_est: set[str] = set()
        self.genres: set[str] = set()

    def set_details(self, d: dict) -> None:
        self.orig = d.get("orig") or self.orig
        k = d.get("key") or ""
        self.props.key = k + (" ~" if k and d.get("key_est") else "")
        bpm = d.get("bpm")
        self.props.bpm = bpm if bpm is not None else -1.0
        self.props.bpm_text = (f"{bpm:g}" + (" ~" if d.get("bpm_est") else "")) if bpm is not None else ""
        dur = d.get("dur")
        self.props.dur = dur if dur is not None else -1.0
        self.props.dur_text = fmt_time(dur)
        self.props.stype = d.get("type") or ""
        pack = d.get("pack") or ""
        self.props.pack = pack.split("/", 1)[1] if "/" in pack else pack   # drop the library root folder
        self.tags = set(d.get("tags") or [])
        self.inst_id = d.get("inst") or ""
        self.itags = set(d.get("itags") or [])
        self.itags_est = set(d.get("itags_est") or [])     # estimated from the audio (shown with "~")
        self.genres = set(d.get("genres") or [])
        self.props.genre = ", ".join(genret.LABEL.get(g, g) for g in sorted(self.genres))
        self.props.inst = (d.get("inst_label") or "") + (" ~" if self.inst_id and d.get("inst_est") else "")

    @property
    def play_path(self) -> str:
        """The file to play or drag: the original in the library. Not checked on disk here (a stat on a
        network share costs ~15 ms on the main thread and hangs if the share is away); if it is gone, the
        player falls back to the entry in the folder (ResultsWindow.on_player_change)."""
        return self.orig or self.path


# ---------------------------------------------------------------- player
SHORT_S = 0.3                # shorter ones are played as a copy extended with silence
PAD_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "samplehaku/lyhyet"
PAD_KEEP = 500          # at most this many padded copies (the most recently played stay, ~50 kB each)


def prune_padded(keep: int = PAD_KEEP) -> None:
    """Remove the oldest padded copies (mtime = last played) when there are more than keep."""
    try:
        files = [(e.stat().st_mtime, e.path) for e in os.scandir(PAD_DIR) if e.name.endswith(".wav")]
    except OSError:
        return
    if len(files) <= keep:
        return
    files.sort()
    for _, p in files[:len(files) - keep]:
        try:
            os.unlink(p)
        except OSError:
            pass


def padded(path: str, dur: float | None) -> str:
    """PipeWire doesn't play a very short audio stream at all (measured: a 6 ms click 0/12 times).
    Short ones are played as a copy with 0.3 s of silence added at the end: the start isn't delayed
    and the click is heard every time (12/12). The copy is made once with ffmpeg (32-bit float, lossless)."""
    if dur is None or dur >= SHORT_S or not shutil.which("ffmpeg"):
        return path
    import hashlib
    try:
        st = os.stat(path)
    except OSError:
        return path
    out = PAD_DIR / (hashlib.sha1(f"{path}|{st.st_mtime_ns}".encode()).hexdigest()[:20] + ".wav")
    if not out.exists():
        PAD_DIR.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", path, "-af", "apad=pad_dur=0.3",
                            "-c:a", "pcm_f32le", str(out)], capture_output=True)
        if r.returncode != 0:
            return path
        prune_padded()
    else:
        try:
            os.utime(out)                          # last played: the cleanup removes the oldest
        except OSError:
            pass
    return str(out)
class Warmer:
    """Reads the samples the user is about to reach into the operating system's file cache, in the
    background: over a network share a 5 MB loop takes ~0.7 s to arrive, a file read a moment ago plays at
    once. Only the newest request counts (holding an arrow key down doesn't queue the whole list)."""
    MAX_BYTES = 32 << 20

    def __init__(self):
        import threading
        self.want: list[str] = []
        self.done: dict[str, float] = {}
        self.cv = threading.Condition()
        threading.Thread(target=self._run, daemon=True).start()

    def warm(self, paths: list[str]) -> None:
        with self.cv:
            self.want = [p for p in paths if time.monotonic() - self.done.get(p, -1e9) > 120]
            self.cv.notify()

    def _run(self) -> None:
        while True:
            with self.cv:
                while not self.want:
                    self.cv.wait()
                p = self.want.pop(0)
                if len(self.done) > 2000:
                    self.done.clear()
                self.done[p] = time.monotonic()
            try:
                with open(p, "rb", buffering=0) as f:
                    n = 0
                    while n < self.MAX_BYTES:
                        b = f.read(1 << 20)
                        if not b:
                            break
                        n += len(b)
                        with self.cv:
                            if self.want and self.want[0] != p and n > (4 << 20):
                                break                  # a newer request: a long file can wait
            except OSError:
                pass


class Player:
    """GStreamer's playbin (not GTK's media player, which crashes on MP3)."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.pb = Gst.ElementFactory.make("playbin", "samplehaku")
        self.pb.set_property("flags", 0x02 | 0x10)   # audio only + volume set by the program
        bus = self.pb.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)
        # Loop preview: the same file queued again just before the end (gapless, playbin's own way)
        self.pb.connect("about-to-finish", self._on_about_to_finish)
        self.path, self.dur = None, None
        self.loop = False
        self._uri = None
        self.state = "stopped"          # stopped | playing | paused | ended

    def play(self, path: str, dur: float | None = None, loop: bool = False) -> None:
        self.pb.set_state(Gst.State.NULL)
        self._uri = Gio.File.new_for_path(padded(path, dur)).get_uri()
        self.loop = loop
        self.pb.set_property("uri", self._uri)
        self.pb.set_state(Gst.State.PLAYING)
        self.path, self.dur, self.state = path, dur, "playing"
        self.on_change()

    def _on_about_to_finish(self, pb) -> None:
        if self.loop and self._uri:       # streaming thread: only the property, as GStreamer documents
            pb.set_property("uri", self._uri)

    def toggle(self) -> None:
        if self.state == "playing":
            self.pb.set_state(Gst.State.PAUSED)
            self.state = "paused"
        elif self.state == "paused":
            self.pb.set_state(Gst.State.PLAYING)
            self.state = "playing"
        elif self.path:
            self.play(self.path, self.dur, self.loop)
            return
        self.on_change()

    def stop(self) -> None:
        self.pb.set_state(Gst.State.NULL)
        self.state = "stopped"
        self.on_change()

    def position(self) -> tuple[float, float]:
        ok_p, pos = self.pb.query_position(Gst.Format.TIME)
        ok_d, dur = self.pb.query_duration(Gst.Format.TIME)
        return (pos / Gst.SECOND if ok_p else 0.0, dur / Gst.SECOND if ok_d else 0.0)

    def seek(self, seconds: float) -> None:
        if self.state in ("stopped", "ended"):
            if not self.path:
                return
            self.play(self.path, self.dur, self.loop)
            self.pb.get_state(200 * Gst.MSECOND)   # wait until the file is open
        self.pb.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                            max(0, int(seconds * Gst.SECOND)))

    def seek_rel(self, frac: float) -> None:
        pos, dur = self.position()
        if dur > 0:
            self.seek(min(max(0.0, pos + frac * dur), dur - 0.05))

    def _on_message(self, _bus, msg) -> None:
        if msg.type == Gst.MessageType.EOS:
            # Don't stop: EOS arrives when the audio has been handed to the sound server, not when it has played.
            # Stopping cut short (< 0.1 s) samples off entirely. The pipeline stays silent in the PLAYING state.
            self.state = "ended"
            self.on_change()
        elif msg.type == Gst.MessageType.ERROR:
            err, _ = msg.parse_error()
            self.pb.set_state(Gst.State.NULL)
            self.state = "stopped"
            self.on_change(error=err.message)


# ---------------------------------------------------------------- window
class ResultsWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Fetch", default_width=1150, default_height=720,
                         icon_name=APP_ID)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.player = Player(self.on_player_change)
        self.warmer = Warmer()
        self._played = None                # (row, path given to the player): for the fallback and warming
        self.folder: Path | None = None
        self.generation = 0            # outdated D-Bus replies are ignored
        self.quiet = False             # selection without playback (loading, right-click)
        self.sidebar_lock = False
        self.last_row = None
        self.live_query = None          # the search shown while typing, not saved yet
        self.active: dict[str, str] = {}   # filter group (type / feature) -> chosen tag
        self.browse = None                 # All samples: {filters, order, seed, offset, total, loading}
        self.inst_sel: list[str] = []      # chosen instrument path per level: ["drums", "drums/perc", …]
        self.ui_cfg = load_ui_cfg()        # collapsed groups etc.
        self.base_view = ALL               # where a cleared search returns (the view chosen in the sidebar)
        self.recent_open = False           # Recent searches closed at startup
        self.tree_open = bool(self.ui_cfg.get("folders_open"))          # the Folders tree shown
        self.tree_expanded = set(self.ui_cfg.get("folders_expanded", []))  # its open folders
        self.tree_cache: dict[str, list] = {}  # folder ("" = roots) -> [{name, path, count}] from the service
        self.tree_pending: set[str] = set()
        self.pending_select = None
        self.role = None                   # "chords" / "melody" (top row, independent of the instrument)
        self.genre_sel: list[str] = []     # [group] or [group, genre]
        self.genre_counts: dict[str, int] = {}
        self.bpm_timer = 0
        self.chips: list[tuple[Gtk.ToggleButton, str, str]] = []
        self.query_gen = 0

        # --- sidebar
        self.side_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.side_list.add_css_class("navigation-sidebar")
        self.side_list.set_header_func(self._side_header)
        self.side_list.connect("row-selected", self.on_side_selected)
        side_scroll = Gtk.ScrolledWindow(vexpand=True, child=self.side_list,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)
        side_tb = Adw.ToolbarView()
        # Logo and name: the icon lines up with the sidebar's row icons, as far from the top as from the
        # left and as far again above "Collections"; the name's capitals are half the icon's height.
        # The dark scheme has its own (inverted) logo print.
        brand = Gtk.Box(spacing=6, valign=Gtk.Align.START)
        brand.add_css_class("sh-brand")
        self.logo = Gtk.Image(pixel_size=round(BRAND_ICON * load_appearance()["ui_scale"]))
        brand.append(self.logo)
        name_lbl = Gtk.Label(label="Fetch", xalign=0, valign=Gtk.Align.END)   # baseline on the tile's bottom
        name_lbl.add_css_class("sh-brand-name")
        brand.append(name_lbl)
        ver = Gtk.Label(label=app_version(), xalign=0, valign=Gtk.Align.END, hexpand=True,
                        tooltip_text=f"Fetch {app_version()}")
        ver.add_css_class("sh-version")
        brand.append(ver)
        gear = Gtk.Button(icon_name="emblem-system-symbolic", valign=Gtk.Align.START, tooltip_text="Settings")
        gear.add_css_class("flat")
        gear.add_css_class("circular")
        gear.add_css_class("sh-gear")
        gear.connect("clicked", lambda *_: self.open_settings())
        brand.append(gear)
        side_tb.add_top_bar(Gtk.WindowHandle(child=brand))   # drag the window from it, as from a header bar
        # a short fade to black at the bottom edge
        fade = Gtk.Box(valign=Gtk.Align.END, height_request=40, can_target=False)
        fade.add_css_class("sh-fade")
        side_overlay = Gtk.Overlay(child=side_scroll)
        side_overlay.add_overlay(fade)
        side_tb.set_content(side_overlay)
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", self._on_scheme)
        self._on_scheme(style)

        # --- search field
        self.entry = Gtk.SearchEntry(hexpand=True, placeholder_text=(
            "Search samples – e.g. dark punchy kick, bpm:124, key:Am, +loop"))
        self.entry.set_search_delay(200)            # ms: the search starts when typing pauses
        self.entry.connect("search-changed", self.on_search_changed)
        self.entry.connect("activate", self.on_search)
        self.entry.connect("stop-search", lambda *_: self.colview.grab_focus())
        # The field fills the header bar with even 6 px margins, as in the plugin. Adw.HeaderBar gives its
        # title only the natural width, so the bar is built here: field, spinner, window buttons.
        self.spinner = Adw.Spinner(visible=False)
        bar = Gtk.Box(spacing=6)
        bar.add_css_class("sh-searchbar")
        bar.append(self.entry)
        bar.append(self.spinner)
        bar.append(Gtk.WindowControls(side=Gtk.PackType.END))
        header = Gtk.WindowHandle(child=bar)   # drag / double-click the bar as a header bar

        # --- title and list
        self.title = Gtk.Label(xalign=0, ellipsize=3)
        self.title.add_css_class("title-4")
        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.add_css_class("dim-label")
        head = Gtk.Box(spacing=12)
        head.add_css_class("sh-heading")
        head.append(self.title)
        head.append(self.subtitle)
        head.append(Gtk.Box(hexpand=True))
        # shuffle: every click = a new random order; the sort button restores folder order
        self.shuffled = False
        self.unshuffle = Gtk.Button(icon_name="view-sort-ascending-symbolic", visible=False,
                                    tooltip_text="Back to folder order")
        self.unshuffle.add_css_class("flat")
        self.unshuffle.connect("clicked", lambda *_: self.set_shuffled(False))
        head.append(self.unshuffle)
        self.shuffle = Gtk.Button(icon_name="media-playlist-shuffle-symbolic", visible=False,
                                        tooltip_text="Shuffle – every click gives a new random order")
        self.shuffle.add_css_class("flat")
        self.shuffle.connect("clicked", lambda *_: self.set_shuffled(True))
        head.append(self.shuffle)

        self.store = Gio.ListStore(item_type=Row)
        self.filter = Gtk.CustomFilter.new(self._match)
        self.filtered = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.sorted = Gtk.SortListModel(model=self.filtered)
        # multiple selection: Ctrl+click, Shift+click / Shift+arrows, Ctrl+A
        self.sel = Gtk.MultiSelection(model=self.sorted)
        self.sel.connect("selection-changed", self.on_selected)
        self.sel_ids: set[int] = set()
        self.colview = Gtk.ColumnView(model=self.sel, show_column_separators=False,
                                      show_row_separators=False, reorderable=False)
        self.colview.add_css_class("sh-list")
        self.colview.connect("activate", lambda _cv, pos: self.play_pos(pos))
        self.sorted.set_sorter(self.colview.get_sorter())
        self.sort_order = ""               # paged views: "sort:<field>:asc|desc" for the service
        self._sort_guard = False
        self.colview.get_sorter().connect("changed", self.on_sort_changed)
        self._build_columns()
        list_scroll = Gtk.ScrolledWindow(vexpand=True, child=self.colview)
        list_scroll.connect("edge-reached", lambda _sw, pos: self.load_more()
                            if pos == Gtk.PositionType.BOTTOM else None)

        self.empty = Adw.StatusPage(icon_name="audio-x-generic-symbolic", title="No samples",
                                    description="Search above, or pick a collection on the left.")
        self.stack = Gtk.Stack()
        self.stack.add_named(list_scroll, "list")
        self.stack.add_named(self.empty, "empty")

        # --- bottom bar: player
        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Space")
        self.play_btn.add_css_class("flat")
        self.play_btn.connect("clicked", lambda *_: self.player.toggle())
        self.now = Gtk.Label(xalign=0, ellipsize=3, width_chars=30, max_width_chars=50)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True,
                               adjustment=Gtk.Adjustment(lower=0, upper=1, step_increment=0.01))
        self.scale.set_draw_value(False)
        self.scale.connect("change-value", self.on_scale)
        self.time = Gtk.Label(width_chars=13, xalign=1)
        self.time.add_css_class("numeric")
        self.time.add_css_class("dim-label")
        # Player options (was the Play on select toggle): a pop-over with switches, kept in ikkuna.json
        self.autoplay_on = bool(self.ui_cfg.get("autoplay", True))
        self.loop_preview = bool(self.ui_cfg.get("loop_preview", False))
        opts = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        opts.add_css_class("boxed-list")
        self.opt_rows = {}
        for key, title, sub, on in (("autoplay", "Play on select", "↑/↓ in the list plays the sample", self.autoplay_on),
                                    ("loop_preview", "Loop preview", "Loops repeat until stopped", self.loop_preview)):
            r = Adw.SwitchRow(title=title, subtitle=sub, active=on)
            r.connect("notify::active", self.on_player_option, key)
            opts.append(r)
            self.opt_rows[key] = r
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6, width_request=300)
        head_lbl = Gtk.Label(label="Player", xalign=0)
        head_lbl.add_css_class("heading")
        box.append(head_lbl)
        box.append(opts)
        self.autoplay = Gtk.MenuButton(icon_name="media-playlist-consecutive-symbolic", tooltip_text="Player options",
                                       popover=Gtk.Popover(child=box), direction=Gtk.ArrowType.UP)
        self.autoplay.add_css_class("flat")
        footer = Gtk.Box(spacing=8)
        footer.add_css_class("sh-footer")
        for w in (self.play_btn, self.now, self.scale, self.time, self.autoplay):
            footer.append(w)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(head)
        content_box.append(self.stack)
        content_tb = Adw.ToolbarView()
        content_tb.add_top_bar(header)
        content_tb.set_content(content_box)
        content_tb.add_bottom_bar(footer)

        split = Adw.OverlaySplitView(sidebar=side_tb, content=content_tb, min_sidebar_width=250,
                                     max_sidebar_width=300)
        self.split = split
        self.toast = Adw.ToastOverlay(child=split)
        self.set_content(self.toast)

        # --- actions and keys
        for name, cb in (("like", self.act_like), ("similar", self.act_similar),
                         ("show-in-files", self.act_show), ("open", self.act_open),
                         ("edit-tags", self.act_edit_tags),
                         ("search", lambda *_: self.focus_search())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        menu = Gio.Menu()
        menu.append("Like / Unlike\tL", "win.like")
        menu.append("Similar sounds…\tS", "win.similar")
        menu.append("Edit tags…\tE", "win.edit-tags")
        menu.append("Show in Files", "win.show-in-files")
        menu.append("Open with default app", "win.open")
        self.menu = Gtk.PopoverMenu.new_from_model(menu)
        self.menu.set_parent(self.colview)
        self.menu.set_has_arrow(False)
        self.connect("notify::is-active", self.on_active)
        GLib.timeout_add(80, self.tick)
        self.filter_row = self._build_filter_row()
        self.load_genre_counts()
        self.refresh_sidebar()

    # ------------------------------------------------------------ columns
    def _build_columns(self) -> None:
        expr = lambda prop: Gtk.PropertyExpression.new(Row.__gtype__, None, prop)  # noqa: E731

        # heart
        def heart_setup(_f, li):
            b = Gtk.Button(has_frame=False, focusable=False, tooltip_text="Like (L)")
            b.add_css_class("sh-heart")
            b.set_child(Gtk.Image(css_classes=["sh-ico"]))
            b.connect("clicked", lambda btn: self.toggle_like(btn._row) if getattr(btn, "_row", None) else None)
            li.set_child(b)

        def heart_bind(_f, li):
            b, row = li.get_child(), li.get_item()
            b._row = row
            b._binding = row.bind_property("liked", b.get_child(), "icon-name",
                                           GObject.BindingFlags.SYNC_CREATE,
                                           lambda _b, v: HEART_ON if v else HEART_OFF)

        def heart_unbind(_f, li):
            b = li.get_child()
            if getattr(b, "_binding", None):
                b._binding.unbind()
                b._binding = None

        f = Gtk.SignalListItemFactory()
        f.connect("setup", heart_setup)
        f.connect("bind", heart_bind)
        f.connect("unbind", heart_unbind)
        col = Gtk.ColumnViewColumn(title="", factory=f, fixed_width=40)
        col._base_width = 40
        col.set_sorter(Gtk.NumericSorter.new(expr("liked")))
        self.colview.append_column(col)

        for title, prop, sort_prop, numeric, width, expand, xalign in (
                ("Name", "name", "name", False, 340, True, 0.0),
                ("Instrument", "inst", "inst", False, 130, False, 0.0),
                ("Genre", "genre", "genre", False, 120, False, 0.0),
                ("Key", "key", "key", False, 70, False, 0.0),
                ("BPM", "bpm_text", "bpm", True, 70, False, 1.0),
                ("Length", "dur_text", "dur", True, 70, False, 1.0),
                ("Type", "stype", "stype", False, 85, False, 0.0),
                ("Pack", "pack", "pack", False, 300, True, 0.0)):
            f = Gtk.SignalListItemFactory()
            f.connect("setup", self._label_setup, xalign)
            f.connect("bind", self._label_bind, prop)
            f.connect("unbind", self._label_unbind)
            col = Gtk.ColumnViewColumn(title=title, factory=f, resizable=True, expand=expand)
            col.set_fixed_width(width)
            col._base_width = width
            col.set_sorter(Gtk.NumericSorter.new(expr(sort_prop)) if numeric
                           else Gtk.StringSorter.new(expr(sort_prop)))
            self.colview.append_column(col)

    def _label_setup(self, _f, li, xalign) -> None:
        lbl = Gtk.Label(xalign=xalign, ellipsize=3)
        if xalign == 1.0:
            lbl.add_css_class("numeric")
        # drag to a DAW: the original file from the library
        drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        drag.connect("prepare", self._drag_prepare, lbl)
        lbl.add_controller(drag)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._context_menu, lbl, li)
        lbl.add_controller(click)
        li.set_child(lbl)

    def _label_bind(self, _f, li, prop) -> None:
        lbl, row = li.get_child(), li.get_item()
        lbl._row = row
        lbl._binding = row.bind_property(prop, lbl, "label", GObject.BindingFlags.SYNC_CREATE)
        if prop == "name":
            lbl.set_tooltip_text(row.orig)

    def _label_unbind(self, _f, li) -> None:
        lbl = li.get_child()
        if getattr(lbl, "_binding", None):
            lbl._binding.unbind()
            lbl._binding = None
        lbl._row = None

    def _drag_prepare(self, _src, _x, _y, lbl):
        row = getattr(lbl, "_row", None)
        if not row:
            return None
        rows = self.selected_rows()
        if row not in rows:
            rows = [row]
        files = Gdk.FileList.new_from_array([Gio.File.new_for_path(r.play_path) for r in rows])
        return Gdk.ContentProvider.new_for_value(files)

    def _context_menu(self, gesture, _n, x, y, lbl, li) -> None:
        if not self.sel.is_selected(li.get_position()):
            self.quiet = True
            self.sel.select_item(li.get_position(), True)
            self.quiet = False
        ok, pt = lbl.compute_point(self.colview, Graphene.Point().init(x, y))
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = (int(pt.x), int(pt.y), 1, 1) if ok else (0, 0, 1, 1)
        self.menu.set_pointing_to(rect)
        self.menu.popup()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    # ------------------------------------------------------------ sidebar
    def _recent_head(self) -> Gtk.ListBoxRow:
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("sh-section-head")
        hb = Gtk.Box(spacing=6)
        hb.append(Gtk.Image(icon_name="pan-down-symbolic" if self.recent_open else "pan-end-symbolic"))
        lbl = Gtk.Label(label="Recent searches", xalign=0)
        lbl.add_css_class("sh-recent-title")
        hb.append(lbl)
        btn.set_child(hb)
        btn.connect("clicked", lambda *_: self.toggle_recent())
        row = Gtk.ListBoxRow(child=btn, selectable=False, activatable=False)
        row._section, row._path = "Recent searches", None
        return row

    # Folders: the library's folders as a tree under All samples, a way to one particular place.
    # Loaded from the service one level at a time when a folder is opened (360k paths).
    def _tree_items(self, parent: str, depth: int) -> list[tuple]:
        if parent not in self.tree_cache:
            self._load_tree(parent)
            return []
        out = []
        for d in self.tree_cache[parent]:
            out.append((d["path"], d["name"], d["count"], depth, d.get("subs", True)))
            if d["path"] in self.tree_expanded:
                out += self._tree_items(d["path"], depth + 1)
        return out

    def after_library_change(self) -> None:
        """A library folder was removed (Settings): the Folders tree is read again from the service."""
        self.tree_cache = {}
        self.refresh_sidebar()

    def _load_tree(self, parent: str) -> None:
        if parent in self.tree_pending:
            return
        self.tree_pending.add(parent)

        def done(out: str) -> None:
            self.tree_pending.discard(parent)
            try:
                d = json.loads(out)
            except ValueError:
                return
            if "error" in d:
                self.show_toast(d["error"])
                return
            self.tree_cache[parent] = d["folders"]
            self.refresh_sidebar()
        self.call("Folders", GLib.Variant("(s)", (parent,)), done, 60_000)

    def _folders_head(self) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=10)
        box.append(Gtk.Image(icon_name="folder-symbolic", css_classes=["sh-ico"]))
        box.append(Gtk.Label(label="Folders", xalign=0, hexpand=True))
        box.append(Gtk.Image(icon_name="pan-down-symbolic" if self.tree_open else "pan-end-symbolic"))
        row = Gtk.ListBoxRow(child=box, tooltip_text="Browse the library folder by folder")
        row._section, row._path = "Collections", "FOLDERS_HEAD"
        return row

    def _tree_row(self, path: str, name: str, count: int, depth: int, subs: bool = True) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=4, margin_start=12 * (depth - 1))
        if not subs or self.tree_cache.get(path) == []:    # the deepest folder: no arrow
            box.append(Gtk.Box(width_request=24))
        else:
            arrow = Gtk.Button(icon_name="pan-down-symbolic" if path in self.tree_expanded else "pan-end-symbolic",
                               valign=Gtk.Align.CENTER, tooltip_text="Show subfolders")
            arrow.add_css_class("flat")
            arrow.add_css_class("sh-tree-arrow")
            arrow.connect("clicked", lambda *_: self.toggle_tree_folder(path))
            box.append(arrow)
        box.append(Gtk.Image(icon_name="folder-symbolic", css_classes=["sh-ico"]))
        box.append(Gtk.Label(label=name, xalign=0, ellipsize=3, hexpand=True,
                             tooltip_text=f"{name} · {count:,} samples".replace(",", " ")))
        row = Gtk.ListBoxRow(child=box)
        row._section, row._path = "Collections", "d:" + path
        return row

    def toggle_folders(self) -> None:
        self.tree_open = not self.tree_open
        if self.tree_open:
            self.tree_cache = {}                   # fresh from the service (folders may have been added)
        self.ui_cfg["folders_open"] = self.tree_open
        save_ui_cfg(self.ui_cfg)
        self.refresh_sidebar()

    def toggle_tree_folder(self, path: str) -> None:
        (self.tree_expanded.discard if path in self.tree_expanded else self.tree_expanded.add)(path)
        self.ui_cfg["folders_expanded"] = sorted(self.tree_expanded)
        save_ui_cfg(self.ui_cfg)
        self.refresh_sidebar()

    def toggle_recent(self) -> None:
        self.recent_open = not self.recent_open
        self.refresh_sidebar()

    def _side_header(self, row, before) -> None:
        if row._section == "Recent searches":           # its own header row (_recent_head)
            row.set_header(None)
            return
        if row._section == "Filters":
            if row.get_header() is not self.filters_header:
                if self.filters_header.get_parent() is not None:
                    self.filters_header.get_parent().set_header(None)
                row.set_header(self.filters_header)
            return
        if before is None or before._section != row._section:
            lbl = Gtk.Label(label=row._section, xalign=0)
            lbl.add_css_class("sh-sidebar-title")
            row.set_header(lbl)
        else:
            row.set_header(None)

    def refresh_sidebar(self) -> None:
        self.sidebar_lock = True
        while (r := self.side_list.get_row_at_index(0)) is not None:
            self.side_list.remove(r)
        items = []
        colls = sorted(p for p in COLLECTIONS_DIR.iterdir() if p.is_dir()) if COLLECTIONS_DIR.is_dir() else []
        if LIKED_DIR not in colls:
            colls.insert(0, LIKED_DIR)
        items.append(("Collections", ALL, "view-list-symbolic"))
        items.append(("Collections", "FOLDERS_HEAD", "folder-symbolic"))
        if self.tree_open:
            items += [("Collections", t, None) for t in self._tree_items("", 1)]
        for p in colls:
            items.append(("Collections", p, "emblem-samplehaku-liked" if p == LIKED_DIR else "folder-symbolic"))
        recent = [(r["t"], "q:" + r["q"], "system-search-symbolic") for r in self.ui_cfg.get("recent", [])]
        if RESULTS_DIR.is_dir():                   # Similar results are folders of 100 samples
            recent += [(p.stat().st_mtime, p, "view-paged-symbolic") for p in RESULTS_DIR.iterdir()
                       if p.is_dir() and p.name.startswith("Similar")]
        if recent:                                  # the header is a row itself: clicking opens/closes the list
            items.append(("Recent searches", "RECENT_HEAD", None))
        if self.recent_open:
            for _, p, icon in sorted(recent, key=lambda x: x[0], reverse=True)[:RECENT_MAX]:
                items.append(("Recent searches", p, icon))
        rows_before = [i for i in items if i[0] == "Collections"]
        items = rows_before + [("Filters", None, None)] + [i for i in items if i[0] != "Collections"]
        for section, path, icon in items:
            if section == "Filters":
                self.side_list.append(self.filter_row)
                continue
            if path == "RECENT_HEAD":
                self.side_list.append(self._recent_head())
                continue
            if isinstance(path, tuple):                # a folder in the Folders tree
                trow = self._tree_row(*path)
                self.side_list.append(trow)
                if self.folder == "d:" + path[0]:
                    self.side_list.select_row(trow)
                continue
            if path == "FOLDERS_HEAD":
                self.side_list.append(self._folders_head())
                continue
            box = Gtk.Box(spacing=10)
            img = Gtk.Image(icon_name=icon, css_classes=["sh-ico"])
            box.append(img)
            label = "All samples" if path == ALL else path[2:] if isinstance(path, str) else path.name
            box.append(Gtk.Label(label=label, xalign=0, ellipsize=3, tooltip_text=label))
            row = Gtk.ListBoxRow(child=box)
            row._section, row._path = section, path
            self.side_list.append(row)
            if self.folder and path == self.folder:
                self.side_list.select_row(row)
        self.sidebar_lock = False

    def _build_filter_row(self) -> Gtk.ListBoxRow:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=6, margin_end=6,
                      margin_bottom=6)
        self.sections = {}
        # Type
        flow = self._flow(len(TYPE_TAGS))   # Loop / One-shot share the full width
        for t, _, lbl in TYPE_TAGS:
            flow.append(self._toggle_chip(lbl, t, "type"))
        self._section(box, "Type", flow)
        # Instrument (levels open according to the selection)
        self.inst_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._section(box, "Instrument", self.inst_box)
        self.render_inst()
        # Genre (per pack; only those in the library)
        gbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.genre_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        gbox.append(self.genre_box)
        edit = Gtk.Button(label="Pack genres…", halign=Gtk.Align.START,
                          tooltip_text="Confirm or change the genre of each sample pack")
        edit.add_css_class("flat")
        edit.add_css_class("caption")
        edit.connect("clicked", lambda *_: PackGenres(self).present(self))
        gbox.append(edit)
        self._section(box, "Genre", gbox)
        self.render_genres()
        # Sound
        flow = self._flow()
        for n, feat in sound_tags():
            flow.append(self._toggle_chip(n.capitalize(), n, feat))
        self._section(box, "Sound", flow)
        # Tempo
        self.bpm = RangeSlider(BPM_MIN, BPM_MAX, self.on_bpm)
        self.bpm.set_tooltip_text("Drag the ends to set a BPM range · double-click resets")
        self._section(box, "Tempo", self.bpm)
        # header: Filters + one Clear for all
        self.filters_header = Gtk.Box(spacing=4)
        t = Gtk.Label(label="Filters", xalign=0, hexpand=True)
        t.add_css_class("sh-sidebar-title")
        self.clear_btn = Gtk.Button(label="Clear", visible=False, valign=Gtk.Align.END,
                                    tooltip_text="Clear all filters")
        self.clear_btn.add_css_class("flat")
        self.clear_btn.add_css_class("caption")
        self.clear_btn.connect("clicked", lambda *_: self.clear_filters())
        self.filters_header.append(t)
        self.filters_header.append(self.clear_btn)
        row = Gtk.ListBoxRow(child=box, selectable=False, activatable=False)
        row._section, row._path = "Filters", None
        self.update_summaries()
        return row

    def _section(self, box: Gtk.Box, title: str, content: Gtk.Widget) -> None:
        """A collapsible group: click the header to hide it; selections stay on
        and show in the header as a summary."""
        collapsed = title in self.ui_cfg.get("collapsed", DEFAULT_COLLAPSED)
        head = Gtk.Button()
        head.add_css_class("flat")
        head.add_css_class("sh-section-head")
        hb = Gtk.Box(spacing=6)
        arrow = Gtk.Image(icon_name="pan-end-symbolic" if collapsed else "pan-down-symbolic")
        name = Gtk.Label(label=title, xalign=0)
        name.add_css_class("sh-filter-label")
        summary = Gtk.Label(xalign=1, hexpand=True, ellipsize=3)
        summary.add_css_class("sh-summary")
        hb.append(arrow)
        hb.append(name)
        hb.append(summary)
        head.set_child(hb)
        rev = Gtk.Revealer(child=content, reveal_child=not collapsed,
                           transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=150)
        head.connect("clicked", lambda *_: self.toggle_section(title))
        box.append(head)
        box.append(rev)
        self.sections[title] = (rev, arrow, summary)

    def toggle_section(self, title: str) -> None:
        rev, arrow, _ = self.sections[title]
        show = not rev.get_reveal_child()
        rev.set_reveal_child(show)
        arrow.set_from_icon_name("pan-down-symbolic" if show else "pan-end-symbolic")
        col = set(self.ui_cfg.get("collapsed", DEFAULT_COLLAPSED))
        (col.discard if show else col.add)(title)
        self.ui_cfg["collapsed"] = sorted(col)
        save_ui_cfg(self.ui_cfg)

    def update_summaries(self) -> None:
        if not getattr(self, "sections", None):
            return
        type_lbl = {t: lbl for t, _, lbl in TYPE_TAGS}
        summ = {
            "Type": type_lbl.get(self.active.get("type"), ""),
            "Instrument": self.inst_summary(),
            "Genre": genret.LABEL.get(self.genre_sel[-1], "") if self.genre_sel else "",
            "Sound": ", ".join(n.capitalize() for g, n in self.active.items() if g != "type"),
            "Tempo": self.bpm.text(),
        }
        for title, (rev, _, lbl) in self.sections.items():
            lbl.set_label(summ.get(title, ""))
        self.clear_btn.set_visible(any(summ.values()))

    def rescale(self, a: dict) -> None:
        """Sizes set in code (not CSS) follow the appearance settings: the logo, the sidebar width
        and the column widths (they hold text, so they follow both scales)."""
        ui, text = a["ui_scale"], a["ui_scale"] * a["font_scale"]
        self.logo.set_pixel_size(round(BRAND_ICON * ui))
        self.split.set_min_sidebar_width(round(250 * ui))
        self.split.set_max_sidebar_width(round(300 * ui))
        cols = self.colview.get_columns()
        for k in range(cols.get_n_items()):
            col = cols.get_item(k)
            if hasattr(col, "_base_width"):
                col.set_fixed_width(round(col._base_width * (ui if k == 0 else text)))

    def open_settings(self) -> None:
        SettingsDialog(self).present(self)

    def _on_scheme(self, style: Adw.StyleManager, *_) -> None:
        dark = style.get_dark()
        self.logo.set_from_file(str(PROJECT / ("fetch-mark-dark.png" if dark else "fetch-mark.png")))
        if dark:
            self.add_css_class("sh-dark")
        else:
            self.remove_css_class("sh-dark")

    def _flow(self, cols: int = 3) -> Gtk.FlowBox:
        # three chips per row (genres two), equal width
        return Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=4, row_spacing=4,
                           min_children_per_line=cols, max_children_per_line=cols, homogeneous=True)

    @staticmethod
    def _fit(b: Gtk.Button, cols: int = 3, indent: int = 0) -> None:
        """A smaller font if the text doesn't fit the column at the narrowest sidebar (250 px)."""
        avail = (250 - 12 - indent - 4 * (cols - 1)) / cols
        if b.measure(Gtk.Orientation.HORIZONTAL, -1)[1] > avail:
            b.add_css_class("sh-tight")

    def _toggle_chip(self, label: str, name: str, group: str) -> Gtk.ToggleButton:
        b = Gtk.ToggleButton(label=label, focusable=False, tooltip_text=f"+{name}")
        b.get_child().set_ellipsize(3)
        b.add_css_class("sh-chip")
        self._fit(b)
        b.connect("toggled", self.on_chip, name, group)
        self.chips.append((b, name, group))
        return b

    def _chip(self, label: str, on: bool, cb, *args, tooltip: str = "", cols: int = 3,
              indent: int = 0) -> Gtk.Button:
        b = Gtk.Button(label=label, focusable=False, tooltip_text=tooltip or label)
        b.get_child().set_ellipsize(3)
        b.add_css_class("sh-chip")
        self._fit(b, cols, indent)
        if on:
            b.add_css_class("sh-on")
        b.connect("clicked", lambda _b: cb(*args))
        return b

    # instrument choice: [main class, subcategory "kind:value", 3rd level]
    def inst_filter(self) -> tuple[str | None, str | None, str | None]:
        """-> (instrument, style tag, type) from the choice. See soittimet.SUBS."""
        if not self.inst_sel:
            return None, None, None
        inst, tag, typ = self.inst_sel[0], None, None
        if len(self.inst_sel) > 1:
            kind, val = self.inst_sel[1].split(":", 1)
            if kind in ("inst", "link"):
                inst = val
            elif kind == "tag":
                tag = val
            elif kind == "type":
                typ = val
        if len(self.inst_sel) > 2:
            inst = self.inst_sel[2]
        return inst, tag, typ

    def inst_summary(self) -> str:
        if not self.inst_sel:
            return dict(soittimet.ROLES).get(self.role, "")
        inst, tag, typ = self.inst_filter()
        parts = [soittimet.label_path(inst)] if inst else []
        if tag:
            parts.append(soittimet.TAG_LABEL.get(tag, tag))
        if typ:
            parts.append("Grooves")
        if self.role:
            parts.append(dict(soittimet.ROLES)[self.role])
        return " · ".join(parts)

    def render_inst(self) -> None:
        while (c := self.inst_box.get_first_child()) is not None:
            self.inst_box.remove(c)
        flow = self._flow()
        for node in soittimet.MAINS:
            flow.append(self._chip(soittimet.chip_label(node), self.inst_sel[:1] == [node], self.pick_inst, 0, node,
                                   tooltip=soittimet.LABEL[node]))
        for role, label in soittimet.ROLES:              # independent of the instrument
            flow.append(self._chip(label, self.role == role, self.pick_role, role,
                                   tooltip=f"All {label.lower()}, any instrument"))
        self.inst_box.append(flow)
        if not self.inst_sel:
            return
        main = self.inst_sel[0]
        self.inst_box.append(self._sublevel(soittimet.LABEL[main], [
            (label, f"{kind}:{val}", self._sub_tip(kind, val)) for label, kind, val in soittimet.SUBS.get(main, [])],
            self.inst_sel[1] if len(self.inst_sel) > 1 else None, 1))
        if len(self.inst_sel) > 1:
            kind, val = self.inst_sel[1].split(":", 1)
            kids = soittimet.children(val) if kind == "inst" else []
            if kids:
                self.inst_box.append(self._sublevel(soittimet.LABEL[val], [
                    (soittimet.chip_label(k), k, soittimet.label_path(k)) for k in kids],
                    self.inst_sel[2] if len(self.inst_sel) > 2 else None, 2))

    @staticmethod
    def _sub_tip(kind: str, val: str) -> str:
        if kind == "tag":
            return f"Tagged {soittimet.TAG_LABEL.get(val, val)}"
        if kind == "type":
            return "Loops"
        return soittimet.label_path(val)

    def _sublevel(self, title: str, items, selected, depth: int) -> Gtk.Box:
        sub = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sub.add_css_class("sh-sublevel")
        lbl = Gtk.Label(label=title + " ›", xalign=0)
        lbl.add_css_class("sh-filter-label")
        sub.append(lbl)
        f = self._flow()
        for label, key, tip in items:
            f.append(self._chip(label, key == selected, self.pick_inst, depth, key, tooltip=tip,
                                indent=9 * depth))
        sub.append(f)
        return sub

    def pick_inst(self, depth: int, key: str) -> None:
        if len(self.inst_sel) > depth and self.inst_sel[depth] == key:
            self.inst_sel = self.inst_sel[:depth]       # the same again: off (the parent stays)
        else:
            self.inst_sel = self.inst_sel[:depth] + [key]
        self.render_inst()
        self.filters_changed()

    def pick_role(self, role: str) -> None:
        self.role = None if self.role == role else role
        self.render_inst()
        self.filters_changed()

    # genre choice: [group, genre]
    def load_genre_counts(self) -> None:
        def done(out):
            try:
                d = json.loads(out)
            except ValueError:
                return
            if "error" not in d:
                self.genre_counts = d
                self.render_genres()
        self.call("GenreCounts", None, done, 120_000)

    def render_genres(self) -> None:
        while (c := self.genre_box.get_first_child()) is not None:
            self.genre_box.remove(c)
        if not self.genre_counts:
            lbl = Gtk.Label(label="Loading…", xalign=0)
            lbl.add_css_class("sh-filter-label")
            self.genre_box.append(lbl)
            return
        short = {"hiphop-rnb": "Hip Hop", "house-techno": "House", "pop-edm": "Pop/EDM",
                 "live": "Live", "bass-music": "Bass music", "electronic": "Electronic", "global": "Global",
                 "cinematic": "Cinematic"}
        flow = self._flow()
        for g, label in genret.GROUPS:
            n = self.genre_counts.get(g, 0)
            if n:
                flow.append(self._chip(short.get(g, label), self.genre_sel[:1] == [g], self.pick_genre, 0, g,
                                       tooltip=f"{label} · {n} samples"))
        self.genre_box.append(flow)
        if self.genre_sel:
            g = self.genre_sel[0]
            items = [(lbl, gid, f"{lbl} · {self.genre_counts.get(gid, 0)} samples")
                     for gid, lbl in genret.GENRES[g] if self.genre_counts.get(gid)]
            sub = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            sub.add_css_class("sh-sublevel")
            l = Gtk.Label(label=genret.LABEL[g] + " ›", xalign=0)
            l.add_css_class("sh-filter-label")
            sub.append(l)
            f = self._flow(2)
            short = {"afropop-and-afrobeats": "Afrobeats", "progressive-house": "Progressive",
                     "tearout": "Tearout", "big-room-house": "Big Room", "indie-electronic": "Indie Electronic",
                     "latin-american": "Latin", "middle-eastern": "Middle East", "lo-fi-hip-hop": "Lo-Fi Hip Hop",
                     "brazilian-funk": "Brazil Funk"}
            for label, gid, tip in items:
                on = len(self.genre_sel) > 1 and self.genre_sel[1] == gid
                f.append(self._chip(short.get(gid, label), on, self.pick_genre, 1, gid, tooltip=tip,
                                    cols=2, indent=9))
            sub.append(f)
            self.genre_box.append(sub)

    def pick_genre(self, depth: int, key: str) -> None:
        if len(self.genre_sel) > depth and self.genre_sel[depth] == key:
            self.genre_sel = self.genre_sel[:depth]
        else:
            self.genre_sel = self.genre_sel[:depth] + [key]
        self.render_genres()
        self.filters_changed()

    def filters_changed(self) -> None:
        self.update_summaries()
        self.apply_filters()

    def on_chip(self, btn: Gtk.ToggleButton, name: str, group: str) -> None:
        if btn.get_active():
            for b, n, g in self.chips:              # the opposite (same group) off
                if g == group and b is not btn and b.get_active():
                    b.handler_block_by_func(self.on_chip)
                    b.set_active(False)
                    b.handler_unblock_by_func(self.on_chip)
            self.active[group] = name
        elif self.active.get(group) == name:
            del self.active[group]
        self.filters_changed()

    def on_bpm(self) -> None:
        self.update_summaries()
        if self.bpm_timer:                              # while dragging, search only once the hand stops
            GLib.source_remove(self.bpm_timer)
        self.bpm_timer = GLib.timeout_add(250, self._bpm_apply)

    def _bpm_apply(self) -> bool:
        self.bpm_timer = 0
        self.apply_filters()
        return False

    def clear_filters(self) -> None:
        self._reset_filters()
        self.apply_filters()

    def _reset_filters(self) -> None:
        """All filter chips, the instrument/genre selections and the tempo range off (no reload)."""
        self.bpm.reset(notify=False)
        for b, _, _ in self.chips:
            b.handler_block_by_func(self.on_chip)
            b.set_active(False)
            b.handler_unblock_by_func(self.on_chip)
        self.active.clear()
        self.inst_sel, self.role, self.genre_sel = [], None, []
        self.render_inst()
        self.render_genres()
        self.update_summaries()

    def tag_query(self) -> str:
        parts = ["+" + n for n in self.active.values()]
        inst, tag, typ = self.inst_filter()
        if inst:
            parts.append("inst:" + inst)
        if tag:
            parts.append("tag:" + tag)
        if typ and "type" not in self.active:
            parts.append("+" + typ)
        if self.role:
            parts.append("tag:" + self.role)
        if self.genre_sel:
            parts.append("genre:" + self.genre_sel[-1])
        if not self.bpm.full:
            lo, hi = self.bpm.bounds()
            parts.append(f"bpm:{lo:g}-{hi:g}")
        return " ".join(parts)

    def apply_filters(self) -> None:
        """Text in the search: tags narrow the search over the whole library. Empty search: tags filter
        the visible folder (e.g. Liked Samples + Loop)."""
        if self.entry.get_text().strip():
            self.load_all(self.entry.get_text().strip())
        elif self._paged():
            self.load_all(under=self._under())      # whole library / a library folder: filtering in the service
        else:
            self.refilter()

    def _match(self, row: Row) -> bool:
        if self.live_query is not None or self._paged():
            return True                             # search / All view results are already filtered in the service
        if not self.bpm.full:
            lo, hi = self.bpm.bounds()
            if not (row.props.bpm >= 0 and lo <= row.props.bpm <= hi):
                return False
        inst, tag, typ = self.inst_filter()
        if inst and not soittimet.matches(row.inst_id, inst):
            return False
        if tag and tag not in row.itags:
            return False
        if typ == "loop" and row.props.stype != "Loop":
            return False
        if self.role and self.role not in row.itags:
            return False
        if self.genre_sel and self.genre_sel[-1] not in row.genres and \
                not any(genret.GROUP_OF.get(x) == self.genre_sel[-1] for x in row.genres):
            return False
        for group, name in self.active.items():
            if group == "type":
                want = next(t for n, t, _ in TYPE_TAGS if n == name)
                if row.props.stype != want:
                    return False
            elif row.tags is None or name not in row.tags:
                return False
        return True

    def refilter(self) -> None:
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self.update_count()

    def update_count(self) -> None:
        shown, total = self.sorted.get_n_items(), self.store.get_n_items()
        self.subtitle.set_label(f"{total} samples" if shown == total else f"{shown} of {total} samples")

    def on_side_selected(self, _lb, row) -> None:
        if self.sidebar_lock or row is None or row._path is None or row._path == self.folder:
            return
        if row._path == "FOLDERS_HEAD":            # opens/closes the tree; the view stays
            self.toggle_folders()
            return
        is_query = isinstance(row._path, str) and row._path.startswith("q:")
        if not is_query:
            self.base_view = row._path              # clearing the search returns here
            if self.entry.get_text():               # the sidebar view replaces the search
                self.set_search_text("")
        self.open_view(row._path)

    def open_view(self, view) -> None:
        if view == ALL:
            self.load_all()
        elif isinstance(view, str) and view.startswith("d:"):
            self.load_all(under=view[2:])
        elif isinstance(view, str) and view.startswith("q:"):
            self.set_search_text(view[2:])
            self.live_query = None
            self.load_all(view[2:])
        else:
            self.load_folder(view)

    def set_search_text(self, text: str) -> None:
        # The search field reports a change after a delay (200 ms) even though the handler is blocked:
        # mark the text so the delayed notification doesn't start the same search again.
        self._set_by_program = text
        self._last_search = text.strip()
        self.entry.handler_block_by_func(self.on_search_changed)
        self.entry.set_text(text)
        self.entry.handler_unblock_by_func(self.on_search_changed)

    # ------------------------------------------------------------ loading
    def load_folder(self, folder: Path, select: str | None = None) -> None:
        folder = Path(folder)
        if folder.parent == RESULTS_DIR and self.tag_query():
            self._reset_filters()                  # a Similar list shows all its matches: the filters go
        self.folder = folder
        self.generation += 1
        gen = self.generation
        ranked = (folder / MANIFEST).exists()
        files = []
        try:
            for p in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                if p.suffix.lower() in AUDIO_EXT and (p.is_file() or p.is_symlink()):
                    files.append(p)
        except OSError as e:
            self.show_toast(f"Cannot open folder: {e}")
        rows = []
        for i, p in enumerate(files):
            name = RANK_RE.sub("", p.name) if ranked else p.name
            rows.append(Row(str(p), i, name))
        self.live_query = None
        self.show_rows(rows, folder.name, gen, select)
        self.refresh_sidebar()

    def _paged(self) -> bool:
        """The view is loaded from the service page by page: All samples, a search or a library folder."""
        return self.folder in (ALL, SEARCH) or (isinstance(self.folder, str) and self.folder.startswith("d:"))

    def _under(self) -> str:
        """The library folder of a Folders view, otherwise ""."""
        return self.folder[2:] if isinstance(self.folder, str) and self.folder.startswith("d:") else ""

    def load_all(self, text: str = "", select: str | None = None, under: str = "") -> None:
        """The whole library, 100 at a time. Filters and tempo narrow it in the service. With text,
        the same whole library in relevance order (a search). under: only that library folder
        (any depth; the Folders tree)."""
        under = "" if text else under
        self.folder = SEARCH if text else ("d:" + under if under else ALL)
        self.sorted.set_sorter(None)               # the order comes from the service (also column sorting)
        if not text:
            self.live_query = None
        self.generation += 1
        self.pending_select = select
        self.browse = {"filters": self.tag_query(), "offset": 0, "total": None, "loading": False,
                       "order": self.sort_order or ("shuffle" if self.shuffled and not text else "path"),
                       "seed": GLib.random_int(), "gen": self.generation, "text": text, "folder": under}
        self.shuffle.set_visible(not text)
        self.unshuffle.set_visible(not text and self.shuffled)
        self.quiet = True
        self.store.remove_all()
        self.last_row = None
        self.sel_ids = set()
        self.quiet = False
        self.title.set_label(f"“{text}”" if text else under.rsplit("/", 1)[-1] if under else "All samples")
        self.subtitle.set_label("Searching…" if text else "Loading…")
        self.stack.set_visible_child_name("list")
        if text:
            self.sidebar_lock = True
            self.side_list.unselect_all()
            self.sidebar_lock = False
        else:
            self.refresh_sidebar()
        self.load_more()

    def set_shuffled(self, on: bool) -> None:
        self.shuffled = on                         # load_all picks a new seed every time
        self.clear_sort()
        self.load_all(under=self._under())

    SORT_FIELDS = {"Name": "name", "Instrument": "inst", "Genre": "genre", "Key": "key", "BPM": "bpm",
                   "Length": "dur", "Type": "type", "Pack": "pack"}

    def on_sort_changed(self, sorter, *_):
        """Column header click. All samples and search are loaded page by page, so the window can't
        sort them itself (only loaded rows): the service sorts the whole set and pages it."""
        if self._sort_guard:
            return
        col = sorter.get_primary_sort_column()
        field = self.SORT_FIELDS.get(col.get_title()) if col else None
        if field:
            desc = sorter.get_primary_sort_order() == Gtk.SortType.DESCENDING
            self.sort_order = f"sort:{field}:{'desc' if desc else 'asc'}"
        else:
            self.sort_order = ""
        if self._paged():
            if self.sort_order:
                self.shuffled = False
            self.load_all(self.browse["text"] if self.browse else "", under=self._under())

    def clear_sort(self) -> None:
        self.sort_order = ""
        self._sort_guard = True
        self.colview.sort_by_column(None, Gtk.SortType.ASCENDING)
        self._sort_guard = False

    def load_more(self) -> None:
        b = self.browse
        if not self._paged() or not b or b["loading"] or \
                (b["total"] is not None and b["offset"] >= b["total"]):
            return
        b["loading"] = True
        args = json.dumps({k: b[k] for k in ("filters", "offset", "order", "seed", "text", "folder")} | {"limit": PAGE})
        self.call("Browse", GLib.Variant("(s)", (args,)), lambda out: self.on_page(out, b), 60_000)

    def on_page(self, out: str, b: dict) -> None:
        b["loading"] = False
        if b is not self.browse or not self._paged():
            return
        self.spinner.set_visible(False)
        try:
            d = json.loads(out)
        except ValueError:
            return
        if "error" in d:
            if b["text"]:                              # e.g. a half-typed "key:"
                self.subtitle.set_label("Incomplete filter – keep typing (e.g. key:Am, bpm:124, +loop)")
            else:
                self.show_toast(d["error"])
            return
        start = b["offset"]
        rows = []
        for i, p in enumerate(d["paths"]):
            r = Row(p, start + i, Path(p).name)
            r.set_details(d["details"].get(p, {}))
            rows.append(r)
        b["offset"] += len(rows)
        b["total"] = d["total"]
        self.store.splice(self.store.get_n_items(), 0, rows)
        tot = f"{d['total']:,}".replace(",", " ")
        if b["text"] and d.get("mode") == "closest":
            self.subtitle.set_label(f"No exact matches · {tot} closest sounds")
        else:
            self.subtitle.set_label(f"{tot} results" if b["text"] else
                                    f"{tot} samples" if not b["filters"] else f"{tot} matching")
        if not d["total"]:
            self.stack.set_visible_child_name("empty")
        if start == 0 and rows and self.pending_select:   # clicked in Super search: select and play
            so = original_path(self.pending_select)
            self.pending_select = None
            for i in range(self.sorted.get_n_items()):
                if self.sorted.get_item(i).orig == so:
                    self.colview.scroll_to(i, None, Gtk.ListScrollFlags.SELECT | Gtk.ListScrollFlags.FOCUS, None)
                    break
        if start == 0 and rows and not self.entry_focused():
            self.colview.grab_focus()

    def show_rows(self, rows: list[Row], title: str, gen: int, select: str | None = None) -> None:
        self.sorted.set_sorter(self.colview.get_sorter())   # folders: all rows loaded -> sorted here
        self.quiet = True
        self.store.splice(0, self.store.get_n_items(), rows)
        self.last_row = None
        self.sel_ids = set()
        self.quiet = False
        self.title.set_label(title)
        self.shuffle.set_visible(False)
        self.unshuffle.set_visible(False)
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self.update_count()
        self.stack.set_visible_child_name("list" if rows else "empty")
        if rows and (select or not self.entry_focused()):
            target = 0
            if select:
                so = original_path(select)
                for i in range(self.sorted.get_n_items()):
                    r = self.sorted.get_item(i)
                    if r.orig == so or r.path == select:
                        target = i
                        break
            if target < self.sorted.get_n_items():   # filtering may hide everything (Details on the way)
                self.quiet = not select
                self.colview.scroll_to(target, None, Gtk.ListScrollFlags.SELECT | Gtk.ListScrollFlags.FOCUS,
                                       None)
                self.quiet = False
        if rows:
            self.call("Details", GLib.Variant("(as)", ([r.path for r in rows],)),
                      lambda out: self.on_details(out, gen), 120_000)
        if not self.entry_focused():
            self.colview.grab_focus()

    def entry_focused(self) -> bool:
        f = self.get_focus()
        return f is not None and (f is self.entry or f.is_ancestor(self.entry))

    def on_details(self, out: str, gen: int) -> None:
        if gen != self.generation:
            return
        try:
            d = json.loads(out)
        except ValueError:
            return
        if "error" in d:
            self.show_toast(f"Fetch: {d['error']}")
            return
        for i in range(self.store.get_n_items()):
            r = self.store.get_item(i)
            if r.path in d:
                r.set_details(d[r.path])
        if not self._paged():          # values arrived: re-sort (folders)
            self.sorted.set_sorter(None)
            self.sorted.set_sorter(self.colview.get_sorter())
        if (self.active or self.inst_sel or self.role or self.genre_sel or not self.bpm.full) and self.live_query is None:
            self.refilter()

    # ------------------------------------------------------------ search
    def focus_search(self) -> None:
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def on_search_changed(self, entry) -> None:
        if getattr(self, "_set_by_program", None) is not None and entry.get_text() == self._set_by_program:
            self._set_by_program = None            # text set by the program: the search has been done already
            return
        self._set_by_program = None
        text = entry.get_text().strip()
        prev, self._last_search = getattr(self, "_last_search", ""), text
        if not text:
            # Only deleting text is "clearing the search". SearchEntry also reports, after a delay, empty to
            # empty (e.g. at startup), which would otherwise take a Similar result folder to All samples.
            if not prev:
                return
            if self.folder == SEARCH or self.live_query is not None or self.folder != self.base_view:
                self.live_query = None
                self.open_view(self.base_view)        # back to the view chosen in the sidebar
            return
        self.live_query = text                     # saved to Recent only on Enter/↓/click
        self.spinner.set_visible(True)
        self.load_all(text)

    def commit_search(self) -> None:
        """Save the search to Recent searches (as the query: opening it searches again)."""
        q, self.live_query = self.live_query, None
        if not q:
            return
        rec = [r for r in self.ui_cfg.get("recent", []) if r.get("q") != q]
        rec.insert(0, {"q": q, "t": GLib.get_real_time() / 1e6})
        self.ui_cfg["recent"] = rec[:RECENT_MAX]
        save_ui_cfg(self.ui_cfg)
        self.refresh_sidebar()

    def on_search(self, entry) -> None:
        """Enter: save the search and move on to listen to the first result."""
        if entry.get_text().strip() and self.live_query is not None:
            self.commit_search()
            self.enter_list()

    def enter_list(self) -> None:
        if not self.store.get_n_items():
            return
        self.colview.grab_focus()
        if self.sel.get_selection().is_empty():
            self.colview.scroll_to(0, None, Gtk.ListScrollFlags.SELECT | Gtk.ListScrollFlags.FOCUS, None)

    def call(self, method: str, args: GLib.Variant, cb, timeout: int) -> None:
        def done(bus, res):
            try:
                out = bus.call_finish(res).unpack()[0]
            except GLib.Error as e:
                self.spinner.set_visible(False)
                self.show_toast(f"Fetch service did not respond: {e.message}")
                return
            cb(out)
        self.bus.call(BUS_NAME, OBJ_PATH, IFACE, method, args, GLib.VariantType("(s)"),
                      Gio.DBusCallFlags.NONE, timeout, None, done)

    # ------------------------------------------------------------ playback
    def selected_rows(self) -> list[Row]:
        bs = self.sel.get_selection()
        return [self.sorted.get_item(bs.get_nth(k)) for k in range(bs.get_size())]

    def current(self) -> Row | None:
        """For single-sample actions: the last selected (played) one, otherwise the first selected."""
        rows = self.selected_rows()
        if self.last_row is not None and self.last_row in rows:
            return self.last_row
        return rows[0] if rows else None

    def on_selected(self, *_):
        # the selection also changes on sorting (same row, new place): play only when
        # a new sample enters the selection (Shift+↓ plays the added one) or a single new one remains
        bs = self.sel.get_selection()
        if self._paged() and not bs.is_empty() and bs.get_maximum() >= self.sorted.get_n_items() - 20:
            self.load_more()
        rows = self.selected_rows()
        new = [r for r in rows if id(r) not in self.sel_ids]
        self.sel_ids = {id(r) for r in rows}
        n = len(rows)
        self.subtitle_sel(n)
        if len(new) == 1:
            row = new[0]
        elif n == 1:
            row = rows[0]
        else:
            return
        if row is self.last_row:
            return
        self.last_row = row
        if row and not self.quiet and self.live_query:
            self.commit_search()
        if row and not self.quiet and self.autoplay_on:
            self.play_row(row)

    def subtitle_sel(self, n: int) -> None:
        base = self.subtitle.get_label().split(" · ")[0]
        self.subtitle.set_label(base + (f" · {n} selected" if n > 1 else ""))

    def play_pos(self, pos: int) -> None:
        row = self.sorted.get_item(pos)
        if row:
            self.play_row(row)

    def play_row(self, row: Row, path: str | None = None) -> None:
        prev = self._played[0] if self._played else None
        self._played = (row, path or row.play_path)
        self.player.play(self._played[1], row.props.dur if row.props.dur >= 0 else None,
                         loop=self.loop_preview and row.props.stype == "Loop")
        self.warm_around(row, prev)

    def warm_around(self, row: Row, prev: Row | None) -> None:
        """Fetch the next rows in the direction of travel (and one behind) while this one plays."""
        n = self.sorted.get_n_items()
        pos = next((k for k in range(self.sel.get_selection().get_minimum(), min(n, self.sel.get_selection().get_maximum() + 1))
                    if self.sorted.get_item(k) is row), None) if not self.sel.get_selection().is_empty() else None
        if pos is None:
            return
        back = prev is not None and pos + 1 < n and self.sorted.get_item(pos + 1) is prev
        order = [pos - 1, pos - 2, pos - 3, pos + 1] if back else [pos + 1, pos + 2, pos + 3, pos - 1]
        self.warmer.warm([self.sorted.get_item(k).play_path for k in order if 0 <= k < n])

    def on_player_option(self, r, _pspec, key: str) -> None:
        on = r.get_active()
        if key == "autoplay":
            self.autoplay_on = on
        else:
            self.loop_preview = on
            if self._played:            # the one playing follows at once
                self.player.loop = on and self._played[0].props.stype == "Loop"
        self.ui_cfg[key] = on
        save_ui_cfg(self.ui_cfg)

    def on_player_change(self, error: str | None = None) -> None:
        if error and self._played and self._played[1] != self._played[0].path and os.path.exists(self._played[0].path):
            row, self._played = self._played[0], None  # the original is gone: the entry in this folder
            self.play_row(row, row.path)
            return
        st = self.player.state
        self.play_btn.set_icon_name("media-playback-pause-symbolic" if st == "playing"
                                    else "media-playback-start-symbolic")
        if self.player.path:
            self.now.set_label(RANK_RE.sub("", Path(self.player.path).name))
        if error:
            self.show_toast(f"Cannot play: {error}")

    def tick(self) -> bool:
        if self.player.state in ("playing", "paused", "ended"):
            pos, dur = self.player.position()
            if self.player.state == "ended":
                pos = dur
            if dur > 0:
                self.scale.get_adjustment().set_upper(dur)
                self.scale.set_value(pos)
            self.time.set_label(f"{fmt_time(pos) or '0.0 s'} / {fmt_time(dur)}")
        return True

    def on_scale(self, _scale, _scroll, value) -> bool:
        self.player.seek(value)
        return False

    # ------------------------------------------------------------ actions
    def toggle_like(self, row: Row) -> None:
        try:
            # hard link to the original (not to the result folder's link, which goes away in cleanup)
            now = LIKED.toggle(row.orig if os.path.exists(row.orig) else row.path)
        except OSError as e:
            self.show_toast(f"Liked: {e}")
            return
        for i in range(self.store.get_n_items()):     # the same sample can be in the list only once,
            r = self.store.get_item(i)                # but update them all to be safe
            if r.orig == row.orig:
                r.props.liked = now
        self.show_toast(("Added to " if now else "Removed from ") + LIKED_DIR.name, timeout=1)

    def act_like(self, *_):
        rows = self.selected_rows()
        if len(rows) <= 1:
            if rows:
                self.toggle_like(rows[0])
            return
        like = not all(r.props.liked for r in rows)    # some not liked -> like them all
        done = 0
        for r in rows:
            try:
                p = r.orig if os.path.exists(r.orig) else r.path
                (LIKED.add if like else LIKED.remove)(p)
                r.props.liked = like
                done += 1
            except OSError as e:
                self.show_toast(f"Liked: {e}")
                break
        self.show_toast(f"{done} {'added to' if like else 'removed from'} {LIKED_DIR.name}", timeout=2)

    def act_similar(self, *_):
        if (row := self.current()):
            try:
                subprocess.Popen([SIMILAR_CMD, row.orig], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as e:
                self.show_toast(f"Cannot open Similar: {e}")

    def act_edit_tags(self, *_):
        rows = self.selected_rows()
        if rows:
            TagEditor(self, rows).present(self)

    def on_tags_saved(self, rows: list["Row"], out: dict, reset: bool) -> None:
        for r in rows:
            if r.orig in out:
                r.set_details(out[r.orig])
        n = len(rows)
        what = "Automatic tags restored" if reset else "Tags saved"
        self.show_toast(what + (f" for {n} samples" if n > 1 else ""), timeout=2)
        if self.live_query is None and self.folder != ALL and not self._under():
            self.refilter()

    def act_show(self, *_):
        if (row := self.current()):
            subprocess.Popen(["nautilus", "--select", row.orig], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def act_open(self, *_):
        if (row := self.current()):
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(row.orig).get_uri(), None)

    def on_active(self, *_):
        # likes may change in Files: refresh when the window gets focus
        if self.is_active():
            for i in range(self.store.get_n_items()):
                r = self.store.get_item(i)
                v = LIKED.has(r.orig) or (r.path != r.orig and LIKED.has(r.path))   # no stat per row (network)
                if r.props.liked != v:
                    r.props.liked = v

    def show_toast(self, text: str, timeout: int = 3) -> None:
        self.toast.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout))

    # ------------------------------------------------------------ keys
    def on_key(self, _ctrl, keyval, _code, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and keyval in (Gdk.KEY_f, Gdk.KEY_l):
            self.focus_search()
            return True
        focus = self.get_focus()
        typing = focus is not None and (focus is self.entry or focus.is_ancestor(self.entry))
        if typing:
            if keyval == Gdk.KEY_Escape:
                self.colview.grab_focus()
                return True
            if keyval == Gdk.KEY_Down and self.store.get_n_items():
                self.commit_search()
                self.enter_list()
                return True
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if keyval == Gdk.KEY_space:
            self.player.toggle()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if (row := self.current()):
                self.play_row(row)
        elif keyval == Gdk.KEY_Left:
            self.player.seek_rel(-0.1)
        elif keyval == Gdk.KEY_Right:
            self.player.seek_rel(0.1)
        elif keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self.act_like()
        elif keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.act_similar()
        elif keyval in (Gdk.KEY_e, Gdk.KEY_E):
            self.act_edit_tags()
        elif keyval == Gdk.KEY_slash:
            self.focus_search()
        elif keyval == Gdk.KEY_Escape:
            self.player.stop()
        else:
            return False
        return True


# ---------------------------------------------------------------- tag editing
class TagEditor(Adw.Dialog):
    """Correct the samples' type, instrument and style tags. Saved to korjaukset.json (by the service)
    and also used as examples for samples estimated from the audio. For several: only changed fields."""

    def __init__(self, win: "ResultsWindow", rows: list["Row"]):
        super().__init__(title="Edit tags", content_width=440, content_height=620)
        self.win, self.rows = win, rows
        tmap = {"Loop": "loop", "One-shot": "oneshot"}
        same = lambda f: f(rows[0]) if all(f(r) == f(rows[0]) for r in rows) else None   # noqa: E731
        self.type = same(lambda r: tmap.get(r.props.stype))
        self.inst = same(lambda r: r.inst_id) or ""
        self.path = self._path_of(self.inst)
        self.tags = set.intersection(*(set(r.itags) for r in rows))
        self.touched: set[str] = set()
        self.touched_tags: set[str] = set()
        tb = Adw.ToolbarView()
        tb.add_top_bar(Adw.HeaderBar())
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=16, margin_end=16,
                        margin_bottom=16)
        name = Gtk.Label(label=rows[0].props.name if len(rows) == 1 else f"{len(rows)} samples selected",
                         xalign=0, ellipsize=3)
        name.add_css_class("heading")
        outer.append(name)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.append(Gtk.ScrolledWindow(child=self.box, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        btns = Gtk.Box(spacing=8, margin_top=8)
        reset = Gtk.Button(label="Automatic", tooltip_text="Forget your changes for the selected samples")
        reset.connect("clicked", lambda *_: self.save(None))
        btns.append(reset)
        btns.append(Gtk.Box(hexpand=True))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self.save(self.changes()))
        btns.append(cancel)
        btns.append(save)
        outer.append(btns)
        tb.set_content(outer)
        self.set_child(tb)
        self.render()

    @staticmethod
    def _path_of(node: str) -> list[str]:
        parts = node.split("/") if node else []
        return ["/".join(parts[:i + 1]) for i in range(len(parts))]

    def _label(self, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.add_css_class("sh-filter-label")
        return lbl

    def render(self) -> None:
        while (c := self.box.get_first_child()) is not None:
            self.box.remove(c)
        w = self.win
        self.box.append(self._label("Type"))
        f = w._flow(len(TYPE_TAGS))
        for t, _, lbl in TYPE_TAGS:
            f.append(w._chip(lbl, self.type == t, self.pick_type, t))
        self.box.append(f)
        self.box.append(self._label("Instrument"))
        f = w._flow()
        for node in soittimet.MAINS:
            f.append(w._chip(soittimet.chip_label(node), self.path[:1] == [node], self.pick, 0, node,
                             tooltip=soittimet.LABEL[node]))
        self.box.append(f)
        for depth, node in enumerate(self.path):
            kids = soittimet.children(node)
            if not kids:
                break
            sub = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            sub.add_css_class("sh-sublevel")
            sub.append(self._label(soittimet.LABEL[node] + " ›"))
            f = w._flow()
            for kid in kids:
                on = len(self.path) > depth + 1 and self.path[depth + 1] == kid
                f.append(w._chip(soittimet.chip_label(kid), on, self.pick, depth + 1, kid,
                                 tooltip=soittimet.label_path(kid)))
            sub.append(f)
            self.box.append(sub)
        for group, tags in soittimet.TAG_GROUPS:
            self.box.append(self._label(group))
            f = w._flow()
            for t, lbl in tags:
                est = t in self.tags and t not in self.touched_tags and all(t in r.itags_est for r in self.rows)
                f.append(w._chip(lbl + (" ~" if est else ""), t in self.tags, self.pick_tag, t,
                                 tooltip="Estimated from the sound" if est else lbl))
            self.box.append(f)

    def pick_type(self, t):
        self.type = t
        self.touched.add("type")
        self.render()

    def pick(self, depth, node):
        if len(self.path) > depth and self.path[depth] == node and depth > 0:
            self.path = self.path[:depth]
        else:
            self.path = self.path[:depth] + [node]
        self.inst = self.path[-1] if self.path else ""
        self.touched.add("inst")
        self.render()

    def pick_tag(self, t):
        self.tags ^= {t}
        self.touched_tags.add(t)
        self.touched.add("tags")
        self.render()

    def changes(self) -> dict:
        full = {"type": self.type, "inst": self.inst, "tags": sorted(self.tags)}
        if len(self.rows) == 1:
            return {k: v for k, v in full.items() if v or k == "tags"}
        return {k: full[k] for k in self.touched}

    def save(self, tags) -> None:
        if tags is not None and not tags:
            self.close()
            return

        def done(out):
            try:
                d = json.loads(out)
            except ValueError:
                return
            if "error" in d:
                self.win.show_toast(d["error"])
                return
            self.win.on_tags_saved(self.rows, d, tags is None)
            self.close()
        paths = [r.orig for r in self.rows]
        self.win.call("SetTagsMany", GLib.Variant("(ass)", (paths, json.dumps(tags))), done, 120_000)


class PackGenres(Adw.Dialog):
    """Pack genres: recognised from the name, confirmed, and analysis suggestions."""

    def __init__(self, win: "ResultsWindow"):
        super().__init__(title="Pack genres", content_width=560, content_height=680)
        self.win, self.info, self.editing = win, {}, None
        tb = Adw.ToolbarView()
        tb.add_top_bar(Adw.HeaderBar())
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12, margin_end=12,
                        margin_bottom=12)
        hint = Gtk.Label(label="Genre is set per pack. Packs whose name says the genre are done; "
                               "for the rest, accept the suggestion or pick genres yourself.",
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        outer.append(hint)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.append(Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        tb.set_content(outer)
        self.set_child(tb)
        self.win.call("Packs", None, self.on_packs, 120_000)

    def on_packs(self, out: str) -> None:
        try:
            self.info = json.loads(out)
        except ValueError:
            return
        if "error" in self.info:
            self.win.show_toast(self.info["error"])
            return
        self.render()

    def render(self) -> None:
        while (c := self.list.get_first_child()) is not None:
            self.list.remove(c)
        # unconfirmed first, then the rest; the biggest at the top
        order = sorted(self.info.items(), key=lambda kv: (kv[1]["source"] != "", -kv[1]["n"]))
        for pack, d in order:
            if d["n"] < 2:
                continue
            self.list.append(self._row(pack, d))

    def _row(self, pack: str, d: dict) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        head = Gtk.Box(spacing=8)
        name = Gtk.Label(label=pack, xalign=0, ellipsize=3, hexpand=True, tooltip_text=pack)
        name.add_css_class("heading")
        n = Gtk.Label(label=f"{d['n']} samples")
        n.add_css_class("dim-label")
        n.add_css_class("caption")
        head.append(name)
        head.append(n)
        box.append(head)
        line = Gtk.Box(spacing=6)
        src = {"user": "confirmed", "name": "from pack name", "": ""}[d["source"]]
        txt = ", ".join(genret.LABEL.get(g, g) for g in d["genres"]) or "No genre"
        g = Gtk.Label(label=txt + (f"  ({src})" if src else ""), xalign=0, hexpand=True, ellipsize=3)
        if not d["genres"]:
            g.add_css_class("dim-label")
        line.append(g)
        if not d["genres"] and d.get("suggest"):
            sug = d["suggest"][0]
            acc = Gtk.Button(label=f"Accept: {genret.LABEL.get(sug, sug)}",
                             tooltip_text="Suggested from how the pack sounds. Other close matches: " +
                             ", ".join(genret.LABEL.get(x, x) for x in d["suggest"][1:]))
            acc.add_css_class("suggested-action")
            acc.add_css_class("caption")
            acc.connect("clicked", lambda *_: self.set(pack, [sug]))
            line.append(acc)
        ed = Gtk.Button(label="Edit…" if self.editing != pack else "Done")
        ed.add_css_class("flat")
        ed.add_css_class("caption")
        ed.connect("clicked", lambda *_: self.toggle_edit(pack))
        line.append(ed)
        box.append(line)
        if self.editing == pack:
            box.append(self._editor(pack, d))
        return box

    def toggle_edit(self, pack: str) -> None:
        self.editing = None if self.editing == pack else pack
        self.sel = set(self.info[pack]["genres"])
        self.group = next((genret.GROUP_OF[g] for g in self.sel if g in genret.GROUP_OF), None)
        self.render()

    def _editor(self, pack: str, d: dict) -> Gtk.Widget:
        w = self.win
        ed = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ed.add_css_class("sh-sublevel")
        f = w._flow()
        for g, label in genret.GROUPS:
            on = self.group == g or any(genret.GROUP_OF.get(x) == g for x in self.sel)
            f.append(w._chip(label.replace(" / ", "/"), on, self.pick_group, g, tooltip=label))
        ed.append(f)
        if self.group:
            f = w._flow()
            for gid, label in genret.GENRES[self.group]:
                f.append(w._chip(label, gid in self.sel, self.pick_genre, gid))
            ed.append(f)
        btns = Gtk.Box(spacing=6)
        clear = Gtk.Button(label="Automatic", tooltip_text="Use the genre from the pack name (or none)")
        clear.add_css_class("caption")
        clear.connect("clicked", lambda *_: self.set(pack, None))
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.add_css_class("caption")
        save.connect("clicked", lambda *_: self.set(pack, sorted(self.sel)))
        btns.append(clear)
        btns.append(Gtk.Box(hexpand=True))
        btns.append(save)
        ed.append(btns)
        return ed

    def pick_group(self, g):
        self.group = None if self.group == g else g
        self.render()

    def pick_genre(self, gid):
        self.sel ^= {gid}
        self.render()

    def set(self, pack: str, genres) -> None:
        def done(out):
            try:
                d = json.loads(out)
            except ValueError:
                return
            if "error" in d:
                self.win.show_toast(d["error"])
                return
            self.info[pack] = d
            self.editing = None
            self.render()
            self.win.load_genre_counts()
            self.win.show_toast(f"{pack}: genres saved", timeout=1)
        self.win.call("SetPackGenres", GLib.Variant("(ss)", (pack, json.dumps(genres))), done, 120_000)


# ---------------------------------------------------------------- application
class SettingsDialog(Adw.PreferencesDialog):
    """Settings (the gear in the sidebar header): appearance and the library's folders."""

    def __init__(self, win: "ResultsWindow"):
        super().__init__(title="Settings", search_enabled=False, content_width=560, content_height=640)
        self.win = win
        self.app = win.get_application()
        self.a = load_appearance()
        page = Adw.PreferencesPage(title="General", icon_name="emblem-system-symbolic")
        look = Adw.PreferencesGroup(title="Appearance",
                                    description="The Fetch plugin follows these too.")
        self._timer = 0
        self.scales = {}
        for key, title, sub in (("ui_scale", "UI scaling", "Everything: text, icons and spacing"),
                                ("font_scale", "Font size", "Text only")):
            row = Adw.ActionRow(title=title, subtitle=sub)
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, SCALE_MIN * 100, SCALE_MAX * 100, 5)
            sc.set_value(round(self.a[key] * 100))
            sc.set_size_request(200, -1)
            sc.set_valign(Gtk.Align.CENTER)
            sc.set_draw_value(True)
            sc.set_value_pos(Gtk.PositionType.RIGHT)
            sc.set_format_value_func(lambda _s, v: f"{v:.0f} %")
            sc.add_mark(100, Gtk.PositionType.BOTTOM, None)
            sc.connect("value-changed", self._changed, key)
            row.add_suffix(sc)
            look.add(row)
            self.scales[key] = sc
        reset = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER, tooltip_text="Back to 100 %")
        reset.add_css_class("flat")
        reset.connect("clicked", lambda *_: [sc.set_value(100) for sc in self.scales.values()])
        look.set_header_suffix(reset)
        page.add(look)

        # Library folders: the roots of the index; Add folder analyses a new one in the background
        self.lib = Adw.PreferencesGroup(title="Library folders",
                                        description="Samples in these folders are analysed and searchable.")
        add = Gtk.Button(label="Add folder…", valign=Gtk.Align.CENTER)
        add.add_css_class("suggested-action")
        add.connect("clicked", lambda *_: self._pick_folder())
        self.lib.set_header_suffix(add)
        self.status_row = Adw.ActionRow(title="", visible=False)
        self.status_spin = Adw.Spinner(visible=False)
        self.status_row.add_prefix(self.status_spin)
        self.lib.add(self.status_row)
        self.root_rows: list[Adw.ActionRow] = []
        page.add(self.lib)
        self.add(page)
        self._load_roots()
        self._poll = GLib.timeout_add_seconds(2, self._poll_status)
        self.connect("closed", lambda *_: GLib.source_remove(self._poll))
        self._poll_status()

    def _load_roots(self) -> None:
        def done(out: str) -> None:
            try:
                roots = json.loads(out).get("folders", [])
            except ValueError:
                return
            for r in self.root_rows:
                self.lib.remove(r)
            self.root_rows = []
            for r in roots:
                n = f"{r['count']:,}".replace(",", " ")
                row = Adw.ActionRow(title=r["name"], subtitle=f"{r['path']} · {n} samples")
                row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
                rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text="Remove from the library")
                rm.add_css_class("flat")
                rm.connect("clicked", lambda _b, r=r: self._confirm_remove(r))
                row.add_suffix(rm)
                self.lib.add(row)
                self.root_rows.append(row)
        self.win.call("Folders", GLib.Variant("(s)", ("",)), done, 60_000)

    def _confirm_remove(self, r: dict) -> None:
        """Remove folder: only out of Fetch's library (RemoveFolder); the files on disk stay."""
        n = f"{r['count']:,}".replace(",", " ")
        dlg = Adw.AlertDialog(heading=f"Remove “{r['name']}” from the library?",
                              body=f"Its {n} samples will no longer be searchable in Fetch. The files on disk are not "
                                   "deleted, and your likes and tag corrections are kept. Adding the folder again "
                                   "analyses it again.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Remove")
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def answered(d, res):
            if d.choose_finish(res) == "remove":
                self.win.call("RemoveFolder", GLib.Variant("(s)", (r["path"],)), self._removed, 120_000)
        dlg.choose(self, None, answered)

    def _removed(self, out: str) -> None:
        try:
            res = json.loads(out)
        except ValueError:
            return
        if res.get("error"):
            self.status_row.set_title("Could not remove the folder")
            self.status_row.set_subtitle(res["error"][:300])
        else:
            n = f"{res.get('removed', 0):,}".replace(",", " ")
            self.status_row.set_title(f"Removed {Path(res.get('folder', '')).name or 'the folder'} from the library")
            self.status_row.set_subtitle(f"{n} samples left the index; searches drop them within a couple "
                                         "of minutes. The files on disk were not touched.")
            self._load_roots()
            self.win.after_library_change()
        self.status_spin.set_visible(False)
        self.status_row.set_visible(True)
        self._hold_status = time.time() + 30

    def _pick_folder(self) -> None:
        dlg = Gtk.FileDialog(title="Add a folder to the library", modal=True)

        def picked(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return                                  # cancelled
            if f and f.get_path():
                self.win.call("AddFolder", GLib.Variant("(s)", (f.get_path(),)), self._show_status, 30_000)
        dlg.select_folder(self.win, None, picked)

    def _poll_status(self) -> bool:
        self.win.call("IndexStatus", None, self._show_status, 10_000)
        return True

    def _show_status(self, out: str) -> None:
        try:
            st = json.loads(out)
        except ValueError:
            return
        state = st.get("state", "")
        if state != "running" and time.time() < getattr(self, "_hold_status", 0):
            return                                      # "Removed …" stays readable for a while
        if st.get("error") and state != "running":
            self.status_row.set_title("Analysis stopped")
            self.status_row.set_subtitle(st["error"][:300])
        elif state == "running":
            name = Path(st.get("folder") or "").name or "…"
            step = f" · step {st['step']}/{st['steps']}" if st.get("steps") else ""
            queued = len(st.get("queue") or []) - 1
            more = f" · {queued} more folder{'s' if queued > 1 else ''} queued" if queued > 0 else ""
            self.status_row.set_title(f"Analysing {name}{step}{more}")
            self.status_row.set_subtitle(" ".join(x for x in (st.get("title", ""), st.get("line", "")) if x)[:300])
        elif state == "done" and time.time() - float(st.get("finished") or 0) < 600:
            self.status_row.set_title("Analysis finished")
            self.status_row.set_subtitle("The new samples appear in Fetch within a minute.")
            if not getattr(self, "_reloaded", False):
                self._reloaded = True
                self._load_roots()
        else:
            self.status_row.set_visible(False)
            return
        self.status_row.set_visible(True)
        self.status_spin.set_visible(state == "running")
        if state == "running":
            self._reloaded = False

    def _changed(self, sc: Gtk.Scale, key: str) -> None:
        v = round(sc.get_value() / 5) * 5 / 100      # 5 % steps
        if abs(v - self.a[key]) < 1e-6:
            return
        self.a[key] = v
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(250, self._apply)   # after the slider settles

    def _apply(self) -> bool:
        self._timer = 0
        save_appearance(self.a)
        self.app.apply_appearance(self.a)
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        Gst.init(None)
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        dpi = Gtk.Settings.get_default().props.gtk_xft_dpi
        self.base_dpi = dpi if dpi > 0 else 96 * 1024       # the desktop's own text size
        self.apply_appearance(load_appearance())

    def apply_appearance(self, a: dict) -> None:
        """UI scale: text, our paddings and icons and the logo; text size: text only (on top of the
        UI scale). Text goes through Gtk.Settings' DPI, which the rest of GTK's sizes follow."""
        ui, font = a["ui_scale"], a["font_scale"]
        Gtk.Settings.get_default().props.gtk_xft_dpi = round(self.base_dpi * ui * font)
        self.css.load_from_string(scaled_css(CSS, ui))
        if self.win is not None:
            self.win.rescale(a)

    def do_command_line(self, cmd):
        args = cmd.get_arguments()[1:]
        select = None
        if "--select" in args:
            k = args.index("--select")
            select = args[k + 1] if k + 1 < len(args) else None
            del args[k:k + 2]
        query = None
        if "--query" in args:
            k = args.index("--query")
            query = args[k + 1] if k + 1 < len(args) else ""
            del args[k:k + 2]
        if not self.win:
            self.win = ResultsWindow(self)
            self.win.rescale(load_appearance())
        if query:                                       # from Super search: a live search, not a folder of 100 results
            self.win.set_search_text(query)
            self.win.live_query = query
            self.win.commit_search()
            self.win.load_all(query, select)
        elif args:
            self.win.load_folder(Path(args[0]), select)
            if not str(args[0]).startswith(str(RESULTS_DIR)):
                self.win.base_view = Path(args[0])      # a collection etc. -> clearing the search returns to it
        else:
            self.win.base_view = ALL
            self.win.open_view(ALL)
        self.win.present()
        return 0


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
