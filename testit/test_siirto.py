"""1b) Library roots, merge and relocate (siirto.py), and the ordered parallel map (rinnakkain.py).
Runs with Fetch's .venv in a temporary folder: its own index, settings and collections (environment
variables are set before samplehaku is imported). Does not touch the user's index or settings, and
needs no model: the index rows are made up, the files are tiny dummies."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]
from apu import check, otsikko, yhteenveto  # noqa: E402

import polut  # noqa: E402

otsikko("Library roots (polut.py)")
paths = ["/a/Lib/P1/x.wav", "/a/Lib/P2/y.wav", "/m/win/P3/z.wav"]
check("fallback root = common folder", polut.fallback_root(paths[:2]) == "/a/Lib")
check("one file: its folder", polut.fallback_root(["/a/Lib/P1/x.wav"]) == "/a/Lib/P1")
check("root_of: the deepest", polut.root_of("/a/Lib/P1/x.wav", ["/a", "/a/Lib"]) == "/a/Lib")
check("root_of: not a prefix of a name", polut.root_of("/a/Library/x.wav", ["/a/Lib"]) == "")
check("roots_per_path with two roots", polut.roots_per_path(paths, ["/a/Lib", "/m/win"]) ==
      ["/a/Lib", "/a/Lib", "/m/win"])
check("roots_per_path without roots = the old single root",
      polut.roots_per_path(paths[:2]) == ["/a/Lib", "/a/Lib"])
check("add_root: a subfolder changes nothing", polut.add_root(["/a/Lib"], "/a/Lib/P1") == ["/a/Lib"])
check("add_root: a parent replaces", polut.add_root(["/a/Lib/P1", "/m/win"], "/a/Lib") == ["/m/win", "/a/Lib"])
check("add_root: Windows and UNC", polut.add_root(["D:/Samples"], "//pi/share/S") == ["D:/Samples", "//pi/share/S"])
check("replace_prefix", polut.replace_prefix("D:/Samples/P/k.wav", "D:/Samples", "/mnt/w") == "/mnt/w/P/k.wav")
check("replace_prefix: other folder -> None", polut.replace_prefix("D:/SamplesX/k.wav", "D:/Samples", "/m") is None)


def run(code: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], env=os.environ | env, capture_output=True,
                          text=True, cwd=ROOT, timeout=120)


otsikko("Merge and relocate (siirto.py, temporary index)")
with tempfile.TemporaryDirectory() as tmp:
    T = Path(tmp)
    env = {"SAMPLEHAKU_DIR": str(T / "data"), "SAMPLEHAKU_FIXES": str(T / "cfg/korjaukset.json"),
           "SAMPLEHAKU_FIX_LOG": str(T / "cfg/korjausloki.jsonl"), "XDG_CACHE_HOME": str(T / "cache"),
           "SAMPLEHAKU_COLLECTIONS": str(T / "coll"), "SAMPLEHAKU_GENRES": str(T / "cfg/genret.json"),
           "PYTHONPATH": str(ROOT)}
    (T / "cfg").mkdir()

    def files(base: Path, names: list[str]) -> list[Path]:
        out = []
        for n in names:
            p = base / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"RIFF" + n.encode())
            out.append(p)
        return out

    def make_db(db: Path, rows: list[tuple[str, float, int]], roots=None):
        db.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE files(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, duration REAL, "
                  "name TEXT, audio_emb BLOB, name_emb BLOB)")
        c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE features(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, centroid REAL, "
                  "low REAL, flat REAL, attack REAL, tail REAL, backend TEXT, bpm REAL, key TEXT, ver INTEGER, "
                  "onset_ac BLOB, key_sal BLOB)")
        c.execute("INSERT INTO meta VALUES('model', 'm')")
        if roots:
            c.execute("INSERT INTO meta VALUES('roots', ?)", (json.dumps(roots),))
        for p, m, s in rows:
            c.execute("INSERT INTO files VALUES(?,?,?,1.0,'n',?,?)", (p, m, s, b"\0" * 8, b"\0" * 8))
            c.execute("INSERT INTO features(path, mtime, size, centroid, ver) VALUES(?,?,?,0.5,2)", (p, m, s))
        c.commit()
        c.close()

    # this computer: an older index (no stored roots) of Lib
    lib = files(T / "Lib", ["P1/a.wav", "P2/b.wav"])
    make_db(T / "data/index.db", [(polut.norm(p), p.stat().st_mtime, p.stat().st_size) for p in lib])
    # the other computer: D:/Samples, the same files visible here under mnt/win (other mtimes)
    win = files(T / "mnt/win", ["P3/c.wav", "P3/d.wav", "P4/e.wav"])
    make_db(T / "win.db", [("D:/Samples/" + str(p.relative_to(T / "mnt/win")).replace(os.sep, "/"),
                            1.0e9, p.stat().st_size) for p in win] + [("E:/Other/x.wav", 1.0e9, 5)],
            roots=["D:/Samples"])
    here = polut.norm(T / "mnt/win")

    r = run(f"import siirto; siirto.merge({str(T / 'win.db')!r}, ['D:\\\\Samples={here}'])", env)
    check("merge runs", r.returncode == 0, r.stderr[-300:])
    check("merge: the unmapped file is reported", "outside the --map folders" in r.stdout, r.stdout[-300:])
    con = sqlite3.connect(T / "data/index.db")
    got = {p: m for p, m in con.execute("SELECT path, mtime FROM files")}
    check("merge: 2 + 3 files", len(got) == 5, str(len(got)))
    check("merge: local modification time adopted",
          all(got.get(polut.norm(p)) == p.stat().st_mtime for p in win))
    fm = {p: m for p, m in con.execute("SELECT path, mtime FROM features")}
    check("merge: features follow (same mtime as files)", all(fm.get(p) == got[p] for p in got))
    roots = json.loads(con.execute("SELECT value FROM meta WHERE key='roots'").fetchone()[0])
    check("merge: the old root is kept and the new one added",
          sorted(roots) == sorted([polut.norm(T / "Lib"), here]), str(roots))
    con.close()
    check("merge: backup made", any((T / "data").glob("index.db.bak-merge-*")))
    r = run(f"import siirto; siirto.merge({str(T / 'win.db')!r}, ['D:/Samples={here}'])", env)
    check("merge again: nothing new", "new or updated: 0" in r.stdout and "already here: 3" in r.stdout,
          r.stdout[-300:])

    code = ("import samplehaku as sh, genret, json; i = sh.Index(sh.db()); i.genres(); "
            "print(json.dumps(sorted(i.pack_info)))")
    r = run(code, env)
    check("packs from each library's own root", r.stdout.strip().endswith('["P1", "P2", "P3", "P4"]'),
          r.stdout[-200:] + r.stderr[-300:])

    # relocate mnt/win -> pi/S (copied without times), with a correction, a log line and a manifest
    c0 = polut.norm(win[0])
    (T / "cfg/korjaukset.json").write_text(json.dumps({c0: {"type": "loop"}, "/else/x.wav": {}}))
    (T / "cfg/korjausloki.jsonl").write_text(json.dumps({"path": c0}) + "\n")
    (T / "coll/Liked Samples").mkdir(parents=True)
    (T / "coll/Liked Samples/.samplehaku.json").write_text(json.dumps({"c.wav": c0}))
    pi = files(T / "pi/S", ["P3/c.wav", "P3/d.wav"])          # P4/e.wav not copied yet
    dst = polut.norm(T / "pi/S")
    r = run(f"import siirto; siirto.relocate({here!r}, {dst!r})", env)
    check("relocate refuses while files are missing", r.returncode != 0 and "Nothing changed" in r.stderr,
          r.stdout[-200:] + r.stderr[-200:])
    files(T / "pi/S", ["P4/e.wav"])
    r = run(f"import siirto; siirto.relocate({here!r}, {dst!r})", env)
    check("relocate runs", r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
    con = sqlite3.connect(T / "data/index.db")
    got = {p: m for p, m in con.execute("SELECT path, mtime FROM files")}
    check("relocate: paths moved", sum(p.startswith(dst + "/") for p in got) == 3 and
          not any(p.startswith(here + "/") for p in got))
    fm = {p: m for p, m in con.execute("SELECT path, mtime FROM features")}
    check("relocate: features follow", all(fm.get(p) == got[p] for p in got))
    roots = json.loads(con.execute("SELECT value FROM meta WHERE key='roots'").fetchone()[0])
    check("relocate: root moved", sorted(roots) == sorted([polut.norm(T / "Lib"), dst]), str(roots))
    con.close()
    nc = polut.norm(pi[0])
    fx = json.loads((T / "cfg/korjaukset.json").read_text())
    check("relocate: correction moved, others kept", nc in fx and "/else/x.wav" in fx and c0 not in fx)
    check("relocate: log moved", json.loads((T / "cfg/korjausloki.jsonl").read_text())["path"] == nc)
    check("relocate: collection manifest moved",
          json.loads((T / "coll/Liked Samples/.samplehaku.json").read_text())["c.wav"] == nc)
    check("relocate: settings backed up", any((T / "cfg").glob("korjaukset.json.bak-relocate-*")))

otsikko("Content hashes and --reuse (siirto.py, temporary indexes)")
with tempfile.TemporaryDirectory() as tmp:
    T = Path(tmp)
    env = {"SAMPLEHAKU_DIR": str(T / "donor"), "XDG_CACHE_HOME": str(T / "cache"), "PYTHONPATH": str(ROOT)}
    old = files(T / "Old", ["A/kick.wav", "A/snare.wav", "B/pad.wav"])
    make_db(T / "donor/index.db", [(polut.norm(p), p.stat().st_mtime, p.stat().st_size) for p in old])
    con = sqlite3.connect(T / "donor/index.db")
    con.execute("UPDATE files SET audio_emb=?, duration=2.5 WHERE path=?", (b"\1" * 8, polut.norm(old[0])))
    con.commit()
    con.close()
    r = run("import siirto; siirto.hashes()", env)
    check("hashes runs", r.returncode == 0, r.stderr[-300:])
    r = run("import siirto; siirto.hashes()", env)
    check("hashes again: nothing to do", "0 need a content hash" in r.stdout, r.stdout[-200:])

    # the new library: a copy of kick (other name, other folder), a same-size file with other bytes, a new one
    new = T / "New"
    (new / "X").mkdir(parents=True)
    (new / "X/Kick 01.wav").write_bytes(old[0].read_bytes())
    (new / "X/fake.wav").write_bytes(b"RIFF" + b"A/snarf.wav")         # same size as A/snare.wav
    (new / "X/new.wav").write_bytes(b"RIFF something new")
    code = f"""
