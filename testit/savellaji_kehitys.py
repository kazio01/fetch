"""Key estimator development: the files with a key in the name (and pitch salience in the database), a
cross-validated evaluation of the key model, and training of the shipped weights. Read-only for the library
and the database; `train` writes only savellaji_malli.npz next to samplehaku.py.

    .venv/bin/python -I testit/savellaji_kehitys.py build    # from index.db, ~10 s -> ~/.cache/samplehaku/key-dev.npz
    .venv/bin/python -I testit/savellaji_kehitys.py eval     # 5-fold by pack series: shown / right per threshold
    .venv/bin/python -I testit/savellaji_kehitys.py train    # all named files -> savellaji_malli.npz

The model (samplehaku.key_features / key_probs): the salience folded into 36 bins per octave in 5 registers
(log shares), read by one small network (180 -> 64 -> major, minor) for each of the 12 rotations; softmax
over the 24 keys. The salience is filled by `aanipiirteet.py --onset` (and by the feature run).
"""
from __future__ import annotations

import argparse
import os
import sys
import zlib
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

import samplehaku as sh  # noqa: E402

CACHE = Path(os.environ.get("SAMPLEHAKU_KEY_DEV", Path.home() / ".cache/samplehaku/key-dev.npz"))
HIDDEN, EPOCHS, FOLDS = 64, 30, 5
BALANCE = 0.0


def build() -> None:
    from tarkkuus import series_all
    con = sh.db()
    sal = {p: s for p, s in con.execute("SELECT path, key_sal FROM features WHERE length(key_sal) > 0")}
    paths, keys = [], []
    for p in sal:
        k = sh.name_bpm_key(p)[1]
        if k and k[1] >= 0:
            paths.append(p)
            keys.append(k)
    roots = sh.get_roots(con)
    np.savez_compressed(CACHE, paths=np.array(paths), key=np.array(keys, dtype=np.int8),
                        S=np.stack([np.frombuffer(sal[p], dtype=np.float16) for p in paths]),
                        ser=np.array(series_all(paths, roots)))
    print(f"{len(paths)} files with a key (major/minor) in the name and salience -> {CACHE}")


def load():
    if not CACHE.exists() or "ser" not in np.load(CACHE).files:     # missing or the old format
        build()
    d = np.load(CACHE)
    y = d["key"][:, 0].astype(int) + 12 * d["key"][:, 1].astype(int)
    return [str(p) for p in d["paths"]], d["S"], y, [str(s) for s in d["ser"]]


def train(S: np.ndarray, y: np.ndarray, seed: int = 0, balance: float | None = None) -> dict:
    """-> weights for samplehaku.key_probs. balance: the loss weight of major vs minor files is
    (minor count / major count) ** balance (0 = none, 1 = both modes equal); None = BALANCE."""
    import torch
    from torch import nn
    torch.manual_seed(seed)
    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 2))
    X = torch.tensor(sh.key_features(S))
    yt = torch.tensor(y)
    net = nn.Sequential(nn.Linear(X.shape[1] * X.shape[2], HIDDEN), nn.ReLU(), nn.Dropout(0.2), nn.Linear(HIDDEN, 2))

    def logits(xb):
        o = torch.stack([net(torch.roll(xb, -3 * r, dims=2).flatten(1)) for r in range(12)], 1)   # (n, 12, 2)
        return o.transpose(1, 2).reshape(len(xb), 24)                                             # pc + 12·mode

    bal = BALANCE if balance is None else balance
    n_min, n_maj = max(int(np.sum(y >= 12)), 1), max(int(np.sum(y < 12)), 1)
    cw = torch.tensor([(n_min / n_maj) ** bal] * 12 + [1.0] * 12, dtype=torch.float32)
    opt = torch.optim.AdamW(net.parameters(), 3e-3, weight_decay=1e-4)
    for _ in range(EPOCHS):
        net.train()
        perm = torch.randperm(len(X))
        for i in range(0, len(X), 256):
            b = perm[i:i + 256]
            opt.zero_grad()
            nn.functional.cross_entropy(logits(X[b]), yt[b], weight=cw).backward()
            opt.step()
    w = {"W1": net[0].weight, "b1": net[0].bias, "W2": net[3].weight, "b2": net[3].bias}
    return {k: v.detach().numpy().astype(np.float32) for k, v in w.items()}


