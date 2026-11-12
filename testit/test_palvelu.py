"""2) Search service (D-Bus): every method the window and Files use, odd inputs, concurrency.
Runs with the system Python against the user's own indexed library. Checks that need specific content
(e.g. 909 hats, many loops) are skipped with a warning if the library doesn't have it.
Writing methods (SetTags…, SetPackGenres) are restored at the end; aja.sh also backs up the settings files."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from apu import Ajastin, check, otsikko, safe, warn, yhteenveto  # noqa: E402

from gi.repository import Gio, GLib  # noqa: E402

BUS = Gio.bus_get_sync(Gio.BusType.SESSION, None)

# the service appends every correction to the correction log: the test's own corrections are cut off at exit
FIX_LOG = Path(os.environ.get("SAMPLEHAKU_FIX_LOG", Path.home() / ".config/samplehaku/korjausloki.jsonl"))
_LOG_SIZE = FIX_LOG.stat().st_size if FIX_LOG.exists() else None


def _restore_fix_log() -> None:
    try:
        if _LOG_SIZE is None:
            FIX_LOG.unlink(missing_ok=True)
        elif FIX_LOG.stat().st_size > _LOG_SIZE:
            os.truncate(FIX_LOG, _LOG_SIZE)
    except OSError as e:
        print(f"correction log not restored: {e}")


import atexit  # noqa: E402
atexit.register(_restore_fix_log)
ERR = ("ERROR:", "VIRHE:")          # VIRHE: = older service


def call(method: str, args: GLib.Variant | None, timeout: int = 300_000) -> str:
    r = BUS.call_sync("fi.samplehaku.Search", "/fi/samplehaku/SearchProvider", "fi.samplehaku.Search",
                      method, args, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, timeout, None)
    return r.unpack()[0]


def js(method, args=None, timeout=300_000):
    return json.loads(call(method, args, timeout))


def browse(**a):
    a.setdefault("limit", 100)
    return js("Browse", GLib.Variant("(s)", (json.dumps(a),)))


def need(cond, what: str) -> bool:
    """Content-dependent check: skip with a warning if the library doesn't have the content."""
    if not cond:
        warn(f"skipped: {what}")
    return bool(cond)


# ---------------------------------------------------------------- basics
otsikko("Service running")
with Ajastin() as t:
    d = safe("Browse (starts the service if needed)", browse)
check("service responds", d is not None and "total" in d, f"{t.s:.1f} s")
if d is None:
    sys.exit(yhteenveto("Service"))
TOTAL = d["total"]
check("All samples: the library is not empty", TOTAL > 0, f"{TOTAL}")
if TOTAL < 300:
    warn("small library: some checks will be skipped", f"{TOTAL} samples")

otsikko("Paging and order")
if need(TOTAL >= 207, "paging needs at least 207 samples"):
    p0, p1 = browse(offset=0)["paths"], browse(offset=100)["paths"]
    check("pages 0 and 1 don't overlap", not set(p0) & set(p1))
    check("100 rows per page", len(p0) == 100 and len(p1) == 100)
check("offset past the end -> empty", browse(offset=TOTAL + 50)["paths"] == [])
check("limit 0 -> empty", browse(limit=0)["paths"] == [])
check("last partial page", len(browse(offset=max(0, TOTAL - 7))["paths"]) == min(7, TOTAL))
a, b = browse(order="shuffle", seed=1)["paths"], browse(order="shuffle", seed=2)["paths"]
if need(TOTAL > 20, "shuffle needs more than 20 samples"):
    check("different seed -> different order", a != b)
check("same seed -> same order", a == browse(order="shuffle", seed=1)["paths"])
check("shuffle: no duplicates", len(set(a)) == len(a))
det = browse(limit=3)["details"]
k = next(iter(det.values()))
want = {"key", "bpm", "dur", "type", "pack", "orig", "inst", "inst_path", "itags", "itags_est", "genres", "tags"}
check("Details fields present", want <= set(k), str(want - set(k)))

