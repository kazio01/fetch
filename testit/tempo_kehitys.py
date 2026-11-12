"""Tempo estimator development: a cached test set of loops with the tempo in the file name, and a fast
evaluation of aanipiirteet.estimate_bpm (or a candidate) on it. Read-only for the library and the database.

    .venv/bin/python -I testit/tempo_kehitys.py build [--n 3000]   # decode once (~5 min), ~/.cache/samplehaku/tempo-dev.npz
    .venv/bin/python -I testit/tempo_kehitys.py eval               # current estimator vs the names, by category

The cache keeps the spectrum per frame reduced to 16 log-spaced bands, as log1p(E / max · 1e4) (float16), so new onset features can be tried
without decoding again. Ground truth = the tempo in the name (the name wins in the app, so this is the test set).
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

import aanipiirteet as ap  # noqa: E402

CACHE = Path(os.environ.get("SAMPLEHAKU_TEMPO_DEV", Path.home() / ".cache/samplehaku/tempo-dev.npz"))
NB = 16
EDGES = np.unique(np.geomspace(1, ap.NBINS, NB + 1).astype(int))


def bands(P: np.ndarray) -> np.ndarray:
    """Power frames (n × 512) -> n × NB band energies."""
    return np.stack([P[:, a:b].sum(axis=1) for a, b in zip(EDGES[:-1], EDGES[1:])], axis=1)


def _one(path: str):
    try:
        y = ap.load_audio(path)
        f = ap.frames_of(y)
        if f is None:
            return None
        P = ap.CpuBackend().power(f)
        Bd = bands(P)
        Bd = np.log1p(Bd / (Bd.max() + 1e-30) * 1e4)   # as estimate_bpm does; safe in float16
        return Bd.astype(np.float32), y.size / ap.SR, ap.estimate_bpm(P, y.size / ap.SR, y.size / ap.SR < ap.MAX_S - 0.05)
    except Exception:
        return None


def build(n: int, seed: int) -> None:
    import samplehaku as sh
    from tarkkuus import series_all
    idx = sh.Index(sh.db())
    ser = series_all(idx.paths, idx.roots)
    cand = defaultdict(list)
    for i, p in enumerate(idx.paths):
        b = sh.name_bpm_key(p)[0]
        if np.isfinite(b) and idx.stype[i] == 1 and idx.dur[i] >= ap.BPM_MIN_S:
            cand[ser[i]].append((p, b))
    rng = random.Random(seed)
    per = max(20, n // len(cand))
    pick = []
    for s, v in cand.items():
        rng.shuffle(v)
        pick += v[:per]
    rng.shuffle(pick)
    pick = pick[:n]
    print(f"{len(pick)} loops from {len(cand)} pack series, decoding…")
    t0 = time.time()
    out = []
    with ProcessPoolExecutor(max(1, (os.cpu_count() or 2) - 2)) as ex:
        for k, ((p, b), r) in enumerate(zip(pick, ex.map(_one, [p for p, _ in pick], chunksize=8))):
            if r is not None:
                out.append((p, b, *r))
            if (k + 1) % 250 == 0:
                print(f"  {k + 1}/{len(pick)}  {time.time() - t0:.0f} s")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    lens = np.array([len(o[2]) for o in out])
    np.savez_compressed(CACHE, paths=np.array([o[0] for o in out]), truth=np.array([o[1] for o in out]),
                        dur=np.array([o[3] for o in out]), old=np.array([np.nan if o[4] is None else o[4] for o in out]),
                        lens=lens, B=np.concatenate([o[2] for o in out]).astype(np.float16))
    print(f"saved {len(out)} to {CACHE} ({time.time() - t0:.0f} s)")


def load():
    d = np.load(CACHE)
    offs = np.concatenate([[0], np.cumsum(d["lens"])])
    B = d["B"].astype(np.float32)
    return [dict(path=str(p), truth=float(t), dur=float(u), old=float(o), B=B[offs[i]:offs[i + 1]])
            for i, (p, t, u, o) in enumerate(zip(d["paths"], d["truth"], d["dur"], d["old"]))]


def category(est, truth) -> str:
    if est is None or not np.isfinite(est) or est <= 1:
        return "none"
    b = float(est)
    for _ in range(16):
        b = b / 2 if b >= 200 else (b * 2 if b < 60 else b)
    r = b / truth
    tol = 0.03
    if abs(r - 1) <= tol:
        return "right"
    if min(abs(r - 0.5), abs(r - 2)) <= 2 * tol:
        return "half/double"
    if min(abs(r - 1.5), abs(r - 2 / 3)) <= 2 * tol:
        return "x1.5 / x2/3"
    if min(abs(r - 4 / 3), abs(r - 3 / 4)) <= 2 * tol:
        return "x4/3 / x3/4"
    return "off a little" if abs(r - 1) <= 0.08 else "wrong"


def report(name: str, items, ests) -> dict:
    c = Counter(category(e, it["truth"]) for e, it in zip(ests, items))
    n = len(items)
    got = n - c["none"]
    print(f"{name:28s} answered {100 * got / n:5.1f} %  right {100 * c['right'] / max(got, 1):5.1f} % of answered  "
          f"({100 * c['right'] / n:5.1f} % of all)  " + ", ".join(f"{k} {v}" for k, v in c.most_common() if k not in ("right", "none")))
    return dict(c)


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("cmd", choices=("build", "eval"))
    a.add_argument("--n", type=int, default=3000)
    a.add_argument("--seed", type=int, default=7)
    o = a.parse_args()
    if o.cmd == "build":
        return build(o.n, o.seed)
    items = load()
    print(f"{len(items)} loops")
    report("old (free autocorrelation)", items, [it["old"] for it in items])
    import samplehaku as sh
    est = new_estimates(items)
    report("new (all answers)", items, [b for b, _ in est])
    for th in (0.6, 0.7, sh.TEMPO_MIN_CONF, 0.9):
        report(f"new, confidence ≥ {th}", items, [b if c >= th else None for b, c in est])


def ac_from_bands(L: np.ndarray) -> np.ndarray:
    """The same as aanipiirteet.onset_ac, from the cached log bands."""
    f = np.maximum(np.diff(L, axis=0), 0).sum(axis=1)
    f = np.convolve(f, [1, 2, 3, 2, 1], mode="same")
    f -= f.mean()
    n = f.size
    ac = np.correlate(f, f, mode="full")[n - 1:n - 1 + ap.ONSET_LAGS]
    return (ac / (ac[0] + 1e-30)).astype(np.float16)


def new_estimates(items) -> list[tuple[float | None, float]]:
    import samplehaku as sh
    idx = sh.Index(sh.db())
    pos = {p: i for i, p in enumerate(idx.paths)}
    pairs = [(pos.get(it["path"]), ac_from_bands(it["B"]), it["dur"]) for it in items]
    return honest_estimates(idx, pairs)


def honest_estimates(idx, pairs) -> list[tuple[float | None, float]]:
    """samplehaku.tempo_from_ac for [(index row, onset ac, length)], with the genre prior built WITHOUT the
    loop's own pack series (its own and its siblings' names would otherwise give the answer away)."""
    import samplehaku as sh
    import genret
    from tarkkuus import series_all
    gs = idx.genres()
    ser = series_all(idx.paths, idx.roots)
    named = []
    for i, p in enumerate(idx.paths):
        b = sh.name_bpm_key(p)[0]
        if np.isfinite(b) and sh.sample_type(p, float(idx.dur[i]), b)[0] == 1:
            named.append((i, b))
    priors = {}
    out = []
    for i, ac, dur in pairs:
        if i is None or ac is None:
            out.append((None, 0.0))
            continue
        key = (ser[i], tuple(sorted(g for g in gs[i] if g in genret.GROUP_OF)))
        if key not in priors:
            vals = [b for j, b in named if ser[j] != ser[i] and gs[j] & set(key[1])]
            if len(vals) < 50:
                vals = [b for j, b in named if ser[j] != ser[i]]
            priors[key] = sh.tempo_prior(vals)
        out.append(sh.tempo_from_ac(ac, dur, priors[key]))
    return out


if __name__ == "__main__":
    main()
