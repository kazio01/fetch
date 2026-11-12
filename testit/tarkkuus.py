"""Detection accuracy: how often Fetch's automatic instrument / type / tempo / key are right.
Read-only: it does not touch the database, the corrections or the service.

    .venv/bin/python -I testit/tarkkuus.py            # everything, saves testit/tulokset/tarkkuus-<time>.json
    .venv/bin/python -I testit/tarkkuus.py --examples 20   # more example errors per section

Sections
  1. Instrument vs the user's corrections (korjaukset.json): classification run as if the corrections did not exist.
  2. Instrument from audio, leave one pack series out: files with a name label get their label hidden, the
     audio vote (same code as soittimet.classify) guesses it from the other packs only. This is how well the
     guess works on the ~6k samples that have no instrument in the name.
  3. Loop / one-shot vs the corrections, and how many samples each rule decides.
  4. Tempo and key from audio vs the tempo / key in the file name (~20k / ~12k files).
  5. Correction log (korjausloki.jsonl): what was shown before each correction since logging started.
  6. Spot check (testit/tarkistus.py): samples you listened to and judged; the random part = the whole library.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "tulokset"
sys.path[:0] = [str(HERE), str(ROOT)]

import polut  # noqa: E402
import samplehaku as sh  # noqa: E402
import soittimet as so  # noqa: E402

R: dict[str, dict] = {}
RO: dict[str, str] = {}      # path -> its library root (main() fills it; several roots since 0.4)
EX: dict[str, list] = defaultdict(list)


def pct(a, b) -> float:
    return round(100.0 * a / b, 1) if b else float("nan")


def head(t: str) -> None:
    print(f"\n\033[1;36m## {t}\033[0m")


def set_roots(paths, roots=None) -> None:
    """Remember every path's library root (polut.roots_per_path), for rel() and root_of()."""
    RO.update(zip(paths, polut.roots_per_path(paths, roots)))


def root_of(p: str) -> str:
    if p not in RO:                                    # a path outside the loaded index (spot check store)
        RO[p] = polut.root_of(p, set(RO.values())) or polut.parent(polut.parent(p))
    return RO[p]


def rel(p: str, root: str | None = None) -> str:
    """The path from its library root ("Pack/Loops/x.wav")."""
    return p[len(root_of(p) if root is None else root):].lstrip("/")


def library(p: str) -> str:
    """The library a path belongs to, as its root folder's name."""
    return root_of(p).rsplit("/", 1)[-1]


def series_of(relpath: str) -> str:
    """Pack series: 'Mark Knight Vol. 6' and 'Vol. 7' are one series (same producer's naming and sound),
    '02. Kits/727 Archive' -> numbered category folders count one level deeper."""
    parts = relpath.split("/")
    pk = parts[0]
    if re.match(r"\d+\.\s", pk) and len(parts) > 2:
        pk = parts[1]
    pk = re.sub(r"(?i)\b(vol(ume)?\.?\s*\d+|[ivx]+|\d+)\s*$", "", pk.replace("_", " ")).strip(" -._")
    return pk.lower() or parts[0].lower()


def series_all(paths, roots=None) -> list[str]:
    """The pack series of every path: its pack (pakkaukset.find: a library can have folders above the packs)
    without the volume number, so 'Mark Knight Vol. 6' and 'Vol. 7' are one series wherever they are."""
    import pakkaukset
    if roots is None:
        roots = sh.get_roots(sh.db())
    packs, titles = pakkaukset.find(paths, roots)
    out = []
    for pk, nt, r in zip(packs, titles, polut.roots_per_path(paths, roots)):
        parts = pk[len(r):].lstrip("/").split("/")
        name = parts[-1] if nt >= len(parts) else "/".join(parts[-2:])     # "ESW SAMPLES/Drums": not just "drums"
        name = re.sub(r"(?i)\b(vol(ume)?\.?\s*\d+|[ivx]+|\d+)\s*$", "", name.replace("_", " ")).strip(" -._")
        out.append(name.lower() or parts[-1].lower())
    return out


