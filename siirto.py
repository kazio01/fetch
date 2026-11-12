"""
Moving a library and bringing in another computer's index, without computing anything again.

    samplehaku merge OTHER.db --map "D:/Samples=/mnt/winsamples" [--dry-run]
        Adds the files of an index made elsewhere (e.g. Fetch portable on a Windows PC with an NVIDIA
        card: <Fetch portable>\\data\\index\\index.db) to this index. --map turns the other computer's
        folder into the path where this computer sees the same files (a mounted share). Every file is
        checked here (exists, same size) and gets this computer's modification time, so the next
        "samplehaku index" / "samplehaku-piirteet" sees nothing new.

    samplehaku relocate OLD NEW [--dry-run] [--allow-missing]
        The files under OLD now live under NEW (copied to a server, a share mounted elsewhere).
        Rewrites the paths in the index, the library roots, the user's corrections (korjaukset.json),
        the correction log, spot-check files and the manifests of collections and result folders.
        Every changed settings file is backed up first (<file>.bak-relocate-<time>).

Both back up index.db first (index.db.bak-<merge|relocate>-<time>). Paths: polut.py ("/" everywhere).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import polut
import samplehaku as sh


def _clean(p: str) -> str:
    """A folder as written on the command line (also Windows forms: D:\\Samples, \\\\pc\\share)."""
    p = p.strip().strip('"').replace("\\", "/")
    return p.rstrip("/") if len(p) > 1 else p


def _local(p: str) -> str:
    return polut.norm(Path(p).expanduser().resolve())


def _backup_db(con: sqlite3.Connection, why: str) -> Path:
    dst = sh.DB_PATH.with_name(f"{sh.DB_PATH.name}.bak-{why}-{time.strftime('%Y%m%d-%H%M%S')}")
    con.commit()            # an open write transaction would make the backup wait for itself forever
    b = sqlite3.connect(dst)
    try:
        con.backup(b)
    finally:
        b.close()
    return dst


def _stat_all(paths: list[str]) -> list[os.stat_result | None]:
    """os.stat for many files in parallel threads (a network share answers one at a time slowly)."""
    def one(p):
        try:
            return os.stat(p)
        except OSError:
            return None
    out, t0 = [], time.time()
    with ThreadPoolExecutor(32) as ex:
        for i, st in enumerate(ex.map(one, paths, chunksize=64)):
            out.append(st)
            if i % 2000 == 1999:
                print(f"\r  checked {i + 1}/{len(paths)} files ({(i + 1) / (time.time() - t0):.0f}/s)   ",
                      end="", flush=True)
    if len(paths) >= 2000:
        print()
    return out


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


# ---------------------------------------------------------------- content hashes / reuse
# The same sample often sits in two libraries (a pack bought twice, copied folders). Its analysis
# depends only on the bytes, so "samplehaku index DIR --reuse OTHER.db" copies the embedding and the
# sound features from the other index instead of computing them. Files are matched by content:
# same size, then the same BLAKE2b hash of the whole file. The other index needs the hashes first:
# "samplehaku hashes" on the computer that made it (stored in the table content_hash).

def file_hash(p: str | Path) -> str:
    import hashlib
    h = hashlib.blake2b(digest_size=16)
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _hash_table(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS content_hash(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, h TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS content_hash_h ON content_hash(h)")


def _hash_all(paths: list[str], label: str) -> list[str | None]:
    def one(p):
        try:
            return file_hash(p)
        except OSError:
            return None
    out, t0 = [], time.time()
    with ThreadPoolExecutor(8) as ex:
        for i, h in enumerate(ex.map(one, paths, chunksize=16)):
            out.append(h)
            if i % 500 == 499 or i == len(paths) - 1:
                print(f"\r  {label} {i + 1}/{len(paths)} ({(i + 1) / (time.time() - t0 + 1e-9):.0f}/s)   ",
                      end="", flush=True)
    if paths:
        print()
    return out


def hashes() -> None:
    """Store a content hash for every indexed file of this computer (only new or changed ones)."""
    con = sh.db()
    _hash_table(con)
    have = {p: (m, s) for p, m, s in con.execute("SELECT path, mtime, size FROM content_hash")}
    rows = con.execute("SELECT path, mtime, size FROM files WHERE audio_emb IS NOT NULL").fetchall()
    todo = [r for r in rows if have.get(r[0]) != (r[1], r[2])]
    print(f"{len(rows)} indexed files, {len(todo)} need a content hash.")
    for i in range(0, len(todo), 2000):
        part = todo[i:i + 2000]
        hs = _hash_all([r[0] for r in part], f"hashing {i}+")
        con.executemany("INSERT OR REPLACE INTO content_hash VALUES(?,?,?,?)",
                        [(p, m, s, h) for (p, m, s), h in zip(part, hs) if h])
        con.commit()
    con.execute("DELETE FROM content_hash WHERE path NOT IN (SELECT path FROM files)")
    con.commit()
    print(f"Done: {con.execute('SELECT COUNT(*) FROM content_hash').fetchone()[0]} hashes in {sh.DB_PATH}")


def reuse(con: sqlite3.Connection, others: list[str], todo: list, roots: list[Path], clap) -> list:
    """Fill files in todo [(Path, stat)] from other indexes with identical content; return the rest."""
    _hash_table(con)
    by_size: dict[int, list] = {}
    srcs = []
    for o in others:
        sp = Path(o).expanduser()
        if not sp.is_file():
            sys.exit(f"--reuse: not found: {o}")
        src = sqlite3.connect(f"file:{sp}?mode=ro", uri=True)
        m_src = src.execute("SELECT value FROM meta WHERE key='model'").fetchone()
        if m_src and m_src[0] != clap.name:
            sys.exit(f"--reuse: {o} uses model {m_src[0]}, this index {clap.name}.")
        if not src.execute("SELECT 1 FROM sqlite_master WHERE name='content_hash'").fetchone():
            sys.exit(f"--reuse: {o} has no content hashes. Run \"samplehaku hashes\" on the computer that made it.")
        n = 0
        for p, s, h in src.execute("""SELECT c.path, c.size, c.h FROM content_hash c JOIN files x
                ON x.path = c.path AND x.mtime = c.mtime AND x.size = c.size WHERE x.audio_emb IS NOT NULL"""):
            by_size.setdefault(s, []).append((h, len(srcs), p))
            n += 1
        srcs.append(src)
        print(f"Reuse: {n} analysed files in {sp}")
    cand = [(p, st) for p, st in todo if st.st_size in by_size]
    if not cand:
        print("Reuse: no file here has the same size as one there.")
        return todo
    hs = _hash_all([str(p) for p, _ in cand], "comparing")
    found = []                       # (Path, stat, hash, src index, src path)
    for (p, st), h in zip(cand, hs):
        if h:
            m = next((x for x in by_size[st.st_size] if x[0] == h), None)
            if m:
                found.append((p, st, h, m[1], m[2]))
    print(f"Reuse: {len(found)} of {len(todo)} files are already analysed there (same content).")
    if not found:
        return todo

    done = set()
    for si, src in enumerate(srcs):
        done |= _copy_analysis(con, src, [(p, st, h, sp) for p, st, h, i, sp in found if i == si], roots, clap)
        src.close()
    return [(p, st) for p, st in todo if polut.norm(p) not in done]


def _copy_analysis(con: sqlite3.Connection, src: sqlite3.Connection, items: list, roots: list[Path], clap) -> set:
    """items [(Path, stat, hash, path in src)]: copy embedding, duration and current sound features from
    src (another index or this one); the name and its embedding are made from the new path."""
    dcols = set(_columns(con, "features"))
    for _, name, typ, *_ in src.execute("PRAGMA table_info(features)"):
        if name not in dcols:         # a column the other index has but this one doesn't yet
            con.execute(f"ALTER TABLE features ADD COLUMN {name} {typ}")
            dcols.add(name)
    fcols = [c for c in _columns(src, "features") if c in dcols]
    i_p, i_m, i_s = fcols.index("path"), fcols.index("mtime"), fcols.index("size")
    done = set()
    for i in range(0, len(items), 256):
        part = items[i:i + 256]
        names = [sh.name_text(p, roots) for p, *_ in part]
        ne = clap.text(names)
        for k, (p, st, h, sp) in enumerate(part):
            r = src.execute("SELECT mtime, size, duration, audio_emb FROM files WHERE path=?", (sp,)).fetchone()
            if r is None or r[3] is None:
                continue
            here = polut.norm(p)
            con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?)",
                        (here, st.st_mtime, st.st_size, r[2], names[k], r[3], ne[k].astype("float32").tobytes()))
            f = src.execute(f"SELECT {', '.join(fcols)} FROM features WHERE path=?", (sp,)).fetchone()
            if f is not None and (f[i_m], f[i_s]) == (r[0], r[1]):
                f = list(f)
                f[i_p], f[i_m] = here, st.st_mtime
                con.execute(f"INSERT OR REPLACE INTO features({', '.join(fcols)}) "
                            f"VALUES({', '.join('?' * len(fcols))})", f)
            con.execute("INSERT OR REPLACE INTO content_hash VALUES(?,?,?,?)", (here, st.st_mtime, st.st_size, h))
            done.add(here)
        con.commit()
    return done


def _own_analysed(con: sqlite3.Connection) -> dict[str, str]:
    """hash -> a path of this index whose file (this version) has an audio embedding."""
    return {h: p for p, h in con.execute("""SELECT c.path, c.h FROM content_hash c JOIN files x
        ON x.path = c.path AND x.mtime = c.mtime AND x.size = c.size WHERE x.audio_emb IS NOT NULL""")}


def split_dups(con: sqlite3.Connection, todo: list) -> tuple[list, list]:
    """Copies inside the library being indexed: todo [(Path, stat)] -> (to compute, copies).
    Only files whose size occurs twice (in todo, or in this index) are hashed. A copy is a file with
    the same content as one already analysed here, or as an earlier file of this run."""
    _hash_table(con)
    sizes: dict[int, int] = {}
    for _, st in todo:
        sizes[st.st_size] = sizes.get(st.st_size, 0) + 1
    # files of this index with a size seen in todo but no current hash yet (they were unique when
    # indexed): hash them now, so a new copy of an old file is found too
    hashed = {(p, m, s_) for p, m, s_ in con.execute("SELECT path, mtime, size FROM content_hash")}
    in_todo = {polut.norm(p) for p, _ in todo}
    old = [(p, m, s_) for p, m, s_ in con.execute("SELECT path, mtime, size FROM files WHERE audio_emb IS NOT NULL")
           if s_ in sizes and (p, m, s_) not in hashed and p not in in_todo]
    if old:
        hs = _hash_all([p for p, _, _ in old], "hashing indexed files")
        con.executemany("INSERT OR REPLACE INTO content_hash VALUES(?,?,?,?)",
                        [(p, m, s_, h) for (p, m, s_), h in zip(old, hs) if h])
        con.commit()
    own = _own_analysed(con)
    own_sizes = {s for (s,) in con.execute("""SELECT c.size FROM content_hash c JOIN files x
        ON x.path = c.path AND x.mtime = c.mtime AND x.size = c.size WHERE x.audio_emb IS NOT NULL""")}
    cand = [(p, st) for p, st in todo if sizes[st.st_size] > 1 or st.st_size in own_sizes]
    if not cand:
        return todo, []
    hs = _hash_all([str(p) for p, _ in cand], "looking for copies")
    con.executemany("INSERT OR REPLACE INTO content_hash VALUES(?,?,?,?)",
                    [(polut.norm(p), st.st_mtime, st.st_size, h) for (p, st), h in zip(cand, hs) if h])
    con.commit()
    hof = {polut.norm(p): h for (p, _), h in zip(cand, hs) if h}
    seen, keep, dups = set(own), [], []
    for p, st in todo:
        h = hof.get(polut.norm(p))
        if h and h in seen:
            dups.append((p, st, h))
        else:
            keep.append((p, st))
            if h:
                seen.add(h)
    if dups:
        print(f"Copies: {len(dups)} files have the same content as another one: analysed once, then copied.")
    return keep, dups


def fill_dups(con: sqlite3.Connection, dups: list, roots: list[Path], clap) -> None:
    """After indexing: give each copy the analysis of its original (sound features follow in
    aanipiirteet via feature_dups, when the original has them)."""
    if not dups:
        return
    own = _own_analysed(con)
    items = [(p, st, h, own[h]) for p, st, h in dups if h in own]
    n = len(_copy_analysis(con, con, items, roots, clap))
    # an original that is silent or unreadable was stored as processed without an embedding:
    # its copies get the same row, so they aren't tried again on every run
    empty = {h: d for h, d in con.execute("""SELECT c.h, x.duration FROM content_hash c JOIN files x
        ON x.path = c.path AND x.mtime = c.mtime AND x.size = c.size WHERE x.audio_emb IS NULL""")}
    rest = [(p, st, h) for p, st, h in dups if h not in own and h in empty]
    con.executemany("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,NULL,NULL)",
                    [(polut.norm(p), st.st_mtime, st.st_size, empty[h], "") for p, st, h in rest])
    con.commit()
    left = len(dups) - n - len(rest)
    print(f"Copies filled: {n} of {len(dups)}" + (f", {len(rest)} copies of silent or unreadable files" if rest else "")
          + (f", {left} not (tried again on the next run)" if left else ""))


def feature_dups(con: sqlite3.Connection, todo: list, version: int) -> tuple[list, list]:
    """Sound features: todo [(path, mtime, size)] -> (to compute, [(copy, mtime, size, original)]).
    Copies of a file that already has current features get them now; copies of a file computed in
    this run get them from copy_features afterwards."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='content_hash'").fetchone():
        return todo, []
    hof = {p: (m, s, h) for p, m, s, h in con.execute("SELECT path, mtime, size, h FROM content_hash")}
    have = {h: p for p, h in con.execute(
        """SELECT f.path, c.h FROM features f JOIN content_hash c
           ON c.path = f.path AND c.mtime = f.mtime AND c.size = f.size WHERE f.ver >= ?""", (version,))}
    rest, now, later, lead = [], [], [], {}
    for p, m, s in todo:
        c = hof.get(p)
        h = c[2] if c and (c[0], c[1]) == (m, s) else None
        if h and h in have:
            now.append((p, m, s, have[h]))
        elif h and h in lead:
            later.append((p, m, s, lead[h]))
        else:
            rest.append((p, m, s))
            if h:
                lead[h] = p
    copy_features(con, now)
    if now or later:
        print(f"Copies: {len(now) + len(later)} files get the sound features of an identical file.")
    return rest, later


