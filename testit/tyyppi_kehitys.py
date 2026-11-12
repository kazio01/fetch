"""Loop / one-shot from the sound, for the samples whose name and folder don't say (samplehaku.sample_type_why
"default": they all count as loops). Learned from the samples the name DOES settle (name / drum hit / fx word),
5-fold cross-validated by pack series. Read-only for the library; `train` writes only tyyppi_malli.npz.

    .venv/bin/python -I testit/tyyppi_kehitys.py build    # from the index -> ~/.cache/samplehaku/type-dev.npz
    .venv/bin/python -I testit/tyyppi_kehitys.py eval     # thresholds: answered / right, the user's type corrections
    .venv/bin/python -I testit/tyyppi_kehitys.py show [--th 0.9] [--n 40]   # what the default bucket would become
    .venv/bin/python -I testit/tyyppi_kehitys.py audit    # named loops the model is sure are one-shots (and back)
    .venv/bin/python -I testit/tyyppi_kehitys.py train    # all labelled samples -> tyyppi_malli.npz

The model (samplehaku.type_features / type_probs): logistic regression on the CLAP audio vector + log length and
the sound features (tail, attack, flat, centroid, low).
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import zlib
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

import samplehaku as sh  # noqa: E402

CACHE = Path(os.environ.get("SAMPLEHAKU_TYPE_DEV", Path.home() / ".cache/samplehaku/type-dev.npz"))
FOLDS = 5
LABELLED = ("name", "drum hit", "fx word")


def build() -> None:
    from tarkkuus import series_all
    idx = sh.Index(sh.db())
    inst = idx.instruments()[0]
    n = len(idx.paths)
    why = [sh.sample_type_why(p, float(idx.dur[i]), idx.bpm[i]) for i, p in enumerate(idx.paths)]
    keep = np.array([i for i in range(n) if idx.dur[i] >= 1.5 and (why[i][1] in LABELLED or why[i][1] == "default")])
    X = sh.type_features(np.asarray(idx.A)[keep], idx.dur[keep], {k: v[keep] for k, v in idx.F.items()})
    fixes = idx.fixes
    fix = np.array([sh.TYPE_IDS.get(fixes.get(idx.paths[i], {}).get("type"), -1) for i in keep], dtype=np.int8)
    np.savez_compressed(CACHE, paths=np.array([idx.paths[i] for i in keep]), X=X.astype(np.float32),
                        y=np.array([why[i][0] for i in keep], dtype=np.int8),
                        rule=np.array([why[i][1] for i in keep]), fix=fix,
                        inst=np.array([str(inst[i] or "") for i in keep]), dur=idx.dur[keep],
                        ser=np.array(series_all([idx.paths[i] for i in keep], idx.roots)))
    print(f"{len(keep)} samples ≥ 1.5 s ({Counter(why[i][1] for i in keep)}) -> {CACHE}")


def load() -> dict:
    if not CACHE.exists():
        build()
    d = np.load(CACHE)
    return {k: d[k] for k in d.files}


def train(X: np.ndarray, y: np.ndarray, C: float = 0.5) -> dict:
    """-> {mu, sd, w, b}: P(one-shot) = sigmoid(((x - mu) / sd) · w + b). Classes balanced."""
    from sklearn.linear_model import LogisticRegression
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-6
    m = LogisticRegression(C=C, class_weight="balanced", max_iter=2000)
    m.fit((X - mu) / sd, (y == 0).astype(int))
    return {"mu": mu.astype(np.float32), "sd": sd.astype(np.float32),
            "w": m.coef_[0].astype(np.float32), "b": np.float32(m.intercept_[0])}


def honest(d: dict) -> np.ndarray:
    """P(one-shot) for every sample from models trained without its pack series (labelled samples only train)."""
    lab = np.isin(d["rule"], LABELLED)
    f = np.array([zlib.crc32(s.encode()) % FOLDS for s in d["ser"]])
    P = np.zeros(len(f), dtype=np.float32)
    for k in range(FOLDS):
        tr, te = lab & (f != k), f == k
        P[te] = sh.type_probs(d["X"][te], train(d["X"][tr], d["y"][tr]))
    return P


def table(d: dict, P: np.ndarray) -> None:
    lab = np.isin(d["rule"], LABELLED)
    y = d["y"]
    print(f"{lab.sum()} labelled samples ≥ 1.5 s (loops {np.sum(lab & (y == 1))}, one-shots {np.sum(lab & (y == 0))}), "
          f"default bucket {np.sum(d['rule'] == 'default')}")
    print("threshold = P(one-shot) needed to call a default-bucket sample a one-shot (else it stays a loop)")
    print(f"{'th':>5} | {'one-shots found':>15} {'of them right':>13} | {'loops wrongly':>13} | default -> one-shot")
    dflt = d["rule"] == "default"
    for th in (0.5, 0.7, 0.8, 0.9, 0.95, 0.98):
        call = P >= th
        found = np.mean(call[lab & (y == 0)])                     # recall on true one-shots
        prec = np.mean(y[lab & call] == 0) if np.any(lab & call) else 0
        wrong = np.mean(call[lab & (y == 1)])                     # loops that would become one-shots
        print(f"{th:5.2f} | {100 * found:14.1f}% {100 * prec:12.1f}% | {100 * wrong:12.2f}% | "
              f"{np.sum(call & dflt):6d} of {dflt.sum()}")
    fx = d["fix"] >= 0
    if fx.any():
        print(f"\nthe user's type corrections ({fx.sum()} ≥ 1.5 s: loop {np.sum(d['fix'] == 1)}, one-shot {np.sum(d['fix'] == 0)}):")
        for th in (0.8, 0.9, 0.95):
            call = P >= th
            print(f"  th {th}: corrected loops called one-shot {np.sum(call & (d['fix'] == 1))}, "
                  f"corrected one-shots found {np.sum(call & (d['fix'] == 0))}/{np.sum(d['fix'] == 0)}")


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("cmd", choices=("build", "eval", "show", "audit", "train"))
    a.add_argument("--th", type=float, default=0.9)
    a.add_argument("--n", type=int, default=40)
    o = a.parse_args()
    if o.cmd == "build":
        return build()
    d = load()
    if o.cmd == "train":
        lab = np.isin(d["rule"], LABELLED)
        w = train(d["X"][lab], d["y"][lab])
        np.savez(sh.TYPE_MODEL, **w)
        P = sh.type_probs(d["X"][lab], w)
        print(f"saved {sh.TYPE_MODEL} (training set: right {100 * np.mean((P >= 0.5) == (d['y'][lab] == 0)):.1f} %)")
        return
    P = honest(d)
    if o.cmd == "eval":
        return table(d, P)
    rng = random.Random(1)
    if o.cmd == "show":
        sel = np.nonzero((d["rule"] == "default") & (P >= o.th))[0]
        print(f"default bucket -> one-shot at P ≥ {o.th}: {len(sel)}")
        print("by instrument:", Counter(str(i).split("/")[0] or "-" for i in d["inst"][sel]).most_common(12))
        stay = np.nonzero((d["rule"] == "default") & (P < o.th))[0]
        for name, ids in (("-> one-shot", sel), ("stays loop", stay)):
            print(f"\n{name}:")
            for i in rng.sample(list(ids), min(o.n, len(ids))):
                print(f"  {P[i]:.2f} {d['dur'][i]:6.1f}s {str(d['inst'][i])[:16]:16s} {str(d['paths'][i])[-95:]}")
        return
    lab = np.isin(d["rule"], LABELLED)
    for name, sel in (("named loop, model ≥ 0.95 one-shot", lab & (d["y"] == 1) & (P >= 0.95)),
                      ("named one-shot, model ≤ 0.05", lab & (d["y"] == 0) & (P <= 0.05))):
        ids = np.nonzero(sel)[0]
        print(f"\n{name}: {len(ids)}")
        for i in rng.sample(list(ids), min(o.n, len(ids))):
            print(f"  {P[i]:.2f} {d['rule'][i]:8s} {d['dur'][i]:6.1f}s {str(d['paths'][i])[-100:]}")


if __name__ == "__main__":
    main()