# ---------------------------------------------------------------- 1. instrument vs corrections
def instrument_vs_fixes(idx, fixes, pos, inst, est, root, nex):
    head("1. Instrument vs your corrections (automatic result without them)")
    n = ok = ok_main = 0
    by_src = Counter()
    wrong_src = Counter()
    pairs = Counter()
    for p, f in fixes.items():
        if "inst" not in f or p not in pos:
            continue
        i = pos[p]
        want, got = so.migrate(f["inst"]), inst[i]
        src = "audio" if est[i] else ("name" if got else "none")
        n += 1
        by_src[src] += 1
        if got == want or got.startswith(want + "/"):
            ok += 1
            continue
        if got.split("/")[0] == want.split("/")[0]:
            ok_main += 1
        wrong_src[src] += 1
        pairs[(got or "-", want)] += 1
        if len(EX["inst_fix"]) < nex:
            EX["inst_fix"].append(f"{got or '-':22s} -> {want:22s} [{src}] {rel(p, root)}")
    print(f"corrected instruments: {n}, automatic already right: {ok} ({pct(ok, n)} %), "
          f"right main class only: {ok_main}")
    print(f"wrong, by source: {dict(wrong_src)}   (all, by source: {dict(by_src)})")
    for (g, w), c in pairs.most_common(8):
        print(f"  {c:4d}  {g} -> {w}")
    R["inst_vs_fixes"] = {"n": n, "right": ok, "right_pct": pct(ok, n), "main_only": ok_main,
                          "wrong_by_source": dict(wrong_src),
                          "top_pairs": [[g, w, c] for (g, w), c in pairs.most_common(15)]}


