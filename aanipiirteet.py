#!/usr/bin/env python3
"""
aanipiirteet – Fetch's sound features, computed on the Ryzen AI NPU.

A spectrum is computed for every file in the Fetch index. The frequency transform
(DFT) is written as a matrix multiplication:

    frames (M x 1024)  @  DFT matrix (1024 x 1024)  =  spectrum (M x 1024)

Matrix multiplication is exactly what the NPU's AI Engine cores do well.
The NPU run uses AMD's open IRON toolkit and its ready-made whole_array
matrix multiplication (bf16 in, f32 out, all 16 cores). The window function is
baked into the DFT matrix, so the NPU does the whole transform in one call.

From the spectrum, the CPU computes (lightweight):
    centroid  spectral centre, Hz              -> bright / dark
    low       share of energy below 150 Hz     -> bass
    flat      spectral flatness 0..1           -> noisy / tonal
    attack    time from the start to the peak, s -> punchy / soft
    tail      time from the peak to -40 dB, s  -> tight / long tail
    bpm       tempo: autocorrelation of the onset curve from the NPU spectrum, for loops
              refined to the file length (4, 8, 16 … beats)         -> bpm:120
    key       key: chromagram (CPU, 8192-point FFT, because 1024 points can't
              separate low notes) + Albrecht–Shanahan profiles        -> key:Am
              Left empty for noisy and uncertain files.

Commands:
    aanipiirteet.py                    compute missing ones (backend auto: NPU if available)
    aanipiirteet.py --backend cpu      force the CPU
    aanipiirteet.py --selftest         compare NPU and CPU, measure speed
    aanipiirteet.py --show file.wav    show one file's features

Dependencies: numpy and ffmpeg. For the NPU also mlir_aie (IRON), XRT and pyxrt.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

SR = 22_050
NFFT = 1024
HOP = 512
NBINS = NFFT // 2               # frequency bands 0 .. SR/2 (Nyquist left out)
MAX_S = 30.0                    # only the beginning of long files is read
FLOOR_DB = -40.0                # limit of an "active" frame below the peak
LOW_HZ = 150.0
FILES_PER_GROUP = 32            # how many files are gathered into one NPU run

DATA_DIR = Path(os.environ.get("SAMPLEHAKU_DIR", Path.home() / ".local/share/samplehaku"))
DB_PATH = DATA_DIR / "index.db"
MLIR_AIE_DIR = Path(os.environ.get("MLIR_AIE_DIR", DATA_DIR / "mlir-aie"))

FEATURES = ("centroid", "low", "flat", "attack", "tail")

SCHEMA = """CREATE TABLE IF NOT EXISTS features(
    path TEXT PRIMARY KEY, mtime REAL, size INTEGER,
    centroid REAL, low REAL, flat REAL, attack REAL, tail REAL, backend TEXT)"""
EXTRA_COLS = (("bpm", "REAL"), ("key", "TEXT"), ("ver", "INTEGER"), ("onset_ac", "BLOB"), ("key_sal", "BLOB"))
VERSION = 2                     # 2 = with tempo and key; older rows are recomputed

FREQS = np.arange(NBINS) * SR / NFFT


# ---------------------------------------------------------------- DFT as a matrix

def dft_matrix() -> np.ndarray:
    """(1024 x 1024): columns 0..511 = Hann * cos, 512..1023 = -Hann * sin."""
    n = np.arange(NFFT)[:, None]
    k = np.arange(NBINS)[None, :]
    ang = 2.0 * np.pi * n * k / NFFT
    win = np.hanning(NFFT + 1)[:-1, None]          # periodic Hann
    return np.concatenate([win * np.cos(ang), -win * np.sin(ang)], axis=1).astype(np.float32)


def power_from_gemm(c: np.ndarray) -> np.ndarray:
    re, im = c[:, :NBINS], c[:, NBINS:]
    return re * re + im * im


# ---------------------------------------------------------------- compute backends

class CpuBackend:
    """The same matrix multiplication on the CPU (float32). Serves as the reference."""
    name = "cpu"

    def __init__(self):
        self.B = dft_matrix()

    def power(self, frames: np.ndarray) -> np.ndarray:
        return power_from_gemm(frames @ self.B)


class Bf16EmuBackend(CpuBackend):
    """On the CPU, but with bf16 rounding like the NPU. For testing."""
    name = "bf16-emu"

    def __init__(self):
        from ml_dtypes import bfloat16
        super().__init__()
        self.bf16 = bfloat16
        self.Bq = self.B.astype(bfloat16).astype(np.float32)

    def power(self, frames: np.ndarray) -> np.ndarray:
        a = frames.astype(self.bf16).astype(np.float32)
        return power_from_gemm(a @ self.Bq)


class NpuBackend:
    """
    DFT on the NPU: mlir-aie's whole_array matrix multiplication, npu1 (Phoenix/Hawk Point),
    4 columns x 4 rows = 16 AI Engine cores. It is compiled on the first run
    (@iron.jit) and cached; later it is only executed.

    The interface matches mlir_aie 1.3.4 (tested: AMD's example at the same
    size PASS, ~2.6 ms / 2048 frames). The source code must be the same version
    (git checkout v<version> in MLIR_AIE_DIR), otherwise whole_array.py won't load.
    """
    name = "npu"
    M = int(os.environ.get("AANIPIIRTEET_NPU_M", 2048))   # frames per NPU call
    # tile sizes: AMD's example defaults for bf16 (m = k = 64, n = 32)
    TM = int(os.environ.get("AANIPIIRTEET_NPU_TM", 64))
    TK = int(os.environ.get("AANIPIIRTEET_NPU_TK", 64))
    TN = int(os.environ.get("AANIPIIRTEET_NPU_TN", 32))

    def __init__(self):
        ex = MLIR_AIE_DIR / "programming_examples/basic/matrix_multiplication/whole_array"
        if not (ex / "whole_array.py").is_file():
            raise RuntimeError(f"mlir-aie source code not found: {ex} (set MLIR_AIE_DIR)")
        sys.path.insert(0, str(ex))
        import aie.iron as iron                                   # noqa: E402
        from aie.iron.device import NPU2, from_name               # noqa: E402
        from aie.utils.hostruntime import set_current_device      # noqa: E402
        from ml_dtypes import bfloat16                            # noqa: E402
        from whole_array import whole_array                       # noqa: E402

        dev = iron.get_current_device()   # detects the machine's NPU through XRT
        if dev is None:
            raise RuntimeError("XRT can't find the NPU")
        self.cols = 8 if isinstance(dev, NPU2) else 4
        set_current_device(from_name("npu2" if self.cols == 8 else "npu",
                                     n_cols=None if self.cols == 8 else 4))

        M, K, N = self.M, NFFT, NFFT
        if M % (self.TM * 4) or N % (self.TN * self.cols) or K % self.TK:
            raise ValueError(f"M={M} doesn't fit the tile size {self.TM}x{self.TK}x{self.TN}")
        self.kernel = whole_array
        self.kw = dict(M=M, K=K, N=N, m=self.TM, k=self.TK, n=self.TN, n_aie_cols=self.cols,
                       dtype_in_str="bf16", dtype_out_str="f32")
        self.bf16 = bfloat16
        self.A_t = iron.zeros((M, K), dtype=bfloat16, device="npu")
        self.B_t = iron.tensor(dft_matrix().astype(bfloat16), dtype=bfloat16, device="npu")
        self.C_t = iron.zeros((M, N), dtype=np.float32, device="npu")
        self.buf = np.zeros((M, K), dtype=bfloat16)
        self.calls = 0
        self.npu_s = 0.0

    def power(self, frames: np.ndarray) -> np.ndarray:
        out = np.empty((frames.shape[0], NBINS), dtype=np.float32)
        for s in range(0, frames.shape[0], self.M):
            blk = frames[s:s + self.M]
            self.buf[:blk.shape[0]] = blk.astype(self.bf16)
            self.buf[blk.shape[0]:] = 0
            t0 = time.perf_counter()
            self.A_t[:] = self.buf                 # moves the data into the NPU's buffer
            self.kernel(self.A_t, self.B_t, self.C_t, **self.kw)
            c = self.C_t.numpy()                   # and the result back
            self.npu_s += time.perf_counter() - t0
            self.calls += 1
            out[s:s + blk.shape[0]] = power_from_gemm(np.asarray(c).reshape(self.M, NFFT)[:blk.shape[0]])
        return out


def make_backend(name: str = "auto"):
    if name == "cpu":
        return CpuBackend()
    if name == "bf16-emu":
        return Bf16EmuBackend()
    try:
        return NpuBackend()
    except Exception as e:
        if name == "npu":
            raise
        print(f"(NPU not available: {e.__class__.__name__}: {e}\n → computing on the CPU)",
              file=sys.stderr)
        return CpuBackend()


# ---------------------------------------------------------------- audio and features

def load_audio(path: str | Path) -> np.ndarray:
    """Decode audio with ffmpeg: mono, 22.05 kHz, float32, at most MAX_S seconds."""
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-t", str(MAX_S),
         "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, timeout=120)
    if r.returncode != 0:
        msg = r.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError(msg[-1][:160] if msg else "ffmpeg failed")
    return np.frombuffer(r.stdout, dtype=np.float32)


def frames_of(y: np.ndarray) -> np.ndarray | None:
    peak = float(np.abs(y).max()) if y.size else 0.0
    if peak < 1e-5:
        return None
    y = y / peak                                   # bf16 works best on level-normalised data
    n = max(1, int(np.ceil(max(y.size - NFFT, 0) / HOP)) + 1)
    pad = (n - 1) * HOP + NFFT - y.size
    y = np.pad(y, (0, max(pad, 0)))
    return np.lib.stride_tricks.sliding_window_view(y, NFFT)[::HOP][:n].astype(np.float32)


def features_from_power(P: np.ndarray) -> dict[str, float]:
    P = np.maximum(P.astype(np.float64), 0.0)
    E = P.sum(axis=1)
    peak_i = int(np.argmax(E))
    e_db = 10 * np.log10(E / (E[peak_i] + 1e-30) + 1e-30)
    act = e_db > FLOOR_DB
    Pa = P[act]
    tot = Pa.sum() + 1e-30
    centroid = float((Pa @ FREQS).sum() / tot)
    low = float(Pa[:, FREQS < LOW_HZ].sum() / tot)
    # flatness: geometric / arithmetic mean of bands 1..N, weighted by energy
    eps = 1e-10 * float(P.max() + 1e-30)
    Q = Pa[:, 1:] + eps
    flat_f = np.exp(np.log(Q).mean(axis=1)) / Q.mean(axis=1)
    flat = float((flat_f * E[act]).sum() / (E[act].sum() + 1e-30))
    first = int(np.argmax(act))
    after = np.nonzero(act[peak_i:])[0]
    last = peak_i + int(after[-1]) if after.size else peak_i
    hop_s = HOP / SR
    return dict(centroid=centroid, low=low, flat=flat,
                attack=(peak_i - first) * hop_s, tail=(last - peak_i + 1) * hop_s)


# ---------------------------------------------------------------- tempo and key

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# Albrecht–Shanahan key profiles (C major / C minor). Tested on a real library
# (129 named files): better than Krumhansl–Kessler (50 % vs 44 % correct,
# 82 % correct or a compatible neighbour). A stricter KEY_MIN_R = fewer but more certain.
KEY_MAJOR = np.array([.238, .006, .111, .006, .137, .094, .016, .214, .009, .080, .008, .081])
KEY_MINOR = np.array([.220, .006, .104, .123, .019, .103, .012, .214, .062, .022, .061, .052])
KEY_MIN_R = 0.65                # correlation below which no key is recorded
KEY_MAX_FLAT = 0.30             # no key for noisier ones (drums, noise)
BPM_MIN_S = 1.5                 # no tempo for shorter ones (one-shots)
BPM_MIN_AC = 0.30               # minimum autocorrelation peak (0..1)
KEY_MIN_S = 1.0                 # no key for shorter ones (hits)
KEY_MIN_TAIL = 0.5              # nor for fast-decaying ones (drums)


def estimate_bpm(P: np.ndarray, dur_s: float, complete: bool) -> float | None:
    """Tempo from spectrum frames. complete = the whole file was read (not cut at MAX_S)."""
    if dur_s < BPM_MIN_S or P.shape[0] < 32:
        return None
    fps = SR / HOP
    L = np.log1p(P / (P.max() + 1e-30) * 1e4)
    flux = np.maximum(np.diff(L, axis=0), 0).sum(axis=1)
    flux = np.convolve(flux, [1, 2, 3, 2, 1], mode="same")   # tolerates hits between frames
    flux -= flux.mean()
    if not np.any(flux):
        return None
    n = flux.size
    ac = np.correlate(flux, flux, mode="full")[n - 1:]
    ac /= ac[0] + 1e-30
    lags = np.arange(int(fps * 60 / 200), min(int(fps * 60 / 55) + 1, n - 1))
    if lags.size < 3:
        return None
    bpms = 60 * fps / lags
    # weighting: the ear prefers to hear the tempo between 80 and 160
    w = np.exp(-0.5 * (np.log2(bpms / 120) / 0.9) ** 2)
    # it must also repeat 2 and 4 beats later: stops half and 4:3 tempos from winning
    # (tested: 64 % -> 70 % correct on the library's named loops)
    score = (ac[lags] + 0.5 * ac[np.minimum(lags * 2, n - 1)]
             + 0.5 * ac[np.minimum(lags * 4, n - 1)]) * w
    j = int(np.argmax(score))
    if ac[lags[j]] < BPM_MIN_AC:        # no clear pulse
        return None
    lag = float(lags[j])
    if 0 < j < lags.size - 1:            # parabola fit for sub-frame precision
        a, b, c = ac[lags[j] - 1], ac[lags[j]], ac[lags[j] + 1]
        den = a - 2 * b + c
        if den < 0:
            lag += 0.5 * (a - c) / den
    bpm = 60 * fps / lag
    if complete:
        # loops are usually exactly N beats long: refine if the length fits
        best = None
        for beats in (4, 8, 12, 16, 24, 32, 48, 64, 96, 128):
            cand = 60 * beats / dur_s
            d = abs(cand - bpm) / bpm
            if d < 0.02 and (best is None or d < best[0]):
                best = (d, cand)
        if best:
            bpm = best[1]
    return round(float(bpm), 1)


# Onset autocorrelation for the tempo: saved per file, the tempo itself is chosen in samplehaku.Index
# (candidates from the loop length + a prior from the library's named tempos by genre; testit/tempo_kehitys.py).
ONSET_LAGS = 200                # lags 0..199 frames (≈ 4.6 s): enough for 4 × the beat at 55 bpm
ONSET_EDGES = np.unique(np.geomspace(1, NBINS, 17).astype(int))     # 16 log-spaced bands


def onset_ac(P: np.ndarray) -> np.ndarray | None:
    """Spectrum frames -> normalised autocorrelation of the onset curve (float16, ≤ ONSET_LAGS long)."""
    if P.shape[0] < 32:
        return None
    B = np.stack([P[:, a:b].sum(axis=1) for a, b in zip(ONSET_EDGES[:-1], ONSET_EDGES[1:])], axis=1)
    L = np.log1p(B / (B.max() + 1e-30) * 1e4)
    f = np.maximum(np.diff(L, axis=0), 0).sum(axis=1)
    f = np.convolve(f, [1, 2, 3, 2, 1], mode="same")     # tolerates hits between frames
    f -= f.mean()
    if not np.any(f):
        return None
    n = f.size
    ac = np.correlate(f, f, mode="full")[n - 1:n - 1 + ONSET_LAGS]
    ac /= ac[0] + 1e-30
    return ac.astype(np.float16)


# Pitch salience for the key: spectral peaks of a 16384-point FFT, each credited to its fundamental
# (8 harmonics, weight 0.8^(h-1)), MIDI 28..96 in 1/3 semitones, every audible frame normalised. Saved per file;
# the key is chosen in samplehaku.Index by a small network trained on the library's named keys
# (samplehaku.keys_from_salience, testit/savellaji_kehitys.py: 76 % right for the 35 % it shows).
KEY_NFFT, KEY_HOP = 16384, 4096
KEY_MIDI_LO, KEY_MIDI_HI = 28, 96
KEY_NSAL = (KEY_MIDI_HI - KEY_MIDI_LO) * 3 + 1
KEY_HARM = 8


def key_salience(y: np.ndarray) -> np.ndarray | None:
    """Peak-normalised audio -> salience over fundamentals (KEY_NSAL, float16), or None (too short / silent)."""
    if y.size < KEY_NFFT // 2:
        return None
    if y.size < KEY_NFFT:
        y = np.pad(y, (0, KEY_NFFT - y.size))
    fr = np.lib.stride_tricks.sliding_window_view(y, KEY_NFFT)[::KEY_HOP]
    f = np.fft.rfftfreq(KEY_NFFT, 1 / SR)
    M = np.abs(np.fft.rfft(fr * np.hanning(KEY_NFFT), axis=1))[:, :np.searchsorted(f, 5000)]
    fmax = M.max(axis=1) + 1e-12
    top = M.max()
    out = np.zeros(KEY_NSAL)
    box = np.ones(101) / 101
    for t in range(M.shape[0]):
        m = M[t]
        if fmax[t] < 1e-4 * top:
            continue                                  # silent frame
        smooth = np.convolve(m, box, mode="same")
        pk = np.nonzero((m[1:-1] > m[:-2]) & (m[1:-1] >= m[2:]) & (m[1:-1] > 2 * smooth[1:-1])
                        & (m[1:-1] > fmax[t] * 10 ** (-50 / 20)))[0] + 1
        if not pk.size:
            continue
        la, lb, lc = np.log(m[pk - 1] + 1e-12), np.log(m[pk] + 1e-12), np.log(m[pk + 1] + 1e-12)
        den = la - 2 * lb + lc
        d = np.where(den < 0, 0.5 * (la - lc) / np.where(den < 0, den, -1), 0)
        fp = (pk + d) * SR / KEY_NFFT                 # parabolic peak frequency
        a = np.sqrt(m[pk] / fmax[t])
        sal = np.zeros(KEY_NSAL)
        for h in range(1, KEY_HARM + 1):
            f0 = fp / h
            ok = f0 > 0
            idx = np.round(3 * (12 * np.log2(f0[ok] / 440.0) + 69 - KEY_MIDI_LO)).astype(int)
            good = (idx >= 0) & (idx < KEY_NSAL)
            np.add.at(sal, idx[good], a[ok][good] * 0.8 ** (h - 1))
        tot = sal.sum()
        if tot > 0:
            out += sal / tot
    return out.astype(np.float16) if out.sum() > 0 else None


def estimate_key(y: np.ndarray, feat: dict) -> str | None:
    """Key from the chromagram. Returns e.g. 'Am' or 'Eb', or None if uncertain."""
    if (feat["flat"] > KEY_MAX_FLAT or y.size < max(8192, KEY_MIN_S * SR)
            or feat["tail"] < KEY_MIN_TAIL):
        return None
    nfft, hop = 8192, 2048
    fr = np.lib.stride_tricks.sliding_window_view(y, nfft)[::hop]
    S = np.abs(np.fft.rfft(fr * np.hanning(nfft), axis=1)) ** 2
    f = np.fft.rfftfreq(nfft, 1 / SR)
    sel = (f >= 60) & (f <= 5000)
    pc = np.round(12 * np.log2(f[sel] / 440.0) + 69).astype(int) % 12
    spec = np.sqrt(S[:, sel].sum(axis=0))            # amplitude evens out the strongest partials
    chroma = np.bincount(pc, weights=spec, minlength=12)
    if chroma.sum() <= 0:
        return None
    best = (-2.0, "")
    for r in range(12):
        for prof, suf in ((KEY_MAJOR, ""), (KEY_MINOR, "m")):
            c = float(np.corrcoef(chroma, np.roll(prof, r))[0, 1])
            if c > best[0]:
                best = (c, NOTE_NAMES[r] + suf)
    return best[1] if best[0] >= KEY_MIN_R else None


def compute_group(backend, paths: list[str]) -> dict[str, dict | None]:
    """Compute features for a group of files with one big NPU run."""
    out: dict[str, dict | None] = {}
    fr, owners, audio = [], [], []
    for p in paths:
        try:
            y = load_audio(p)
            f = frames_of(y)
        except Exception as e:
            print(f"  ! {Path(p).name}: {e}", file=sys.stderr)
            f = None
        out[p] = None
        if f is not None:
            fr.append(f)
            owners.append(p)
            audio.append(y)
    if not fr:
        return out
    allf = np.concatenate(fr)
    P = backend.power(allf)
    s = 0
    for p, f, y in zip(owners, fr, audio):
        Pf = P[s:s + len(f)]
        feat = features_from_power(Pf)
        dur = y.size / SR
        feat["bpm"] = estimate_bpm(Pf, dur, complete=dur < MAX_S - 0.05)
        feat["key"] = estimate_key(y / (np.abs(y).max() + 1e-12), feat)
        oa = onset_ac(Pf) if dur >= BPM_MIN_S else None
        feat["onset_ac"] = oa.tobytes() if oa is not None else None
        ks = key_salience(y / (np.abs(y).max() + 1e-12)) if dur >= KEY_MIN_S else None
        feat["key_sal"] = ks.tobytes() if ks is not None else None
        out[p] = feat
        s += len(f)
    return out


# ---------------------------------------------------------------- database

def open_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        sys.exit(f"The Fetch index was not found ({DB_PATH}). Run first: samplehaku index ~/Samples")
    con = sqlite3.connect(DB_PATH, timeout=300)    # the search service may be reading a large index
    con.execute(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(features)")}
    for col, typ in EXTRA_COLS:
        if col not in have:
            con.execute(f"ALTER TABLE features ADD COLUMN {col} {typ}")
    con.commit()
    return con


def _compute_cpu(path: str) -> dict | None:
    """compute_group for one file on the CPU, in a worker process."""
    global _CPU
    if _CPU is None:
        _CPU = CpuBackend()
    return compute_group(_CPU, [path])[path]


_CPU = None


def cmd_update(backend, limit: int | None, jobs: int = 1) -> None:
    con = open_db()
    files = con.execute("SELECT path, mtime, size FROM files WHERE audio_emb IS NOT NULL").fetchall()
    have = {p: (m, s) for p, m, s, v in con.execute("SELECT path, mtime, size, ver FROM features")
            if (v or 0) >= VERSION}
    live = {p for p, _, _ in files}
    gone = [p for (p,) in con.execute("SELECT path FROM features") if p not in live]
    con.executemany("DELETE FROM features WHERE path=?", [(p,) for p in gone])
    con.commit()
    todo = [(p, m, s) for p, m, s in files if have.get(p) != (m, s)]
    import siirto
    todo, copies = siirto.feature_dups(con, todo, VERSION)
    if limit:
        todo = todo[:limit]
    # The CPU backend runs file by file in parallel processes; the NPU wants big groups in one process.
    par = type(backend) is CpuBackend and jobs > 1
    print(f"{len(files)} files in the index, {len(todo)} to compute ({backend.name}"
          + (f", {jobs} processes" if par else "") + ").")
    if par:
        import rinnakkain
        feats = rinnakkain.ordered_map(_compute_cpu, [p for p, _, _ in todo], jobs)
    t0, done = time.time(), 0
    for i in range(0, len(todo), FILES_PER_GROUP):
        grp = todo[i:i + FILES_PER_GROUP]
        if par:
            res = {p: next(feats) for p, _, _ in grp}
        else:
            res = compute_group(backend, [p for p, _, _ in grp])
        for p, m, s in grp:
            f = res[p]
            vals = [f[k] for k in FEATURES + ("bpm", "key", "onset_ac", "key_sal")] if f else [None] * (len(FEATURES) + 4)
            con.execute("INSERT OR REPLACE INTO features(path, mtime, size, centroid, low, flat, attack, "
                        "tail, bpm, key, onset_ac, key_sal, backend, ver) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (p, m, s, *vals, backend.name, VERSION))
        con.commit()
        done += len(grp)
        rate = done / (time.time() - t0)
        print(f"\r  {done}/{len(todo)}  ({rate:.1f} files/s)   ", end="", flush=True)
    if todo:
        print()
    siirto.copy_features(con, copies)
    if isinstance(backend, NpuBackend) and backend.calls:
        print(f"NPU: {backend.calls} calls, {backend.npu_s:.1f} s "
              f"({backend.npu_s / backend.calls * 1000:.0f} ms/call, {backend.M} frames/call)")
    print("Done.")


def _fill_one(args) -> tuple[bytes | None, bytes | None]:
    path, need_onset, need_key = args
    try:
        y = load_audio(path)
        dur = y.size / SR
        oa = ks = None
        if need_onset and dur >= BPM_MIN_S:
            f = frames_of(y)
            oa = onset_ac(CpuBackend().power(f)) if f is not None else None
        if need_key and dur >= KEY_MIN_S:
            ks = key_salience(y / (np.abs(y).max() + 1e-12))
        return (oa.tobytes() if oa is not None else None), (ks.tobytes() if ks is not None else None)
    except Exception:
        return None, None


def cmd_onset(limit: int | None, jobs: int) -> None:
    """Fill onset_ac (tempo) and key_sal (key) for rows computed before they existed (CPU, in parallel;
    the other features stay). b"" = tried, nothing found."""
    from concurrent.futures import ProcessPoolExecutor
    con = open_db()
    todo = [(p, bool(on) and d >= BPM_MIN_S, bool(kn)) for p, on, kn, d in con.execute(
        "SELECT f.path, f.onset_ac IS NULL, f.key_sal IS NULL, x.duration FROM features f "
        "JOIN files x ON x.path = f.path AND x.mtime = f.mtime AND x.size = f.size "
        "WHERE (f.onset_ac IS NULL AND x.duration >= ?) OR (f.key_sal IS NULL AND x.duration >= ?)",
        (BPM_MIN_S, KEY_MIN_S))]
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} files without tempo/key analysis data ({jobs} processes).")
    t0 = time.time()
    with ProcessPoolExecutor(jobs) as ex:
        for i in range(0, len(todo), 256):
            grp = todo[i:i + 256]
            res = list(ex.map(_fill_one, grp, chunksize=8))
            con.executemany("UPDATE features SET onset_ac=? WHERE path=?",
                            [(oa or b"", p) for (p, no, _), (oa, _) in zip(grp, res) if no])
            con.executemany("UPDATE features SET key_sal=? WHERE path=?",
                            [(ks or b"", p) for (p, _, nk), (_, ks) in zip(grp, res) if nk])
            con.commit()
            done = i + len(grp)
            print(f"\r  {done}/{len(todo)}  ({done / (time.time() - t0):.1f} files/s)   ", end="", flush=True)
    if todo:
        print()
    print("Done.")


# ---------------------------------------------------------------- tests

def synth_signals() -> dict[str, np.ndarray]:
    t = np.arange(int(SR * 2.0)) / SR
    rng = np.random.default_rng(0)
    kick = np.sin(2 * np.pi * (50 + 120 * np.exp(-t * 30)) * t) * np.exp(-t * 6)
    hat = rng.standard_normal(t.size) * np.exp(-t * 40)
    pad = sum(np.sin(2 * np.pi * f * t) for f in (220, 277, 330)) * np.minimum(t / 0.8, 1)
    return {"kick": kick, "hihat": hat, "pad": pad}


def selftest(name: str) -> None:
    ref = CpuBackend()
    be = make_backend(name)
    print(f"Backend: {be.name}")
    rng = np.random.default_rng(1)
    X = rng.standard_normal((4096, NFFT)).astype(np.float32) * 0.3
    t0 = time.perf_counter(); Pr = ref.power(X); t_cpu = time.perf_counter() - t0
    be.power(X[:64])                                              # warm-up / compile
    t0 = time.perf_counter(); Pb = be.power(X); t_be = time.perf_counter() - t0
    err = np.abs(Pb - Pr).sum() / np.abs(Pr).sum()
    print(f"Spectrum error vs. float32 CPU: {err * 100:.2f} % (relative L1)")
    print(f"4096 frames: CPU {t_cpu * 1000:.0f} ms, {be.name} {t_be * 1000:.0f} ms")
    print(f"\n{'':8s}" + "".join(f"{k:>10s}" for k in FEATURES))
    for nm, y in synth_signals().items():
        f = frames_of(y.astype(np.float32))
        a = features_from_power(ref.power(f))
        b = features_from_power(be.power(f))
        print(f"{nm:8s}" + "".join(f"{a[k]:10.3f}" for k in FEATURES) + "   cpu")
        print(f"{'':8s}" + "".join(f"{b[k]:10.3f}" for k in FEATURES) + f"   {be.name}")
    ok = err < 0.05
    print("\nOK" if ok else "\nERROR: the results differ too much")
    sys.exit(0 if ok else 1)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Fetch sound features on the NPU")
    ap.add_argument("--backend", default=os.environ.get("AANIPIIRTEET_BACKEND", "auto"),
                    choices=["auto", "npu", "cpu", "bf16-emu"])
    ap.add_argument("--limit", type=int, help="compute at most this many (for testing)")
    ap.add_argument("--selftest", action="store_true", help="compare the backend with the CPU")
    ap.add_argument("--show", metavar="FILE", help="show one file's features")
    ap.add_argument("--onset", action="store_true",
                    help="only fill the tempo/key analysis data (onset_ac, key_sal) for files computed before "
                         "they existed (CPU, parallel)")
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("SAMPLEHAKU_JOBS") or 0)
                    or max(1, (os.cpu_count() or 2) - 2),
                    help="worker processes for the CPU (default: all cores but two, or SAMPLEHAKU_JOBS)")
    a = ap.parse_args(argv)
    if a.onset:
        return cmd_onset(a.limit, a.jobs)
    if a.selftest:
        selftest(a.backend)
    be = make_backend(a.backend)
    if a.show:
        f = compute_group(be, [a.show])[a.show]
        print(f if f else "Silent or unreadable file.")
        return
    cmd_update(be, a.limit, a.jobs)


if __name__ == "__main__":
    main()
