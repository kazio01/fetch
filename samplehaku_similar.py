#!/usr/bin/env python3
"""
samplehaku_similar – a small "Similar sounds" window opened from the Files menu.

    samplehaku_similar.py /path/to/sample.wav

Three toggles (Timbre / Key / BPM), at least one on; the choice is remembered
(~/.config/samplehaku/similar.json). Enter = search, Esc = close. The search runs in
the Fetch D-Bus service and the results open in the Fetch window (or in Files as a folder).

System Python + GTK 4 + libadwaita (not the Fetch .venv).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

BUS_NAME = "fi.samplehaku.Search"
OBJ_PATH = "/fi/samplehaku/SearchProvider"
IFACE = "fi.samplehaku.Search"
CFG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "samplehaku/similar.json"
CONDS = (("timbre", "Timbre"), ("key", "Key"), ("bpm", "BPM"))

# Selected condition in the accent colour (same blue as the Search button), unselected in grey.
CSS = """
button.cond:checked { background-color: var(--accent-bg-color); color: var(--accent-fg-color); }
button.cond:checked:hover { background-image: image(rgb(255 255 255 / 10%)); }
button.cond:checked:disabled { opacity: 0.6; }
"""


def load_css() -> None:
    prov = Gtk.CssProvider()
    prov.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def load_cfg() -> dict[str, bool]:
    cfg = {"timbre": True, "key": False, "bpm": False}
    try:
        cfg.update({k: bool(v) for k, v in json.loads(CFG.read_text()).items() if k in cfg})
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(cfg: dict[str, bool]) -> None:
    try:
        CFG.parent.mkdir(parents=True, exist_ok=True)
        CFG.write_text(json.dumps(cfg))
    except OSError:
        pass


class SimilarWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, path: str):
        super().__init__(application=app, title="Similar sounds", resizable=False)
        self.path = path
        self.cfg = load_cfg()
        self.known = {"timbre": True, "key": None, "bpm": None}   # None = not known yet
        self.busy = False
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)

        name = Gtk.Label(label=Path(path).name, xalign=0, ellipsize=3, max_width_chars=38)
        name.add_css_class("heading")
        box.append(name)

        row = Gtk.Box(spacing=0, homogeneous=True)
        row.add_css_class("linked")
        self.buttons: dict[str, Gtk.ToggleButton] = {}
        for key, label in CONDS:
            b = Gtk.ToggleButton(label=label, active=self.cfg[key])
            b.add_css_class("cond")
            b.connect("toggled", self.on_toggle, key)
            self.buttons[key] = b
            row.append(b)
        box.append(row)

        self.status = Gtk.Label(label="", xalign=0, wrap=True, max_width_chars=40)
        self.status.add_css_class("dim-label")
        self.status.add_css_class("caption")
        box.append(self.status)

        bottom = Gtk.Box(spacing=8)
        self.spinner = Gtk.Spinner()
        bottom.append(self.spinner)
        bottom.append(Gtk.Box(hexpand=True))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        bottom.append(cancel)
        self.search = Gtk.Button(label="Search")
        self.search.add_css_class("suggested-action")
        self.search.connect("clicked", self.on_search)
        bottom.append(self.search)
        box.append(bottom)

        self.set_content(box)
        self.set_default_widget(self.search)          # Enter = search

        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                      action=Gtk.CallbackAction.new(lambda *_: self.close() or True)))
        self.add_controller(esc)

        self.refresh()
        self.call("Info", GLib.Variant("(s)", (path,)), self.on_info, timeout=60_000)
        self.set_status("Reading sample info…")

    # ------------------------------------------------------------ state
    def selected(self) -> list[str]:
        return [k for k, _ in CONDS if self.buttons[k].get_active()]

    def refresh(self) -> None:
        sel = self.selected()
        for k, b in self.buttons.items():
            # the last selected one can't be turned off; an unknown condition can't be selected
            b.set_sensitive(not self.busy and self.known[k] is not False
                            and not (b.get_active() and len(sel) == 1))
        self.search.set_sensitive(not self.busy and bool(sel))

    def set_status(self, text: str) -> None:
        self.status.set_label(text)
        self.status.set_visible(bool(text))

    def on_toggle(self, button: Gtk.ToggleButton, key: str) -> None:
        if not self.selected():                       # safeguard: always at least one
            button.set_active(True)
            return
        self.cfg[key] = button.get_active()
        save_cfg({k: self.buttons[k].get_active() for k, _ in CONDS})
        self.refresh()

    # ------------------------------------------------------------ D-Bus
    def call(self, method: str, args: GLib.Variant, cb, timeout: int) -> None:
        self.bus.call(BUS_NAME, OBJ_PATH, IFACE, method, args, GLib.VariantType("(s)"),
                      Gio.DBusCallFlags.NONE, timeout, None, cb)

    def on_info(self, bus, res) -> None:
        try:
            info = json.loads(bus.call_finish(res).unpack()[0])
        except (GLib.Error, ValueError) as e:
            self.set_status(f"Fetch service did not respond: {getattr(e, 'message', e)}")
            return
        if info.get("error"):
            self.set_status(info["error"])
            return
        if not info.get("indexed"):
            self.known = {"timbre": False, "key": False, "bpm": False}
            self.set_status("This file is not in the samplehaku index yet.")
            self.search.set_sensitive(False)
            for b in self.buttons.values():
                b.set_sensitive(False)
            return
        est = lambda flag: " ~" if flag else ""    # noqa: E731  (~ = estimated from the audio)
        self.known["key"] = bool(info.get("key"))
        self.known["bpm"] = info.get("bpm") is not None
        if self.known["key"]:
            self.buttons["key"].set_label(f"Key · {info['key']}{est(info.get('key_est'))}")
        if self.known["bpm"]:
            self.buttons["bpm"].set_label(f"BPM · {info['bpm']:g}{est(info.get('bpm_est'))}")
        unknown = [lbl for k, lbl in CONDS if self.known[k] is False]
        for k in ("key", "bpm"):                      # unknown condition off for this time
            if self.known[k] is False and self.buttons[k].get_active():
                self.buttons[k].handler_block_by_func(self.on_toggle)
                self.buttons[k].set_active(False)
                self.buttons[k].handler_unblock_by_func(self.on_toggle)
        if not self.selected():
            self.buttons["timbre"].set_active(True)
        self.set_status(f"{' and '.join(unknown)} unknown for this file." if unknown else "")
        self.refresh()

    def on_search(self, *_):
        sel = self.selected()
        if not sel or self.busy:
            return
        self.busy = True
        self.refresh()
        self.spinner.start()
        self.set_status("Searching…")
        self.call("Similar", GLib.Variant("(ss)", (self.path, "+".join(sel))), self.on_done,
                  timeout=180_000)

    def on_done(self, bus, res) -> None:
        self.spinner.stop()
        self.busy = False
        try:
            out = bus.call_finish(res).unpack()[0]
        except GLib.Error as e:
            self.set_status(f"Fetch service did not respond: {e.message}")
            self.refresh()
            return
        if out.startswith(("ERROR:", "VIRHE:")):   # VIRHE: = older service
            self.set_status(out[6:].strip())
            self.refresh()
            return
        # results to the Fetch window; if it's missing, to Files as before
        cmd = os.environ.get("SAMPLEHAKU_RESULTS_CMD", str(Path.home() / ".local/bin/samplehaku-results"))
        if os.environ.get("SAMPLEHAKU_RESULTS_UI", "window") == "window" and os.access(cmd, os.X_OK):
            import subprocess
            subprocess.Popen([cmd, out], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(out).get_uri(), None)
        self.close()


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    path = os.path.abspath(sys.argv[1])
    app = Adw.Application(application_id="fi.samplehaku.Similar",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("startup", lambda a: load_css())
    app.connect("activate", lambda a: SimilarWindow(a, path).present())
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