import numpy as np, sqlite3, siirto, samplehaku as sh
from pathlib import Path
class Clap:
    name = "m"
    def text(self, t): return np.ones((len(t), 2), np.float32)
con = sh.db()
con.execute("INSERT OR IGNORE INTO meta VALUES('model', 'm')")
todo = [(p, p.stat()) for p in sorted(Path({str(new)!r}).rglob("*.wav"))]
rest = siirto.reuse(con, [{str(T / 'donor/index.db')!r}], todo, [Path({str(new)!r})], Clap())
print("REST", sorted(p.name for p, _ in rest))
"""
    r = run(code, env | {"SAMPLEHAKU_DIR": str(T / "here")})
    check("reuse runs", r.returncode == 0, r.stderr[-400:])
    check("reuse: only the identical file is taken, the rest are computed",
          "REST ['fake.wav', 'new.wav']" in r.stdout, r.stdout[-300:])
    con = sqlite3.connect(T / "here/index.db")
    row = con.execute("SELECT path, duration, audio_emb, name FROM files").fetchall()
    k = polut.norm(new / "X/Kick 01.wav")
    check("reuse: embedding and duration copied, new path and name",
          row == [(k, 2.5, b"\1" * 8, "x kick 01")], str(row))
    f = con.execute("SELECT path, mtime, centroid, ver FROM features").fetchall()
    check("reuse: features copied with this file's mtime",
          f == [(k, (new / "X/Kick 01.wav").stat().st_mtime, 0.5, 2)], str(f))
    check("reuse: hash stored here too", con.execute("SELECT COUNT(*) FROM content_hash").fetchone()[0] == 1)
    con.close()
    # copies inside the library: one of an already analysed file, two of a new file
    (new / "Y").mkdir()
    (new / "Y/Kick copy.wav").write_bytes(old[0].read_bytes())
    (new / "Y/a1.wav").write_bytes(b"RIFF twin")
    (new / "Y/a2.wav").write_bytes(b"RIFF twin")
    code = f"""
