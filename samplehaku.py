#!/usr/bin/env python3
"""
samplehaku – local semantic search for a sound and sample library (the engine behind Fetch).

Uses the CLAP model (Contrastive Language-Audio Pretraining), which places audio and text
in the same "meaning space". So you can search for e.g. "punchy kick with long tail" or
"dark evolving pad" and find samples whose names say neither.

Commands:
  samplehaku.py index ~/Samples ~/Music         index (re-running updates only changed files)
  samplehaku.py search "dusty vinyl drum loop"  search by text
  samplehaku.py similar path/to/file.wav        find similar sounds
  samplehaku.py stats                           index information
  samplehaku.py search "kick +dark +tight"      narrow by sound features (see TAGS)
  samplehaku.py search "drum loop bpm:124"      narrow by tempo; key:Am / key:Am~ by key
  samplehaku.py                                 interactive mode (search, listen, refine)

Sound features (brightness, bass, noisiness, attack, tail) are computed separately by
aanipiirteet.py, preferably on the Ryzen AI NPU. Without them, search works as before.

Everything runs locally. The model is downloaded from Hugging Face only the first time.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

import polut

# ---------------------------------------------------------------- settings

AUDIO_EXT = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".opus", ".m4a"}
SR = 48_000                 # CLAP expects 48 kHz audio
WIN_S = 10.0                # CLAP window length in seconds
MAX_WINDOWS = 3             # at most this many windows are taken from long files
MAX_LOAD_S = 90.0           # only the beginning of long files is read
BATCH = 8
BATCH_GPU = int(os.environ.get("SAMPLEHAKU_BATCH_GPU", 16))   # files per CLAP run on a CUDA card

DEFAULT_MODEL = os.environ.get("SAMPLEHAKU_MODEL", "laion/larger_clap_general")
DATA_DIR = Path(os.environ.get("SAMPLEHAKU_DIR",
                               Path.home() / ".local/share/samplehaku"))
DB_PATH = DATA_DIR / "index.db"

# Optional local language model for translating non-English queries.
# E.g. Ollama:  SAMPLEHAKU_LLM_MODEL=qwen3:4b
# Lemonade:      SAMPLEHAKU_LLM_URL=http://localhost:8000/api/v1  SAMPLEHAKU_LLM_MODEL=...
LLM_URL = os.environ.get("SAMPLEHAKU_LLM_URL", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("SAMPLEHAKU_LLM_MODEL", "")

W_AUDIO = 0.75              # weights: audio content vs. file name / folder path
W_NAME = 0.25

# Sound-feature filters: type e.g. "+dark" or "+tumma" in a search.
# Definitions are read from ~/.config/samplehaku/tagit.json (edit freely).
# Thresholds are relative to your own library: "dark" = the darkest third.
TAGS_PATH = Path(os.environ.get("SAMPLEHAKU_TAGS", Path.home() / ".config/samplehaku/tagit.json"))
# Tags corrected by the user (results window "Edit tags…"): {path: {"inst", "type", "roles"}}
FIXES_PATH = Path(os.environ.get("SAMPLEHAKU_FIXES", Path.home() / ".config/samplehaku/korjaukset.json"))
TYPE_IDS = {"loop": 1, "oneshot": 0}


def load_fixes() -> dict:
    try:
        return json.loads(FIXES_PATH.read_text())
    except (OSError, ValueError):
        return {}
DEFAULT_TAGS = [
    (["dark", "tumma"], "centroid", -1), (["bright", "kirkas"], "centroid", 1),
    (["bass", "basso"], "low", 1),
    (["noisy", "kohina"], "flat", 1), (["tonal", "tonaalinen"], "flat", -1),
    (["punchy", "iskevä"], "attack", -1), (["soft", "pehmeä"], "attack", 1),
    (["tight", "lyhyt"], "tail", -1), (["long", "pitkä"], "tail", 1),
]


def load_tags() -> tuple[dict[str, tuple[str, int]], list[list[str]], float]:
    """-> (name -> (feature, direction), name groups for the help text, share)."""
    defs, share = DEFAULT_TAGS, 1 / 3
    if TAGS_PATH.is_file():
        try:
            cfg = json.loads(TAGS_PATH.read_text())
            # English keys (tags/names/feature/direction/share); the original Finnish keys still work
            defs = [(t.get("names", t.get("nimet")), t.get("feature", t.get("piirre")),
                     int(t.get("direction", t.get("suunta")))) for t in cfg.get("tags", cfg.get("tagit"))]
            share = float(cfg.get("share", cfg.get("osuus", share)))
        except Exception as e:
            print(f"(error in {TAGS_PATH}: {e}; using the default tags)", file=sys.stderr)
            defs = DEFAULT_TAGS
    tags, groups = {}, []
    for names, feat, sign in defs:
        if feat not in ("centroid", "low", "flat", "attack", "tail"):
            print(f"(unknown feature '{feat}' for tag {names[0]}, skipped)", file=sys.stderr)
            continue
        groups.append([n.lower() for n in names])
        for n in names:
            tags[n.lower()] = (feat, -1 if sign < 0 else 1)
    return tags, groups, min(max(share, 0.01), 1.0)


TAGS, TAG_GROUPS, TAG_SHARE = load_tags()
FEATURES = ("centroid", "low", "flat", "attack", "tail")


# ---------------------------------------------------------------- tempo and key
#
# In a search:  bpm:120  bpm:118-126  key:Am  key:F#  key:Am~   (also tempo: and sävel:)
# Sources: 1) the file name ("Loop_124bpm_Am.wav"), 2) aanipiirteet.py's estimate from the audio.
# The name wins, because sample pack names are usually right.

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_PC = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
BPM_TOL = 0.03              # bpm:120 accepts ±3 % (and half/double time: 60, 240)

# "124bpm", "120 BPM", "120_BPM", "bpm120"; "1254BPM" = 125.4 (SMGP: the decimal point left out)
_BPM_RE = re.compile(r"(?<![\d.])(\d{2,3}(?:[.,]\d)?|\d{4})[\s_-]?bpm|bpm[\s_-]?(\d{2,3}(?:[.,]\d)?)(?!\d)", re.I)
_BARE_BPM_RE = re.compile(r"(?<![\w#])([1-9]\d{1,2})(?![\w#])")
# a tempo glued to a part word: "US_DAT_drm120_…", "pad125", "bs120", "chord120" (not machine names: MPC60, Juno60, CR78)
_GLUED_BPM_RE = re.compile(r"(?<![a-z0-9])(?:drm|drums?|pads?|bs|bass|synth|chords?|fx|perc|tops?|hats?|arp|lead|"
                           r"vox|keys|gtr|groove|beat|loop)(\d{2,3})(?!\d)", re.I)
_PAIR_RE = re.compile(r"\(\s*(\d{2,3})\s*,\s*([A-G][#b♯♭]?(?:m|min|maj)?)(?![A-Za-z#♯♭])[^)]*\)")
_INDEX_PREFIX_RE = re.compile(r"^\d+\.\d+-\d+\s")      # "3.05-06 Breakbeat …": a track index, not a tempo
_KEY_RE = re.compile(r"(?<![A-Za-z0-9#♯♭])([A-Ga-g])(#|♯|b|♭)?"
                     r"(?:\s?((?i:minor|min|moll|major|maj|dur)|m))?(\d)?(?![A-Za-z#♯♭])")


SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def key_name(pc: int, mode: int, spell: int = 0) -> str:
    """mode: 0 = major, 1 = minor, -1 = only the root is known.
    spell: 0 = the usual names (C# Eb F# Ab Bb), 1 = sharps, 2 = flats (as the file name writes it)."""
    names = SHARP_NAMES if spell == 1 else FLAT_NAMES if spell == 2 else NOTE_NAMES
    return names[pc] + ("m" if mode == 1 else "")


def parse_audio_key(s: str | None) -> tuple[int, int] | None:
    """aanipiirteet.estimate_key writes 'Am' or 'A' – and 'A' there means A major, not 'only the root known'
    (parse_key would give mode -1: older indexes stored estimated major keys without their mode)."""
    if not s:
        return None
    k = parse_key(s, strict=False)
    return (k[0], 0) if k and k[1] == -1 else k


def key_spelling(path: str, pc: int) -> int:
    """How the file name writes the key pc: 1 = sharp (D#), 2 = flat (Eb), 0 = no accidental / not found.
    A file named 'D#' must not be shown as 'Eb': show it as written."""
    if NOTE_NAMES[pc] in SHARP_NAMES and NOTE_NAMES[pc] in FLAT_NAMES:      # a white key: nothing to choose
        return 0
    stem = re.sub(r"[_\-\.\(\)\[\],]+", " ", Path(path).stem)
    spell = 0
    for m in _KEY_RE.finditer(stem):
        note, acc = m.group(1), m.group(2)
        if acc and (_PC[note.lower()] + (1 if acc in ("#", "♯") else -1)) % 12 == pc:
            spell = 1 if acc in ("#", "♯") else 2          # the last match wins, as in parse_key
    return spell


def parse_key(s: str, strict: bool = True) -> tuple[int, int] | None:
    """'Am' -> (9, 1), 'F# maj' -> (6, 0), 'C3' -> (0, -1). strict=False: also a bare 'C' (in searches)."""
    if strict:
        matches = list(_KEY_RE.finditer(s))
    else:
        m = _KEY_RE.fullmatch(s.strip())
        matches = [m] if m else []
    found = None
    for m in matches:
        note, acc, mode, octave = m.groups()
        # in file names a single letter is too often something else ("Loop A", "a b c")
        if strict and (not (acc or mode or octave) or (note.islower() and not mode)):
            continue
        pc = (_PC[note.lower()] + (1 if acc in ("#", "♯") else -1 if acc in ("b", "♭") else 0)) % 12
        md = -1 if not mode else (1 if mode.lower() in ("m", "min", "minor", "moll") else 0)
        found = (pc, md)             # the last match wins: the key is usually at the end of the name
    return found


def name_bpm_key(path: str) -> tuple[float, tuple[int, int] | None]:
    """Tempo and key from the file name (tempo also from the folder name)."""
    p = Path(path)
    bpm = np.nan
    for part in (p.stem, p.parent.name):
        m = _BPM_RE.search(part)
        if m:
            t = (m.group(1) or m.group(2)).replace(",", ".")
            v = float(t) / 10 if len(t) == 4 and t.isdigit() else float(t)
            if 40 <= v <= 300:
                bpm = v
                break
    pair = _PAIR_RE.search(p.stem)             # "(170, A)" / "(145, F Phrygian)": tempo + key in brackets
    if pair and np.isnan(bpm) and 60 <= int(pair.group(1)) <= 200:
        bpm = float(pair.group(1))
    if np.isnan(bpm):
        m = _GLUED_BPM_RE.search(p.stem)
        if m and 60 <= int(m.group(1)) <= 200:
            bpm = float(m.group(1))
    stem = re.sub(r"[_\-\.\(\)\[\],]+", " ", _INDEX_PREFIX_RE.sub("", p.stem))
    stem = _BPM_RE.sub(" ", stem)    # "120bpm" must not look like a key
    key = parse_key(stem)
    tail = re.search(r"(?<![A-Za-z0-9#])([A-G][#b]?)\s*$", stem)
    if np.isnan(bpm) and (key or re.search(r"loop|fill", path, re.I)):     # fills too
        # in packs the tempo is often a bare number: "Flute_Loop_115_Dm", "(125, Gm)"
        for m in _BARE_BPM_RE.finditer(stem):
            v = int(m.group(1))
            if 60 <= v <= 200:
                bpm = float(v)
                break
    if key is None:
        # "(C)" / "(F#m)" in its own brackets is always a key (some packs use this), unlike a lone "C"
        par = re.search(r"\(\s*([A-G][#b♯♭]?(?:m|min|maj)?)\s*\)", p.stem)
        if par and re.search(r"\d+\s*bars?\W*$", p.stem[:par.start()], re.I):
            par = None               # "Breakbeat - 1 bar (B)" = variation B, not the key of B
        if par:
            key = parse_key(par.group(1), strict=False)
    if key is None and pair:
        key = parse_key(pair.group(2), strict=False)
    if key is None and tail and np.isfinite(bpm):
        # "Artist_104_pop_loop_surf_dad_full_C": together with a tempo, the last letter is the key
        key = parse_key(tail.group(1), strict=False)
    return bpm, key


# ---------------------------------------------------------------- loop or one-shot
# In a search: +loop / +oneshot (also +luuppi, +isku). Inferred from the path, the deepest hint wins
# ("Orange Groove Samples/One Shot Drums/Kick 3.wav" = one-shot). Words common in pack
# names (groove, beats, kit) don't count as hints.
_LOOP_RE = re.compile(r"(?<!\bb)(?<!\bk)(?<!prime )(?<!prime)loops?(?![a-z])|loopkit|\blps\b|breakbeats?|\bbreaks\b|"
                      r"songstarters?|\bss\b|grooves", re.I)
# not "Loopy"; "LoopKit03", "ZTH_DRUM_LPS"; "Bloop"/"Kloop" are sounds and "Prime Loops" a label
# A folder that names what a whole pack holds ("… - Samples and Loops", "Breakbeats-Loops-and-Drum-Cuts",
# "Drum Loops & Samples") says nothing about one file (those folders hold single hits too).
_TITLE_RE = re.compile(r"(samples?|cuts|one ?shots?|hits|sounds)\s*(and|&)\s*loops|"
                       r"loops\s*(and|&)\s*(samples?|cuts|one ?shots?|hits|drum cuts)", re.I)
# Vocals: a short one (a chant, a shout, one word: under VOCAL_SHOT_S) is a one-shot even
# in a loop folder; a longer one (VOCAL_LOOP_S or more) is a loop unless the name says one-shot. In between the
# sound decides (TYPE_MODEL). Measured on hand-made corrections: one-shots ≤ 1.6 s, loops ≥ 1.9 s.
_VOCAL_RE = re.compile(r"(?<![a-z])(vocals?|vox|voices?|acapellas?|phrases?|chants?|shouts?|speech|spoken|"
                       r"whispers?|ad ?libs?|adlibs?)(?![a-z])", re.I)
VOCAL_SHOT_S = 1.5
VOCAL_LOOP_S = 2.0
# A long single shout, scream or yell is a one-shot too; a tempo in the name means a repeating one.
_SHOUT_RE = re.compile(r"(?<![a-z])(shouts?|screams?|yells?)(?![a-z])", re.I)
SHOUT_MAX_S = 8.0
# A file the name calls a loop but that is shorter than any loop and sounds like a hit is a one-shot
# ("BT-Kick Punch Loop", 0.1 s): Index._build with TYPE_MODEL.
SHORT_LOOP_S = 0.75
_SHOT_RE = re.compile(r"one[ _-]?shots?|\bshots?\b|\bhits?\b|\bfills?\b", re.I)   # fills = one-shot
_DRUM_HIT_RE = re.compile(r"\b(kicks?|snares?|claps?|snaps?|(open|closed|hi)?[ _-]?hats?|hihats?|"
                          r"cymbals?|crash(es)?|rides?|toms?|rims?(hots?)?|cowbells?|shakers?|808s?)\b", re.I)
TYPE_WORDS = {"loop": 1, "loops": 1, "luuppi": 1, "oneshot": 0, "oneshots": 0, "one-shot": 0,
              "one_shot": 0, "shot": 0, "shots": 0, "isku": 0,
              "one": 0}   # "+one" = half-typed "+one shot" (Super search gets the words one at a time)
# Sounds without a pulse count as one-shots when the name says neither loop nor one-shot (drones, ambiences,
# vocal tones…; risers, sweeps and impacts too). Name or folder.
_FX_SHOT_RE = re.compile(r"(?<![a-z])(drones?|ambien[cs]es?|ambiances?|atmos|atmospheres?|textures?|soundscapes?|"
                         r"vowels?|tones?|reverses?|reversed|risers?|uplifters?|downlifters?|sweeps?|swells?|"
                         r"impacts?)(?![a-z])", re.I)
_ONE_SHOT_RE = re.compile(r"\+one[\s_-]*shots?\b", re.I)


def normalize_type_words(line: str) -> str:
    """'+one shot' / '+One-Shots' -> '+oneshot' (a space would split it into two words)."""
    return _ONE_SHOT_RE.sub("+oneshot", line)


def sample_type(path: str, dur: float, bpm: float) -> tuple[int, bool]:
    """-> (1 = loop, 0 = one-shot; True = inferred from the length, not the name).
    Every sample is one of the two: what the name and the length don't settle (risers, drones,
    ambiences, long phrases) counts as a loop."""
    t, why = sample_type_why(path, dur, bpm)
    return t, why not in ("name", "drum hit", "fx word")


def sample_type_why(path: str, dur: float, bpm: float) -> tuple[int, str]:
    """sample_type with the rule that decided: vocal short / name / drum hit / fx word / vocal shout / vocal long / beats /
    short / default."""
    parts = Path(path).parts
    vocal = any(_VOCAL_RE.search(re.sub(r"[_\-.]+", " ", part)) for part in parts[-3:])   # file + 2 folders
    if vocal and dur < VOCAL_SHOT_S:
        return 0, "vocal short"
    for part in reversed(parts[-4:]):                   # file name first, then folders upwards
        is_file = part == parts[-1]
        part = re.sub(r"[_\-.]+", " ", part)
        if not is_file and _TITLE_RE.search(part):
            continue
        l, s = bool(_LOOP_RE.search(part)), bool(_SHOT_RE.search(part))
        if l != s:
            return (1 if l else 0), "name"
    stem = re.sub(r"[_\-.]+", " ", Path(path).stem)
    kit = re.match(r"(bd|sd|ch|oh|hh|cp|rs|cb|cy|lt|mt|ht)\b", stem, re.I)   # "BD DMX 612 Clip"
    if (_DRUM_HIT_RE.search(stem) or kit) and not re.search(r"roll|fill|groove", stem, re.I) and dur < 4:
        return 0, "drum hit"
    if any(_FX_SHOT_RE.search(re.sub(r"[_\-.]+", " ", part)) for part in parts[-4:]):
        return 0, "fx word"
    if (vocal and dur < SHOUT_MAX_S and any(_SHOUT_RE.search(re.sub(r"[_\-.]+", " ", part)) for part in parts[-2:])
            and not np.isfinite(name_bpm_key(path)[0])):
        return 0, "vocal shout"
    if vocal and dur >= VOCAL_LOOP_S:
        return 1, "vocal long"
    if np.isfinite(bpm) and dur >= 1.5:
        beats = dur * bpm / 60
        if beats >= 3.5 and abs(beats - round(beats)) < 0.08 * max(1, round(beats) / 16):
            return 1, "beats"
    if dur < 1.5:
        return 0, "short"
    return 1, "default"


# ---------------------------------------------------------------- loop or one-shot from the audio
# What the name doesn't settle ("default", ~27k samples) all counted as loops, and many are single notes,
# chords, hits, FX and vocal shots. A logistic regression on the CLAP audio vector + length and sound features,
# learned from the samples the name settles (testit/tyyppi_kehitys.py, shipped as TYPE_MODEL), turns the ones it is
# sure about (P(one-shot) ≥ TYPE_MIN_P) into one-shots. Index._build; the user's corrections still win.
TYPE_MODEL = Path(__file__).with_name("tyyppi_malli.npz")
TYPE_MIN_P = 0.9
_TYPE_F = ("tail", "attack", "flat", "centroid", "low")
_type_model_cache: dict = {}


def type_features(A: np.ndarray, dur: np.ndarray, F: dict) -> np.ndarray:
    """(n, 512) CLAP + length + sound features -> (n, d). Missing features (not analysed) = 0."""
    extra = [np.log(np.maximum(np.asarray(dur, dtype=np.float64), 0.05))]
    for k in _TYPE_F:
        v = np.asarray(F[k], dtype=np.float64)
        extra.append(np.nan_to_num(np.log(np.maximum(v, 1e-3)) if k == "centroid" else v))
    return np.hstack([np.asarray(A, dtype=np.float32), np.stack(extra, axis=1).astype(np.float32)])


def type_model(path: Path = TYPE_MODEL) -> dict | None:
    if path not in _type_model_cache:
        try:
            with np.load(path) as z:
                _type_model_cache[path] = {k: z[k] for k in ("mu", "sd", "w", "b")}
        except (OSError, KeyError, ValueError):
            _type_model_cache[path] = None
    return _type_model_cache[path]


def type_probs(X: np.ndarray, model: dict | None = None) -> np.ndarray:
    """type_features -> P(one-shot) per row (zeros without a model)."""
    model = model or type_model()
    if model is None or not len(X):
        return np.zeros(len(X), dtype=np.float32)
    z = ((X - model["mu"]) / model["sd"]) @ model["w"] + model["b"]
    return (1 / (1 + np.exp(-np.clip(z, -40, 40)))).astype(np.float32)


# ---------------------------------------------------------------- tempo from the audio
# Measured (testit/tempo_kehitys.py, 2.9k named loops, prior with the loop's own pack series left out):
# the old free autocorrelation peak was right for 50 % of the answered loops (half/double the most common error).
# Now: loops are nearly always a whole number of beats long (92 %) -> candidate tempos 60·N/length (N = 4, 8, 16 …),
# the onset autocorrelation (aanipiirteet.onset_ac) at the beat, 2 and 4 beats scores them, and a prior from
# the library's named loop tempos of the same genre decides between half and double. Shown only when confident:
# 87 % right with 84 % of the loops getting a tempo (files cut at 30 s: none); for samples whose name or
# folder says loop 92.5 %. Only those get one (see Index._build).
TEMPO_FPS = 22_050 / 512            # aanipiirteet SR / HOP
TEMPO_MAX_S = 30.0                  # aanipiirteet.MAX_S: longer files are cut -> no length candidates
TEMPO_NSET = (4, 8, 16, 32, 64, 128)
TEMPO_MIN_CONF = 0.8
TEMPO_GRID = np.arange(55, 211)


def tempo_prior(bpms) -> np.ndarray:
    """How common each tempo 55..210 is among named loops (log-Gaussian bumps ±2 %, with a floor)."""
    h = np.zeros(TEMPO_GRID.size)
    b = np.asarray([x for x in bpms if 40 <= x <= 300], dtype=float)
    if b.size:
        h = np.exp(-0.5 * (np.log(TEMPO_GRID[:, None] / b[None, :]) / 0.02) ** 2).sum(axis=1)
        h /= h.sum()
    return 0.9 * h + 0.1 / TEMPO_GRID.size


def _ac_at(ac: np.ndarray, lag: float) -> float:
    if lag >= ac.size - 1:
        return 0.0
    i = int(lag)
    t = lag - i
    return float(ac[i]) * (1 - t) + float(ac[i + 1]) * t


def tempo_from_ac(ac: np.ndarray, dur: float, prior: np.ndarray) -> tuple[float | None, float]:
    """-> (tempo or None, confidence 0..1). Files cut at 30 s get none: without the length the
    autocorrelation alone was right for only 36 % of them."""
    if ac is None or ac.size < 8 or dur < 1.5 or dur >= TEMPO_MAX_S - 0.05:
        return None, 0.0
    ac = ac.astype(np.float64)
    ts, sc = [], []
    for t in (60 * n / dur for n in TEMPO_NSET):
        lag = 60 * TEMPO_FPS / t
        if not 55 <= t <= 210 or lag >= ac.size - 1:
            continue
        comb = _ac_at(ac, lag) + 0.5 * _ac_at(ac, 2 * lag) + 0.5 * _ac_at(ac, 4 * lag)
        p = prior[min(max(int(round(t)) - TEMPO_GRID[0], 0), TEMPO_GRID.size - 1)]
        ts.append(t)
        sc.append(np.log(max(comb, 1e-3)) + np.log(p))
    if not sc:
        return None, 0.0
    sc = np.array(sc)
    p = np.exp(sc - sc.max())
    p /= p.sum()
    j = int(np.argmax(sc))
    return round(float(ts[j]), 1), float(p[j])


# ---------------------------------------------------------------- key from the audio
# Measured (testit/savellaji_kehitys.py: 24.7k files with a key in the name, 5-fold cross-validation by pack
# series): aanipiirteet.key_salience (fundamentals from spectral peaks) folded into 36 bins per octave in 5
# registers, read by a small network that is the same for all 12 transpositions (KEY_MODEL, trained on named
# keys and shipped with the code). Shown when its probability ≥ KEY_MIN_P:
# 35 % of the files get a key, 76 % right, 90 % right or harmonically compatible. The learned profiles used
# before were right for 56 % at the same coverage. Drums and percussion get no key from the audio (55 % right
# even when confident, and a drum loop's key means little): see Index._drop_drum_keys.
KEY_MIN_P = 0.6
KEY_MODEL = Path(__file__).with_name("savellaji_malli.npz")
KEY_NO_AUDIO = ("drums", "percussion")             # instrument classes that never get a key from the audio
_SAL_MIDI = 28 + np.arange((96 - 28) * 3 + 1) / 3    # aanipiirteet.KEY_NSAL, MIDI 28..96 in 1/3 semitones
_KEY_EDGES = (28, 40, 52, 64, 76, 97)


def _key_map() -> np.ndarray:
    """(KEY_NSAL, registers × 36): salience bin -> its register and its place in the octave (0 = C centre)."""
    M = np.zeros((_SAL_MIDI.size, len(_KEY_EDGES) - 1, 36))
    for j, m in enumerate(_SAL_MIDI):
        b = np.searchsorted(_KEY_EDGES, m + 1 / 3, side="right") - 1
        M[j, b, (28 * 3 + j) % 36] = 1
    return M.reshape(_SAL_MIDI.size, -1)


_KEY_MAP = _key_map()


def key_features(sal) -> np.ndarray:
    """Salience (one or a stack) -> (n, 5, 36) log shares per register."""
    S = np.atleast_2d(np.asarray(sal, dtype=np.float64))
    S = S / (S.sum(axis=1, keepdims=True) + 1e-12)
    X = (S @ _KEY_MAP).reshape(len(S), len(_KEY_EDGES) - 1, 36)
    X = X / (X.sum(axis=2, keepdims=True) + 1e-9)
    return np.log(X + 1e-3).astype(np.float32)


_key_model_cache: dict = {}


def key_model(path: Path = KEY_MODEL) -> dict | None:
    """The shipped weights (W1, b1, W2, b2), or None if the file is missing."""
    if path not in _key_model_cache:
        try:
            with np.load(path) as z:
                _key_model_cache[path] = {k: z[k].astype(np.float32) for k in ("W1", "b1", "W2", "b2")}
        except (OSError, KeyError, ValueError):
            _key_model_cache[path] = None
    return _key_model_cache[path]


def key_probs(X: np.ndarray, model: dict) -> np.ndarray:
    """key_features -> (n, 24) probabilities, column pc + 12 × mode (0 major, 1 minor). The same network
    reads the features rotated to each of the 12 tonics and gives a major and a minor score."""
    n = len(X)
    logits = np.zeros((n, 24), dtype=np.float32)
    for r in range(12):
        h = np.maximum(np.roll(X, -3 * r, axis=2).reshape(n, -1) @ model["W1"].T + model["b1"], 0)
        o = h @ model["W2"].T + model["b2"]
        logits[:, r], logits[:, 12 + r] = o[:, 0], o[:, 1]
    p = np.exp(logits - logits.max(axis=1, keepdims=True))
    return p / p.sum(axis=1, keepdims=True)


def keys_from_salience(sals, model: dict | None = None) -> list[tuple[tuple[int, int] | None, float]]:
    """Saliences -> [((pc, mode), probability)]; (None, 0) for a missing salience or a missing model."""
    model = model or key_model()
    out: list = [(None, 0.0)] * len(sals)
    ok = [i for i, s in enumerate(sals) if s is not None and np.any(s)]
    if not ok or model is None:
        return out
    for a in range(0, len(ok), 8192):                 # bounded memory for 200k files
        part = ok[a:a + 8192]
        P = key_probs(key_features(np.stack([sals[i] for i in part])), model)
        best = P.argmax(axis=1)
        for i, k, p in zip(part, best, P[np.arange(len(part)), best]):
            out[i] = ((int(k) % 12, int(k) // 12), float(p))
    return out


def compatible_keys(pc: int, mode: int) -> set[tuple[int, int]]:
    """Harmonically compatible keys (Camelot wheel neighbours): same, relative, a fifth up/down."""
    if mode == -1:
        return {((pc + d) % 12, md) for d in (0, 7, 5) for md in (0, 1)}
    rel = ((pc + 3) % 12, 0) if mode == 1 else ((pc - 3) % 12, 1)
    return {(pc, mode), rel, ((pc + 7) % 12, mode), ((pc + 5) % 12, mode)}


def parse_filter(word: str) -> str | None:
    """'bpm:120' / 'key:am~' -> normalised filter, or None if the word is not a filter."""
    m = re.fullmatch(r"(bpm|tempo):(.*)", word, re.I)
    if m:
        v = m.group(2).replace(",", ".")
        r = re.fullmatch(r"(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?", v)
        if not r:
            raise ValueError(f"Unknown tempo '{word}'. E.g. bpm:120 or bpm:118-126")
        lo = float(r.group(1))
        hi = float(r.group(2)) if r.group(2) else None
        return f"bpm:{lo:g}" + (f"-{hi:g}" if hi is not None else "")
    m = re.fullmatch(r"(inst|soitin|instrument):(.+)", word, re.I)
    if m:
        import soittimet
        v = m.group(2).lower().strip("/")
        if v not in soittimet.LABEL:
            raise ValueError(f"Unknown instrument '{word}'. E.g. inst:drums, inst:drums/kick, inst:synth/pad")
        return "inst:" + v
    m = re.fullmatch(r"(tag|role|rooli):(.+)", word, re.I)
    if m:
        import soittimet
        v = m.group(2).lower()
        if v not in soittimet.TAG_LABEL:
            raise ValueError(f"Unknown tag '{word}'. E.g. tag:chords, tag:distorted, tag:female")
        return "tag:" + v
    m = re.fullmatch(r"(genre|tyylilaji):(.+)", word, re.I)
    if m:
        import genret
        v = m.group(2).lower()
        if v not in genret.LABEL:
            raise ValueError(f"Unknown genre '{word}'. E.g. genre:tech-house, genre:house-techno")
        return "genre:" + v
    m = re.fullmatch(r"(key|sävel|savel|sävellaji):(.*)", word, re.I)
    if m:
        v = re.sub(r"^[Hh](?![#♯b♭])", "B", m.group(2))   # Finnish/German H = B (B = Bb is left to the international meaning)
        near = v.endswith("~")
        k = parse_key(v.rstrip("~"), strict=False)
        if not k:
            raise ValueError(f"Unknown key '{word}'. E.g. key:Am, key:F#, key:Eb~")
        return "key:" + key_name(*k) + ("~" if near else "")
    return None


# ---------------------------------------------------------------- model

class Clap:
    """Wrapper for the CLAP model: text and audio embeddings as normalised vectors."""

    def __init__(self, name: str = DEFAULT_MODEL, model=None, processor=None):
        import torch
        self.torch = torch
        if model is None:
            from transformers import ClapModel, ClapProcessor
            print(f"Loading model {name} ...", file=sys.stderr)
            model = ClapModel.from_pretrained(name)
            processor = ClapProcessor.from_pretrained(name)
        self.model = model.eval()
        self.fe = processor.feature_extractor
        self.tok = processor.tokenizer
        # Preprocessing must match the model: fusion mode only for fusion models.
        fusion = getattr(self.model.config.audio_config, "enable_fusion", False)
        if not fusion and getattr(self.fe, "truncation", None) == "fusion":
            self.fe.truncation = "rand_trunc"
        # ROCm PyTorch shows up as a "cuda" device; otherwise CPU.
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.name = name

    @staticmethod
    def _as_tensor(out):
        # transformers versions return either a tensor or an output object
        if hasattr(out, "pooler_output") and out.pooler_output is not None:
            return out.pooler_output
        if isinstance(out, (tuple, list)):
            return out[0]
        return out

    def _norm(self, t) -> np.ndarray:
        v = t.detach().float().cpu().numpy()
        return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)

    def text(self, texts: list[str]) -> np.ndarray:
        with self.torch.inference_mode():
            inp = self.tok(texts, padding=True, truncation=True, return_tensors="pt").to(self.device)
            return self._norm(self._as_tensor(self.model.get_text_features(**inp)))

    def audio(self, windows: list[np.ndarray]) -> np.ndarray:
        with self.torch.inference_mode():
            inp = self.fe(windows, sampling_rate=SR, return_tensors="pt").to(self.device)
            return self._norm(self._as_tensor(self.model.get_audio_features(**inp)))

    def audio_features(self, feats: np.ndarray, longer: np.ndarray) -> np.ndarray:
        """Embeddings from feature-extractor output made elsewhere (the indexer's worker processes)."""
        t = self.torch
        with t.inference_mode():
            out = self.model.get_audio_features(input_features=t.from_numpy(feats).to(self.device),
                                                is_longer=t.from_numpy(longer).to(self.device))
            return self._norm(self._as_tensor(out))


# ---------------------------------------------------------------- vectors in memory
# The service took 2–2.5 GB for a 200k-sample library: the audio and name vectors are kept as float16 (200k samples:
# 2 × 410 MB -> 2 × 205 MB) and the text model as bfloat16 (~500 -> ~250 MB). Measured on such a library:
# Similar top-100 99.6 % the same, text search top-10 98.5 % the same (only near-ties swap places).
# numpy has no fast float16 product (180–330 ms for 200k rows), so dot() uses torch (4 ms, float32 numpy 10 ms);
# without torch, float32 blocks.
EMB_DTYPE = np.float16


def dot(M, q) -> np.ndarray:
    """M (n, d) vectors (float16 or float32, may be memory-mapped) · q (d,) -> (n,) float32 scores."""
    q = np.asarray(q, dtype=np.float32)
    M = np.asarray(M)
    if M.dtype == np.float32:
        return M @ q
    try:
        import warnings
        import torch
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")          # a read-only memory map: torch only reads it
            t = torch.from_numpy(M)
        return torch.mv(t, torch.from_numpy(q).to(t.dtype)).float().numpy()
    except ImportError:
        out = np.empty(len(M), dtype=np.float32)
        for s in range(0, len(M), 16384):
            out[s:s + 16384] = M[s:s + 16384].astype(np.float32) @ q
        return out


class TextClap:
    """Only CLAP's text part, for the search service (the audio encoder is not loaded at all).
    Measured: the same vectors as the full model (cosine 1.0000), loads faster."""

    def __init__(self, name: str = DEFAULT_MODEL):
        import torch
        from transformers import AutoTokenizer, ClapTextModelWithProjection
        self.torch = torch
        self.model = self._bf16(name, torch, ClapTextModelWithProjection)
        self.tok = AutoTokenizer.from_pretrained(name)
        self.name = name

    @staticmethod
    def _bf16(name: str, torch, cls):
        """The text model in bfloat16 (query vectors cosine ≥ 0.9999 to float32), from a text-only copy that is
        made once (DATA_DIR/clap-text-bf16, 239 MB). Converting on every start read the whole checkpoint,
        audio model included, and kept it in memory: 0.8 GB more than the float32 model, not less.
        Measured after a search: float32 ~750 MB, this copy ~420 MB."""
        d = DATA_DIR / "clap-text-bf16"
        mark = d / "source.txt"
        try:
            if mark.read_text(encoding="utf-8") == name:
                return cls.from_pretrained(d, torch_dtype=torch.bfloat16).eval()
        except (OSError, ValueError):
            pass
        m = cls.from_pretrained(name).eval().to(torch.bfloat16)
        try:
            tmp = d.with_name(d.name + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            m.save_pretrained(tmp)
            (tmp / "source.txt").write_text(name, encoding="utf-8")
            shutil.rmtree(d, ignore_errors=True)
            os.replace(tmp, d)
        except OSError:
            pass                                      # works without the copy, only with more memory
        return m

    def text(self, texts: list[str]) -> np.ndarray:
        with self.torch.inference_mode():
            inp = self.tok(texts, padding=True, truncation=True, return_tensors="pt")
            v = self.model(**inp).text_embeds.float().numpy()
        return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


# ---------------------------------------------------------------- helpers

def load_windows(path: Path) -> tuple[list[np.ndarray], float]:
    """Read audio as 48 kHz mono and split it into at most MAX_WINDOWS windows of 10 s."""
    import librosa
    try:
        total = librosa.get_duration(path=str(path))
        y, _ = librosa.load(str(path), sr=SR, mono=True, duration=MAX_LOAD_S)
    except Exception:  # noqa: BLE001 – libsndfile can't read it (some MP3s, odd WAV headers): ffmpeg
        y, total = _ffmpeg_load(path)
    if y.size == 0 or float(np.abs(y).max()) < 1e-5:
        return [], total
    win = int(WIN_S * SR)
    if y.size <= win:
        return [y.astype(np.float32)], total
    n = min(MAX_WINDOWS, int(np.ceil(y.size / win)))
    starts = np.linspace(0, y.size - win, n).astype(int)
    return [y[s:s + win].astype(np.float32) for s in starts], total


def _ffmpeg_load(path: Path) -> tuple[np.ndarray, float]:
    """Fallback decoder: 48 kHz mono float32 (at most MAX_LOAD_S) and the full duration from ffmpeg."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-t", str(MAX_LOAD_S),
                        "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, timeout=300)
    err = r.stderr.decode(errors="replace")
    y = np.frombuffer(r.stdout, dtype=np.float32)
    if r.returncode != 0 or y.size == 0:
        last = [ln for ln in err.strip().splitlines() if ln.strip()]
        raise RuntimeError(f"ffmpeg: {last[-1][:160] if last else 'failed'}")
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    total = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else y.size / SR
    return y, max(total, y.size / SR)


def junk_file(p: Path) -> bool:
    """macOS leftovers that look like audio: AppleDouble "._x.wav" (4 KB of metadata) and __MACOSX folders.
    Not indexed; rows of them in an older index are removed on the next run (like deleted files)."""
    return p.name.startswith("._") or "__MACOSX" in p.parts


def name_text(path: Path, roots: list[Path]) -> str:
    """Turn a path into readable text: 'Drums/Kicks/808_Kick_Boomy_C.wav' -> 'drums kicks 808 kick boomy c'."""
    rel = path
    for r in roots:
        try:
            rel = path.relative_to(r)
            break
        except ValueError:
            pass
    parts = list(rel.parent.parts[-3:]) + [rel.stem]
    s = " ".join(parts)
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)          # camelCase
    s = re.sub(r"[_\-\.\(\)\[\]]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower() or rel.stem


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 300 s: a reader (the search service loading a large index) can hold the database for minutes on
    # Windows; the indexer's commit then waits instead of failing with "database is locked"
    con = sqlite3.connect(DB_PATH, timeout=300)
    con.execute("""CREATE TABLE IF NOT EXISTS files(
        path TEXT PRIMARY KEY, mtime REAL, size INTEGER, duration REAL,
        name TEXT, audio_emb BLOB, name_emb BLOB)""")
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    # the same table as in aanipiirteet.py
    con.execute("""CREATE TABLE IF NOT EXISTS features(
        path TEXT PRIMARY KEY, mtime REAL, size INTEGER,
        centroid REAL, low REAL, flat REAL, attack REAL, tail REAL, backend TEXT)""")
    migrate_features(con)
    return con


def migrate_features(con: sqlite3.Connection) -> None:
    """Add the tempo/key columns to an old database (same as in aanipiirteet.py)."""
    have = {r[1] for r in con.execute("PRAGMA table_info(features)")}
    for col, typ in (("bpm", "REAL"), ("key", "TEXT"), ("ver", "INTEGER"), ("onset_ac", "BLOB"), ("key_sal", "BLOB")):
        if col not in have:
            con.execute(f"ALTER TABLE features ADD COLUMN {col} {typ}")
    con.commit()


def check_model(con: sqlite3.Connection, model_name: str) -> None:
    row = con.execute("SELECT value FROM meta WHERE key='model'").fetchone()
    if row is None:
        con.execute("INSERT INTO meta VALUES('model', ?)", (model_name,))
        con.commit()
    elif row[0] != model_name:
        sys.exit(f"The index was built with model {row[0]}, now using {model_name}.\n"
                 f"Delete {DB_PATH} and index again, or change SAMPLEHAKU_MODEL.")


def get_roots(con: sqlite3.Connection) -> list[str]:
    """The index's library roots (polut.py). An older index without stored roots: [] (= one root,
    the common folder of all paths)."""
    row = con.execute("SELECT value FROM meta WHERE key='roots'").fetchone()
    try:
        return [str(r) for r in json.loads(row[0])] if row else []
    except (ValueError, TypeError):
        return []


def set_roots(con: sqlite3.Connection, roots: list[str]) -> None:
    con.execute("INSERT OR REPLACE INTO meta VALUES('roots', ?)", (json.dumps(sorted(set(roots)), ensure_ascii=False),))


def ensure_roots(con: sqlite3.Connection) -> list[str]:
    """Stored roots; an older index gets its current single root stored first, so adding a
    second library does not change the first one's packs."""
    roots = get_roots(con)
    if not roots:
        paths = [r[0] for r in con.execute("SELECT path FROM files")]
        if paths:
            roots = [polut.fallback_root(paths)]
            set_roots(con, roots)
    return roots


def remove_root(con: sqlite3.Connection, root: str) -> int:
    """Settings > Remove folder: the folder (and any library roots inside it) leaves the library and its
    samples leave the index. Files on disk are never touched; the user's corrections and likes are kept
    (they come back if the folder is added again). A folder inside a remaining root keeps its samples:
    they are still in the library, only the root list changes. -> how many samples left the index."""
    root = root.rstrip("/") or root
    roots = ensure_roots(con)
    if root not in roots:
        raise ValueError(f"Not a library folder: {root}")
    left = [r for r in roots if r != root and not r.startswith(root + "/")]
    if not left:
        raise ValueError("This is the only library folder. Add another one first.")
    n = 0
    if not any(root.startswith(r + "/") for r in left):
        lo, hi = root + "/", root + "0"                  # every path below root sorts between these ("/" + 1 = "0")
        n = con.execute("SELECT COUNT(*) FROM files WHERE path >= ? AND path < ?", (lo, hi)).fetchone()[0]
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("files", "features", "content_hash"):
            if table in have:
                con.execute(f"DELETE FROM {table} WHERE path >= ? AND path < ?", (lo, hi))
    set_roots(con, left)
    con.commit()
    return n


def fmt_dur(s: float) -> str:
    return f"{s:5.1f}s" if s < 60 else f"{int(s // 60)}:{int(s % 60):02d} "


# ---------------------------------------------------------------- indexing

def _load_one(p: Path):
    """load_windows in a worker process -> (windows, duration, error name or None)."""
    try:
        return (*load_windows(p), None)
    except Exception as e:  # noqa: BLE001 – reported by the indexer
        return [], 0.0, f"{e.__class__.__name__}: {str(e)[:160]}" if str(e) else e.__class__.__name__


_FE = {}


def _load_mel(job):
    """In a worker process: load_windows + CLAP's feature extractor (the mel spectrogram, which took
    ~80 % of the indexing time in the main process, measured with an NVIDIA GPU). Each window is
    processed on its own, which gives the same numbers as the batched call (windows are at most
    10 s, so rand_trunc never crops). -> (input_features | None, is_longer | None, duration, error)."""
    p, name, truncation = job
    w, dur, err = _load_one(p)
    if not w:
        return None, None, dur, err
    fe = _FE.get(name)
    if fe is None:
        from transformers import ClapFeatureExtractor
        fe = _FE[name] = ClapFeatureExtractor.from_pretrained(name)
        fe.truncation = truncation
    try:
        out = fe(w, sampling_rate=SR, return_tensors="np")
    except Exception as e:  # noqa: BLE001
        return None, None, dur, e.__class__.__name__
    return out["input_features"].astype(np.float32), np.asarray(out["is_longer"], dtype=bool), dur, err


def cmd_index(clap: Clap, dirs: list[str], reuse: list[str] | None = None) -> None:
    con = db()
    check_model(con, clap.name)
    roots = [Path(d).expanduser().resolve() for d in dirs]
    lib = ensure_roots(con)
    for r in roots:
        if r.is_dir():
            lib = polut.add_root(lib, polut.norm(r))
    set_roots(con, lib)
    con.commit()
    known = {p: (m, s) for p, m, s in con.execute("SELECT path, mtime, size FROM files")}

    todo, seen = [], set()
    for root in roots:
        if not root.is_dir():
            print(f"Skipping, not a folder: {root}", file=sys.stderr)
            continue
        for p in root.rglob("*"):
            if p.suffix.lower() in AUDIO_EXT and not junk_file(p) and p.is_file():
                sp = polut.norm(p)
                seen.add(sp)
                st = p.stat()
                if known.get(sp) != (st.st_mtime, st.st_size):
                    todo.append((p, st))

    # remove files from the index that were deleted under these folders
    gone = [p for p in known if p not in seen and any(p.startswith(polut.norm(r) + "/") for r in roots)]
    con.executemany("DELETE FROM files WHERE path=?", [(p,) for p in gone])
    con.commit()

    print(f"{len(seen)} audio files, {len(todo)} new or changed, {len(gone)} removed.")
    import siirto
    if todo and reuse:
        todo = siirto.reuse(con, reuse, todo, roots, clap)
    todo, dups = siirto.split_dups(con, todo) if todo else (todo, [])
    _index_files(con, clap, todo, roots)
    siirto.fill_dups(con, dups, roots, clap)
    if (todo or dups) and not os.environ.get("FETCH_PORTABLE"):
        print("Next, compute the sound features (+dark, +tight ...):  samplehaku-piirteet")


def _index_files(con, clap: Clap, todo: list, roots: list[Path]) -> None:
    if not todo:
        return

    import rinnakkain
    jobs = rinnakkain.default_jobs()
    batch = BATCH_GPU if clap.device == "cuda" else BATCH
    print(f"CLAP on {clap.device}, decoding in {jobs} processes.")
    trunc = getattr(clap.fe, "truncation", "rand_trunc")
    loaded = rinnakkain.ordered_map(_load_mel, [(p, clap.name, trunc) for p, _ in todo], jobs,
                                    ahead=max(2 * batch, jobs * 3))
    t0, done, failed = time.time(), 0, 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        feats, longer, owners, rows = [], [], [], []
        for j, (p, st) in enumerate(chunk):
            f, lg, dur, err = next(loaded)
            if err:  # broken or unsupported file
                failed += 1
                print(f"  ! {p.name}: {err}", file=sys.stderr)
            if f is None:  # silent or unreadable: mark as processed so it isn't retried every time
                con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,NULL,NULL)",
                            (polut.norm(p), st.st_mtime, st.st_size, dur, ""))
                continue
            feats.append(f)
            longer.append(lg)
            owners += [j] * len(f)
            rows.append((j, p, st, dur))
        if not rows:
            con.commit()
            done += len(chunk)
            continue
        a = clap.audio_features(np.concatenate(feats), np.concatenate(longer))
        names = [name_text(p, roots) for _, p, _, _ in rows]
        ne = clap.text(names)
        owners = np.array(owners)
        for k, (j, p, st, dur) in enumerate(rows):
            v = a[owners == j].mean(axis=0)
            v /= np.linalg.norm(v) + 1e-9
            con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?)",
                        (polut.norm(p), st.st_mtime, st.st_size, dur, names[k],
                         v.astype(np.float32).tobytes(), ne[k].astype(np.float32).tobytes()))
        con.commit()
        done += len(chunk)
        rate = done / (time.time() - t0)
        eta = (len(todo) - done) / max(rate, 1e-9)
        print(f"\r  {done}/{len(todo)}  ({rate:.1f} files/s, about {eta / 60:.0f} min left)   ",
              end="", flush=True)
    print(f"\nDone. Failed: {failed}.")