# ---------------------------------------------------------------- 2. audio vote, leave one series out
def audio_leave_pack_out(idx, fixes, pos, root, nex, chunk=1000):
    head("2. Instrument from audio, leave one pack series out")
    paths = idx.paths
    t0 = time.time()
    if hasattr(so, "name_labels"):                     # as the app labels them (titles need the sound: not used)
        named = [a or "" for a, _ in so.name_labels(paths, idx.roots)[0]]
    else:
        named = [so.from_name(p, root_of(p))[0] or "" for p in paths]
    inst = np.array(named, dtype=object)
    for i, node in so.stem_families(paths).items():
        if not (inst[i] == node or inst[i].startswith(node + "/")):
            inst[i] = node
    for p, f in fixes.items():                         # the user's labels are the best truth there is
        if p in pos and so.migrate(f.get("inst", "")) in so.LABEL:
            inst[pos[p]] = so.migrate(f["inst"])
    L = np.array([i for i in range(len(paths)) if inst[i] and inst[i] not in so.PARTIAL
                  and inst[i] not in getattr(so, "REFINE", ())])
    classes = sorted(set(inst[L]))
    cid = {c: j for j, c in enumerate(classes)}
    yL = np.array([cid[c] for c in inst[L]])
    wcls = 1 / np.sqrt(np.maximum(np.bincount(yL, minlength=len(classes)).astype(np.float32), 1))
    ser_all = series_all(paths, idx.roots)
    ser = np.array([ser_all[i] for i in L])
    sid = {s: j for j, s in enumerate(sorted(set(ser)))}
    sL = np.array([sid[s] for s in ser])
    AL = idx.A[L].astype(np.float32)
    k = 15
    answered = leaf_ok = main_ok = twins = 0
    per_main = defaultdict(lambda: [0, 0, 0])          # main class: [n, answered, main right]
    per_lib = defaultdict(lambda: [0, 0, 0, 0])        # library: [n, answered, main right, exact right]
    pairs = Counter()
    for s in range(0, len(L), chunk):
        q = np.arange(s, min(s + chunk, len(L)))
        sim = AL[q] @ AL.T
        sim[sL[q][:, None] == sL[None, :]] = -np.inf     # the same series is out
        nn = np.argpartition(-sim, k, axis=1)[:, :k]
        votes = np.zeros((len(q), len(classes)), dtype=np.float32)
        rows = np.arange(len(q))
        twins += int((sim.max(axis=1) > 0.9995).sum())   # the same audio under another series (a copy)
        for j in range(k):
            yy = yL[nn[:, j]]
            w = np.exp(np.where(np.isfinite(sim[rows, nn[:, j]]), sim[rows, nn[:, j]], -np.inf) * 10)
            np.add.at(votes, (rows, yy), w * wcls[yy])
        for r, qi in enumerate(q):
            want = classes[yL[qi]]
            m = want.split("/")[0]
            per_main[m][0] += 1
            lb = per_lib[library(paths[L[qi]])]
            lb[0] += 1
            got = so.vote_pick(votes[r], classes)
            if not got:
                continue
            answered += 1
            per_main[m][1] += 1
            lb[1] += 1
            if got == want:
                leaf_ok += 1
                lb[3] += 1
            if got.split("/")[0] == m:
                main_ok += 1
                per_main[m][2] += 1
                lb[2] += 1
            else:
                pairs[(got.split("/")[0], m)] += 1
                if len(EX["inst_audio"]) < nex and np.random.random() < 0.02:
                    EX["inst_audio"].append(f"{got:22s} (is {want:22s}) {rel(paths[L[qi]], root)}")
    n = len(L)
    print(f"labelled files: {n} in {len(sid)} pack series, {len(classes)} classes ({time.time() - t0:.0f} s)")
    print(f"answered: {answered} ({pct(answered, n)} %)  ·  of the answered: main class right "
          f"{pct(main_ok, answered)} %, exact class right {pct(leaf_ok, answered)} %")
    print(f"  {'main class':14s} {'n':>6s} {'answered':>9s} {'right':>7s}")
    rows_out = {}
    for m, (cn, ca, cr) in sorted(per_main.items(), key=lambda x: -x[1][0]):
        print(f"  {m:14s} {cn:6d} {pct(ca, cn):8.1f}% {pct(cr, ca):6.1f}%")
        rows_out[m] = {"n": cn, "answered_pct": pct(ca, cn), "right_pct": pct(cr, ca)}
    print("  most common confusions (guessed -> is):")
    for (g, w), c in pairs.most_common(8):
        print(f"  {c:5d}  {g} -> {w}")
    print(f"  {'library':28s} {'n':>7s} {'answered':>9s} {'main':>7s} {'exact':>7s}")
    for lb, (cn, ca, cm, ce) in sorted(per_lib.items(), key=lambda x: -x[1][0]):
        print(f"  {lb[:28]:28s} {cn:7d} {pct(ca, cn):8.1f}% {pct(cm, ca):6.1f}% {pct(ce, ca):6.1f}%")
    print(f"  files with an identical copy in another series: {twins} ({pct(twins, n)} %)")
    R["inst_audio_lopo"] = {"n": n, "series": len(sid), "answered_pct": pct(answered, n),
                            "main_right_pct": pct(main_ok, answered), "exact_right_pct": pct(leaf_ok, answered),
                            "per_main": rows_out, "twins": twins,
                            "per_library": {lb: {"n": cn, "answered_pct": pct(ca, cn), "main_right_pct": pct(cm, ca),
                                                 "exact_right_pct": pct(ce, ca)} for lb, (cn, ca, cm, ce) in per_lib.items()},
                            "top_confusions": [[g, w, c] for (g, w), c in pairs.most_common(15)]}


