"""4) Results window: a scripted session with a separate application id, temporary settings and
a temporary Liked folder. Exceptions raised in the background count as failures.
Runs against the user's own indexed library; checks that need specific content are skipped with a warning."""
from __future__ import annotations

import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

TMP = Path(tempfile.mkdtemp(dir=Path.home() / ".cache", prefix="fetch-test-"))
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")               # ikkuna.json (layout, recent)
os.environ["SAMPLEHAKU_COLLECTIONS"] = str(TMP / "Collections")    # likes
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from apu import check, otsikko, warn, yhteenveto  # noqa: E402

import samplehaku_tulokset as t  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

t.APP_ID = "fi.samplehaku.ResultsTest"          # doesn't collide with the user's open window
t.PAD_DIR = TMP / "padded"                      # padded copies of short samples
EXCEPTIONS: list[str] = []
_hook = sys.excepthook
Q = "kick"                                      # a search almost every library has


def hook(tp, val, tb):
    EXCEPTIONS.append(f"{tp.__name__}: {val}")
    _hook(tp, val, tb)


sys.excepthook = hook
app = t.App()
W: list = []


def _descendants(wd):
    c = wd.get_first_child()
    while c is not None:
        yield c
        yield from _descendants(c)
        c = c.get_next_sibling()


def rows(n=100):
    w = W[0]
    return [w.sorted.get_item(i) for i in range(min(n, w.sorted.get_n_items()))]


def _set(w, text):
    w.entry.set_text(text)            # as if the user typed: the search field starts the search itself (200 ms)


def key(kv, mod=0):
    return W[0].on_key(None, kv, 0, mod)


def side_rows():
    w, out, i = W[0], [], 0
    while (r := w.side_list.get_row_at_index(i)) is not None:
        out.append(r)
        i += 1
    return out


def loaded():
    w = W[0]
    b = w.browse
    return (b is None or not b["loading"]) and w.sorted.get_n_items() > 0


def settled():
    """Loaded, also when the result is empty (a filter the library has no samples for)."""
    w = W[0]
    b = w.browse
    return b is None or (not b["loading"] and b["total"] is not None)


def need(cond, what: str) -> bool:
    if not cond:
        warn(f"skipped: {what}")
    return bool(cond)


def click_wav(path: Path, ms: float = 6.0) -> Path:
    """A synthetic very short click (no library file needed)."""
    sr = 44100
    n = int(sr * ms / 1000)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(b"".join(struct.pack("<h", int(20000 * math.sin(i / 3) * (1 - i / n))) for i in range(n)))
    return path


def tone_mp3(path: Path) -> Path | None:
    """A 2 s test tone as MP3 (needs ffmpeg with an MP3 encoder)."""
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        str(path)], capture_output=True)
    return path if r.returncode == 0 and path.exists() else None