# ---------------------------------------------------------------- search

def parse_tags(line: str) -> tuple[str, list[str]]:
    """Split +tags from the search text: 'kick +dark +tight' -> ('kick', ['dark', 'tight'])."""
    words, tags = [], []
    for w in normalize_type_words(line).split():
        f = parse_filter(w)
        if f:
            tags.append(f)
        elif w.startswith("+") and w[1:].lower() in TYPE_WORDS:
            tags.append("type:" + {1: "loop", 0: "oneshot"}[TYPE_WORDS[w[1:].lower()]])
        elif w.startswith("+") and len(w) > 1:
            t = w[1:].lower()
            if t not in TAGS:
                raise ValueError(f"Unknown feature +{t}. Options: "
                                 + " ".join("+" + k for k in TAGS) + " +loop +oneshot")
            tags.append(t)
        else:
            words.append(w)
    return " ".join(words), tags


INDEX_CACHE = DATA_DIR / "indeksi-valimuisti"


def _shared_sets(sets: list) -> list:
    """200k tag sets, but only a few hundred different ones: one shared frozenset each (~90 MB -> ~2 MB).
    Read-only from here on (a frozenset)."""
    pool: dict = {}
    return [pool.setdefault(frozenset(x), frozenset(x)) for x in sets]


def _shared(val: tuple) -> tuple:
    """(instrument array, estimated, tag sets) with the instrument names and tag sets shared."""
    inst, est, roles = val
    names: dict = {}
    inst = np.array([names.setdefault(x, x) for x in inst], dtype=object)
    return inst, est, _shared_sets(roles)