# ---------------------------------------------------------------- 3. loop / one-shot
def sample_types(idx, fixes, pos, raw_bpm, root, nex):
    head("3. Loop / one-shot")
    why = [sh.sample_type_why(p, float(idx.dur[i]), raw_bpm[i]) for i, p in enumerate(idx.paths)]
    auto = getattr(idx, "_raw", {}).get("stype")       # name + the sound model (Index._build)
    if auto is not None:
        why = [(int(auto[i]), "sound" if int(auto[i]) != t else w) for i, (t, w) in enumerate(why)]
    rules = Counter(w for _, w in why)
    print("decided by: " + ", ".join(f"{w} {c} ({pct(c, len(why))} %)" for w, c in rules.most_common()))
    tab = defaultdict(lambda: [0, 0])                  # rule: [n, right]
    for p, f in fixes.items():
        if f.get("type") not in ("loop", "oneshot") or p not in pos:
            continue
        i = pos[p]
        t, w = why[i]
        right = t == sh.TYPE_IDS[f["type"]]
        tab[w][0] += 1
        tab[w][1] += right
        if not right and len(EX["type_fix"]) < nex:
            EX["type_fix"].append(f"{'loop' if t else 'one-shot':8s} -> {f['type']:8s} [{w}] {rel(p, root)}")
    n = sum(a for a, _ in tab.values())
    r = sum(b for _, b in tab.values())
    print(f"corrected types: {n}, automatic right: {r} ({pct(r, n)} %)")
    for w, (a, b) in sorted(tab.items(), key=lambda x: -x[1][0]):
        print(f"  {w:9s} {a:6d} corrected, right {pct(b, a)} %")
    print("  (most of the corrections were made when 'Other' still existed: 'default' = loop is now right by"
          " definition for those, so this number flatters; the spot check measures it honestly)")
    R["type"] = {"rules": dict(rules), "fixes_n": n, "fixes_right_pct": pct(r, n),
                 "by_rule": {w: {"n": a, "right_pct": pct(b, a)} for w, (a, b) in tab.items()}}
    return why


# ---------------------------------------------------------------- 4. tempo and key from audio vs name
def tempo_key(idx, why, root, nex):
    head("4. Tempo and key from audio vs the file name")
    con = sh.db()
    audio = dict(((p, (b, k)) for p, b, k in con.execute("SELECT path, bpm, key FROM features")))
    tb = Counter()
    tk = Counter()
    for i, p in enumerate(idx.paths):
        nb, nk = sh.name_bpm_key(p)
        ab, ak = audio.get(p, (None, None))
        if np.isfinite(nb) and why[i][0] == 1:          # tempo: only loops get an estimate in the app
            if ab is None or not ab > 1:
                tb["no estimate"] += 1
            else:
                b = float(ab)
                for _ in range(16):
                    b = b / 2 if b >= 200 else (b * 2 if b < 60 else b)
                r = b / nb
                c = ("right" if abs(r - 1) <= sh.BPM_TOL else
                     "half/double" if min(abs(r - 0.5), abs(r - 2)) <= 2 * sh.BPM_TOL else
                     "×1.5 / ×2/3" if min(abs(r - 1.5), abs(r - 2 / 3)) <= 2 * sh.BPM_TOL else
                     "off by a little (3–8 %)" if abs(r - 1) <= 0.08 else "wrong")
                tb[c] += 1
                if c != "right" and len(EX["bpm"]) < nex and np.random.random() < 0.05:
                    EX["bpm"].append(f"{b:6.1f} (name {nb:g}) {c:12s} {rel(p, root)}")
        if nk:
            ek = sh.parse_audio_key(ak)
            if ek is None:
                tk["no estimate"] += 1
                continue
            (npc, nm), (epc, em) = nk, ek
            if nm == -1 or em == -1:                    # "A" without mode in the name: tonic only
                c = "right" if npc == epc else "wrong"
            elif (npc, nm) == (epc, em):
                c = "right"
            elif ek in sh.compatible_keys(npc, nm):
                rel_ = (npc + (3 if nm == 1 else -3)) % 12 == epc and em != nm
                c = "relative maj/min" if rel_ else "fifth (Camelot neighbour)"
            elif npc == epc:
                c = "parallel (same tonic)"
            else:
                c = "wrong"
            tk[c] += 1
            if c != "right" and len(EX["key"]) < nex and np.random.random() < 0.05:
                EX["key"].append(f"{sh.key_name(epc, em):4s} (name {sh.key_name(npc, nm)}) {c:24s} {rel(p, root)}")
    for title, t in (("tempo (loops with a tempo in the name)", tb), ("key (files with a key in the name)", tk)):
        n = sum(t.values())
        got = n - t["no estimate"]
        print(f"{title}: {n}, estimated {got} ({pct(got, n)} %)")
        for c, v in t.most_common():
            if c != "no estimate":
                print(f"  {c:26s} {v:6d}  {pct(v, got):5.1f} % of the estimated")
    R["bpm"] = {**dict(tb), "right_pct": pct(tb["right"], sum(tb.values()) - tb["no estimate"])}
    tempo_new(idx, why, con, root, nex)
    key_new(idx, con, root, nex)
    R["key"] = {**dict(tk), "right_pct": pct(tk["right"], sum(tk.values()) - tk["no estimate"])}
    print("  (the name always wins in the app, so these errors show only on files without a name value;"
          " files with a name value are the test set)")