otsikko("Folders tree")
roots = js("Folders", GLib.Variant("(s)", ("",)))
check("roots listed", roots.get("folders") and roots["files"] == 0, str(roots)[:80])
if roots.get("folders"):
    check("roots cover the whole library", sum(r["count"] for r in roots["folders"]) == TOTAL,
          f'{sum(r["count"] for r in roots["folders"])} vs {TOTAL}')
    root = roots["folders"][0]
    lvl = js("Folders", GLib.Variant("(s)", (root["path"],)))
    check("one level: subfolder counts + loose files add up to the folder",
          sum(f["count"] for f in lvl["folders"]) + lvl["files"] == root["count"])
    check("subfolders sorted by name", [f["name"].lower() for f in lvl["folders"]]
          == sorted(f["name"].lower() for f in lvl["folders"]))
    if need(lvl["folders"], "the library root has no subfolders"):
        sub = max(lvl["folders"], key=lambda f: f["count"])
        b = browse(folder=sub["path"], limit=100)
        check("Browse folder: total = the folder's count", b["total"] == sub["count"], f'{b["total"]} vs {sub["count"]}')
        check("Browse folder: every path is under the folder", all(p.startswith(sub["path"] + "/") for p in b["paths"]))
        loops_in = browse(folder=sub["path"], filters="+loop", limit=1)["total"]
        check("Browse folder + filter narrows", loops_in <= sub["count"])
        check("Browse folder with search text: searches the whole library",
              browse(folder=sub["path"], text="kick", limit=1)["total"] >= browse(text="kick", limit=1)["total"])
    check("unknown folder -> empty level", js("Folders", GLib.Variant("(s)", ("/no/such/dir",))) == {"folders": [], "files": 0})
    # Remove folder: only error paths here (a real removal would take a folder out of the user's library)
    r = js("RemoveFolder", GLib.Variant("(s)", ("/no/such/dir",)))
    check("RemoveFolder: not a library folder -> error", "error" in r and "removed" not in r, str(r))
    if lvl["folders"]:
        r = js("RemoveFolder", GLib.Variant("(s)", (lvl["folders"][0]["path"],)))
        check("RemoveFolder: a subfolder of a root is not a library folder -> error", "error" in r, str(r))
    check("RemoveFolder errors change nothing", js("Folders", GLib.Variant("(s)", ("",))) == roots)

otsikko("Column sorting across the whole set (paging)")
loops = browse(filters="+loop", limit=1)["total"]
if need(loops >= 300, f"sorting across 3 pages needs 300 loops (library has {loops})"):
    for order, f, rev in (("sort:dur:asc", "dur", False), ("sort:dur:desc", "dur", True),
                          ("sort:bpm:asc", "bpm", False), ("sort:bpm:desc", "bpm", True)):
        seq = []
        for off in (0, 100, 200):
            seq += list(browse(order=order, filters="+loop", offset=off)["details"].values())
        vals = [x[f] for x in seq if x[f] is not None]
        check(f"{order}: in order across 3 pages", vals == sorted(vals, reverse=rev) and len(vals) > 250)
shots = browse(filters="+oneshot", limit=1)["total"]
if need(shots > 0, "no one-shots in the library"):
    last = browse(order="sort:bpm:desc", filters="+oneshot", offset=max(0, shots - 5))
    check("unknown tempo last also in descending order", all(x["bpm"] is None for x in last["details"].values()))
r = browse(text="kick", order="sort:dur:asc")
d = [x["dur"] for x in r["details"].values()]
check("search + sorting", d == sorted(d) and r["total"] > 0)
check("unknown field -> error, no crash", "error" in browse(order="sort:nope:asc"))

otsikko("Filters in the service")
counts = {}
for main in ("drums", "percussion", "bass", "synth", "brasswind", "keys", "guitar", "strings", "vocal", "fx"):
    counts[main] = browse(filters=f"inst:{main}", limit=1)["total"]
check("instrument filters return samples", any(counts.values()), str(counts))
missing = [m for m, n in counts.items() if n == 0]
if missing:
    warn("no samples for some instruments (fine if the library has none)", ", ".join(missing))
check("+loop +oneshot = 0 (no error)", browse(filters="+loop +oneshot", limit=1).get("total") == 0)
r = browse(filters="inst:drums/kick +oneshot", limit=50)
check("Kicks + One-shot: every row is a kick one-shot", all(x["inst"].startswith("drums/kick") and x["type"] == "One-shot"
                                                         for x in r["details"].values()))