class Index:
    def __init__(self, con: sqlite3.Connection):
        """Load the index. Fast path: a ready cache (audio vectors as .npy files that are
        memory-mapped straight from disk -> no copy, memory can be reclaimed). Slow path
        (SQLite, ~1.6 s) only when the database or this code has changed."""
        key = self._cache_key()
        if not self._load_cache(key):
            self._build(con)
            # while indexing, the database changes constantly: save only after it has been quiet for 2 min
            self._unsaved_key = key
            self.save_cache_if_quiet()
        self._finish()

    def save_cache_if_quiet(self) -> bool:
        """Save a cache that couldn't be saved at load time (the database was still changing), once the
        database is unchanged since this index was built and has been quiet for 2 min. -> saved now?"""
        key = getattr(self, "_unsaved_key", None)
        try:
            if not key or key != self._cache_key() or time.time() - DB_PATH.stat().st_mtime <= 120:
                return False
        except OSError:
            return False
        self._unsaved_key = None
        self._save_cache(key)
        return True

    # ---------------------------------------------------------------- cache
    @staticmethod
    def _cache_key() -> str:
        st = DB_PATH.stat()
        models = "|".join(str(m.stat().st_mtime_ns) if m.exists() else "-" for m in (KEY_MODEL, TYPE_MODEL))
        return f"{st.st_mtime_ns}|{st.st_size}|{Path(__file__).stat().st_mtime_ns}|{models}|v8"

    _RAW = ("paths", "roots", "dur", "F", "has_features", "bpm", "key_pc", "key_mode", "bpm_est", "key_est", "stype", "key_spell")

    def _load_cache(self, key: str) -> bool:
        import pickle
        try:
            with open(INDEX_CACHE / "meta.pickle", "rb") as f:
                meta = pickle.load(f)
            if meta.get("key") != key:
                return False
            for k in self._RAW:
                setattr(self, k, meta[k])
            files = meta.get("files", {"A": "A.npy", "N": "N.npy"})
            self.A = np.load(INDEX_CACHE / files["A"], mmap_mode="r")
            self.N = np.load(INDEX_CACHE / files["N"], mmap_mode="r")
            return len(self.A) == len(self.paths) == len(self.N) and self.A.dtype == self.N.dtype == EMB_DTYPE
        except (OSError, ValueError, KeyError, EOFError, pickle.UnpicklingError):
            return False

    def _save_cache(self, key: str) -> None:
        """Write new files and swap meta.pickle last -> an interrupted write never leaves a broken cache.
        The arrays get names of their own per version: on Windows a file that a running service has
        memory-mapped can't be replaced or deleted (WinError 5), so old versions are removed only
        when nothing holds them any more."""
        import hashlib
        import pickle
        try:
            INDEX_CACHE.mkdir(parents=True, exist_ok=True)
            tag = hashlib.sha1(f"{key}|{os.getpid()}|{time.time()}".encode()).hexdigest()[:12]
            files = {}
            for name, arr in (("A", self.A), ("N", self.N)):
                files[name] = f"{name}.{tag}.npy"
                tmp = INDEX_CACHE / f"{name}.{tag}.tmp.npy"
                np.save(tmp, np.ascontiguousarray(arr, dtype=EMB_DTYPE))
                os.replace(tmp, INDEX_CACHE / files[name])
            tmp = INDEX_CACHE / f"meta.{tag}.tmp"
            with open(tmp, "wb") as f:
                pickle.dump({"key": key, "files": files} | {k: getattr(self, "_raw", {}).get(k, getattr(self, k)) for k in self._RAW}, f,
                            protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, INDEX_CACHE / "meta.pickle")
            # use the memory-mapped copies right away (frees the copy read from SQLite)
            self.A = np.load(INDEX_CACHE / files["A"], mmap_mode="r")
            self.N = np.load(INDEX_CACHE / files["N"], mmap_mode="r")
            for old in INDEX_CACHE.glob("*.npy"):     # older versions; skipped while another process maps them
                if old.name not in files.values() and not old.name.endswith(".tmp.npy"):
                    try:
                        old.unlink()
                    except OSError:
                        pass
        except OSError as e:
            print(f"(could not save the index cache: {e})", file=sys.stderr)

    # ---------------------------------------------------------------- from SQLite
    def _build(self, con: sqlite3.Connection) -> None:
        migrate_features(con)
        cols = ", ".join(f"f.{c}" for c in FEATURES)
        rows = con.execute(f"""SELECT x.path, x.duration, x.audio_emb, x.name_emb, {cols}, f.bpm, f.key, f.onset_ac, f.key_sal
            FROM files x LEFT JOIN features f ON f.path = x.path AND f.mtime = x.mtime AND f.size = x.size
            WHERE x.audio_emb IS NOT NULL""").fetchall()
        if not rows:
            sys.exit("The index is empty. Run first:  samplehaku.py index ~/Samples")
        self.paths = [r[0] for r in rows]
        self.roots = get_roots(con)
        self.dur = np.array([r[1] for r in rows], dtype=np.float32)
        self.A = np.stack([np.frombuffer(r[2], dtype=np.float32) for r in rows]).astype(EMB_DTYPE)
        self.N = np.stack([np.frombuffer(r[3], dtype=np.float32) for r in rows]).astype(EMB_DTYPE)
        self.F = {c: np.array([np.nan if r[4 + j] is None else r[4 + j] for r in rows], dtype=np.float64)
                  for j, c in enumerate(FEATURES)}
        self.has_features = bool(np.isfinite(self.F["centroid"]).any())

        # tempo and key: from the name, otherwise estimated from the audio (aanipiirteet.py)
        nf = len(FEATURES)
        self.bpm = np.full(len(rows), np.nan)
        self.key_pc = np.full(len(rows), -1, dtype=np.int8)
        self.key_mode = np.full(len(rows), -1, dtype=np.int8)
        # estimated from the audio (not from the file name): shown with a ~ sign
        self.bpm_est = np.zeros(len(rows), dtype=bool)
        self.key_est = np.zeros(len(rows), dtype=bool)
        self.key_spell = np.zeros(len(rows), dtype=np.int8)   # key_spelling(): as the name writes it
        onset = {}
        sal = {}
        for i, r in enumerate(rows):
            nb, nk = name_bpm_key(r[0])
            ab, ak, oa = r[4 + nf], r[5 + nf], r[6 + nf]
            if np.isfinite(nb):
                self.bpm[i] = nb
            elif sample_type_why(r[0], float(r[1]), np.nan) != (1, "name"):
                pass        # only loops named as loops get a tempo from the audio:
                            # on vocals/FX/risers that are loops only by default it was right for ~70 % only
            elif oa is not None:                      # onset data (b"" = computed, no pulse): decided below
                if oa:
                    onset[i] = np.frombuffer(oa, dtype=np.float16)
            elif ab is not None:                      # older rows without onset data: the old estimate
                self.bpm[i], self.bpm_est[i] = ab, True
            ks = r[7 + nf]
            if nk is None:
                if ks is not None:                    # salience (b"" = no pitch): decided below
                    if ks:
                        sal[i] = np.frombuffer(ks, dtype=np.float16)
                elif ak:                              # older rows without salience: the old estimate
                    nk = parse_audio_key(ak)
                    self.key_est[i] = nk is not None
            if nk:
                self.key_pc[i], self.key_mode[i] = nk
                if not self.key_est[i]:
                    self.key_spell[i] = key_spelling(r[0], nk[0])
        self._tempo_from_audio(onset)
        if sal:
            ids = list(sal)
            for i, (k, p_) in zip(ids, keys_from_salience([sal[i] for i in ids])):
                if k and p_ >= KEY_MIN_P:
                    self.key_pc[i], self.key_mode[i], self.key_est[i] = k[0], k[1], True
        why = [sample_type_why(p, float(self.dur[i]), self.bpm[i]) for i, p in enumerate(self.paths)]
        self.stype = np.array([w[0] for w in why], dtype=np.int8)
        # what the name doesn't settle: one-shot when the sound says so with confidence (TYPE_MODEL)
        dflt = np.array([i for i, w in enumerate(why) if w[1] == "default"], dtype=int)
        # and a "loop" by name shorter than any loop that sounds like a hit (SHORT_LOOP_S)
        tiny = np.array([i for i, w in enumerate(why) if w == (1, "name") and self.dur[i] < SHORT_LOOP_S], dtype=int)
        ask = np.concatenate([dflt, tiny])
        if ask.size and type_model() is not None and self.has_features:
            X = type_features(self.A[ask], self.dur[ask], {k: v[ask] for k, v in self.F.items()})
            self.stype[ask[type_probs(X) >= TYPE_MIN_P]] = 0

    def _tempo_from_audio(self, onset: dict[int, np.ndarray]) -> None:
        """Tempo for files without one in the name (see tempo_from_ac). The prior = the named loops' tempos
        in the same genre(s) (the pack's genres; all named loops if fewer than 50)."""
        if not onset:
            return
        import genret
        gs = self.genres()
        named = [i for i in range(len(self.paths)) if np.isfinite(self.bpm[i]) and not self.bpm_est[i]
                 and sample_type(self.paths[i], float(self.dur[i]), self.bpm[i])[0] == 1]
        by_genre: dict[str, list[float]] = {}
        for i in named:
            for g in gs[i]:
                if g in genret.GROUP_OF:
                    by_genre.setdefault(g, []).append(float(self.bpm[i]))
        overall = tempo_prior([self.bpm[i] for i in named])
        priors: dict[tuple, np.ndarray] = {}
        for i, ac in onset.items():
            key = tuple(sorted(g for g in gs[i] if g in genret.GROUP_OF))
            if key not in priors:
                vals = [b for g in key for b in by_genre.get(g, [])]
                priors[key] = tempo_prior(vals) if len(vals) >= 50 else overall
            b, conf = tempo_from_ac(ac, float(self.dur[i]), priors[key])
            if b is not None and conf >= TEMPO_MIN_CONF:
                self.bpm[i], self.bpm_est[i] = b, True

    def _finish(self) -> None:
        """User corrections and tempo rules on top of the raw values (on every start, ms). The raw values
        stay in self._raw for the cache: a correction undone later must not stay baked in."""
        self._raw = {k: getattr(self, k) for k in self._RAW}
        self.bpm, self.bpm_est, self.stype = self.bpm.copy(), self.bpm_est.copy(), self.stype.copy()
        self.fixes = load_fixes()
        pos = {p: i for i, p in enumerate(self.paths)}
        for p, f in self.fixes.items():
            if p in pos and f.get("type") in TYPE_IDS:
                self.stype[pos[p]] = TYPE_IDS[f["type"]]
        # tempo estimated from the audio only for loops: a single hit or a drone has no tempo,
        # and an estimate (e.g. an 808 kick at 172 bpm) would only confuse tempo searches. A tempo in the name stays.
        drop = self.bpm_est & (self.stype != 1)
        self.bpm[drop] = np.nan
        self.bpm_est[drop] = False
        # the estimate sometimes hits a multiple of the tempo (987 bpm = 123 bpm × 8): fold into 60–200
        e = self.bpm_est & np.isfinite(self.bpm)
        b = self.bpm[e]
        b = np.where(b > 1, b, np.nan)                  # 0 / negative = no estimate
        for _ in range(16):
            b = np.where(b >= 200, b / 2, np.where(b < 60, b * 2, b))
        self.bpm[e] = np.round(b, 1)
        self.bpm_est[e & ~np.isfinite(self.bpm)] = False
        self.has_bk = bool(np.isfinite(self.bpm).any() or (self.key_pc >= 0).any())

    def genres(self) -> list[set]:
        """Genres per sample (groups included). See genret.py; genre is a property of the pack."""
        if getattr(self, "_genres", None) is None:
            import pickle
            import genret
            # ~2.6 s to compute -> cached; key: index + rules + the user's pack genres
            import pakkaukset
            import soittimet
            key = self._cache_key() + "".join(str(Path(m.__file__).stat().st_mtime_ns)
                                              for m in (genret, pakkaukset, soittimet)) + \
                json.dumps(genret.load_cfg(), sort_keys=True)
            cache = INDEX_CACHE / "genret.pickle"
            try:
                with open(cache, "rb") as f:
                    k, val = pickle.load(f)
                if k == key:
                    self._genres, self.genre_est, self.pack_info = val
                    return self._genres
            except (OSError, ValueError, EOFError, pickle.UnpicklingError):
                pass
            val = genret.classify(self.paths, self.A, roots=self.roots)
            self._genres, self.genre_est, self.pack_info = val
            try:
                INDEX_CACHE.mkdir(parents=True, exist_ok=True)
                with open(str(cache) + ".tmp", "wb") as f:
                    pickle.dump((key, val), f, protocol=pickle.HIGHEST_PROTOCOL)
                os.replace(str(cache) + ".tmp", cache)
            except OSError:
                pass
        return self._genres

    def instruments(self):
        """-> (instrument classes, estimated from audio, roles). Computed once (~4 s) and saved
        to disk; recomputed only when the library or the classification rules change."""
        if getattr(self, "_inst", None) is not None:
            return self._inst
        import hashlib
        import pickle
        import pakkaukset
        import soittimet
        pos = {p: i for i, p in enumerate(self.paths)}
        fixed = {pos[p]: soittimet.migrate(f["inst"]) for p, f in self.fixes.items()
                 if p in pos and soittimet.migrate(f.get("inst", "")) in soittimet.LABEL}
        # Two steps, cached separately: the neighbour vote (1.5 min for 200k samples) depends only on the
        # library, the rules and the corrected INSTRUMENTS; type and tag corrections, the common ones,
        # used to throw it away too, and the next start of the service then took that long.
        base = "\n".join(self.paths) + "\n".join(self.roots) + "".join(
            str(Path(m.__file__).stat().st_mtime_ns) for m in (soittimet, pakkaukset))
        key1 = hashlib.sha1((base + json.dumps(sorted(fixed.items()))).encode()).hexdigest()
        tagfix = {p: {k: v for k, v in f.items() if k != "len"} for p, f in self.fixes.items()}   # len = playback only
        key2 = hashlib.sha1((key1 + json.dumps(tagfix, sort_keys=True)).encode()).hexdigest()
        cache = DATA_DIR / "soittimet.pickle"
        saved = {}
        try:
            with open(cache, "rb") as f:
                saved = pickle.load(f)
            if not isinstance(saved, dict):
                saved = {}
        except (OSError, ValueError, pickle.UnpicklingError, EOFError, AttributeError):
            saved = {}
        if saved.get("key2") == key2:
            val = saved["val"]
            self._inst, self.tag_est = _shared(val[:3]), _shared_sets(val[3])
            self._drop_drum_keys(self._inst[0])
            return self._inst
        if saved.get("key1") == key1:
            inst, est, roles = saved["voted"]
        else:
            inst, est, roles = soittimet.classify(self.paths, self.A, fixed=fixed, roots=self.roots)
        voted = (inst.copy(), est.copy(), [set(r) for r in roles])
        for p, f in self.fixes.items():
            if p in pos and ("tags" in f or "roles" in f):
                roles[pos[p]] = set(f.get("tags", f.get("roles", [])))
        # character and sound tags from the audio (only reliable ones, see soittimet.SOUND_TAGS); corrected ones untouched
        tag_est = soittimet.estimate_tags(inst, roles, self.A)
        for p, f in self.fixes.items():
            if p in pos and "tags" in f:
                tag_est[pos[p]] = set()
        for i, extra in enumerate(tag_est):
            roles[i] |= extra
        self.tag_est = tag_est
        val = (inst, est, roles)
        try:
            tmp = str(cache) + f".{os.getpid()}.tmp"
            with open(tmp, "wb") as f:
                pickle.dump({"key1": key1, "voted": voted, "key2": key2, "val": val + (tag_est,)}, f,
                            protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, cache)
        except OSError:
            pass
        self._inst, self.tag_est = _shared(val), _shared_sets(tag_est)
        self._drop_drum_keys(inst)
        return self._inst

    def _drop_drum_keys(self, inst) -> None:
        """No key from the audio for drums and percussion (KEY_NO_AUDIO); a key in the name stays.
        Needs the instrument classes, so done here, from the raw values (a corrected instrument gets its key back)."""
        if not hasattr(self, "key_est"):
            return
        raw = getattr(self, "_raw", {})
        pc, mode, est = (raw.get(k, getattr(self, k)) for k in ("key_pc", "key_mode", "key_est"))
        drop = est & np.array([str(x).split("/")[0] in KEY_NO_AUDIO for x in inst], dtype=bool)
        self.key_pc, self.key_mode, self.key_est = np.where(drop, -1, pc).astype(np.int8), mode.copy(), est & ~drop
        self.key_mode[drop] = -1

    def _rel(self, tag: str) -> np.ndarray:
        """The feature's rank in the library 0..1 in the tag's direction (1 = most 'dark' etc.)."""
        feat, sign = TAGS[tag]
        v = self.F[feat] * sign
        ok = np.isfinite(v)
        r = np.full(v.shape, np.nan)
        if ok.sum() > 1:
            r[ok] = v[ok].argsort().argsort() / (ok.sum() - 1)
        return r

    @staticmethod
    def _bpm_range(t: str) -> tuple[float, float, bool]:
        """'bpm:120' -> (116.4, 123.6, True = also half/double), 'bpm:118-126' -> (118, 126, False)."""
        v = t[4:]
        if "-" in v:
            lo, hi = sorted(float(x) for x in v.split("-"))
            return lo, hi, False
        x = float(v)
        d = max(x * BPM_TOL, 1.5)
        return x - d, x + d, True

    def key_label(self, i: int) -> str:
        """The key as shown ('' = unknown): spelled as the file name writes it ('D#m', not 'Ebm')."""
        if self.key_pc[i] < 0:
            return ""
        spell = int(self.key_spell[i]) if getattr(self, "key_spell", None) is not None else 0
        return key_name(int(self.key_pc[i]), int(self.key_mode[i]), spell)

    def bpm_mask(self, t: str) -> np.ndarray:
        lo, hi, octaves = self._bpm_range(t)
        b = np.nan_to_num(self.bpm, nan=-1.0)
        m = (b >= lo) & (b <= hi)
        if octaves:
            for f in (0.5, 2.0):
                m |= (b >= lo * f) & (b <= hi * f)
        return m

    def key_mask(self, t: str) -> np.ndarray:
        v = t[4:]
        near = v.endswith("~")
        pc, mode = parse_key(v.rstrip("~"), strict=False)
        if near:
            ok = compatible_keys(pc, mode)
        elif mode == -1:
            ok = {(pc, 0), (pc, 1), (pc, -1)}
        else:
            ok = {(pc, mode)}
        ok_roots = {p for p, _ in ok}
        m = np.zeros(len(self.paths), dtype=bool)
        for i in np.nonzero(self.key_pc >= 0)[0]:
            kp, km = int(self.key_pc[i]), int(self.key_mode[i])
            # root only ("Bass_C2"): accepted if the note fits one of the allowed keys
            m[i] = (kp, km) in ok or (km == -1 and kp in ok_roots)
        return m

    def tag_mask(self, tags: list[str]) -> np.ndarray:
        mask = np.ones(len(self.paths), dtype=bool)
        for t in tags:
            if t.startswith("bpm:"):
                mask &= self.bpm_mask(t)
            elif t.startswith("key:"):
                mask &= self.key_mask(t)
            elif t.startswith("inst:"):
                inst, _, _ = self.instruments()
                node = t[5:]
                mask &= np.array([x == node or x.startswith(node + "/") for x in inst])
            elif t.startswith("tag:"):
                _, _, tg = self.instruments()
                mask &= np.array([t[4:] in r for r in tg])
            elif t.startswith("genre:"):
                g = t[6:]
                mask &= np.array([g in gs for gs in self.genres()])
            elif t.startswith("type:"):
                mask &= self.stype == {"type:loop": 1, "type:oneshot": 0}[t]
            elif self.has_features:
                r = self._rel(t)
                mask &= np.nan_to_num(r, nan=-1.0) >= 1 - TAG_SHARE
        return mask

    def tag_scores(self, tags: list[str]) -> np.ndarray:
        """Order when searching with tags only: the more clearly it matches the tag, the higher."""
        parts = [self._rel(t) for t in tags if ":" not in t]
        for t in tags:
            if t.startswith("bpm:"):            # closest to the requested tempo first
                lo, hi, _ = self._bpm_range(t)
                c = (lo + hi) / 2
                b = self.bpm.copy()
                b = np.where(np.abs(b * 2 - c) < np.abs(b - c), b * 2, b)
                b = np.where(np.abs(b / 2 - c) < np.abs(b - c), b / 2, b)
                parts.append(1 - np.minimum(np.abs(b - c) / max(hi - lo, 1), 1))
        if not parts:                           # key: only -> keys read from the name first, then the short ones
            return np.where(self.key_est, 0.5, 1.0) - self.dur / 1e4
        return np.nan_to_num(np.mean(parts, axis=0), nan=0.0)

    def rank(self, scores: np.ndarray, n: int, min_dur=None, max_dur=None,
             in_path=None, exclude=None, tags=None) -> list[tuple[int, float]]:
        mask = np.ones(len(self.paths), dtype=bool)
        if tags:
            if not self.has_features and any(":" not in t for t in tags):
                print("(Sound features have not been computed yet: run samplehaku-piirteet. "
                      "+tags are ignored.)")
            mask &= self.tag_mask(tags)
        if min_dur is not None:
            mask &= self.dur >= min_dur
        if max_dur is not None:
            mask &= self.dur <= max_dur
        if in_path:
            needle = in_path.lower()
            mask &= np.array([needle in p.lower() for p in self.paths])
        if exclude is not None:
            mask[exclude] = False
        s = np.where(mask, scores, -np.inf)
        top = np.argsort(-s)[:n]
        return [(int(i), float(s[i])) for i in top if np.isfinite(s[i])]