def tempo_new(idx, why, con, root, nex):
    """The tempo the app now shows for loops without a name tempo (samplehaku.tempo_from_ac),
    measured on loops WITH one: the genre prior leaves the loop's own pack series out."""
    from tempo_kehitys import category, honest_estimates
    oa = {p: a for p, a in con.execute("SELECT path, onset_ac FROM features WHERE onset_ac IS NOT NULL")}
    rows = []
    for i, p in enumerate(idx.paths):
        nb = sh.name_bpm_key(p)[0]
        if np.isfinite(nb) and why[i] == (1, "name") and idx.dur[i] >= 1.5:     # as in the app: named loops only
            a = oa.get(p)
            rows.append((i, nb, np.frombuffer(a, dtype=np.float16) if a else None))
    have = [r for r in rows if r[2] is not None]
    print(f"tempo, new method (length candidates + genre prior, shown at confidence ≥ {sh.TEMPO_MIN_CONF}): "
          f"{len(have)}/{len(rows)} loops have onset data")
    if not have:
        print("  run: .venv/bin/python -I aanipiirteet.py --onset")
        R["bpm_new"] = {"n": 0}
        return
    est = honest_estimates(idx, [(i, ac, float(idx.dur[i])) for i, _, ac in have])
    c = Counter()
    by_lib = defaultdict(Counter)
    for (i, nb, _), (b, conf) in zip(have, est):
        cat = category(b if conf >= sh.TEMPO_MIN_CONF else None, nb)
        c[cat] += 1
        by_lib[library(idx.paths[i])][cat] += 1
        if cat not in ("right", "none") and len(EX["bpm_new"]) < nex and np.random.random() < 0.05:
            EX["bpm_new"].append(f"{b:6.1f} (name {nb:g}, conf {conf:.2f}) {cat:12s} {rel(idx.paths[i], root)}")
    n = len(have)
    got = n - c["none"]
    print(f"  shown for {pct(got, n)} % of the loops, right {pct(c['right'], got)} % of the shown")
    for k, v in c.most_common():
        if k not in ("none", "right"):
            print(f"  {k:26s} {v:6d}  {pct(v, got):5.1f} % of the shown")
    for lb, cl in sorted(by_lib.items(), key=lambda x: -sum(x[1].values())):
        ln = sum(cl.values())
        lg = ln - cl["none"]
        print(f"    {lb[:28]:28s} {ln:6d} loops, shown {pct(lg, ln):5.1f} %, right {pct(cl['right'], lg):5.1f} %")
    R["bpm_new"] = {**dict(c), "n": n, "answered_pct": pct(got, n), "right_pct": pct(c["right"], got),
                    "per_library": {lb: {"n": sum(cl.values()), "right_pct": pct(cl["right"], sum(cl.values()) - cl["none"])}
                                    for lb, cl in by_lib.items()}}