def copy_features(con: sqlite3.Connection, items: list) -> None:
    cols = _columns(con, "features")
    i_p, i_m, i_s = cols.index("path"), cols.index("mtime"), cols.index("size")
    for p, m, s, src in items:
        f = con.execute("SELECT * FROM features WHERE path=?", (src,)).fetchone()
        if f is None:
            continue
        f = list(f)
        f[i_p], f[i_m], f[i_s] = p, m, s
        con.execute(f"INSERT OR REPLACE INTO features VALUES({', '.join('?' * len(cols))})", f)
    con.commit()


# ---------------------------------------------------------------- merge

def merge(other: str, maps: list[str], dry_run: bool = False) -> None:
    src_path = Path(other).expanduser()
    if not src_path.is_file():
        sys.exit(f"Not found: {other}")
    if src_path.resolve() == sh.DB_PATH.resolve():
        sys.exit("That is this computer's own index.")
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    con = sh.db()
    m_src = src.execute("SELECT value FROM meta WHERE key='model'").fetchone()
    m_dst = con.execute("SELECT value FROM meta WHERE key='model'").fetchone()
    if m_src and m_dst and m_src[0] != m_dst[0]:
        sys.exit(f"The other index uses model {m_src[0]}, this one {m_dst[0]}: they cannot be mixed.")

    pairs = []
    for m in maps:
        if "=" not in m:
            sys.exit(f"--map wants OTHER=HERE, e.g. \"D:/Samples=/mnt/winsamples\" (got {m!r})")
        a, b = m.split("=", 1)
        pairs.append((_clean(a), _local(b)))
    pairs.sort(key=lambda ab: -len(ab[0]))        # the deepest folder first

    def mapped(p: str) -> str | None:
        p = p.replace("\\", "/")
        for a, b in pairs:
            q = polut.replace_prefix(p, a, b)
            if q is not None:
                return q
        return None if pairs else p

    rows = src.execute("SELECT path, mtime, size, duration, name, audio_emb, name_emb FROM files").fetchall()
    src_roots = sh.get_roots(src) or ([polut.fallback_root([r[0] for r in rows])] if rows else [])
    print(f"{len(rows)} files in {src_path}; its library folders: {', '.join(src_roots) or '-'}")
    new_paths = [mapped(r[0]) for r in rows]
    unmapped = sum(p is None for p in new_paths)
    if unmapped:
        ex = next(r[0] for r, p in zip(rows, new_paths) if p is None)
        print(f"  {unmapped} files are outside the --map folders (e.g. {ex}): skipped")
    todo = [(r, p) for r, p in zip(rows, new_paths) if p is not None]
    if not todo:
        sys.exit("Nothing to bring in. Give the other computer's folder with --map OTHER=HERE.")

    print(f"Checking that the files are visible here ({todo[0][1].rsplit('/', 1)[0]} …)")
    stats = _stat_all([p for _, p in todo])
    known = {p: (m, s) for p, m, s in con.execute("SELECT path, mtime, size FROM files")}
    fcols = [c for c in _columns(src, "features") if c in set(_columns(con, "features"))]
    feats = {r[0]: r for r in src.execute(f"SELECT {', '.join(fcols)} FROM features")}
    i_m, i_s = fcols.index("mtime"), fcols.index("size")

    add_f, add_x, missing, changed, same = [], [], [], 0, 0
    for (r, p), st in zip(todo, stats):
        if st is None:
            missing.append(p)
            continue
        if st.st_size != r[2]:
            changed += 1
            continue
        if known.get(p) == (st.st_mtime, st.st_size):
            same += 1
            continue
        add_f.append((p, st.st_mtime, *r[2:]))
        f = feats.get(r[0])
        if f is not None and (f[i_m], f[i_s]) == (r[1], r[2]):      # features of this very version
            f = list(f)
            f[0], f[i_m] = p, st.st_mtime
            add_x.append(f)
    print(f"  new or updated: {len(add_f)} (sound features for {len(add_x)}), already here: {same}, "
          f"not found here: {len(missing)}, changed since (other size): {changed}")
    if missing:
        print(f"  e.g. not found: {missing[0]}\n  (is the share mounted? check the --map folders)")
    lib = sh.ensure_roots(con)
    for r in src_roots:
        q = mapped(r)
        if q:
            lib = polut.add_root(lib, q)
    print(f"Library folders after merging: {', '.join(lib)}")
    if dry_run:
        print("(--dry-run: nothing written)")
        return
    if not add_f:
        sh.set_roots(con, lib)
        con.commit()
        return
    print(f"Backup: {_backup_db(con, 'merge')}")
    con.executemany("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?)", add_f)
    if add_x:
        con.executemany(f"INSERT OR REPLACE INTO features({', '.join(fcols)}) VALUES({', '.join('?' * len(fcols))})",
                        add_x)
    sh.set_roots(con, lib)
    con.commit()
    print(f"Merged {len(add_f)} files. The search service picks them up within a minute "
          f"(or: systemctl --user restart samplehaku-haku).")
    if len(add_x) < len(add_f):
        print(f"{len(add_f) - len(add_x)} files have no sound features yet: run samplehaku-piirteet.")