r = browse(filters="bpm:120-128 +loop", limit=50)
check("bpm 120–128: row tempos in range", all(x["bpm"] is not None and 120 <= x["bpm"] <= 128 for x in r["details"].values()))
r = browse(filters="genre:house-techno", limit=50)
check("genre group: row genres in the group", all(any(g in ("techno", "house", "tech-house", "deep-house", "afro-house", "hard-techno",
          "melodic-techno", "melodic-house", "bass-house", "uk-garage", "disco", "nu-disco", "electro", "rave", "hardstyle",
          "minimal-techno", "dub-techno", "speed-garage", "french-house", "acid-house", "progressive-house", "big-room-house",
          "amapiano") for g in x["genres"]) for x in r["details"].values()))
for bad in ("inst:nope", "key:zz", "bpm:abc", "+nonexistent", "genre:nope"):
    r = browse(filters=bad, limit=1)
    check(f"invalid filter '{bad}' -> error message, no crash", "error" in r)

otsikko("Text search")
r = browse(text="909 open hat")
if need(r["total"] > 0 and r["mode"] == "match", "no 909 open hats in the library"):
    check("'909 open hat': all open hats", all(x["inst"] == "drums/hat/open" for x in r["details"].values()),
          str({x["inst"] for x in r["details"].values()}))
    check("'909 open hat': all contain 909", all("909" in p for p in r["paths"]))
r = browse(text="perc loop")
if need(r["mode"] == "match", "no percussion loops in the library"):
    check("'perc loop' narrows the library", 0 < r["total"] < TOTAL, str(r["total"]))
r = browse(text="zqxjvkw")
check("unknown word -> the 300 closest", r["mode"] == "closest" and r["total"] == min(300, TOTAL), f"{r['total']} {r['mode']}")
r1, r2 = browse(text="warm pad")["total"], browse(text="warm pad", filters="+loop")["total"]
check("a filter narrows the search", r2 <= r1, f"{r2} ≤ {r1}")
r1, r2 = browse(text="snare drum loop", limit=3000), browse(text="snare drum loop -kick", limit=3000)
if need(any("kick" in p.lower() for p in r1["paths"]), "no snare/drum loops mentioning kick"):
    check("-kick removes kick-named ones", not any("kick" in p.rsplit("/", 1)[1].lower() for p in r2["paths"])
          and r2["total"] < r1["total"])
check("-one-shot = -oneshot", browse(text="snare -one-shot")["total"] == browse(text="snare -oneshot")["total"])
check("just -kick filters the whole library", browse(text="-kick")["total"] <= TOTAL)
check("search paging: no overlap", not set(browse(text="kick")["paths"]) & set(browse(text="kick", offset=100)["paths"]))
WEIRD = ["", " ", "(", "[", "\\", "*", "+", "?", "key:", "bpm:", "bpm:999-1", "🥁🔥", "a" * 3000,
         "'; DROP TABLE files; --", "ääni öljy", "kick\nsnare", "\t", "%s %d", "../../etc/passwd", "+++", "inst:"]
for w in WEIRD:
    r = safe(f"search {w[:20]!r}", lambda: browse(text=w))
    check(f"odd search {w[:20]!r} doesn't crash", r is not None and ("total" in r or "error" in r))

otsikko("Super search (org.gnome.Shell.SearchProvider2)")


def shell(method: str, *args, sig: str = "(as)", reply: str = "(as)"):
    r = BUS.call_sync("fi.samplehaku.Search", "/fi/samplehaku/SearchProvider", "org.gnome.Shell.SearchProvider2",
                      method, GLib.Variant(sig, args), GLib.VariantType(reply), Gio.DBusCallFlags.NONE,
                      300_000, None)
    return r.unpack()[0]


ids = safe("GetInitialResultSet doesn't crash", shell, "GetInitialResultSet", ["fetch", "kick"]) or []
check("'fetch kick': 1–10 matches", 0 < len(ids) <= 10, f"{len(ids)}")
check("matches are existing files", ids and all(os.path.isfile(p) for p in ids))
check("other prefixes (sample, ääni, Fetch) work",
      all(shell("GetInitialResultSet", [p, "kick"]) for p in ("sample", "ääni", "Fetch")))