def key_new(idx, con, root, nex):
    """The key the app now shows for files without one in the name (samplehaku.keys_from_salience),
    measured on files WITH one: models trained without the file's own pack series; drums and percussion
    left out like in the app (Index._drop_drum_keys)."""
    from savellaji_kehitys import category, honest_keys
    ks = {p: a for p, a in con.execute("SELECT path, key_sal FROM features WHERE length(key_sal) > 0")}
    items = []
    inst = idx.instruments()[0]
    for p, ins in zip(idx.paths, inst):
        k = sh.name_bpm_key(p)[1]
        if k and p in ks and str(ins).split("/")[0] not in sh.KEY_NO_AUDIO:
            items.append(dict(path=p, key=k, S=np.frombuffer(ks[p], dtype=np.float16)))
    n_named = sum(1 for p in idx.paths if sh.name_bpm_key(p)[1])
    print(f"key, new method (key model, shown at p ≥ {sh.KEY_MIN_P}, no drums): {len(items)}/{n_named} named files "
          f"have salience data")
    if not items:
        print("  run: .venv/bin/python -I aanipiirteet.py --onset")
        R["key_new"] = {"n": 0}
        return
    est = honest_keys(items)
    c = Counter()
    by_lib = defaultdict(Counter)
    for it, (k, r) in zip(items, est):
        cat = category(k if r >= sh.KEY_MIN_P else None, it["key"])
        c[cat] += 1
        by_lib[library(it["path"])][cat] += 1
        if cat not in ("right", "none") and len(EX["key_new"]) < nex and np.random.random() < 0.05:
            EX["key_new"].append(f"{sh.key_name(*k):4s} (name {sh.key_name(*it['key'])}, p {r:.2f}) {cat:9s} "
                                 f"{rel(it['path'], root)}")
    n = len(items)
    got = n - c["none"]
    print(f"  shown for {pct(got, n)} % of the files, right {pct(c['right'], got)} % of the shown, "
          f"+ fifth/relative {pct(c['fifth'] + c['relative'], got)} %")
    for lb, cl in sorted(by_lib.items(), key=lambda x: -sum(x[1].values())):
        ln = sum(cl.values())
        lg = ln - cl["none"]
        print(f"    {lb[:28]:28s} {ln:6d} files, shown {pct(lg, ln):5.1f} %, right {pct(cl['right'], lg):5.1f} %, "
              f"+ fifth/relative {pct(cl['fifth'] + cl['relative'], lg):4.1f} %")
    R["key_new"] = {**dict(c), "n": n, "answered_pct": pct(got, n), "right_pct": pct(c["right"], got),
                    "per_library": {lb: {"n": sum(cl.values()), "right_pct": pct(cl["right"], sum(cl.values()) - cl["none"])}
                                    for lb, cl in by_lib.items()}}


# ---------------------------------------------------------------- 5. correction log
def correction_log(root, nex):
    head("5. Correction log")
    log = Path(os.environ.get("SAMPLEHAKU_FIX_LOG", sh.FIXES_PATH.parent / "korjausloki.jsonl"))
    rows = []
    try:
        rows = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        pass
    if not rows:
        print("no corrections logged yet (each Edit tags… adds a row)")
        R["log"] = {"n": 0}
        return
    ch = Counter()
    for r in rows:
        new = r.get("new") or {}
        for f in ("inst", "type"):
            if f in new and new[f] != r["was"].get(f):
                src = r.get("src", {}).get(f, "")
                ch[(f, src)] += 1
                if len(EX["log"]) < nex:
                    EX["log"].append(f"{f}: {r['was'].get(f) or '-'} -> {new[f]} [{src}] {rel(r['path'], root)}")
    print(f"{len(rows)} corrections logged; real changes: " +
          ", ".join(f"{f} ({s or '-'}) {c}" for (f, s), c in ch.most_common()))
    R["log"] = {"n": len(rows), "changes": {f"{f}/{s}": c for (f, s), c in ch.items()}}