# ---------------------------------------------------------------- relocate

def _rewrite(obj, old: str, new: str):
    """Swap the folder in every string (dict keys too) of a JSON value -> (value, changes)."""
    n = 0

    def go(v):
        nonlocal n
        if isinstance(v, str):
            q = polut.replace_prefix(v, old, new)
            if q is not None:
                n += 1
                return q
            return v
        if isinstance(v, list):
            return [go(x) for x in v]
        if isinstance(v, dict):
            return {go(k): go(x) for k, x in v.items()}
        return v
    return go(obj), n


def _settings_files() -> list[Path]:
    """Files that hold sample paths, besides index.db."""
    from samplehaku_kokoelmat import COLLECTIONS_DIR, MANIFEST
    log = Path(os.environ.get("SAMPLEHAKU_FIX_LOG", sh.FIXES_PATH.parent / "korjausloki.jsonl"))
    results = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "samplehaku/tulokset"
    out = [sh.FIXES_PATH, log, *sorted(sh.DATA_DIR.glob("*.json"))]
    for d in (COLLECTIONS_DIR, results):
        if d.is_dir():
            out += sorted(d.glob(f"*/{MANIFEST}"))
    return [p for p in out if p.is_file()]


def relocate(old: str, new: str, dry_run: bool = False, allow_missing: bool = False) -> None:
    old = _clean(old)
    if not old.startswith("/") and ":" not in old:
        old = _local(old)
    new = _local(new)
    if old == new:
        sys.exit("OLD and NEW are the same folder.")
    con = sh.db()
    rows = [r for r in con.execute("SELECT path, mtime, size FROM files")
            if polut.replace_prefix(r[0], old, new) is not None]
    if not rows:
        sys.exit(f"No indexed files under {old}.")
    moved = [polut.replace_prefix(r[0], old, new) for r in rows]
    print(f"{len(rows)} indexed files under {old} -> {new}. Checking the new place …")
    stats = _stat_all(moved)
    bad = [(r, p) for r, p, st in zip(rows, moved, stats) if st is None or st.st_size != r[2]]
    if bad:
        print(f"  {len(bad)} are missing or differ in size there, e.g. {bad[0][1]}")
        if not allow_missing:
            sys.exit("Nothing changed. Finish copying (or --allow-missing: those keep the old path and are "
                     "dropped by the next index run of that folder).")
    ok = [(r, p, st) for r, p, st in zip(rows, moved, stats) if st is not None and st.st_size == r[2]]
    fkeys = {r[0]: (r[1], r[2]) for r in con.execute("SELECT path, mtime, size FROM features")}

    roots = sh.ensure_roots(con)
    lib = []
    for r in roots:
        q = polut.replace_prefix(r, old, new)
        if q is not None:
            lib.append(q)
        elif old.startswith(r + "/"):       # a pack (or deeper folder) moved out of the root
            lib.append(r)
            rel = old[len(r) + 1:]
            lib = polut.add_root(lib, new[:-len(rel) - 1] if new.endswith("/" + rel) else polut.parent(new))
        else:
            lib.append(r)
    print(f"Library folders: {', '.join(roots)}  ->  {', '.join(sorted(set(lib)))}")

    files = []
    for f in _settings_files():
        try:
            if f.suffix == ".jsonl":
                lines = f.read_text().splitlines()
                done = [_rewrite(json.loads(x), old, new) if x.strip() else (x, 0) for x in lines]
                n = sum(c for _, c in done)
                text = "\n".join(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                                 for v, _ in done) + "\n"
            else:
                v, n = _rewrite(json.loads(f.read_text()), old, new)
                text = json.dumps(v, ensure_ascii=False, indent=1)
        except (OSError, ValueError) as e:
            print(f"  ! {f}: not read ({e.__class__.__name__}), left as it is")
            continue
        if n:
            files.append((f, text, n))
            print(f"  {f}: {n} paths")
    if dry_run:
        print(f"(--dry-run: nothing written; {len(ok)} files would move)")
        return

    print(f"Backup: {_backup_db(con, 'relocate')}")
    ups_f, ups_x = [], []
    for (p0, m0, s0), p, st in ok:
        ups_f.append((p, st.st_mtime, p0))
        if fkeys.get(p0) == (m0, s0):                  # features of this version: keep them matching
            ups_x.append((p, st.st_mtime, p0))
        elif p0 in fkeys:
            ups_x.append((p, fkeys[p0][0], p0))
    new_set = [p for _, p, _ in ok]
    con.executemany("DELETE FROM files WHERE path=?", [(p,) for p in new_set])     # an earlier index of NEW
    con.executemany("DELETE FROM features WHERE path=?", [(p,) for p in new_set])
    con.executemany("UPDATE files SET path=?, mtime=? WHERE path=?", ups_f)
    con.executemany("UPDATE features SET path=?, mtime=? WHERE path=?", ups_x)
    sh.set_roots(con, lib)
    con.commit()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for f, text, _ in files:
        bak = f.with_name(f"{f.name}.bak-relocate-{stamp}")
        bak.write_bytes(f.read_bytes())
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, f)
    print(f"Moved {len(ok)} files. Restart the search service: systemctl --user restart samplehaku-haku")
