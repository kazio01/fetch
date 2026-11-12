"""Screenshot of the Fetch window (for comparing with the VST3 plugin's look).
    /usr/bin/python3 -I testit/kuvakaappaus.py out.png [--dark] [--width 1150 --height 700]
Runs the window with a separate application id and temporary settings; renders it offscreen to a PNG."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(dir=Path.home() / ".cache", prefix="fetch-shot-"))
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

import samplehaku_tulokset as t  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

out = sys.argv[1]
dark = "--dark" in sys.argv
W = int(sys.argv[sys.argv.index("--width") + 1]) if "--width" in sys.argv else 1150
H = int(sys.argv[sys.argv.index("--height") + 1]) if "--height" in sys.argv else 700
t.APP_ID = "fi.samplehaku.ResultsShot"
app = t.App()


def shoot(win) -> bool:
    if win.browse is not None and win.browse.get("loading"):
        return True                         # wait for the first page
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable(widget=win).snapshot(snap, win.get_width(), win.get_height())
    node = snap.to_node()
    tex = win.get_native().get_renderer().render_texture(node, None)
    tex.save_to_png(out)
    print("saved", out, win.get_width(), win.get_height())
    app.quit()
    return False


def start() -> bool:
    if app.win is None:
        return True
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    app.win.set_default_size(W, H)
    GLib.timeout_add(2500, shoot, app.win)
    return False


GLib.timeout_add(100, start)
app.run(["samplehaku-results"])