# ---------------------------------------------------------------- 6. spot check
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % interval for a share k/n (honest with small n)."""
    if not n:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5 / d
    return round(100 * (c - h), 1), round(100 * (c + h), 1)


def spot_check(nex):
    head("6. Spot check (testit/tarkistus.py: you listened, judged what was shown then)")
    store = Path(os.environ.get("SAMPLEHAKU_SPOTCHECK", sh.DATA_DIR / "tarkistus.json"))
    try:
        st = json.loads(store.read_text())
    except (OSError, ValueError):
        st = {}
    items = [it for r in st.get("rounds", []) for it in r.get("items", [])] + st.get("items", [])
    items = [it for it in items if it.get("verdict")]
    if not items:
        print("nothing judged yet: .venv/bin/python -I testit/tarkistus.py")
        R["spot"] = {"n": 0}
        return
    out = {"n": len(items)}
    names = {"inst": "instrument", "type": "type", "bpm": "tempo", "key": "key"}
    print(f"{len(items)} judged ({sum(it['part'] == 'uniform' for it in items)} random, "
          f"{sum(it['part'] == 'audio' for it in items)} extra audio guesses)")
    print(f"  {'random sample = the whole library':38s} {'n':>4s} {'right':>7s}  95 % range")
    for f in ("inst", "type", "bpm", "key"):
        v = [it["verdict"][f] for it in items if it["part"] == "uniform" and it["verdict"][f] != "unsure"]
        ok = sum(x == "ok" for x in v)
        lo, hi = wilson(ok, len(v))
        print(f"  {names[f]:38s} {len(v):4d} {pct(ok, len(v)):6.1f}%  {lo}–{hi}")
        out[f] = {"n": len(v), "right_pct": pct(ok, len(v))}
        if f == "inst":
            m = sum(x in ("ok", "sub") for x in v)
            print(f"  {'  main class right':38s} {len(v):4d} {pct(m, len(v)):6.1f}%")
            out["inst"]["main_right_pct"] = pct(m, len(v))
    print("  by source (random + extra):")
    for f in ("inst", "type", "bpm", "key"):
        by = defaultdict(lambda: [0, 0])
        for it in items:
            x = it["verdict"][f]
            if x == "unsure":
                continue
            src = it["shown"].get(f + "_src", "")
            by[src][0] += 1
            by[src][1] += x == "ok"
        print(f"    {names[f]:11s} " + " · ".join(f"{s} {b}/{a} = {pct(b, a)} %" for s, (a, b) in
                                                 sorted(by.items(), key=lambda x: -x[1][0])))
        out[f]["by_source"] = {s: {"n": a, "right_pct": pct(b, a)} for s, (a, b) in by.items()}
    bl = [it for it in st.get("blind", {}).get("items", []) if it.get("answer") and it["answer"] != "unsure"]
    if bl:
        print("  blind re-check (you named the main class before seeing Fetch's):")
        out["blind"] = {}
        for part in ("audio", "control"):
            v = [it for it in bl if it["part"] == part]
            ok = sum((it["fetch"] or "").split("/")[0] == it["answer"] for it in v)
            lo, hi = wilson(ok, len(v))
            label = "audio guesses" if part == "audio" else "name-based (control)"
            print(f"    {label:22s} {ok}/{len(v)} = {pct(ok, len(v))} %  (95 % range {lo}–{hi})")
            out["blind"][part] = {"n": len(v), "right_pct": pct(ok, len(v))}
        # audio guesses split: the name gave nothing (a pure audio guess) / the name gave a class the audio refined
        pure = [it for it in bl if it["part"] == "audio" and not so.from_name(it["path"], root_of(it["path"]))[0]]
        ok = sum((it["fetch"] or "").split("/")[0] == it["answer"] for it in pure)
        print(f"      of which pure audio guesses (nothing in the name) {ok}/{len(pure)} = {pct(ok, len(pure))} %")
        out["blind"]["pure_audio"] = {"n": len(pure), "right_pct": pct(ok, len(pure))}
        # the same samples in the open round: how many were marked right there but differ blind
        open_ok = {it["path"] for it in items if it["verdict"]["inst"] in ("ok", "sub")}
        flip = [it for it in bl if it["path"] in open_ok and (it["fetch"] or "").split("/")[0] != it["answer"]]
        print(f"    marked right when shown, but you named another class blind: {len(flip)}")
        out["blind"]["flipped"] = len(flip)
        for it in flip:
            EX["blind"].append(f"Fetch {it['fetch'] or '-':22s} you {it['answer']:12s} {Path(it['path']).name}")
    for it in items:
        bad = [f for f in ("inst", "type", "bpm", "key") if it["verdict"][f] in ("sub", "wrong")]
        if bad and len(EX["spot"]) < nex * 3:
            s = it["shown"]
            fix = it.get("fix", {})
            desc = ", ".join(f"{f} {s.get(f) or '-'}" + (f"->{fix[f]}" if f in fix else "")
                             + (f"->{it['right_bpm']:g}" if f == "bpm" and "right_bpm" in it else "")
                             + (f"->{it['right_key']}" if f == "key" and "right_key" in it else "")
                             + f" [{s.get(f + '_src')}]" for f in bad)
            EX["spot"].append(f"{desc}   {Path(it['path']).name}")
    R["spot"] = out


def compare(prev: Path | None) -> None:
    if not prev:
        return
    try:
        old = json.loads(prev.read_text())["results"]
    except (OSError, ValueError, KeyError):
        return
    head(f"Compared with {prev.name}")
    keys = [("inst_vs_fixes", "right_pct"), ("inst_audio_lopo", "answered_pct"),
            ("inst_audio_lopo", "main_right_pct"), ("inst_audio_lopo", "exact_right_pct"),
            ("type", "fixes_right_pct"), ("bpm", "right_pct"), ("bpm_new", "answered_pct"),
            ("bpm_new", "right_pct"), ("key", "right_pct"), ("key_new", "answered_pct"), ("key_new", "right_pct"),
            ("spot", "n")] + [("spot_" + f, "right_pct") for f in ("inst", "type", "bpm", "key")]
    old.update({"spot_" + f: old.get("spot", {}).get(f, {}) for f in ("inst", "type", "bpm", "key")})
    R.update({"spot_" + f: R.get("spot", {}).get(f, {}) for f in ("inst", "type", "bpm", "key")})
    for a, b in keys:
        o, n = old.get(a, {}).get(b), R.get(a, {}).get(b)
        if o is None or n is None:
            continue
        d = n - o
        mark = "\033[32m" if d > 0.05 else ("\033[31m" if d < -0.05 else "")
        print(f"  {a + '.' + b:34s} {o:6.1f} -> {n:6.1f}  {mark}{d:+.1f}\033[0m")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", type=int, default=8)
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    np.random.seed(0)
    t0 = time.time()
    idx = sh.Index(sh.db())
    fixes = sh.load_fixes()
    pos = {p: i for i, p in enumerate(idx.paths)}
    set_roots(idx.paths, idx.roots)
    root = None                                        # rel() uses each path's own library root
    print(f"{len(idx.paths)} samples, {len(fixes)} corrected  ("
          + ", ".join(f"{lb} {c}" for lb, c in Counter(library(p) for p in idx.paths).most_common()) + ")")
    inst, est, _ = so.classify(idx.paths, idx.A, fixed={}, roots=idx.roots)
    instrument_vs_fixes(idx, fixes, pos, inst, est, root, a.examples)
    audio_leave_pack_out(idx, fixes, pos, root, a.examples)
    # raw tempo as the type rules see it: the name, otherwise the audio estimate (before the loop-only rule)
    con = sh.db()
    ab = dict(con.execute("SELECT path, bpm FROM features"))
    raw_bpm = np.array([sh.name_bpm_key(p)[0] if np.isfinite(sh.name_bpm_key(p)[0])
                        else (ab.get(p) if ab.get(p) else np.nan) for p in idx.paths], dtype=float)
    why = sample_types(idx, fixes, pos, raw_bpm, root, a.examples)
    tempo_key(idx, why, root, a.examples)
    correction_log(root, a.examples)
    spot_check(a.examples)
    for sec, rows in EX.items():
        head(f"Examples: {sec}")
        for r in rows:
            print("  " + r)
    print(f"\n({time.time() - t0:.0f} s)")
    if not a.no_save:
        OUT.mkdir(exist_ok=True)
        prev = sorted(OUT.glob("tarkkuus-*.json"))
        f = OUT / f"tarkkuus-{datetime.now():%Y%m%d-%H%M%S}.json"
        f.write_text(json.dumps({"time": datetime.now().isoformat(timespec="seconds"),
                                 "results": R, "examples": EX}, ensure_ascii=False, indent=1))
        compare(prev[-1] if prev else None)
        print(f"saved {f.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