check("without a prefix: ordinary searches are left alone ('firefox')", shell("GetInitialResultSet", ["firefox"]) == [])
if need(loops > 0, "no loops in the library"):
    check("without a prefix: a +tag searches ('+loop')", len(shell("GetInitialResultSet", ["+loop"])) > 0)
check("without a prefix: a filter searches ('kick bpm:120')", isinstance(shell("GetInitialResultSet", ["kick", "bpm:120"]), list))
check("prefix only -> no matches", shell("GetInitialResultSet", ["fetch"]) == [])
for terms in (["fetch", "+tu"], ["fetch", "key:"], ["fetch", "bpm:12-"], ["+"], ["fetch", "kick", "-"]):
    r = safe(f"half-typed {' '.join(terms)!r} doesn't crash", shell, "GetInitialResultSet", terms)
    check(f"half-typed {' '.join(terms)!r} -> a list", isinstance(r, list))
sub = safe("GetSubsearchResultSet", lambda: shell("GetSubsearchResultSet", ids, ["fetch", "kick", "909"],
                                                   sig="(asas)"))
if need(sub and any("909" in p for p in sub), "no 909 kicks in the library"):
    check("GetSubsearchResultSet narrows ('fetch kick 909')", all("909" in p for p in sub[:3]), f"{len(sub or [])}")
metas = safe("GetResultMetas", lambda: shell("GetResultMetas", ids[:3] + ["/no/such.wav"], reply="(aa{sv})")) or []
check("GetResultMetas: a row for every identifier", len(metas) == len(ids[:3]) + 1, f"{len(metas)}")
check("metas: id, name, description, gicon", metas and all({"id", "name", "description", "gicon"} <= set(m)
                                                           for m in metas))
check("metas: name = file name", metas and metas[0]["name"] == Path(ids[0]).name)
check("metas: description starts from the library root", metas and "/" in metas[0]["description"],
      metas[0]["description"][:70] if metas else "")
cmd = Path.home() / ".local/bin/samplehaku-results"      # ActivateResult/LaunchSearch open this
check("ActivateResult/LaunchSearch: samplehaku-results is executable", os.access(cmd, os.X_OK))

otsikko("Details, Info, Similar")
paths = browse(limit=5)["paths"]
d = js("Details", GLib.Variant("(as)", (paths + ["/no/such/file.wav", ""],)))
check("Details: existing ones included, missing ones left out", set(d) == set(paths), f"{len(d)}")
check("Details with an empty list", js("Details", GLib.Variant("(as)", ([],))) == {})
check("Info: missing -> indexed false", js("Info", GLib.Variant("(s)", ("/no/such.wav",))).get("indexed") is False)
check("Info: existing", js("Info", GLib.Variant("(s)", (paths[0],))).get("indexed") is True)
for mode in ("timbre", "key", "bpm", "timbre+key", "timbre+bpm", "timbre+key+bpm", "key+bpm"):
    out = call("Similar", GLib.Variant("(ss)", (paths[0], mode)))
    ok = out.startswith(ERR) or (os.path.isdir(out) and len(os.listdir(out)) > 1)
    check(f"Similar {mode}", ok, out[:70])
check("Similar for a missing file -> error message", call("Similar", GLib.Variant("(ss)", ("/no/such.wav", "timbre"))).startswith(ERR))
liked = Path(os.environ.get("SAMPLEHAKU_COLLECTIONS", Path.home() / "Documents/Fetch Collections")) / "Liked Samples"
lk = next((p for p in liked.glob("*.wav")), None) if liked.is_dir() else None
if need(lk, "no liked samples yet"):
    check("Similar for a liked sample (hard link)", not call("Similar", GLib.Variant("(ss)", (str(lk), "timbre"))).startswith(ERR))