def folds(ser: list[str]) -> np.ndarray:
    return np.array([zlib.crc32(s.encode()) % FOLDS for s in ser])


def honest_probs(S: np.ndarray, y: np.ndarray, ser: list[str], **kw) -> np.ndarray:
    """(n, 24) probabilities from models trained WITHOUT the file's own pack series (5 folds)."""
    f = folds(ser)
    P = np.zeros((len(y), 24), dtype=np.float32)
    for k in range(FOLDS):
        tr, te = f != k, f == k
        if te.any() and tr.any():
            P[te] = sh.key_probs(sh.key_features(S[te]), train(S[tr], y[tr], **kw))
    return P


def honest_keys(items, sers=None) -> list[tuple[tuple[int, int] | None, float]]:
    """For tarkkuus.py: items with "key" (pc, mode) and "S" -> [((pc, mode), probability)] cross-validated.
    Files without a mode in the name are estimated by the models but not trained on."""
    from tarkkuus import series_all
    ser = sers or series_all([it["path"] for it in items])
    tr = [i for i, it in enumerate(items) if it["key"][1] >= 0]
    S = np.stack([items[i]["S"] for i in range(len(items))]).astype(np.float32)
    y = np.array([items[i]["key"][0] + 12 * max(items[i]["key"][1], 0) for i in range(len(items))])
    f = folds(ser)
    P = np.zeros((len(items), 24), dtype=np.float32)
    trm = np.zeros(len(items), dtype=bool)
    trm[tr] = True
    for k in range(FOLDS):
        te = f == k
        if te.any():
            P[te] = sh.key_probs(sh.key_features(S[te]), train(S[trm & (f != k)], y[trm & (f != k)]))
    b = P.argmax(axis=1)
    return [((int(k) % 12, int(k) // 12), float(P[i, k])) for i, k in enumerate(b)]


def category(est, truth) -> str:
    """est, truth = (pc, mode) with mode 0 major, 1 minor, -1 unknown (only the tonic is compared)."""
    if est is None:
        return "none"
    (tp, tm), (ep, em) = truth, est
    if tm == -1 or em == -1:
        return "right" if tp == ep else "wrong"
    if (tp, tm) == (ep, em):
        return "right"
    if (ep, em) in sh.compatible_keys(tp, tm):
        return "relative" if em != tm else "fifth"
    if tp == ep:
        return "parallel"
    return "wrong"


def report(name: str, truth, ests) -> dict:
    c = Counter(category(e, t) for e, t in zip(ests, truth))
    n = len(truth)
    got = n - c["none"]
    print(f"{name:26s} shown {100 * got / n:5.1f} %  right {100 * c['right'] / max(got, 1):5.1f} %  "
          f"(+fifth/relative {100 * (c['fifth'] + c['relative']) / max(got, 1):4.1f} %)  "
          + ", ".join(f"{k} {v}" for k, v in c.most_common() if k not in ("right", "none")))
    return dict(c)


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("cmd", choices=("build", "eval", "train"))
    o = a.parse_args()
    if o.cmd == "build":
        return build()
    paths, S, y, ser = load()
    print(f"{len(y)} files, {len(set(ser))} pack series")
    if o.cmd == "train":
        w = train(S, y)
        np.savez(sh.KEY_MODEL, **w)
        P = sh.key_probs(sh.key_features(S), w)
        print(f"saved {sh.KEY_MODEL} (training set: right {100 * np.mean(P.argmax(1) == y):.1f} %)")
        return
    P = honest_probs(S, y, ser)
    truth = [(int(k) % 12, int(k) // 12) for k in y]
    best = P.argmax(axis=1)
    conf = P[np.arange(len(y)), best]
    est = [(int(k) % 12, int(k) // 12) for k in best]
    for th in (0.0, 0.5, sh.KEY_MIN_P, 0.7, 0.8):
        report(f"p ≥ {th}", truth, [e if c >= th else None for e, c in zip(est, conf)])


if __name__ == "__main__":
    main()
