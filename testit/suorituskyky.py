"""Performance: measures the speed + memory of the service, the engine and the window, saves the result
to testit/tulokset/suorituskyky-<time>.json and compares it with the previous run (baseline / after optimising).

    python3 testit/suorituskyky.py          # everything (restarts the service for a cold measurement)
    python3 testit/suorituskyky.py --warm   # without restarting the service
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "tulokset"
VENV = ROOT / ".venv/bin/python"
sys.path[:0] = [str(HERE), str(ROOT)]

from gi.repository import Gio, GLib  # noqa: E402

BUS = Gio.bus_get_sync(Gio.BusType.SESSION, None)
R: dict[str, dict] = {}


def call(method, args=None, timeout=300_000):
    r = BUS.call_sync("fi.samplehaku.Search", "/fi/samplehaku/SearchProvider", "fi.samplehaku.Search",
                      method, args, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, timeout, None)
    return r.unpack()[0]


def browse(**a):
    a.setdefault("limit", 100)
    return json.loads(call("Browse", GLib.Variant("(s)", (json.dumps(a),))))


def kerran(nimi, fn, yksikko="s"):
    t = time.perf_counter()
    fn()
    v = time.perf_counter() - t
    R[nimi] = {"median": v, "p95": v, "n": 1, "unit": yksikko}
    print(f"  {nimi:46} {v * 1000:9.1f} ms", flush=True)


def toisto(nimi, fn, n=15):
    ts = []
    for i in range(n):
        t = time.perf_counter()
        fn(i)
        ts.append(time.perf_counter() - t)
    ts.sort()
    R[nimi] = {"median": statistics.median(ts), "p95": ts[min(len(ts) - 1, int(0.95 * len(ts)))], "n": n, "unit": "s"}
    print(f"  {nimi:46} {R[nimi]['median'] * 1000:9.1f} ms  (p95 {R[nimi]['p95'] * 1000:.1f} ms, n={n})", flush=True)


def arvo(nimi, v, yksikko):
    R[nimi] = {"median": v, "p95": v, "n": 1, "unit": yksikko}
    print(f"  {nimi:46} {v:9.1f} {yksikko}", flush=True)


def service_pid():
    out = subprocess.run(["systemctl", "--user", "show", "-p", "MainPID", "--value", "samplehaku-haku"],
                         capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() and out != "0" else None


def rss_mb(pid, field="VmRSS"):
    """VmRSS = everything; RssAnon = the program's own memory; RssFile = mapped from disk (reclaimable)."""
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith(field):
                return int(line.split()[1]) / 1024
    except OSError:
        return float("nan")


kylma = not ({"--warm", "--lämmin"} & set(sys.argv))
print("\033[1m== Service\033[0m")
if kylma:
    subprocess.run(["systemctl", "--user", "restart", "samplehaku-haku"])
    time.sleep(0.5)
    kerran("service: cold start -> 1st page", lambda: browse())
    kerran("service: 1st text search (model loads)", lambda: browse(text="warm pad"))
else:
    browse()
    browse(text="warm pad")
pid = service_pid()
if pid:
    arvo("service: memory at rest", rss_mb(pid), "MB")
    arvo("service: program memory (anon) at rest", rss_mb(pid, "RssAnon"), "MB")