otsikko("Tag corrections (restored)")
if need(len(paths) >= 2, "tag corrections need at least 2 samples"):
    t1, t2 = paths[0], paths[1]
    before = js("Details", GLib.Variant("(as)", ([t1, t2],)))
    r = js("SetTagsMany", GLib.Variant("(ass)", ([t1, t2], json.dumps({"inst": "percussion/bongo", "tags": ["distorted"]}))))
    check("SetTagsMany sets", all(r[p]["inst"] == "percussion/bongo" and "distorted" in r[p]["itags"] for p in (t1, t2)))
    r = js("SetTagsMany", GLib.Variant("(ass)", ([t1], json.dumps({"type": "loop"}))))
    check("a partial update keeps the instrument", r[t1]["inst"] == "percussion/bongo" and r[t1]["type"] == "Loop")
    r = js("SetTagsMany", GLib.Variant("(ass)", ([t1, t2], json.dumps(None))))
    check("Automatic restores", all(r[p]["inst"] == before[p]["inst"] and r[p]["type"] == before[p]["type"] for p in (t1, t2)))
    check("invalid instrument -> error", "error" in js("SetTagsMany", GLib.Variant("(ass)", ([t1], json.dumps({"inst": "xx"})))))
check("missing files -> error", "error" in js("SetTagsMany", GLib.Variant("(ass)", (["/no/such.wav"], json.dumps({"type": "loop"})))))

otsikko("Genres")
gc = js("GenreCounts")
check("GenreCounts", isinstance(gc, dict), f"{len(gc)} genres")
packs = js("Packs")
check("Packs", isinstance(packs, dict) and len(packs) >= 1, f"{len(packs)} packs")
check("invalid genre -> error", "error" in js("SetPackGenres", GLib.Variant("(ss)", (next(iter(packs)), json.dumps(["xx"])))))
pk = next((p for p, v in packs.items() if v["n"] > 1), None)
if need(pk, "no pack with more than one sample"):
    old = packs[pk]
    r = js("SetPackGenres", GLib.Variant("(ss)", (pk, json.dumps(["jazz"]))))
    check("SetPackGenres sets", r.get("genres") == ["jazz"] and r.get("source") == "user")
    restore = json.dumps(old["genres"]) if old["source"] == "user" else json.dumps(None)
    r = js("SetPackGenres", GLib.Variant("(ss)", (pk, restore)))
    check("SetPackGenres restored", r.get("genres") == old["genres"], f"{r.get('genres')} vs {old['genres']}")

otsikko("Concurrent calls (30 at once)")
errors, times = [], []


def worker(i):
    try:
        with Ajastin() as t:
            r = browse(offset=(i * 37) % max(1, TOTAL), filters=["", "+loop", "inst:drums", "tag:female"][i % 4],
                       text=["", "kick", "warm pad"][i % 3])
        times.append(t.s)
        if "total" not in r:
            errors.append(r)
    except Exception as e:
        errors.append(str(e))


th = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
with Ajastin() as tt:
    [x.start() for x in th]
    [x.join() for x in th]
check("all 30 succeeded", not errors, str(errors[:2]))
check("service still responds", "total" in browse(limit=1), f"total {tt.s:.1f} s, slowest {max(times):.2f} s")

# ---------------------------------------------------------------- HTTP API (VST3 plugin)
otsikko("HTTP API for the VST3 plugin (127.0.0.1)")
from http.client import HTTPConnection  # noqa: E402

PORT = int(os.environ.get("SAMPLEHAKU_HTTP_PORT", 47811))