import numpy as np, siirto, samplehaku as sh, polut
from pathlib import Path
class Clap:
    name = "m"
    def text(self, t): return np.ones((len(t), 2), np.float32)
con = sh.db()
Y = Path({str(new / "Y")!r})
todo = [(p, p.stat()) for p in sorted(Y.glob("*.wav"))]
keep, dups = siirto.split_dups(con, todo)
print("KEEP", [p.name for p, _ in keep], "DUPS", sorted(p.name for p, _, _ in dups))
a1 = Y / "a1.wav"; st = a1.stat()       # "index" a1
con.execute("INSERT INTO files VALUES(?,?,?,3.0,'a1',?,NULL)", (polut.norm(a1), st.st_mtime, st.st_size, b"\\2" * 8))
siirto.fill_dups(con, dups, [Y], Clap())
rows = dict(con.execute("SELECT path, audio_emb FROM files"))
print("FILLED", rows.get(polut.norm(Y / "a2.wav")) == b"\\2" * 8, rows.get(polut.norm(Y / "Kick copy.wav")) == b"\\1" * 8)
print("KICKFEAT", con.execute("SELECT centroid FROM features WHERE path=?", (polut.norm(Y / "Kick copy.wav"),)).fetchone())
ft = [(polut.norm(p), p.stat().st_mtime, p.stat().st_size) for p in (a1, Y / "a2.wav")]
rest, later = siirto.feature_dups(con, ft, 2)
print("FEAT", [Path(p).name for p, *_ in rest], [Path(p).name for p, *_ in later])
con.execute("INSERT INTO features(path, mtime, size, centroid, ver) VALUES(?,?,?,0.7,2)", ft[0])
siirto.copy_features(con, later)
print("A2FEAT", con.execute("SELECT centroid, mtime FROM features WHERE path=?", (ft[1][0],)).fetchone() == (0.7, ft[1][1]))
"""
    # an indexed file that was never hashed (its size was unique), and a new copy of it
    code += f"""