toisto("All samples: page (100)", lambda i: browse(offset=i * 100))
toisto("All samples: filtered page", lambda i: browse(filters=["inst:drums +loop", "tag:female", "genre:house-techno bpm:120-130",
                                                                   "inst:keys tag:chords"][i % 4], offset=(i // 4) * 100))
toisto("All samples: shuffled page", lambda i: browse(order="shuffle", seed=i))
QS = ["909 open hat", "dark punchy kick", "latin perc loop", "warm pad", "female vocal phrase", "acid bass",
      "dusty vinyl drums", "bright pluck", "clap", "riser", "tabla loop", "808", "deep house chords",
      "glitch fx", "piano"]
toisto("text search: new search (ranking computed)", lambda i: browse(text=QS[i % len(QS)] + f" {'x' * (i // len(QS))}"), n=15)
def _seuraava(i):
    pass
_tm = []
for i in range(15):                                   # page 1 first (ranking cached), page 2 is measured
    browse(text=QS[i])
    t0 = time.perf_counter(); browse(text=QS[i], offset=100); _tm.append(time.perf_counter() - t0)
_tm.sort()
R["text search: next page (from cache)"] = {"median": statistics.median(_tm), "p95": _tm[-1], "n": 15, "unit": "s"}
print(f"  {'text search: next page (from cache)':46} {statistics.median(_tm) * 1000:9.1f} ms  (p95 {_tm[-1] * 1000:.1f} ms, n=15)")
paths = browse(limit=100)["paths"]
toisto("Details: 100 files", lambda i: call("Details", GLib.Variant("(as)", (paths,))), n=10)
toisto("Similar (timbre) -> 100 results", lambda i: call("Similar", GLib.Variant("(ss)", (paths[i], "timbre"))), n=5)
toisto("Similar (timbre+key+bpm)", lambda i: call("Similar", GLib.Variant("(ss)", (paths[i + 10], "timbre+key+bpm"))), n=5)
toisto("GenreCounts", lambda i: call("GenreCounts"), n=5)
toisto("Packs", lambda i: call("Packs"), n=5)
if pid:
    arvo("service: memory after use", rss_mb(pid), "MB")
    arvo("service: program memory (anon) after use", rss_mb(pid, "RssAnon"), "MB")
    arvo("service: file cache (reclaimable)", rss_mb(pid, "RssFile"), "MB")

print("\033[1m== Engine (.venv)\033[0m")
code = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
import samplehaku as sh, soittimet as so, genret
r = {}
t = time.perf_counter(); idx = sh.Index(sh.db()); r["index load (SQLite -> memory)"] = time.perf_counter() - t
t = time.perf_counter(); inst, est, tg = idx.instruments(); r["instruments: from cache"] = time.perf_counter() - t
t = time.perf_counter(); so.classify(idx.paths, idx.A, roots=idx.roots); r["instruments: computed from scratch (names + neighbours)"] = time.perf_counter() - t
t = time.perf_counter(); so.estimate_tags(inst, [set(x) for x in tg], idx.A); r["style tags from audio (classifiers)"] = time.perf_counter() - t
t = time.perf_counter(); idx.genres(); r["genres (packs + suggestions)"] = time.perf_counter() - t
t = time.perf_counter(); idx.tag_mask(sh.parse_tags("inst:drums +loop bpm:120-128 genre:house-techno")[1]); r["filter mask (4 conditions)"] = time.perf_counter() - t
r["library size"] = len(idx.paths)
print(json.dumps(r))
'''
out = subprocess.run([str(VENV), "-I", "-c", code, str(ROOT)], capture_output=True, text=True)
try:
    for k, v in json.loads(out.stdout.strip().splitlines()[-1]).items():
        if k == "library size":
            arvo(k, v, "samples")
        else:
            R[k] = {"median": v, "p95": v, "n": 1, "unit": "s"}
            print(f"  {k:46} {v * 1000:9.1f} ms", flush=True)
except (ValueError, IndexError):
    print("  engine measurement failed:", out.stderr[-500:])

print("\033[1m== Window\033[0m")
code = r'''
import os, sys, time, json, tempfile
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1])
t0 = time.perf_counter()
import samplehaku_tulokset as t
from gi.repository import GLib
t.APP_ID = "fi.samplehaku.ResultsPerf"
app = t.App(); r = {}; st = {}
def ready(w): return w.browse and not w.browse["loading"] and w.sorted.get_n_items() > 0
def step():
    w = app.win
    if w is None: return True
    if "start" not in r:
        if ready(w): r["window: start -> 1st page visible"] = time.perf_counter() - t0; st["t"] = time.perf_counter(); w.pick_inst(0, "drums")
        return True
    if "filt" not in st:
        if ready(w) and w.browse["filters"]: r["window: filter -> new page"] = time.perf_counter() - st["t"]; st["filt"] = 1; st["t"] = time.perf_counter(); w.set_search_text("kick"); w.on_search_changed(w.entry)
        return True
    if "haku" not in st:
        if ready(w) and w.browse["text"] == "kick": r["window: search -> results"] = time.perf_counter() - st["t"]; st["haku"] = 1; st["t"] = time.perf_counter(); w.sel.select_item(3, True)
        return True
    r["window: select -> playback starts"] = time.perf_counter() - st["t"] if w.player.state == "playing" else float("nan")
    print(json.dumps(r)); app.quit(); return False
r["start"] = 0; r.pop("start")
def first():
    st["go"] = 1
    GLib.timeout_add(10, step); return False
GLib.timeout_add(10, first)
app.connect("activate", lambda a: None)
app.run(["perf"])
'''
code = code.replace('if "start" not in r:', 'if "window: start -> 1st page visible" not in r:')
out = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True, text=True, timeout=180)
try:
    for k, v in json.loads(out.stdout.strip().splitlines()[-1]).items():
        R[k] = {"median": v, "p95": v, "n": 1, "unit": "s"}
        print(f"  {k:46} {v * 1000:9.1f} ms", flush=True)
except (ValueError, IndexError):
    print("  window measurement failed:", out.stderr[-800:])

# ---------------------------------------------------------------- save and compare
OUT.mkdir(exist_ok=True)
prev = sorted(OUT.glob("suorituskyky-*.json"))
stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
meta = {"time": stamp, "cold": kylma,
        "kernel": os.uname().release,
        "git": "", "results": R}
(OUT / f"suorituskyky-{stamp}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
print(f"\nSaved: {OUT / f'suorituskyky-{stamp}.json'}")
if prev:
    prev_json = json.loads(prev[-1].read_text())
    old = prev_json.get("results", prev_json.get("tulokset", {}))
    print(f"\n\033[1mCompared with the previous run ({prev[-1].name}):\033[0m")
    for k, v in R.items():
        if k in old and old[k]["median"] and v["median"] == v["median"]:
            d = (v["median"] - old[k]["median"]) / old[k]["median"] * 100
            yks = v["unit"]
            a, b = (old[k]["median"] * 1000, v["median"] * 1000) if yks == "s" else (old[k]["median"], v["median"])
            u = "ms" if yks == "s" else yks
            col = "\033[32m" if d < -5 else "\033[31m" if d > 5 else ""
            print(f"  {k:46} {a:9.1f} -> {b:9.1f} {u:6} {col}{d:+6.0f} %\033[0m")
else:
    print("First run = baseline. The next run compares with this one.")