def post(path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    c = HTTPConnection("127.0.0.1", PORT, timeout=120)
    h = {"X-Fetch-Client": "test", "Content-Type": "application/json"} if headers is None else headers
    c.request("POST", path, json.dumps(body or {}), h)
    r = c.getresponse()
    out = r.status, json.loads(r.read() or b"{}")
    c.close()
    return out


st, r = safe("ping", post, "/v1/ping") or (0, {})
if check("ping", st == 200 and r.get("ok"), f"{st} {r}"):
    st, r = post("/v1/browse", {"filters": "+loop", "limit": 3})
    check("browse +loop: 3 paths with details", len(r.get("paths", [])) == 3
          and all(p in r.get("details", {}) for p in r["paths"]), f"total {r.get('total')}")
    st, r2 = post("/v1/browse", {"filters": "+loop", "limit": 3})
    check("same as D-Bus Browse", r2.get("paths") == browse(filters="+loop", limit=3)["paths"])
    if r.get("paths"):
        st, s = post("/v1/similar", {"path": r["paths"][0], "conds": ["timbre"], "n": 5})
        check("similar: 5 paths", len(s.get("paths", [])) == 5, s.get("error", ""))
    st, e = post("/v1/browse", {"filters": "+nosuchtag"})
    check("bad filter -> error, not a crash", st == 200 and "error" in e, e.get("error", "")[:60])
    check("no X-Fetch-Client header -> 403", post("/v1/ping", headers={})[0] == 403)
    check("Origin header (web page) -> 403",
          post("/v1/ping", headers={"X-Fetch-Client": "t", "Origin": "http://example.com"})[0] == 403)
    check("unknown request -> 404", post("/v1/nothing")[0] == 404)
    st, f = post("/v1/removefolder", {"path": "/no/such/dir"})
    check("removefolder: not a library folder -> error", st == 200 and "error" in f, str(f))
    st, f = post("/v1/folders", {"parent": ""})
    check("folders: same roots as D-Bus Folders", st == 200 and f == js("Folders", GLib.Variant("(s)", ("",))))

    st, cat = post("/v1/catalog")
    check("catalog: instruments, genres, sound tags, collections",
          cat.get("instruments") and cat.get("sound") and "genres" in cat and cat.get("collections"),
          f"{len(cat.get('instruments', []))} instruments, {len(cat.get('genres', []))} genre groups")
    liked_dir = next((c["path"] for c in cat.get("collections", []) if c.get("liked")), None)
    if liked_dir:
        st, f = post("/v1/folder", {"path": liked_dir})
        check("folder: Liked Samples lists with details", "paths" in f and f.get("total") == len(f["paths"]),
              f"{f.get('total')} samples")
    st, rec = post("/v1/recent")
    check("recent: a list", isinstance(rec.get("recent"), list))
    if r.get("paths"):
        p0 = r["paths"][0]
        was = r["details"][p0].get("liked")
        st, a = post("/v1/like", {"paths": [p0]})               # toggle
        st, b = post("/v1/like", {"paths": [p0], "on": bool(was)})  # back
        check("like: toggles and restores", a.get(p0) == (not was) and b.get(p0) == bool(was))
    def sh_fixes() -> dict:
        try:
            return json.loads((Path(os.environ.get("SAMPLEHAKU_FIXES", Path.home() / ".config/samplehaku/korjaukset.json"))).read_text())
        except (OSError, ValueError):
            return {}
    # Edit tags over HTTP (plugin): only on a sample without the user's own corrections; back to automatic after
    st, page = post("/v1/browse", {"filters": "+oneshot", "limit": 50})
    free = [p for p in page.get("paths", []) if not page["details"][p].get("fixed")]
    if need(free, "settags needs a one-shot without corrections"):
        p0 = free[0]
        st, a = post("/v1/settags", {"paths": [p0], "tags": {"type": "loop"}})
        st, b = post("/v1/settags", {"paths": [p0], "tags": None})
        check("settags: type saved, then back to automatic",
              a.get(p0, {}).get("type") == "Loop" and a[p0].get("fixed")
              and b.get(p0, {}).get("type") == "One-shot" and not b[p0].get("fixed"), str(a.get("error", "")))
        # loop length (plugin player menu /2, x2): a playback choice, kept by "back to automatic", not "fixed"
        st, a = post("/v1/settags", {"paths": [p0], "tags": {"len": 0.5}})
        st, b = post("/v1/settags", {"paths": [p0], "tags": None})
        st, c = post("/v1/settags", {"paths": [p0], "tags": {"len": None}})
        st, bad = post("/v1/settags", {"paths": [p0], "tags": {"len": 3}})
        check("settags len: saved, not 'fixed', kept by 'automatic', removed with null, 3 refused",
              a.get(p0, {}).get("len") == 0.5 and not a[p0].get("fixed") and b.get(p0, {}).get("len") == 0.5
              and "len" not in c.get(p0, {}) and "error" in bad and p0 not in sh_fixes(),
              f"{a.get(p0, {}).get('len')} {b.get(p0, {}).get('len')} {c.get(p0, {}).get('len')} {bad}")
    st, e = post("/v1/settags", {"paths": ["/nonexistent.wav"], "tags": {"type": "loop"}})
    check("settags: unknown file -> error", "error" in e)

sys.exit(yhteenveto("Service"))