u = Y / "u.wav"; u.write_bytes(b"RIFF unique old"); st = u.stat()
con.execute("INSERT INTO files VALUES(?,?,?,1.0,'u',?,NULL)", (polut.norm(u), st.st_mtime, st.st_size, b"\\3" * 8))
con.commit()
(Y / "Z").mkdir(); c = Y / "Z" / "u copy.wav"; c.write_bytes(u.read_bytes())
keep, dups = siirto.split_dups(con, [(c, c.stat())])
print("OLDCOPY", keep == [] and [p.name for p, _, _ in dups] == ["u copy.wav"])
s1 = Y / "silent.wav"; s1.write_bytes(b"RIFF silent!!"); s2 = Y / "Z" / "silent 2.wav"; s2.write_bytes(s1.read_bytes())
keep, dups = siirto.split_dups(con, [(s1, s1.stat()), (s2, s2.stat())])
st = s1.stat(); con.execute("INSERT INTO files VALUES(?,?,?,0.5,'',NULL,NULL)", (polut.norm(s1), st.st_mtime, st.st_size))
siirto.fill_dups(con, dups, [Y], Clap())
print("SILENT", con.execute("SELECT duration, audio_emb FROM files WHERE path=?", (polut.norm(s2),)).fetchone())
"""
    r = run(code, env | {"SAMPLEHAKU_DIR": str(T / "here")})
    check("copies: a copy of an indexed file without a hash is found", "OLDCOPY True" in r.stdout, r.stdout[-300:] + r.stderr[-300:])
    check("copies: a copy of a silent original is stored as processed too", "SILENT (0.5, None)" in r.stdout,
          r.stdout[-300:] + r.stderr[-300:])
    check("copies: runs", r.returncode == 0, r.stderr[-400:])
    check("copies: only one of each content is computed",
          "KEEP ['a1.wav'] DUPS ['Kick copy.wav', 'a2.wav']" in r.stdout, r.stdout[-300:])
    check("copies: filled from the original after indexing", "FILLED True True" in r.stdout, r.stdout[-300:])
    check("copies: features of an analysed original come along", "KICKFEAT (0.5,)" in r.stdout, r.stdout[-300:])
    check("copies: features computed once in this run",
          "FEAT ['a1.wav'] ['a2.wav']" in r.stdout and "A2FEAT True" in r.stdout, r.stdout[-300:])
    r = run(f"import siirto, samplehaku as sh; siirto.reuse(sh.db(), [{str(T / 'win.db')!r}], [], [], None)",
            env | {"SAMPLEHAKU_DIR": str(T / "here")})
    check("reuse: a missing index is an error", r.returncode != 0 and "not found" in r.stderr, r.stderr[-200:])

otsikko("macOS leftovers are not indexed")
r = run("import samplehaku as sh; from pathlib import Path as P; print([sh.junk_file(P(x)) for x in "
        "['/S/._kick.wav', '/S/__MACOSX/P/kick.wav', '/S/P/kick.wav', '/S/P/_kick.wav']])", {"PYTHONPATH": str(ROOT)})
check("._* and __MACOSX skipped, normal names kept", r.stdout.strip() == "[True, True, False, False]",
      r.stdout + r.stderr[-300:])

otsikko("CLAP mel in the worker processes = the batched call")
code = """
import numpy as np, soundfile as sf, tempfile, os, samplehaku as sh
from transformers import ClapFeatureExtractor
rng = np.random.default_rng(1)
d = tempfile.mkdtemp()
ps = []
for i, s in enumerate((0.4, 3.0, 25.0)):          # short, medium, several windows
    p = os.path.join(d, f"t{i}.wav"); sf.write(p, (rng.standard_normal(int(s * 44100)) * 0.1).astype("float32"), 44100)
    ps.append(p)
