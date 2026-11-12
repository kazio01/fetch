"""Would another audio model recognise instruments better than the CLAP vectors Fetch uses now?
Embeds a sample of labelled files with candidate models and runs the same held-out pack series test
(testit/soitin_kehitys.py). Read-only for the library; embeddings cached in ~/.cache/samplehaku/emb-*.npz.

    .venv/bin/python -I testit/upotus_kokeilu.py embed clap_music|ast [--n 5000]
    .venv/bin/python -I testit/upotus_kokeilu.py eval
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

CACHE = Path.home() / ".cache/samplehaku"
MODELS = {"clap_music": "laion/larger_clap_music", "ast": "MIT/ast-finetuned-audioset-10-10-0.4593"}


def sample(n: int, seed: int = 3):
    """The same labelled samples for every model: up to n, spread evenly over the pack series."""
    import soitin_kehitys as skh
    idx, L, classes, y, ser, _ = skh.dataset()
    rng = random.Random(seed)
    by = defaultdict(list)
    for k, s in enumerate(ser):
        by[s].append(k)
    per = max(10, n // len(by))
    pick = []
    for s, v in sorted(by.items()):
        rng.shuffle(v)
        pick += v[:per]
    rng.shuffle(pick)
    pick = np.array(sorted(pick[:n]))
    return idx, L[pick], classes, y[pick], ser[pick]


def embed(which: str, n: int) -> None:
    import torch
    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 2))
    idx, rows, classes, y, ser = sample(n)
    paths = [idx.paths[i] for i in rows]
    out_f = CACHE / f"emb-{which}.npz"
    print(f"{len(paths)} files -> {which} ({MODELS[which]})")
    t0 = time.time()
    vecs, extra = [], []
    if which == "clap_music":
        import samplehaku as sh
        clap = sh.Clap(MODELS[which])
        for k, p in enumerate(paths):
            try:
                w, _ = sh.load_windows(Path(p))
                v = clap.audio(w).mean(axis=0) if w else np.zeros(512)
            except Exception:
                v = np.zeros(512)
            vecs.append(v / (np.linalg.norm(v) + 1e-9))
            if (k + 1) % 250 == 0:
                print(f"  {k + 1}/{len(paths)}  {time.time() - t0:.0f} s", flush=True)
    else:
        import librosa
        from transformers import ASTFeatureExtractor, ASTForAudioClassification
        fe = ASTFeatureExtractor.from_pretrained(MODELS[which])
        model = ASTForAudioClassification.from_pretrained(MODELS[which]).eval()
        for k, p in enumerate(paths):
            try:
                yv, _ = librosa.load(p, sr=16000, mono=True, duration=10.24)
                if yv.size < 1600:
                    yv = np.pad(yv, (0, 1600 - yv.size))
                inp = fe(yv, sampling_rate=16000, return_tensors="pt")
                with torch.inference_mode():
                    o = model(**inp, output_hidden_states=True)
                h = o.hidden_states[-1][0]
                v = h[:2].mean(0).numpy()                # CLS + distillation token (the pooled output)
                lg = torch.sigmoid(o.logits[0]).numpy()  # AudioSet class probabilities (527)
            except Exception:
                v, lg = np.zeros(768), np.zeros(527)
            vecs.append(v)
            extra.append(lg)
            if (k + 1) % 250 == 0:
                print(f"  {k + 1}/{len(paths)}  {time.time() - t0:.0f} s", flush=True)
    np.savez_compressed(out_f, rows=rows, E=np.array(vecs, dtype=np.float32),
                        X=np.array(extra, dtype=np.float32) if extra else np.zeros((0,)))
    print(f"saved {out_f} ({time.time() - t0:.0f} s)")


def evaluate() -> None:
    import soitin_kehitys as skh
    idx, L, classes, y_all, ser_all, Xe_all = skh.dataset()
    pos = {r: k for k, r in enumerate(L)}
    have = {w: np.load(CACHE / f"emb-{w}.npz") for w in MODELS if (CACHE / f"emb-{w}.npz").exists()}
    if not have:
        sys.exit("embed first")
    rows = sorted(set.intersection(*[set(d["rows"].tolist()) for d in have.values()]))
    k_of = np.array([pos[r] for r in rows])
    y, ser, Xe = y_all[k_of], ser_all[k_of], Xe_all[k_of]
    ncls = len(classes)
    fold = skh.folds_of(ser)
    print(f"{len(rows)} samples in common, {len(set(ser))} pack series, {len(set(y))} classes present")
    feats = {"CLAP general (now)": np.asarray(idx.A[rows], dtype=np.float32)}
    G = feats["CLAP general (now)"] - feats["CLAP general (now)"].mean(axis=0)
    feats["CLAP general, centred"] = (G / (np.linalg.norm(G, axis=1, keepdims=True) + 1e-9)).astype(np.float32)
    for w, d in have.items():
        m = {r: j for j, r in enumerate(d["rows"])}
        E = d["E"][[m[r] for r in rows]]
        E = E - E.mean(axis=0)            # centred: the raw vectors of a model can all point the same way
        feats[w] = (E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)).astype(np.float32)
        if w == "ast" and d["X"].size:
            X = d["X"][[m[r] for r in rows]]
            feats["ast AudioSet probabilities"] = ((X - X.mean(0)) / (X.std(0) + 1e-6) / np.sqrt(X.shape[1])).astype(np.float32)
    if "clap_music" in feats and "ast" in feats:
        feats["CLAP general + ast"] = np.concatenate([feats["CLAP general, centred"], feats["ast"]], axis=1) / np.sqrt(2)
        feats["CLAP music + ast"] = np.concatenate([feats["clap_music"], feats["ast"]], axis=1) / np.sqrt(2)
    for name, F in feats.items():
        Pk = np.zeros((len(y), ncls), dtype=np.float32)
        Pl = np.zeros((len(y), ncls), dtype=np.float32)
        for f in range(skh.FOLDS):
            tr, te = np.nonzero(fold != f)[0], np.nonzero(fold == f)[0]
            Pk[te] = skh.knn_probs(F, tr, te, y, ncls)
            X = np.concatenate([F * 8, Xe * 2], axis=1).astype(np.float32)
            Pl[te] = skh.predict(skh.train_torch(X[tr], y[tr], ncls), X[te])
        print(f"--- {name} ({F.shape[1]} dims)")
        skh.score("  neighbour vote", skh.decide(Pk, classes), y, classes)
        skh.score("  logreg (+ sound features)", skh.decide(Pl, classes), y, classes)
        mains = sorted({c.split("/")[0] for c in classes})
        M = np.zeros((ncls, len(mains)))
        for j, c in enumerate(classes):
            M[j, mains.index(c.split("/")[0])] = 1
        ym = np.array([mains.index(classes[i].split("/")[0]) for i in y])
        for lab, P in (("vote", Pk), ("logreg", Pl)):
            pm = P @ M
            print(f"  {lab:7s} main class right when every sample is answered: "
                  f"{100 * (pm.argmax(1) == ym).mean():.1f} %")


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("cmd", choices=("embed", "eval"))
    a.add_argument("model", nargs="?", choices=tuple(MODELS))
    a.add_argument("--n", type=int, default=5000)
    o = a.parse_args()
    if o.cmd == "embed":
        if not o.model:
            sys.exit("which model?")
        return embed(o.model, o.n)
    evaluate()


if __name__ == "__main__":
    main()
