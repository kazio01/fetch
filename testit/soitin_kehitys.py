"""Instrument-from-audio development: compares the app's neighbour vote (soittimet.knn_votes + vote_pick)
with trained classifiers on the same CLAP audio vectors (+ the measured sound features). Read-only.

    .venv/bin/python -I testit/soitin_kehitys.py            # 8 folds, whole pack series held out

Labels = instrument from the name (+ the user's corrections), as in tarkkuus.py section 2. A model never sees
the pack series it is tested on, so the result is what a sample without an instrument word in its name gets.
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

import samplehaku as sh  # noqa: E402
import soittimet as so  # noqa: E402
from tarkkuus import series_all  # noqa: E402

FOLDS = 8


def dataset():
    idx = sh.Index(sh.db())
    fixes = sh.load_fixes()
    paths = idx.paths
    pos = {p: i for i, p in enumerate(paths)}
    import polut
    proots = polut.roots_per_path(paths, idx.roots)
    inst = np.array([so.from_name(p, r)[0] or "" for p, r in zip(paths, proots)], dtype=object)
    for i, node in so.stem_families(paths).items():
        if not (inst[i] == node or inst[i].startswith(node + "/")):
            inst[i] = node
    for p, f in fixes.items():
        if p in pos and so.migrate(f.get("inst", "")) in so.LABEL:
            inst[pos[p]] = so.migrate(f["inst"])
    L = np.array([i for i in range(len(paths)) if inst[i] and inst[i] not in so.PARTIAL])
    classes = sorted(set(inst[L]))
    y = np.array([classes.index(c) for c in inst[L]])
    ser_all = series_all(paths, idx.roots)
    ser = np.array([ser_all[i] for i in L])
    # sound features: rank-normalised (robust), log length, loop/one-shot
    extra = []
    for k in sh.FEATURES:
        v = np.asarray(idx.F[k], dtype=float)[L]
        ok = np.isfinite(v)
        r = np.full(v.shape, 0.5)
        r[ok] = v[ok].argsort().argsort() / max(ok.sum() - 1, 1)
        extra.append(r)
    extra.append(np.log1p(np.asarray(idx.dur, dtype=float)[L]) / 4)
    extra.append(idx.stype[L].astype(float))
    X_extra = np.stack(extra, axis=1).astype(np.float32)
    return idx, L, classes, y, ser, X_extra


def folds_of(ser: np.ndarray) -> np.ndarray:
    """Series -> fold, balancing sizes (largest series first)."""
    cnt = Counter(ser)
    size = np.zeros(FOLDS)
    f_of = {}
    for s, n in cnt.most_common():
        j = int(np.argmin(size))
        f_of[s] = j
        size[j] += n
    return np.array([f_of[s] for s in ser])


def decide(prob: np.ndarray, classes: list[str], leaf=so.VOTE_LEAF, main=so.VOTE_MAIN) -> list[str]:
    """Like soittimet.vote_pick, from probabilities."""
    mains = sorted({c.split("/")[0] for c in classes})
    M = np.zeros((len(classes), len(mains)))
    for j, c in enumerate(classes):
        M[j, mains.index(c.split("/")[0])] = 1
    pm = prob @ M
    out = []
    for r in range(prob.shape[0]):
        j = int(np.argmax(prob[r]))
        if prob[r, j] >= leaf:
            out.append(classes[j])
        else:
            m = int(np.argmax(pm[r]))
            out.append(mains[m] if pm[r, m] >= main else "")
    return out


def score(name, pred, y, classes) -> dict:
    n = len(y)
    ans = [i for i, p in enumerate(pred) if p]
    main_ok = sum(pred[i].split("/")[0] == classes[y[i]].split("/")[0] for i in ans)
    exact = sum(pred[i] == classes[y[i]] for i in ans)
    print(f"{name:34s} answered {100 * len(ans) / n:5.1f} %  main right {100 * main_ok / max(len(ans), 1):5.1f} %  "
          f"exact right {100 * exact / max(len(ans), 1):5.1f} %")
    return dict(answered=len(ans) / n, main=main_ok / max(len(ans), 1), exact=exact / max(len(ans), 1))


def knn_probs(A, tr, te, y, ncls, k=15):
    freq = np.bincount(y[tr], minlength=ncls).astype(np.float32)
    w = 1 / np.sqrt(np.maximum(freq, 1))
    out = np.zeros((len(te), ncls), dtype=np.float32)
    AL = A[tr]
    for s in range(0, len(te), 2000):
        q = te[s:s + 2000]
        out[s:s + len(q)] = so.knn_votes(A[q], AL, y[tr], w, ncls, k)
    return out / np.maximum(out.sum(axis=1, keepdims=True), 1e-12)


def train_torch(X, y, ncls, hidden=0, epochs=60, wd=1e-4, lr=3e-3, seed=0):
    import torch
    torch.manual_seed(seed)
    Xt = torch.tensor(X)
    yt = torch.tensor(y)
    freq = np.bincount(y, minlength=ncls).astype(np.float32)
    cw = torch.tensor(1 / np.sqrt(np.maximum(freq, 1)))
    cw = cw / cw[yt].mean()
    if hidden:
        model = torch.nn.Sequential(torch.nn.Linear(X.shape[1], hidden), torch.nn.GELU(), torch.nn.Dropout(0.2),
                                    torch.nn.Linear(hidden, ncls))
    else:
        model = torch.nn.Linear(X.shape[1], ncls)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)
    lossf = torch.nn.CrossEntropyLoss(weight=cw)
    n = len(y)
    for ep in range(epochs):
        model.train()
        perm = torch.randperm(n)
        for s in range(0, n, 1024):
            b = perm[s:s + 1024]
            opt.zero_grad()
            loss = lossf(model(Xt[b]), yt[b])
            loss.backward()
            opt.step()
    model.eval()
    return model


def predict(model, X):
    import torch
    with torch.no_grad():
        return torch.softmax(model(torch.tensor(X)), dim=1).numpy()


def main() -> None:
    import torch
    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 2))
    t0 = time.time()
    idx, L, classes, y, ser, Xe = dataset()
    A = np.asarray(idx.A[L], dtype=np.float32)
    fold = folds_of(ser)
    ncls = len(classes)
    print(f"{len(L)} labelled samples, {ncls} classes, {len(set(ser))} pack series, {FOLDS} folds "
          f"({time.time() - t0:.0f} s)")
    X1 = A * 8                                   # unit vectors -> a scale linear layers like
    X2 = np.concatenate([X1, Xe * 2], axis=1)
    P = defaultdict(lambda: np.zeros((len(y), ncls), dtype=np.float32))
    for f in range(FOLDS):
        tr, te = np.nonzero(fold != f)[0], np.nonzero(fold == f)[0]
        P["knn (now)"][te] = knn_probs(A, tr, te, y, ncls)
        for name, X, h in (("logreg CLAP", X1, 0), ("logreg CLAP+features", X2, 0),
                           ("MLP CLAP+features", X2, 512)):
            m = train_torch(X[tr], y[tr], ncls, hidden=h)
            P[name][te] = predict(m, X[te])
        print(f"  fold {f + 1}/{FOLDS}  {time.time() - t0:.0f} s")
    for name, pr in P.items():
        score(name, decide(pr, classes), y, classes)
    np.savez_compressed(Path.home() / ".cache/samplehaku/inst-dev-probs.npz", y=y, classes=np.array(classes),
                        **{k.replace(" ", "_").replace("(", "").replace(")", "").replace("+", "_"): v
                           for k, v in P.items()})


if __name__ == "__main__":
    main()