fe = ClapFeatureExtractor.from_pretrained(sh.DEFAULT_MODEL); fe.truncation = "rand_trunc"
ok = True
for p in ps:
    f, lg, dur, err = sh._load_mel((sh.Path(p), sh.DEFAULT_MODEL, "rand_trunc"))
    w, _ = sh.load_windows(sh.Path(p))
    ref = fe(w, sampling_rate=sh.SR, return_tensors="np")
    ok &= err is None and f.shape[0] == len(w) and np.array_equal(f, ref["input_features"].astype(np.float32))
print("SAME", ok)
"""
r = run(code, {"PYTHONPATH": str(ROOT), "HF_HUB_OFFLINE": "1"})
check("per-window mel equals the batched feature extractor", "SAME True" in r.stdout,
      r.stdout[-200:] + r.stderr[-400:])

otsikko("Ordered parallel map (rinnakkain.py)")
r = run("import rinnakkain, math; print(list(rinnakkain.ordered_map(math.isqrt, range(50), 3, ahead=4)))", {})
check("same order as the input, in 3 processes", r.stdout.strip() == str([__import__("math").isqrt(i)
                                                                         for i in range(50)]), r.stderr[-300:])
r = run("import rinnakkain, math; print(list(rinnakkain.ordered_map(math.isqrt, [4, 9], 1)))", {})
check("jobs=1 runs in the same process", r.stdout.strip() == "[2, 3]")

sys.exit(yhteenveto("Merge and relocate"))