def translate(query: str) -> str:
    """Translate the query into English with a local language model, if one is configured."""
    if not LLM_MODEL:
        return query
    prompt = ("Rewrite this sound/sample search query as a short English description of the sound, "
              "using music-production vocabulary. Reply with the description only.\n\nQuery: " + query)
    body = json.dumps({"model": LLM_MODEL, "temperature": 0,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(LLM_URL.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.load(r)["choices"][0]["message"]["content"]
        out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().strip('"')
        return out.splitlines()[0] if out else query
    except Exception as e:
        print(f"(language model did not respond: {e}; searching with the original)", file=sys.stderr)
        return query


def text_scores(clap: Clap, idx: Index, query: str, tags=None) -> np.ndarray:
    if not query.strip():
        if not tags:
            raise ValueError("Empty search.")
        return idx.tag_scores(tags)
    q = clap.text([query])[0]
    return W_AUDIO * dot(idx.A, q) + W_NAME * dot(idx.N, q)


def similar_scores(clap: Clap, idx: Index, path: str) -> tuple[np.ndarray, int | None]:
    p = polut.norm(Path(path).expanduser().resolve())
    if p in idx.paths:
        i = idx.paths.index(p)
        return dot(idx.A, idx.A[i]), i
    w, _ = load_windows(Path(p))
    if not w:
        sys.exit("The file is silent or could not be read.")
    v = clap.audio(w).mean(axis=0)
    return dot(idx.A, v / np.linalg.norm(v)), None


def fmt_bpm_key(idx: Index, i: int) -> str:
    """' 124bpm Am  ' or '~98bpm  ~F#m' (~ = estimated from the audio, not from the file name)."""
    if not np.isfinite(idx.bpm[i]) and idx.key_pc[i] < 0:
        return " " * 13 if idx.has_bk else ""
    b = (("~" if idx.bpm_est[i] else " ") + f"{idx.bpm[i]:3.0f}bpm") if np.isfinite(idx.bpm[i]) else " " * 7
    k = (("~" if idx.key_est[i] else "") + idx.key_label(i) if idx.key_pc[i] >= 0 else "")
    return f"{b} {k:<5s}"


def fmt_feat(idx: Index, i: int) -> str:
    c, t = idx.F["centroid"][i], idx.F["tail"][i]
    bk = fmt_bpm_key(idx, i)
    if not np.isfinite(c):
        return f"{bk} " if bk else ""
    c_s = f"{c / 1000:4.1f}k" if c >= 1000 else f"{c:4.0f} "
    return f"{c_s}Hz {t:4.1f}s  " + (f"{bk} " if bk else "")


def show(idx: Index, hits: list[tuple[int, float]]) -> None:
    if not hits:
        print("No matches. Try looser filters.")
        return
    home = str(Path.home())
    for k, (i, s) in enumerate(hits, 1):
        p = idx.paths[i].replace(home, "~", 1)
        print(f"{k:3d}. {s:5.3f}  {fmt_dur(idx.dur[i])}  {fmt_feat(idx, i)}{p}")


# ---------------------------------------------------------------- playback

class Player:
    CMDS = [["pw-play"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["paplay"], ["aplay", "-q"]]

    def __init__(self):
        self.cmd = next((c for c in self.CMDS if shutil.which(c[0])), None)
        self.proc = None

    def play(self, path: str) -> None:
        self.stop()
        if not self.cmd:
            print("No player found (pw-play, ffplay, paplay or aplay).")
            return
        self.proc = subprocess.Popen(self.cmd + [path], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


# ---------------------------------------------------------------- interactive mode

HELP = """\
  <text>          search by description, e.g. "warm analog bass pluck"
  <text> +tag     narrow by a sound feature, e.g. "kick +dark +tight" or just "+bass +long"
                  {tags}
                  (relative to your own library; tags can be edited: {tags_path})
  <number>        play a result
  +loop / +oneshot  (also +shot, +one shot) only loops / single hits (from name, folder or length)
  bpm:120         tempo ±3 % (also half/double time), bpm:118-126 = range
  key:Am          key; key:A = A major or minor; key:Am~ = also compatible keys
                  (C, Dm, Em: Camelot neighbours). From the file name or estimated from audio (~)
  s <number>      find sounds like a result (also "s 3 +bright", "s 3 key:Am~")
  c <number>      copy the path to the clipboard (wl-copy/xclip)
  .               stop playback
  max 2 / min 4   limit by duration in seconds (max off removes it)
  in kicks        limit by path (in off removes it)
  q               quit"""


def copy_clip(text: str) -> None:
    for c in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(c[0]):
            subprocess.run(c, input=text.encode())
            print("Kopioitu.")
            return
    print(text)


def interactive(clap: Clap, idx: Index, n: int) -> None:
    player = Player()
    hits: list[tuple[int, float]] = []
    filt = {"min_dur": None, "max_dur": None, "in_path": None}
    print(f"{len(idx.paths)} sounds in the index. Type a search, ? = help.")
    while True:
        try:
            line = input("search> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        cmd, _, arg = line.partition(" ")
        try:
            if line in ("q", "quit", "exit"):
                break
            elif line == "?":
                tl = "  ".join("/".join("+" + n for n in g) for g in TAG_GROUPS)
                print(HELP.format(tags=tl, tags_path=TAGS_PATH))
            elif line == ".":
                player.stop()
            elif line.isdigit() and hits:
                i = hits[int(line) - 1][0]
                print(f"♪ {Path(idx.paths[i]).name}")
                player.play(idx.paths[i])
            elif cmd == "s" and arg.split()[:1] and arg.split()[0].isdigit() and hits:
                num, _, rest = arg.partition(" ")
                _, tags = parse_tags(rest)
                src = hits[int(num) - 1][0]
                hits = idx.rank(dot(idx.A, idx.A[src]), n, exclude=src, tags=tags, **filt)
                show(idx, hits)
            elif cmd == "c" and arg.isdigit() and hits:
                copy_clip(idx.paths[hits[int(arg) - 1][0]])
            elif cmd in ("min", "max") and arg:
                filt[f"{cmd}_dur"] = None if arg == "off" else float(arg)
                print(f"Rajaus: {filt}")
            elif cmd == "in" and arg:
                filt["in_path"] = None if arg == "off" else arg
                print(f"Rajaus: {filt}")
            else:
                text, tags = parse_tags(line)
                q = translate(text) if text else text
                if q != text:
                    print(f"→ {q}")
                hits = idx.rank(text_scores(clap, idx, q, tags), n, tags=tags, **filt)
                show(idx, hits)
        except (IndexError, ValueError) as e:
            msg = str(e)
            print(msg if msg.startswith(("Unknown", "Empty")) else
                  "Unknown command or number, ? = help.")
    player.stop()


# ---------------------------------------------------------------- main program

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Semantic search for a sound and sample library (CLAP).")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Hugging Face model (default {DEFAULT_MODEL})")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("index", help="index folders")
    p.add_argument("dirs", nargs="+")
    p.add_argument("--reuse", action="append", metavar="OTHER.db",
                   help="copy the analysis of identical files (same content) from another index")

    for name, h in (("search", "search by text"), ("similar", "find similar")):
        p = sub.add_parser(name, help=h)
        p.add_argument("query", help="search text" if name == "search" else "audio file")
        p.add_argument("-n", type=int, default=20)
        p.add_argument("--min-dur", type=float)
        p.add_argument("--max-dur", type=float)
        p.add_argument("--in", dest="in_path", help="limit by part of the path, e.g. --in drums")
        p.add_argument("--play", action="store_true", help="play the best match")
        p.add_argument("-t", "--tag", action="append",
                       help="sound feature, e.g. -t dark -t tight (also in the search text: +dark)")
        p.add_argument("--bpm", help="tempo, e.g. --bpm 120 or --bpm 118-126 (also in the search: bpm:120)")
        p.add_argument("--key", help="key, e.g. --key Am, --key Am~ = also compatible keys (key:Am)")

    sub.add_parser("stats", help="index information")
    sub.add_parser("hashes", help="store content hashes of the indexed files (for --reuse elsewhere)")
    p = sub.add_parser("merge", help="bring in an index made on another computer (siirto.py)")
    p.add_argument("other", help="the other computer's index.db")
    p.add_argument("--map", action="append", default=[], metavar="OTHER=HERE",
                   help='its folder = the same files here, e.g. "D:/Samples=/mnt/winsamples"')
    p.add_argument("--dry-run", action="store_true", help="only report what would happen")
    p = sub.add_parser("relocate", help="the files under OLD are now under NEW (siirto.py)")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--dry-run", action="store_true", help="only report what would happen")
    p.add_argument("--allow-missing", action="store_true", help="move what is there, leave the rest")
    p = sub.add_parser("shell", help="interactive mode (default)")
    p.add_argument("-n", type=int, default=15)

    a = ap.parse_args(argv)
    cmd = a.cmd or "shell"

    if cmd == "stats":
        con = db()
        n, tot = con.execute("SELECT COUNT(*), COALESCE(SUM(duration),0) FROM files WHERE audio_emb IS NOT NULL").fetchone()
        m = con.execute("SELECT value FROM meta WHERE key='model'").fetchone()
        print(f"{n} files, {tot / 3600:.1f} h of audio, model {m[0] if m else '-'}\n{DB_PATH}")
        print("Library folders: " + (", ".join(get_roots(con)) or "(one, not stored yet)"))
        fb = con.execute("""SELECT f.backend, COUNT(*) FROM features f JOIN files x
            ON f.path = x.path AND f.mtime = x.mtime AND f.size = x.size
            WHERE f.centroid IS NOT NULL GROUP BY f.backend""").fetchall()
        print("Sound features: " + (", ".join(f"{c} ({b})" for b, c in fb) if fb
                                      else "not computed (samplehaku-piirteet)"))
        return

    if cmd in ("merge", "relocate", "hashes"):
        import siirto
        if cmd == "hashes":
            siirto.hashes()
        elif cmd == "merge":
            siirto.merge(a.other, a.map, a.dry_run)
        else:
            siirto.relocate(a.old, a.new, a.dry_run, a.allow_missing)
        return

    clap = Clap(a.model)
    if cmd == "index":
        cmd_index(clap, a.dirs, a.reuse)
        return

    con = db()
    check_model(con, clap.name)
    idx = Index(con)
    if cmd == "shell":
        interactive(clap, idx, getattr(a, "n", 15))
        return

    filt = dict(min_dur=a.min_dur, max_dur=a.max_dur, in_path=a.in_path)
    try:
        tags = [parse_tags("+" + t.lstrip("+"))[1][0] for t in (a.tag or [])]
        if a.bpm:
            tags.append(parse_filter("bpm:" + a.bpm))
        if a.key:
            tags.append(parse_filter("key:" + a.key))
        if cmd == "search":
            text, more = parse_tags(a.query)
            tags += more
            q = translate(text) if text else text
            if q != text:
                print(f"→ {q}")
            hits = idx.rank(text_scores(clap, idx, q, tags), a.n, tags=tags, **filt)
        else:
            scores, self_i = similar_scores(clap, idx, a.query)
            hits = idx.rank(scores, a.n, exclude=self_i, tags=tags, **filt)
    except ValueError as e:
        sys.exit(str(e))
    show(idx, hits)
    if a.play and hits:
        player = Player()
        player.play(idx.paths[hits[0][0]])
        if player.proc:
            player.proc.wait()


if __name__ == "__main__":
    main()