def script():
    w = W[0]
    w.player.pb.set_property("volume", 0.0)
    otsikko("Startup")
    yield loaded, 60
    check("start view All samples", w.title.get_label() == "All samples", w.subtitle.get_label())
    check("window title Fetch", w.get_title() == "Fetch")
    check("default layout: Genre/Sound/Tempo collapsed",
          all(not w.sections[s][0].get_reveal_child() for s in ("Genre", "Sound", "Tempo")))
    check("Recent searches closed", not w.recent_open)
    check("Type filter: only Loop and One-shot (no Other)",
          [n for _, n, g in w.chips if g == "type"] == ["loop", "oneshot"])
    total = w.browse["total"]

    otsikko("Similar result folder")            # Files > Similar sounds… opens the folder in the window
    sim = TMP / "Similar (timbre) – test"
    sim.mkdir()
    for i, r in enumerate(rows(3)):
        (sim / f"{i:03d} {Path(r.orig).name}").symlink_to(r.orig)
    w.load_folder(sim)
    w.entry.emit("search-changed")              # SearchEntry's delayed notification from empty to empty
    yield 0.5
    check("result folder stays open (doesn't jump to All samples)", w.folder == sim and w.store.get_n_items() == min(3, total),
          w.title.get_label())
    w.open_view(t.ALL)
    yield loaded, 30

    otsikko("Instrument filter (3 levels)")
    w.pick_inst(0, "drums")
    yield settled, 30
    if need(w.browse["total"], "no drums in the library"):
        check("Drums narrows", w.browse["total"] <= total, w.subtitle.get_label())
        w.pick_inst(1, "inst:drums/hat")
        yield settled, 30
        w.pick_inst(2, "drums/hat/open")
        yield settled, 30
        if need(w.browse["total"], "no open hats in the library"):
            check("Drums › Hats › Open: every row is an open hat", all(r.inst_id == "drums/hat/open" for r in rows()))
            check("the summary shows the selection", "Open" in w.sections["Instrument"][2].get_label(),
                  w.sections["Instrument"][2].get_label())
    w.pick_inst(0, "keys")
    w.pick_inst(1, "tag:chords")
    yield settled, 30
    if need(w.browse["total"], "no keys chords in the library"):
        check("Keys › Chords: rows are keys + chords", all(r.inst_id.startswith("keys") and "chords" in r.itags for r in rows()))
    w.pick_inst(0, "keys")                                       # off
    w.pick_role("melody")
    yield settled, 30
    if need(w.browse["total"], "no melodies in the library"):
        check("Melody (independent of the instrument)", all("melody" in r.itags for r in rows()))
    w.pick_role("melody")

    otsikko("Genre, type, tempo")
    gc_before = w.genre_counts
    w.load_genre_counts()
    yield (lambda: w.genre_counts is not gc_before), 30      # replaced when the reply arrives (may be empty)
    w.pick_genre(0, "house-techno")
    yield settled, 30
    if need(w.browse["total"], "no house/techno packs in the library"):
        check("genre group narrows", w.browse["total"] <= total)
    w.pick_genre(0, "house-techno")
    next(b for b, n, g in w.chips if n == "loop").set_active(True)
    w.bpm.lo, w.bpm.hi = 120, w.bpm.vmax
    w.bpm.on_change()
    yield (lambda: w.bpm_timer == 0), 5
    yield settled, 30
    if need(w.browse["total"], "no loops at 120+ bpm in the library"):
        check("Loop + 120+: rows are loops at ≥ 120 bpm",
              all(r.props.stype == "Loop" and r.props.bpm >= 120 for r in rows()), w.subtitle.get_label())
    check("tempo summary '120+'", w.bpm.text() == "120+")
    w.bpm.lo, w.bpm.hi = w.bpm.vmin, 90
    check("tempo summary '≤ 90'", w.bpm.text() == "≤ 90")
    w.clear_filters()
    yield loaded, 30
    check("Clear restores everything", w.browse["total"] == total and not w.clear_btn.get_visible())

    otsikko("Search")
    w.focus_search()
    for k in range(1, len(Q) + 1):                               # one letter at a time
        w.entry.set_text(Q[:k])
        yield 0.04
    yield (lambda: w.folder == t.SEARCH and settled() and w.browse["text"] == Q), 30
    if need(w.browse["total"], f"no '{Q}' samples in the library"):
        check(f"search: every row matches '{Q}'", all(Q in r.inst_id or Q in r.orig.lower() for r in rows()),
              w.subtitle.get_label())
    check("not saved while typing", not w.ui_cfg.get("recent"))
    key(Gdk.KEY_Down)
    yield 0.3
    check("↓ saves the search to Recent", any(r["q"] == Q for r in w.ui_cfg.get("recent", [])))
    check("↓ plays the first one", w.player.state in ("playing", "ended"))
    _set(w, "")
    yield loaded, 30
    check("clearing returns to All samples", w.title.get_label() == "All samples")
    w.open_view("q:" + Q)
    yield settled, 30
    check("a Recent search opens again", w.entry.get_text() == Q and w.folder == t.SEARCH)
    _set(w, "")
    yield loaded, 30
    _set(w, "zqxjvkw")
    yield (lambda: loaded() and w.browse["text"] == "zqxjvkw"), 30
    check("no matches -> 'closest' notice", "closest" in w.subtitle.get_label(), w.subtitle.get_label())
    _set(w, "")
    yield loaded, 30

    otsikko("Folders tree")
    heads = lambda: [r for r in side_rows() if getattr(r, "_path", None) == "FOLDERS_HEAD"]  # noqa: E731
    check("Folders row under All samples", [getattr(r, "_path", None) for r in side_rows()][:2] == [t.ALL, "FOLDERS_HEAD"])
    w.on_side_selected(None, heads()[0])
    yield (lambda: "" in w.tree_cache), 30
    tree = lambda: [r for r in side_rows() if str(getattr(r, "_path", "")).startswith("d:")]  # noqa: E731
    check("opening it lists the library roots", len(tree()) == len(w.tree_cache[""]) > 0)
    root = w.tree_cache[""][0]["path"]
    w.toggle_tree_folder(root)
    yield (lambda: root in w.tree_cache), 30
    subs = w.tree_cache[root]
    check("a root opens to its subfolders, indented below it", len(tree()) == len(w.tree_cache[""]) + len(subs))
    if need(subs, "the library root has no subfolders"):
        sub = subs[0]
        w.on_side_selected(None, next(r for r in tree() if r._path == "d:" + sub["path"]))
        yield loaded, 30
        check("a folder shows its samples page by page", w.folder == "d:" + sub["path"] and w.browse["total"] == sub["count"],
              f'{w.browse["total"]} vs {sub["count"]}')
        check("title = the folder name", w.title.get_label() == sub["name"])
        check("the folder's row is selected", getattr(w.side_list.get_selected_row(), "_path", None) == "d:" + sub["path"])
        check("all rows are under the folder", all(r.orig.startswith(sub["path"] + "/") for r in rows()))
        _set(w, "kick")
        yield (lambda: w.folder == t.SEARCH and settled()), 30
        _set(w, "")
        yield loaded, 30
        check("clearing the search returns to the folder", w.folder == "d:" + sub["path"])
    w.toggle_tree_folder(root)
    w.on_side_selected(None, heads()[0])
    yield 0.2
    check("closing the tree hides its rows", not tree())
    check("tree state saved", t.load_ui_cfg().get("folders_open") is False)
    w.open_view(t.ALL)
    w.base_view = t.ALL
    yield loaded, 30

    otsikko("Settings")
    dlg = t.SettingsDialog(w)
    dlg.present(w)
    yield (lambda: dlg.root_rows), 30
    check("library folders listed", len(dlg.root_rows) == len(w.tree_cache.get("", [])) or len(dlg.root_rows) > 0)
    check("each library folder has a Remove button", all(any(isinstance(c, Gtk.Button) and c.get_icon_name() == "user-trash-symbolic"
                      for c in _descendants(r)) for r in dlg.root_rows))
    dlg.scales["ui_scale"].set_value(125)
    yield (lambda: dlg._timer == 0), 5
    check("UI scale saved to appearance.json", t.load_appearance()["ui_scale"] == 1.25)
    check("UI scale applied: sidebar and columns wider", w.split.get_max_sidebar_width() == round(300 * 1.25))
    dlg.scales["ui_scale"].set_value(100)
    yield (lambda: dlg._timer == 0), 5
    check("back to 100 %", t.load_appearance()["ui_scale"] == 1.0 and w.split.get_max_sidebar_width() == 300)
    dlg.close()
    yield 0.2

    otsikko("Multiple selection, likes, tags")
    w.colview.grab_focus()
    w.sel.select_range(0, 3, True)
    yield 0.2
    sel = w.selected_rows()
    check("3 selected", len(sel) == 3 and "3 selected" in w.subtitle.get_label())
    w.act_like()
    liked = Path(os.environ["SAMPLEHAKU_COLLECTIONS"]) / "Liked Samples"
    files = list(liked.iterdir()) if liked.exists() else []
    check("L likes them all (hard links)", len(files) == 3 and all(f.stat().st_nlink >= 2 for f in files))
    w.act_like()
    check("L again removes them", not any(liked.iterdir()))
    before = [(r.inst_id, r.props.stype) for r in sel]
    ed = t.TagEditor(w, sel)
    ed.pick(0, "percussion")
    ed.pick(1, "percussion/cowbell")
    ed.save(ed.changes())
    yield (lambda: all(r.inst_id == "percussion/cowbell" for r in sel)), 30
    check("Edit tags for several: instrument changed, type kept",
          all(r.inst_id == "percussion/cowbell" for r in sel) and [r.props.stype for r in sel] == [b[1] for b in before])
    t.TagEditor(w, sel).save(None)
    yield (lambda: [r.inst_id for r in sel] == [b[0] for b in before]), 30
    check("Automatic restores", [r.inst_id for r in sel] == [b[0] for b in before])

    otsikko("Shuffle")
    first = [r.props.name for r in rows(3)]
    w.shuffle.emit("clicked")
    yield loaded, 30
    a = [r.props.name for r in rows(3)]
    w.shuffle.emit("clicked")
    yield loaded, 30
    b = [r.props.name for r in rows(3)]
    if need(total > 20, "shuffle needs more than 20 samples"):
        check("every click gives a new order", a != first and b != a)
    check("the restore button shows", w.unshuffle.get_visible())
    w.unshuffle.emit("clicked")
    yield loaded, 30
    check("restored to folder order", [r.props.name for r in rows(3)] == first)

    otsikko("Keys and playback")
    w.colview.grab_focus()
    w.sel.select_item(min(5, w.sorted.get_n_items() - 1), True)
    yield 0.3
    key(Gdk.KEY_space)
    st1 = w.player.state
    key(Gdk.KEY_space)
    check("space toggles the state", st1 != w.player.state, f"{st1} -> {w.player.state}")
    key(Gdk.KEY_Right)
    key(Gdk.KEY_Left)
    key(Gdk.KEY_Escape)
    check("Esc stops", w.player.state == "stopped")
    key(Gdk.KEY_slash)
    check("/ moves to the search", w.entry_focused())
    w.on_key(None, Gdk.KEY_Escape, 0, 0)
    check("Esc in the search returns to the list", not w.entry_focused())
    check("L in the search doesn't like", (w.focus_search(), w.on_key(None, Gdk.KEY_l, 0, 0))[1] is False)
    w.colview.grab_focus()

    click = click_wav(TMP / "click.wav")
    w.player.play(str(click), 0.006)
    yield 0.5
    check("a short (6 ms) sample is played padded", Path(t.padded(str(click), 0.006)) != click
          and w.player.state in ("playing", "ended"))
    mp3 = tone_mp3(TMP / "tone.mp3") if shutil.which("ffmpeg") else None
    if need(mp3, "ffmpeg can't write MP3 (no encoder)"):
        w.player.play(str(mp3), 2)
        yield 1.0
        check("MP3 plays without errors", w.player.state == "playing", mp3.name)
    w.player.play("/no/such/file.wav", 1)
    yield 1.0
    check("missing file: no crash, stops", w.player.state == "stopped")

    otsikko("Player options")
    check("the old Play on select toggle is a menu button now", isinstance(w.autoplay, Gtk.MenuButton))
    w.opt_rows["autoplay"].set_active(False)
    w.opt_rows["loop_preview"].set_active(True)
    cfg = json.loads((TMP / "config/samplehaku/ikkuna.json").read_text())
    check("switches saved to ikkuna.json", cfg.get("autoplay") is False and cfg.get("loop_preview") is True, str(cfg))
    w.player.stop()
    w.sel.select_item(1, True)
    yield 0.3
    check("Play on select off: selecting doesn't play", w.player.state == "stopped", w.player.state)
    r0 = w.sorted.get_item(0)
    if need(r0, "needs a row"):
        was = r0.props.stype
        r0.props.stype = "Loop"
        w.play_row(r0)
        a = w.player.loop
        r0.props.stype = "One-shot"
        w.play_row(r0)
        r0.props.stype = was
        check("Loop preview: loops repeat, one-shots don't", a is True and w.player.loop is False)
        w.player.play(str(click), 0.006, loop=True)
        yield 1.5
        check("a looped sample keeps playing past its end", w.player.state == "playing", w.player.state)
    w.player.stop()
    w.opt_rows["autoplay"].set_active(True)
    w.opt_rows["loop_preview"].set_active(False)

    otsikko("Stress")
    n = w.sorted.get_n_items()
    t0 = time.time()
    for i in range(80):                                          # fast arrow-key presses
        w.sel.select_item(random.randrange(n), True)
        yield 0.015
    yield 0.5
    check("80 fast selections", not EXCEPTIONS and w.player.state in ("playing", "ended"),
          f"{time.time() - t0:.1f} s, state {w.player.state}")
    words = ["kick", "warm pad", "909", "+loop", "bpm:12", "key:A", "dark", "xyz", "vocal female", ""]
    for i in range(40):                                          # fast typing
        _set(w, random.choice(words))
        yield 0.02
    _set(w, "snare")
    yield (lambda: settled() and w.browse["text"] == "snare"), 30
    check("40 fast searches: the result matches the last one", w.title.get_label() == "“snare”" and
          all("snare" in r.inst_id or "snare" in r.orig.lower() for r in rows(30)), w.subtitle.get_label())
    _set(w, "")
    yield loaded, 30
    for i in range(30):                                          # tapping filters
        w.pick_inst(0, random.choice(t.soittimet.MAINS))
        yield 0.01
    yield settled, 30
    check("30 fast instrument choices", not EXCEPTIONS)
    w.clear_filters()

    otsikko("Sidebar and dialogs")
    w.toggle_recent()
    check("Recent opens", w.recent_open)
    w.toggle_recent()
    w.toggle_section("Sound")
    check("opening a section is remembered", "Sound" not in w.ui_cfg.get("collapsed", []))
    pg = t.PackGenres(w)
    yield (lambda: bool(pg.info)), 30
    check("the Pack genres list loads", len(pg.info) >= 1, f"{len(pg.info)} packs")
    w.open_view(t.LIKED_DIR)
    yield 0.5
    check("an empty Liked shows the empty page", w.stack.get_visible_child_name() == "empty")
    w.open_view(t.ALL)
    yield loaded, 30

    otsikko("Background errors")
    check("no exceptions in the window", not EXCEPTIONS, "; ".join(EXCEPTIONS[:3]))
    app.quit()


def runner(gen):
    def step():
        try:
            item = next(gen)
        except StopIteration:
            return False
        except Exception as e:
            check("test script", False, f"{type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            app.quit()
            return False
        if isinstance(item, tuple):                               # (condition, time limit s)
            cond, limit = item
            t0 = time.time()

            def poll():
                if cond() or time.time() - t0 > limit:
                    if time.time() - t0 > limit:
                        check("wait", False, f"time limit {limit} s exceeded ({getattr(cond, '__name__', 'condition')})")
                    GLib.idle_add(step)
                    return False
                return True
            GLib.timeout_add(50, poll)
        else:
            GLib.timeout_add(int(item * 1000), lambda: (step(), False)[1])
        return False
    GLib.idle_add(step)


def start():
    if app.win is None:
        return True
    W.append(app.win)
    runner(script())
    return False


GLib.timeout_add(200, start)
app.connect("activate", lambda a: None)
rc = app.run(["fetch-test"])
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(yhteenveto("Window"))
