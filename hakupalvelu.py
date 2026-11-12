#!/usr/bin/env python3
"""
hakupalvelu – the Fetch search service for the GNOME desktop.

A background service (D-Bus, name fi.samplehaku.Search) that
  1. answers GNOME Shell's search (Super key): "fetch dark kick", "sample +bass +long"
     or any search containing a +tag. Clicking a result opens the Fetch window with the
     whole search, the clicked sample selected.
  2. serves the Fetch window, the Similar dialog and the Files extension: Browse, Query, Details,
     Info, Similar(path, mode) (collects similar sounds into ~/.cache/samplehaku/tulokset/<name>/
     as hard links, so they can be dragged straight into a DAW), tag and genre edits.

  3. serves the VST3 plugin over HTTP on 127.0.0.1 (fetchhttp.py; SAMPLEHAKU_HTTP_PORT, 0 = off).

The service starts automatically on the first search (D-Bus activation) and stops when it
hasn't been used for SAMPLEHAKU_IDLE_MIN minutes (default 10; 0 = never).
It uses less memory than the command-line tool: only CLAP's text part is loaded.
"""
from __future__ import annotations

import asyncio
import bisect
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import ctypes
import gc
from pathlib import Path

import numpy as np
try:
    from dbus_fast import BusType, Variant
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method
except ImportError:          # no D-Bus (Windows): only the HTTP API (fetchhttp.py) is usable
    BusType = Variant = MessageBus = None

    class ServiceInterface:  # type: ignore[no-redef]
        def __init__(self, name: str):
            self.name = name

    def method(*_a, **_k):   # type: ignore[no-redef]
        return lambda f: f

sys.path.insert(0, str(Path(__file__).resolve().parent))
import samplehaku as sh  # noqa: E402
import soittimet  # noqa: E402
import genret  # noqa: E402
import fetchhttp  # noqa: E402
import polut  # noqa: E402

BUS_NAME = "fi.samplehaku.Search"
PROVIDER_PATH = "/fi/samplehaku/SearchProvider"
PREFIXES = {p.strip().lower() for p in
            os.environ.get("SAMPLEHAKU_PREFIX", "fetch,ääni,sample,samplehaku").split(",") if p.strip()}
SHELL_RESULTS = 10
SHELL_PLAY = os.environ.get("SAMPLEHAKU_SHELL_PLAY", "0") == "1"   # 1 = play on click (the old behaviour)
FOLDER_RESULTS = 100
IDLE_MIN = float(os.environ.get("SAMPLEHAKU_IDLE_MIN", 10))
# Results open in the Fetch window (samplehaku_tulokset.py); "files" = the old behaviour.
RESULTS_UI = os.environ.get("SAMPLEHAKU_RESULTS_UI", "window")
RESULTS_CMD = os.environ.get("SAMPLEHAKU_RESULTS_CMD", str(Path.home() / ".local/bin/samplehaku-results"))
def vapauta_muisti() -> None:
    """Return temporary buffers from loading to the operating system (glibc doesn't do it by itself)."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass


RESULTS_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "samplehaku/tulokset"

SIMILAR_MODES = {
    "": ("Similar", None, 0),
    "tummemmat": ("Similar, darker", "centroid", -1),
    "kirkkaammat": ("Similar, brighter", "centroid", 1),
    "lyhyemmat": ("Similar, shorter", "tail", -1),
    "pidemmat": ("Similar, longer", "tail", 1),
    # tempo/key: the source file's value from the name or the audio (samplehaku.Index)
    "savellaji": ("Similar, compatible key", "key", 0),
    "tempo": ("Similar, same tempo", "bpm", 0),
}


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- search engine

class Engine:
    """Keeps CLAP's text part and the index in memory; reloads the index if it changes."""

    def __init__(self):
        self.ready = threading.Event()
        self.error: str | None = None
        self.clap = None
        self.idx = None
        self.idx_stamp = None
        self.rel_cache: dict[str, np.ndarray] = {}
        self.player = sh.Player()
        self.lock = threading.Lock()
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        """Startup: first the index + instruments + genres + search texts (from caches, ~0.1 s),
        so the first page doesn't wait; then CLAP's text model (~2.5 s) for text search.
        Run in parallel (measured), the total time doesn't shrink but the first page gets slower."""
        t0 = time.time()
        try:
            idx = self.index()
            idx.instruments()
            idx.genres()
            log(f"Index ready {time.time() - t0:.1f} s")
            self.docs()
            self.vocab()
            self.clap = sh.TextClap(sh.DEFAULT_MODEL)
            vapauta_muisti()
            log(f"Ready for text search {time.time() - t0:.1f} s")
        except (Exception, SystemExit) as e:           # SystemExit: samplehaku's "The index is empty" – without
            self.error = str(e) if isinstance(e, SystemExit) else f"{e.__class__.__name__}: {e}"   # it nobody
            log("Startup failed:", self.error)         # was told, and every search waited for ever
        self.ready.set()

    def index(self):
        """The loaded index. A changed database is reloaded in the background while requests keep
        using the old one (a 270k-sample index can take minutes to rebuild on Windows, and a request that
        waited for it timed out in the plugin). Checked at most every 60 s."""
        if self.idx is not None and time.monotonic() - getattr(self, "_checked", 0.0) < 60:
            return self.idx
        self._checked = time.monotonic()
        if self.idx is None:
            with self.lock:
                if self.idx is None:
                    stamp = self._stamp()
                    self._swap(self._load_index(), stamp)
            return self.idx
        stamp = self._stamp()
        with self.lock:                       # requests come from several threads: one background job at a time
            if not getattr(self, "_rebuilding", False):
                if stamp != self.idx_stamp:
                    self._rebuilding = True
                    threading.Thread(target=self._rebuild, args=(stamp,), daemon=True).start()
                elif getattr(self.idx, "_unsaved_key", None):
                    self._rebuilding = True
                    threading.Thread(target=self._save_quiet, daemon=True).start()
        return self.idx

    def reload_soon(self, removed_root: str = "") -> None:
        """The database was just changed here (Remove folder): the next request starts the reload, and
        until it is done the removed folder is already gone from the Folders tree and Settings."""
        self._checked = 0.0
        idx = self.idx
        if removed_root and idx is not None and idx.roots:
            idx.roots = [r for r in idx.roots if r != removed_root and not r.startswith(removed_root + "/")]
            self._tree_cache = {}

    @staticmethod
    def _stamp():
        st = sh.DB_PATH.stat() if sh.DB_PATH.exists() else None
        return (st.st_mtime, st.st_size) if st else None

    @staticmethod
    def _load_index():
        con = sh.db()
        try:
            return sh.Index(con)  # sys.exit if empty
        finally:
            con.close()

    def _swap(self, idx, stamp) -> None:
        self.idx, self.idx_stamp = idx, stamp
        self.rel_cache = {}
        # Let go of everything made for the previous index. Each helper remembered "its" index until it was
        # next called, so after a few reloads (indexing in progress: the database changes all the time)
        # several old indexes, ~0.9 GB each for 200k samples, stayed in memory (stress test).
        for a in ("_docs_for", "_vocab_for", "_sortkeys_for", "_path_order_for", "_ino_for", "_ps_for", "_root_for",
                  "_word_cache_for"):
            setattr(self, a, None)
        self._sortkeys, self._ino, self._word_cache = {}, {}, {}
        self._docs = self._vocab = self._path_rank = self._ps = None     # (each guarded by its _for)
        self._rank_cache, self._tree_cache = {}, {}
        self.path_pos = {p: i for i, p in enumerate(idx.paths)}
        vapauta_muisti()

    def _rebuild(self, stamp) -> None:
        t0 = time.time()
        try:
            idx = self._load_index()
            idx.instruments()
            idx.genres()
            with self.lock:
                self._swap(idx, stamp)
            self.docs()
            self.vocab()
            log(f"Index reloaded in the background {time.time() - t0:.1f} s ({len(idx.paths)} samples)")
        except BaseException as e:  # noqa: BLE001 – keep serving the old index
            log("Background reload failed:", f"{e.__class__.__name__}: {e}")
        finally:
            self._rebuilding = False

    def _save_quiet(self) -> None:
        try:
            if self.idx.save_cache_if_quiet():
                log("Index cache saved")
        except Exception as e:  # noqa: BLE001
            log("Saving the index cache failed:", e)
        finally:
            self._rebuilding = False

    def rel(self, tag: str) -> np.ndarray:
        if tag not in self.rel_cache:
            if len(self.rel_cache) >= 64:                # 1.7 MB each for 200k samples
                self.rel_cache.pop(next(iter(self.rel_cache)))
            self.rel_cache[tag] = self.idx._rel(tag)
        return self.rel_cache[tag]

    def search(self, text: str, tags: list[str], n: int) -> list[str]:
        self.ready.wait()
        if self.error:
            raise RuntimeError(self.error)
        idx = self.index()
        if not text.strip():
            scores = sh.text_scores(self.clap, idx, text, tags)
            return [idx.paths[i] for i, _ in idx.rank(scores, n, tags=tags)]
        sel, _ = self.ranked(text, tags)
        return [idx.paths[i] for i in sel[:n]]

    def similar_combo(self, path: str, conds: set[str], n: int) -> tuple[list[str], list[str]]:
        """Combined search from the Similar dialog: conds ⊆ {timbre, key, bpm}.
        -> (paths, skipped conditions whose value is unknown for the source file)."""
        idx = self.index()
        i = self.pos(path)
        if i is None:
            raise LookupError("This file is not in the samplehaku index yet (samplehaku index <folder>).")
        skipped = []
        if "key" in conds and idx.key_pc[i] < 0:
            skipped.append("key")
        if "bpm" in conds and not np.isfinite(idx.bpm[i]):
            skipped.append("bpm")
        use = conds - set(skipped)
        if not use:
            raise LookupError("Key/BPM of this file is unknown (not in its name, not estimated). "
                              "Turn on Timbre instead.")
        timbre = sh.dot(idx.A, idx.A[i])
        mask = np.ones(len(idx.paths), dtype=bool)
        score = np.zeros(len(idx.paths))
        # small extras: timbre decides the order when selected (timbre differences ~0.05–0.3)
        bonus = 0.03 if "timbre" in use else 0.2
        if "key" in use:
            k = sh.key_name(int(idx.key_pc[i]), int(idx.key_mode[i]))
            mask &= idx.key_mask(f"key:{k}~")
            score += np.where(idx.key_mask(f"key:{k}"), bonus, 0.0)   # the same key before the neighbours
        if "bpm" in use:
            t = f"bpm:{idx.bpm[i]:g}"
            mask &= idx.bpm_mask(t)
            score += bonus * idx.tag_scores([t])                        # closest tempo first
        # timbre decides the order if selected; otherwise it only breaks ties
        score += timbre if "timbre" in use else 0.01 * timbre
        score = np.where(mask, score, -np.inf)
        return [idx.paths[j] for j, _ in idx.rank(score, n, exclude=i)], skipped

    def similar(self, path: str, mode: str, n: int) -> list[str]:
        idx = self.index()
        i = self.pos(path)
        if i is None:
            raise LookupError("The file is not in the Fetch index. Run: samplehaku index <folder>")
        scores = sh.dot(idx.A, idx.A[i])
        _, feat, sign = SIMILAR_MODES.get(mode, SIMILAR_MODES[""])
        if feat == "key":
            if idx.key_pc[i] < 0:
                raise LookupError("The key of this file is unknown (not in the name, and not estimated from the audio).")
            k = sh.key_name(int(idx.key_pc[i]), int(idx.key_mode[i]))
            scores = np.where(idx.key_mask(f"key:{k}~"), scores, -np.inf)
        elif feat == "bpm":
            if not np.isfinite(idx.bpm[i]):
                raise LookupError("The tempo of this file is unknown (not in the name, and not estimated from the audio).")
            scores = np.where(idx.bpm_mask(f"bpm:{idx.bpm[i]:g}"), scores, -np.inf)
        elif feat:
            v = idx.F[feat]
            if not np.isfinite(v[i]):
                raise LookupError("Sound features have not been computed for this file (samplehaku-piirteet).")
            ok = np.isfinite(v) & ((v - v[i]) * sign > 0)
            scores = np.where(ok, scores, -np.inf)
        return [idx.paths[j] for j, _ in idx.rank(scores, n, exclude=i)]

    def docs(self) -> list[str]:
        """Search text per sample: path from the library root (pack, folders, name), instrument class,
        style tags, genres and type. " 909 oh clean 01 drums hats open hats one shot … "."""
        idx = self.index()
        if getattr(self, "_docs_for", None) is idx:
            return self._docs
        inst, _, tg = idx.instruments()
        gs = idx.genres()
        tops = [polut.parent(r) for r in polut.roots_per_path(idx.paths, idx.roots)]
        tmap = {1: "loop loops", 0: "one shot oneshot"}
        docs = []
        for i, p in enumerate(idx.paths):
            rel = p[len(tops[i]) + 1:].rsplit(".", 1)[0]       # from the library folder's name
            rel = re.sub(r"([a-z])([A-Z])", r"\1 \2", rel)
            parts = [rel, soittimet.label_path(inst[i]) if inst[i] else "",
                     " ".join(soittimet.TAG_LABEL.get(t, t) for t in tg[i]),
                     " ".join(genret.LABEL.get(g, g) for g in gs[i]), tmap.get(int(idx.stype[i]), "")]
            d = re.sub(r"[^a-z0-9#]+", " ", " ".join(parts).lower())
            docs.append(" " + d + " ")
        self._docs, self._docs_for = docs, idx
        return docs

    def vocab(self) -> set[str]:
        """Class words that narrow a search: instruments, tags, genres, types and pack names."""
        idx = self.index()
        if getattr(self, "_vocab_for", None) is idx:
            return self._vocab
        v = set(soittimet.KW) | set(soittimet.KIT_ABBR) | set(soittimet.TW)
        labels = list(soittimet.LABEL.values()) + list(soittimet.TAG_LABEL.values()) + list(genret.LABEL.values())
        v |= {w for lbl in labels for w in re.sub(r"[^a-z0-9]+", " ", lbl.lower()).split() if len(w) > 2}
        v |= {"loop", "loops", "oneshot", "one", "shot", "shots", "drums", "drum", "hit", "hits"}
        tops = [polut.parent(r) for r in polut.roots_per_path(idx.paths, idx.roots)]
        for p in {p[len(r) + 1:].split("/")[0] for p, r in zip(idx.paths, tops)}:     # pack names
            v |= {w for w in re.sub(r"[^a-z0-9]+", " ", p.lower()).split() if len(w) > 2 and not w.isdigit()}
        self._vocab, self._vocab_for = v - {"and", "the", "vol", "wav", "sounds", "samples", "pack", "sample"}, idx
        return self._vocab

    def word_match(self, w: str) -> np.ndarray:
        """A word in the sample's search text (start or end of a word: "hat" = hats / hihat, numbers also
        in the middle: "909" = tr909)."""
        docs = self.docs()
        cache = getattr(self, "_word_cache", None)
        if cache is None or getattr(self, "_word_cache_for", None) is not docs:
            cache, self._word_cache_for = {}, docs      # per loaded index: a scan of every search text takes
        self._word_cache = cache                        # ~50 ms, and the same words come again and again
        m = cache.get(w)
        if m is None:
            stem = w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            if any(c.isdigit() for c in stem):
                m = np.fromiter((stem in d for d in docs), dtype=bool, count=len(docs))
            else:
                a, b = " " + stem, stem + " "
                m = np.fromiter((a in d or b in d for d in docs), dtype=bool, count=len(docs))
            if len(cache) > 120:                        # 200 kB each for 200k samples
                cache.clear()
            cache[w] = m
        return m

    def ranked(self, text: str, tags: list[str]) -> tuple[np.ndarray, str]:
        """Search ranking.
        Class words (instrument, tag, genre, type, pack, numbers like 909) NARROW: all must match.
        Other words ("warm", "dusty") only RANK: in the name -> first, then by the meaning of the
        audio and the name. Without class words: name matches + the 300 closest-sounding.
        -word REMOVES everything whose search text (path, instrument, tags, genres, type) contains the word.
        -> (indices, mode: "match" | "closest")."""
        idx = self.index()
        self.ready.wait()
        if self.error:
            raise RuntimeError(self.error)
        text, negs = split_negatives(text)
        scores = (sh.text_scores(self.clap, idx, sh.translate(text), tags) if text.strip() or tags
                  else np.zeros(len(idx.paths), dtype=np.float32))     # just "-kick": library order
        base = idx.tag_mask(tags) if tags else np.ones(len(idx.paths), dtype=bool)
        for w in negs:                                  # -kick: remove everything whose search text contains the word
            base &= ~self.word_match(w)
        order = lambda sel: sel[np.argsort(-scores[sel], kind="stable")]   # noqa: E731
        if negs and not re.sub(r"[^a-z0-9#]+", "", text.lower()):
            return order(np.nonzero(base)[0]), "match"  # exclusions only: a filter
        vocab = self.vocab()
        strict, soft = [], []
        # each word once, and only the first 16: a pasted paragraph must not scan the library for minutes
        for w in list(dict.fromkeys(re.sub(r"[^a-z0-9#]+", " ", text.lower()).split()))[:16]:
            m = self.word_match(w)
            if not m.any():
                continue                                # nowhere: audio order only
            stem = w[:-1] if len(w) > 3 and w.endswith("s") else w
            (strict if (w in vocab or stem in vocab or any(c.isdigit() for c in w)) else soft).append(m)
        mask = base.copy()
        for m in strict:
            mask &= m
        named = mask.copy() if soft else np.zeros(len(mask), dtype=bool)   # no name words -> no name matches
        for m in soft:
            named &= m
        first = order(np.nonzero(named)[0]) if soft else np.array([], dtype=int)
        if strict:
            if not mask.any():
                return order(np.nonzero(base)[0])[:300], "closest"
            rest = order(np.nonzero(mask & ~named)[0]) if soft else order(np.nonzero(mask)[0])
            return np.concatenate([first, rest]), "match"
        rest = order(np.nonzero(base & ~named)[0])[:300]
        return np.concatenate([first, rest]), ("match" if len(first) else "closest")

    def detail(self, i: int) -> dict:
        """The results window's columns for one file (index row i)."""
        idx = self.index()
        has_key = bool(idx.key_pc[i] >= 0)
        inst, iest, roles = idx.instruments()
        return {
            "key": idx.key_label(i),
            "key_est": bool(idx.key_est[i]),
            "bpm": round(float(idx.bpm[i]), 1) if np.isfinite(idx.bpm[i]) else None,
            "bpm_est": bool(idx.bpm_est[i]),
            "dur": float(idx.dur[i]),
            "type": {1: "Loop", 0: "One-shot"}.get(int(idx.stype[i]), ""),
            "pack": self.folder_label(idx.paths[i]),
            "orig": idx.paths[i],
            "fixed": bool(set(getattr(idx, "fixes", {}).get(idx.paths[i], {})) - {"len"}),
            **({"len": idx.fixes[idx.paths[i]]["len"]} if "len" in getattr(idx, "fixes", {}).get(idx.paths[i], {}) else {}),
            "inst": inst[i], "inst_est": bool(iest[i]), "itags": sorted(roles[i]),
            "itags_est": sorted(getattr(idx, "tag_est", [set()] * len(roles))[i]),
            "genres": sorted(g for g in idx.genres()[i] if g in genret.GROUP_OF),
            "inst_label": soittimet.leaf_label(inst[i]) if inst[i] else "",
            "inst_path": soittimet.label_path(inst[i]) if inst[i] else "",
            # sound-feature tags (tagit.json) the file belongs to: for the window's filters
            "tags": [g[0] for g in sh.TAG_GROUPS
                     if np.nan_to_num(self.rel(g[0])[i], nan=-1) >= 1 - sh.TAG_SHARE]
            if np.isfinite(idx.F["centroid"][i]) else [],
        }

    def sort_keys(self, field: str) -> np.ndarray:
        """Sort key for the whole library (results window columns). Unknown values always last."""
        idx = self.index()
        cache = getattr(self, "_sortkeys", None) or {}
        if getattr(self, "_sortkeys_for", None) is not idx:
            cache, self._sortkeys_for = {}, idx
        if field not in cache:
            n = len(idx.paths)
            if field == "dur":
                k = np.asarray(idx.dur, dtype=np.float64)
            elif field == "bpm":
                k = np.where(np.isfinite(idx.bpm), idx.bpm, np.inf)
            else:
                if field == "name":
                    vals = [os.path.basename(p).lower() for p in idx.paths]
                elif field == "key":
                    # by the standard name, so 'D#' and 'Eb' (same key, spelled as in the file name) sort together
                    vals = [sh.key_name(int(idx.key_pc[i]), int(idx.key_mode[i])) if idx.key_pc[i] >= 0 else ""
                            for i in range(n)]
                elif field == "inst":
                    inst = idx.instruments()[0]
                    vals = [soittimet.leaf_label(x).lower() if x else "" for x in inst]
                elif field == "genre":
                    gs = idx.genres()
                    vals = [", ".join(sorted(genret.LABEL.get(g, g) for g in s if g in genret.GROUP_OF)).lower()
                            for s in gs]
                elif field == "type":
                    vals = [{1: "loop", 0: "one-shot"}.get(int(t), "") for t in idx.stype]
                elif field == "pack":
                    vals = [self.folder_label(p).lower() for p in idx.paths]
                else:
                    raise ValueError(f"Unknown sort field {field}")
                # empty ones last, otherwise the alphabetical rank
                order = sorted(range(n), key=lambda i: vals[i])
                k = np.empty(n, dtype=np.float64)
                k[order] = np.arange(n)
                k[np.array([v == "" for v in vals])] = np.inf    # empty ones always last
            cache[field] = k
            self._sortkeys = cache
        return cache[field]

    def browse(self, filters: str, offset: int, limit: int, order: str, seed: int, text: str = "",
               folder: str = "") -> dict:
        """The whole library page by page (the window's "All samples" and search): filters as in search
        (+loop, +dark, bpm:118-126 …). With text, the whole library in relevance order
        (the order is kept in memory, so the next pages are ready at once); otherwise by folder or shuffled.
        folder: only the samples under that library folder (the Folders tree)."""
        idx = self.index()
        if folder and not text:
            return self._browse_folder(folder, filters, offset, limit, order, seed)
        words, tags = sh.parse_tags(" ".join(x for x in (text, filters) if x))
        if words:
            key = (words, tuple(tags), id(idx))
            cache = getattr(self, "_rank_cache", {})
            if key not in cache:
                cache = dict(list(cache.items())[-7:])     # keep the last few searches in memory
                cache[key] = self.ranked(words, tags)
                self._rank_cache = cache
            sel, mode = cache[key]
            sel = self._sorted(sel, order)
            page = sel[offset:offset + limit]
            return {"total": int(len(sel)), "mode": mode, "paths": [idx.paths[i] for i in page],
                    "details": {idx.paths[i]: self.detail(int(i)) for i in page}}
        sel = np.nonzero(idx.tag_mask(tags))[0] if tags else np.arange(len(idx.paths))
        if order.startswith("sort:"):
            sel = self._sorted(sel, order)
        elif order == "shuffle":
            sel = np.random.default_rng(seed).permutation(sel)
        else:
            sel = sel[np.argsort(self._lower_rank()[sel], kind="stable")]
        page = sel[offset:offset + limit]
        return {"total": int(len(sel)),
                "paths": [idx.paths[i] for i in page],
                "details": {idx.paths[i]: self.detail(int(i)) for i in page}}

    def _lower_rank(self) -> np.ndarray:
        idx = self.index()
        if getattr(self, "_path_order_for", None) is not idx:
            self._path_rank = np.argsort(np.argsort(np.array([p.lower() for p in idx.paths])))
            self._path_order_for = idx
        return self._path_rank

    def _browse_folder(self, folder: str, filters: str, offset: int, limit: int, order: str, seed: int) -> dict:
        idx = self.index()
        _, tags = sh.parse_tags(filters)
        lo, hi = self._range(folder)
        sel = self._path_sorted()[1][lo:hi]              # already in path order
        if tags:
            sel = sel[idx.tag_mask(tags)[sel]]
        if order.startswith("sort:"):
            sel = self._sorted(sel, order)
        elif order == "shuffle":
            sel = np.random.default_rng(seed).permutation(sel)
        else:                                            # same order as All samples (ignores case)
            sel = sel[np.argsort(self._lower_rank()[sel], kind="stable")]
        page = sel[offset:offset + limit]
        return {"total": int(len(sel)), "paths": [idx.paths[i] for i in page],
                "details": {idx.paths[i]: self.detail(int(i)) for i in page}}

    def _sorted(self, sel: np.ndarray, order: str) -> np.ndarray:
        """order "sort:<field>:asc|desc" -> sel sorted (stable: ties keep the previous order).
        Unknown values (no tempo, no key) stay last also in descending order."""
        if not order.startswith("sort:"):
            return sel
        _, field, direction = (order.split(":") + ["asc"])[:3]
        k = self.sort_keys(field)[sel]
        if direction == "desc":
            fin = np.isfinite(k)
            k = np.where(fin, -k, np.inf)
        return sel[np.argsort(k, kind="stable")]

    def pos(self, path: str) -> int | None:
        """A file's position in the index: directly, from a result folder's manifest, or by inode
        (hard links, e.g. Liked Samples). The inode table is built once per index."""
        idx = self.index()
        i = self.path_pos.get(polut.norm(path))         # an indexed path as it is: no disk access (a manifest
        if i is None:                                   # probe and resolve() cost ~15 ms each on a network share)
            i = self.path_pos.get(polut.norm(original_path(path)))
        if i is not None:
            return i
        try:
            st = os.stat(path)
        except OSError:
            return None
        if getattr(self, "_ino_for", None) is not idx:
            self._ino, self._ino_for = {}, idx
        if st.st_dev not in self._ino:
            # A hard link lives on the same file system as its original: only the library roots on the
            # file's device are looked through (a root on a network share is never stat'ed file by file,
            # which took minutes and blocked the service with a large library on a network share).
            roots = idx.roots or [polut.fallback_root(idx.paths)]
            same = set()
            for r in roots:
                try:
                    if os.stat(r).st_dev == st.st_dev:
                        same.add(r)
                except OSError:
                    pass
            ino = {}
            if same:
                for j, (q, r) in enumerate(zip(idx.paths, polut.roots_per_path(idx.paths, roots))):
                    if r in same:
                        try:
                            t = os.stat(q)
                            ino[(t.st_dev, t.st_ino)] = j
                        except OSError:
                            pass
            self._ino[st.st_dev] = ino
        return self._ino[st.st_dev].get((st.st_dev, st.st_ino))

    def _path_sorted(self):
        """(paths sorted as strings, their index positions) for prefix ranges: the folder tree and
        browsing one folder. Built once per loaded index (~0.3 s for 360k paths)."""
        idx = self.index()
        if getattr(self, "_ps_for", None) is not idx:
            order = sorted(range(len(idx.paths)), key=idx.paths.__getitem__)
            self._ps = ([idx.paths[i] for i in order], np.array(order, dtype=np.int64))
            self._ps_for = idx
            self._tree_cache = {}
        return self._ps

    def _range(self, folder: str) -> tuple[int, int]:
        """Positions in _path_sorted() of the paths under folder (any depth)."""
        sp, _ = self._path_sorted()
        pre = folder.rstrip("/") + "/"
        return bisect.bisect_left(sp, pre), bisect.bisect_left(sp, pre[:-1] + "0")   # "0" follows "/"

    def under(self, folder: str) -> np.ndarray:
        """Index positions of the samples under folder (any depth)."""
        lo, hi = self._range(folder)
        return self._path_sorted()[1][lo:hi]

    def tree_roots(self) -> list[str]:
        """The top of the Folders tree: the library roots, except one inside another (it shows as a
        folder there, e.g. Library/Drums)."""
        idx = self.index()
        roots = idx.roots or [polut.fallback_root(idx.paths)]
        return sorted(r for r in roots if not any(r != o and r.startswith(o + "/") for o in roots))

    def folders(self, parent: str = "") -> dict:
        """The folder tree one level at a time: parent "" -> the library roots, otherwise the folders
        directly inside parent, each {name, path, count (samples at any depth), subs (has subfolders)} + "files" (samples
        directly in parent). Skips over a whole subfolder with one bisect, so a level costs
        about (subfolders × log n), not the number of samples under it."""
        sp, _ = self._path_sorted()
        cache = self._tree_cache
        if parent in cache:
            return cache[parent]
        if not parent:
            out = {"folders": [], "files": 0}
            for r in self.tree_roots():
                lo, hi = self._range(r)
                if hi > lo:
                    out["folders"].append({"name": r.rsplit("/", 1)[-1] or r, "path": r, "count": hi - lo,
                                           "subs": any("/" in sp[k][len(r) + 1:] for k in range(lo, hi))})
            cache[parent] = out
            return out
        lo, hi = self._range(parent)
        pre = parent.rstrip("/") + "/"
        subs, files, i = [], 0, lo
        while i < hi:
            rest = sp[i][len(pre):]
            if "/" not in rest:
                files += 1
                i += 1
                continue
            name = rest.split("/", 1)[0]
            sub = pre + name
            s_lo, s_hi = self._range(sub)
            # has subfolders: a path below it one level deeper (stops at the first; files and folders
            # interleave in name order, so this usually looks at a few paths)
            cut = len(sub) + 1
            nested = any("/" in sp[k][cut:] for k in range(s_lo, s_hi))
            subs.append({"name": name, "path": sub, "count": s_hi - s_lo, "subs": nested})
            i = max(s_hi, i + 1)
        subs.sort(key=lambda d: d["name"].lower())
        out = {"folders": subs, "files": files}
        cache[parent] = out
        return out

    def folder_label(self, path: str) -> str:
        """'Samples/Pack Vol 5/Instruments' from the library root; otherwise a ~/ path."""
        idx = self.index()
        if getattr(self, "_root_for", None) is not idx:
            self._roots = idx.roots or [polut.fallback_root(idx.paths)]
            self._root_for = idx
        parent = polut.parent(polut.norm(path))
        root = polut.root_of(parent + "/", self._roots)
        if root:
            return root.rsplit("/", 1)[-1] + parent[len(root):]
        return parent.replace(polut.norm(Path.home()), "~", 1)

    def describe(self, path: str) -> str:
        idx = self.index()
        i = self.path_pos.get(path)
        if i is None:
            return ""
        parts = [self.folder_label(path)]
        bk = sh.fmt_bpm_key(idx, i).strip()
        st = {1: "loop", 0: "one-shot"}.get(int(idx.stype[i]))
        bk = " ".join(x for x in (st, bk) if x)
        if bk:
            parts.append(bk)
        c, t = idx.F["centroid"][i], idx.F["tail"][i]
        if np.isfinite(c):
            parts.append(f"{c / 1000:.1f} kHz" if c >= 1000 else f"{c:.0f} Hz")
            parts.append(f"tail {t:.1f} s")
            words = [g[0] for g in sh.TAG_GROUPS
                     if np.nan_to_num(self.rel(g[0])[i], nan=-1) >= 1 - sh.TAG_SHARE]
            if words:
                parts.append(", ".join(words))
        return " · ".join(parts)


MANIFEST = ".samplehaku.json"     # result folder name -> original path (hard links)


def original_path(path: str) -> str:
    """Hard link in a result folder -> the original sample; other paths as they are.
    Only result folders and collections have manifests (samplehaku_kokoelmat._manifest_dir): elsewhere
    nothing is looked for."""
    import samplehaku_kokoelmat as kok
    p = Path(path)
    if not kok._manifest_dir(p.parent):
        return str(p.resolve())
    try:
        m = json.loads((p.parent / MANIFEST).read_text())
        return m.get(p.name, str(p.resolve()))
    except (OSError, ValueError):
        return str(p.resolve())


def results_folder(title: str, paths: list[str], source: str | None = None) -> str:
    """Collect results into a folder as hard links: '01 Kick_Deep.wav' -> the original.
    source = the Similar search's source sample: first, numbered 0 ('000 …'), so you can go back."""
    safe = re.sub(r"[/\\:*?\"<>|]+", " ", title).strip()[:80] or "search"
    d = RESULTS_DIR / safe
    # The same search again: empty the folder in place instead of deleting it. If Files is showing
    # this very folder, a deleted and re-created folder would show up empty there.
    d.mkdir(parents=True, exist_ok=True)
    for c in d.iterdir():
        if c.is_dir() and not c.is_symlink():
            shutil.rmtree(c)
        else:
            c.unlink()
    width = max(2, len(str(len(paths))))
    start = 1
    if source:
        paths = [source] + [p for p in paths if p != source]
        start = 0
    dev = d.stat().st_dev
    folder_dev: dict[str, int | None] = {}              # one stat per source folder, not a failed link() per
    for k, p in enumerate(paths, start):                # sample: on a network share each costs ~15 ms
        dst = d / f"{k:0{width}d} {Path(p).name}"
        # Hard link: looks like an ordinary file to every program (GNOME Audio Player
        # a.k.a. Decibels refuses symlinks: "not a regular file"). Takes no space.
        # A symlink only if the sample is on a different disk than the cache.
        parent = os.path.dirname(p)
        if parent not in folder_dev:
            try:
                folder_dev[parent] = os.stat(parent).st_dev
            except OSError:
                folder_dev[parent] = None
        try:
            if folder_dev[parent] != dev:
                raise OSError
            os.link(p, dst)
        except OSError:
            dst.symlink_to(p)
    (d / MANIFEST).write_text(json.dumps(
        {f"{k:0{width}d} {Path(p).name}": p for k, p in enumerate(paths, start)}, ensure_ascii=False))
    # remove old result folders, keep the newest 20
    old = sorted(RESULTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[20:]
    for o in old:
        shutil.rmtree(o, ignore_errors=True)
    return str(d)


def show_in_files(path: str) -> None:
    for cmd in (["nautilus", "--select", path], ["gio", "open", str(Path(path).parent)]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


def open_folder(path: str) -> None:
    for cmd in (["nautilus", "--new-window", path], ["gio", "open", path]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


def open_results(folder: str, select: str | None = None) -> None:
    """Open a result folder in the Fetch window (or in Files if the window isn't used)."""
    if RESULTS_UI == "window" and os.access(RESULTS_CMD, os.X_OK):
        cmd = [RESULTS_CMD, folder] + (["--select", select] if select else [])
        subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif select:
        show_in_files(select)
    else:
        open_folder(folder)


def add_folder(path: str) -> dict:
    import indeksoija
    p = Path(path).expanduser()
    if not p.is_dir():
        return {"error": f"Not a folder: {path}"}
    return indeksoija.start_detached([str(p.resolve())])


def remove_folder(path: str) -> dict:
    """Settings > Remove folder: out of the library and the index (files on disk stay). Not while the
    background indexer works on that folder. -> {removed: n, folder} or {error}."""
    import indeksoija
    root = path.replace("\\", "/").rstrip("/")
    st = indeksoija.status()
    busy = ([st.get("folder", "")] if st.get("state") == "running" else []) + list(st.get("queue") or [])
    if any(b and (b == root or b.startswith(root + "/")) for b in busy):
        return {"error": "This folder is being analysed. Remove it when the analysis has finished."}
    con = sh.db()
    try:
        n = sh.remove_root(con, root)
    except ValueError as e:
        return {"error": str(e)}
    finally:
        con.close()
    log(f"removed library folder {root}: {n} samples left the index")
    return {"removed": n, "folder": root}


def index_status() -> dict:
    import indeksoija
    return indeksoija.status()


def split_negatives(text: str) -> tuple[str, list[str]]:
    """'snare loop -kick -one-shot' -> ('snare loop', ['kick', 'one shot'])."""
    keep, negs = [], []
    for w in text.split():
        if w.startswith("-") and len(w) > 1:
            n = " ".join(re.sub(r"[^a-z0-9#]+", " ", w[1:].lower()).split())
            if n:
                negs.append(n)
        else:
            keep.append(w)
    return " ".join(keep), negs


def _is_filter(w: str) -> bool:
    return bool(re.match(r"(bpm|tempo|key|sävel|savel|sävellaji):", w, re.I))


def _valid_filter(w: str) -> bool:
    try:
        return sh.parse_filter(w) is not None
    except ValueError:
        return False


def parse_terms(terms: list[str]) -> tuple[str, list[str]] | None:
    """Shell search terms -> (text, tags), or None if the search isn't meant for Fetch."""
    if not terms:
        return None
    words = sh.normalize_type_words(" ".join(terms)).split()
    if words[0].lower() in PREFIXES:
        words = words[1:]
    elif not any((w.startswith("+") and len(w) > 1) or _is_filter(w) for w in words):
        return None
    # while typing, half-typed tags ("+tu") and filters ("key:", "bpm:12-") are ignored
    words = [w for w in words if not (w.startswith("+") and w[1:].lower() not in sh.TAGS
                                      and w[1:].lower() not in sh.TYPE_WORDS)
             and not (_is_filter(w) and not _valid_filter(w))]
    text, tags = sh.parse_tags(" ".join(words))
    if not text and not tags:
        return None
    return text, tags


FIX_LOG_PATH = Path(os.environ.get("SAMPLEHAKU_FIX_LOG", sh.FIXES_PATH.parent / "korjausloki.jsonl"))


def _fix_log_row(idx, i: int, orig: str, prev_fix: dict | None, new: dict | None) -> dict:
    """What Fetch showed before this correction (for measuring detection errors, testit/tarkkuus.py).
    "was" = the value on screen (automatic, or an earlier correction if prev_fix has that field)."""
    inst, est, roles = idx.instruments()
    sources = {"inst": "user" if "inst" in (prev_fix or {}) else
               ("audio" if est[i] else ("name" if inst[i] else "none")),
               "type": "user" if "type" in (prev_fix or {}) else
               ("name" if not sh.sample_type(orig, float(idx.dur[i]), idx.bpm[i])[1] else "length")}
    return {
        "t": round(time.time()), "path": orig, "new": new,
        "was": {"inst": inst[i], "type": {1: "loop", 0: "oneshot"}.get(int(idx.stype[i]), ""),
                "tags": sorted(roles[i])},
        "src": sources, "prev_fix": prev_fix or {},
    }


def _append_fix_log(rows: list[dict]) -> None:
    """Append-only; never read by the app. A failure must not block the correction itself."""
    try:
        FIX_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FIX_LOG_PATH, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError as e:
        log(f"correction log: {e}")


LEN_MULTS = {0.25, 0.5, 1.0, 2.0, 4.0}


def set_tags(eng: "Engine", paths: list[str], new: dict | None) -> dict:
    """Shared by D-Bus (SetTags, SetTagsMany) and HTTP (settags). Corrections for several files: new {inst?, type?, roles?} is merged with earlier corrections
    (only the given fields change); None = back to automatic. -> {path: Details}.
    "len": the plugin's loop length (player menu /2, x2): the loop is counted at bpm · len when synced to the host;
    null removes it. It is a playback choice, not a detection fix: kept by "back to automatic", not "fixed"."""
    idx = eng.index()
    fixes = sh.load_fixes()
    if new and new.get("inst") and new["inst"] not in soittimet.LABEL:
        raise ValueError(f"Unknown instrument {new['inst']}")
    if new and new.get("len") is not None and new["len"] not in LEN_MULTS:
        raise ValueError(f"Loop length must be one of {sorted(LEN_MULTS)}")
    len_only = bool(new) and set(new) == {"len"}
    hit = []
    log_rows = []
    for path in paths:
        i = eng.pos(path)
        if i is None:
            continue
        orig = idx.paths[i]
        if not len_only:
            log_rows.append(_fix_log_row(idx, i, orig, fixes.get(orig), new))
        cur = fixes.get(orig, {})
        if new:
            cur.update({k: v for k, v in new.items() if k in ("inst", "type", "tags")})
            cur.pop("roles", None)
            if "len" in new:
                if new["len"] is None:
                    cur.pop("len", None)
                else:
                    cur["len"] = float(new["len"])
        else:
            cur = {"len": cur["len"]} if "len" in cur else {}
        if cur:
            fixes[orig] = cur
        else:
            fixes.pop(orig, None)
        hit.append((path, i))
    if not hit:
        raise LookupError("These files are not in the samplehaku index.")
    sh.FIXES_PATH.parent.mkdir(parents=True, exist_ok=True)
    polut.write_atomic(sh.FIXES_PATH, json.dumps(fixes, ensure_ascii=False, indent=1, sort_keys=True))
    _append_fix_log(log_rows)
    # in memory at once; neighbours are recomputed on the next index load
    inst, est, roles = idx.instruments()
    idx.fixes = fixes
    if new:
        for _, i in hit:
            if new.get("inst"):
                inst[i], est[i] = new["inst"], False
            if new.get("type") in sh.TYPE_IDS:
                idx.stype[i] = sh.TYPE_IDS[new["type"]]
            if "tags" in new:
                roles[i] = frozenset(new["tags"])
    else:
        idx._inst = None
        raw = getattr(idx, "_raw", {}).get("stype")   # automatic = name, length and sound (Index._build)
        for _, i in hit:
            idx.stype[i] = raw[i] if raw is not None else sh.sample_type(idx.paths[i], float(idx.dur[i]), idx.bpm[i])[0]
    return {path: eng.detail(i) for path, i in hit}


# ---------------------------------------------------------------- D-Bus interfaces

class Activity:
    def __init__(self):
        self.last = time.time()

    def touch(self):
        self.last = time.time()


class ShellProvider(ServiceInterface):
    def __init__(self, eng: Engine, act: Activity):
        super().__init__("org.gnome.Shell.SearchProvider2")
        self.eng, self.act = eng, act

    async def _search(self, terms: list[str], n: int) -> list[str]:
        self.act.touch()
        q = parse_terms(terms)
        if q is None:
            return []
        try:
            return await asyncio.to_thread(self.eng.search, q[0], q[1], n)
        except (Exception, SystemExit) as e:
            log("Search failed:", e)
            return []

    @method()
    async def GetInitialResultSet(self, terms: "as") -> "as":  # noqa: F821
        return await self._search(terms, SHELL_RESULTS)

    @method()
    async def GetSubsearchResultSet(self, previous: "as", terms: "as") -> "as":  # noqa: F821
        return await self._search(terms, SHELL_RESULTS)

    @method()
    def GetResultMetas(self, identifiers: "as") -> "aa{sv}":  # noqa: F821
        out = []
        for p in identifiers:
            try:
                desc = self.eng.describe(p)
            except (Exception, SystemExit):
                desc = ""
            out.append({"id": Variant("s", p), "name": Variant("s", Path(p).name),
                        "description": Variant("s", desc), "gicon": Variant("s", "audio-x-generic")})
        return out

    @method()
    async def ActivateResult(self, identifier: "s", terms: "as", timestamp: "u"):  # noqa: F821
        self.act.touch()
        # No automatic playback: Shell's search can't stop or preview sounds
        # (no hover/key API). The Fetch window opens with the whole search, the clicked one selected.
        if SHELL_PLAY:
            self.eng.player.play(identifier)
        if RESULTS_UI != "window":
            show_in_files(identifier)
            return
        await self._open_window(terms, select=identifier)

    async def _open_window(self, terms: list[str], select: str | None = None) -> None:
        try:
            query = " ".join(t for t in terms if t.lower() not in PREFIXES)
            if RESULTS_UI == "window" and os.access(RESULTS_CMD, os.X_OK):
                cmd = [RESULTS_CMD, "--query", query] + (["--select", select] if select else [])
                subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            q = parse_terms(terms) or (" ".join(terms), [])
            text = await asyncio.to_thread(sh.translate, q[0]) if q[0] else ""
            paths = await asyncio.to_thread(self.eng.search, text, q[1], FOLDER_RESULTS)
            open_results(results_folder(query, paths), select)
        except (Exception, SystemExit) as e:
            log("Search failed:", e)
            if select:
                show_in_files(select)

    @method()
    async def LaunchSearch(self, terms: "as", timestamp: "u"):  # noqa: F821
        self.act.touch()
        await self._open_window(terms)


class SampleSearch(ServiceInterface):
    """Interface used by the Fetch window, the Similar dialog and the Files extension."""

    def __init__(self, eng: Engine, act: Activity):
        super().__init__("fi.samplehaku.Search")
        self.eng, self.act = eng, act

    @method()
    async def Similar(self, path: "s", mode: "s") -> "s":  # noqa: F821
        """Returns the result folder's path, or the text 'ERROR: …'."""
        self.act.touch()
        try:
            i = self.eng.pos(path)
            src = self.eng.index().paths[i] if i is not None else original_path(path)
            conds = set(mode.split("+")) & {"timbre", "key", "bpm"}
            if conds:   # a Similar dialog combination, e.g. "timbre+key"
                paths, skipped = await asyncio.to_thread(self.eng.similar_combo, path, conds,
                                                         FOLDER_RESULTS)
                used = [c for c in ("timbre", "key", "bpm") if c in conds and c not in skipped]
                note = "".join(f"; {c} unknown" for c in skipped)
                title = f"Similar ({' + '.join(used)}{note}) – {Path(src).stem}"
                return results_folder(title, paths, source=src)
            paths = await asyncio.to_thread(self.eng.similar, path, mode, FOLDER_RESULTS)
            title = f"{SIMILAR_MODES.get(mode, SIMILAR_MODES[''])[0]} – {Path(src).stem}"
            return results_folder(title, paths, source=src)
        except (Exception, SystemExit) as e:
            return f"ERROR: {e}"

    @method()
    async def Info(self, path: "s") -> "s":  # noqa: F821
        """For the Similar dialog: indexed or not, key and tempo (JSON)."""
        self.act.touch()

        def work():
            idx = self.eng.index()
            i = self.eng.pos(path)
            if i is None:
                return {"indexed": False}
            has_key = bool(idx.key_pc[i] >= 0)
            return {"indexed": True,
                    "key": idx.key_label(i),
                    "key_est": bool(idx.key_est[i]),
                    "bpm": round(float(idx.bpm[i]), 1) if np.isfinite(idx.bpm[i]) else None,
                    "bpm_est": bool(idx.bpm_est[i])}
        try:
            return json.dumps(await asyncio.to_thread(work))
        except (Exception, SystemExit) as e:
            return json.dumps({"error": str(e)})

    @method()
    async def Details(self, paths: "as") -> "s":  # noqa: F821
        """For the results window: key, tempo, length and type per path (JSON {path: {...}})."""
        self.act.touch()

        def work():
            idx = self.eng.index()
            out = {}
            for p in paths:
                i = self.eng.pos(p)
                if i is None:
                    continue
                out[p] = self.eng.detail(i)
            return out
        try:
            return json.dumps(await asyncio.to_thread(work))
        except (Exception, SystemExit) as e:
            return json.dumps({"error": str(e)})

    @method()
    async def SetTags(self, path: "s", tags: "s") -> "s":  # noqa: F821
        """The results window's "Edit tags…" for one file: -> Details (JSON)."""
        self.act.touch()
        try:
            out = await asyncio.to_thread(set_tags, self.eng, [path], json.loads(tags))
            return json.dumps(next(iter(out.values())))
        except (Exception, SystemExit) as e:
            return json.dumps({"error": str(e)})

    @method()
    async def SetTagsMany(self, paths: "as", tags: "s") -> "s":  # noqa: F821
        """The same for several selected files: -> {path: Details} (JSON)."""
        self.act.touch()
        try:
            return json.dumps(await asyncio.to_thread(set_tags, self.eng, list(paths), json.loads(tags)))
        except (Exception, SystemExit) as e:
            return json.dumps({"error": str(e)})

    @method()
    async def Packs(self) -> "s":  # noqa: F821
        """Genre list: packs, their genres, source (user/name/'') and suggestions."""
        self.act.touch()
        try:
            idx = self.eng.index()
            idx.genres()
            return json.dumps(idx.pack_info)
        except (Exception, SystemExit) as e:
            return json.dumps({"error": str(e)})

    @method()
    async def GenreCounts(self) -> "s":  # noqa: F821
        """{genre or group: number of samples} – the window shows only genres present in the library."""
        self.act.touch()
        try:
            from collections import Counter
            c = Counter(g for gs in self.eng.index().genres() for g in gs)
            return json.dumps(dict(c))
        except (Exception, SystemExit) as e:
            return json.dumps({"error": str(e)})

    @method()
    async def SetPackGenres(self, pack: "s", genres: "s") -> "s":  # noqa: F821
        """Confirm a pack's genres (JSON list; null = back to the name / nothing)."""
        self.act.touch()
        try:
            g = json.loads(genres)
            cfg = genret.load_cfg()
            packs = cfg.setdefault("packs", {})
            if g is None:
                packs.pop(pack, None)
            else:
                bad = [x for x in g if x not in genret.GROUP_OF]
                if bad:
                    raise ValueError(f"Unknown genre {bad}")
                packs[pack] = g
            genret.save_cfg(cfg)
            idx = self.eng.index()
            idx._genres = None                     # recomputed (~2 s)
            idx.genres()
            return json.dumps(idx.pack_info.get(pack, {}))
        except (Exception, SystemExit) as e:
            return json.dumps({"error": str(e)})

    @method()
    async def Browse(self, args: "s") -> "s":  # noqa: F821
        """args JSON: {filters, offset, limit, order: "path"|"shuffle", seed} -> {total, paths, details}."""
        self.act.touch()
        try:
            a = json.loads(args)
            out = await asyncio.to_thread(self.eng.browse, a.get("filters", ""), int(a.get("offset", 0)),
                                          int(a.get("limit", 100)), a.get("order", "path"),
                                          int(a.get("seed", 0)), a.get("text", ""),
                                          a.get("folder", ""))
            return json.dumps(out)
        except (Exception, SystemExit) as e:
            return json.dumps({"error": str(e)})

    @method()
    async def AddFolder(self, path: "s") -> "s":  # noqa: F821
        """Settings > Add folder: the folder joins the library and is analysed in the background
        (indeksoija.py, its own process). -> IndexStatus JSON."""
        self.act.touch()
        return json.dumps(add_folder(path))

    @method()
    async def RemoveFolder(self, path: "s") -> "s":  # noqa: F821
        """Settings > Remove folder: the library folder leaves the index (files on disk stay).
        -> JSON {removed, folder} or {error}. The index reloads in the background."""
        self.act.touch()
        out = await asyncio.to_thread(remove_folder, path)
        if "removed" in out:
            self.eng.reload_soon(out["folder"])
        return json.dumps(out)

    @method()
    async def IndexStatus(self) -> "s":  # noqa: F821
        """The background indexer: {state: running|done|error|"", folder, step, steps, title, line, queue, error}."""
        self.act.touch()
        return json.dumps(index_status())

    @method()
    async def Folders(self, parent: "s") -> "s":  # noqa: F821
        """The library folder tree one level at a time: "" -> roots. JSON {folders: [{name, path, count}], files}."""
        self.act.touch()
        try:
            return json.dumps(await asyncio.to_thread(self.eng.folders, parent))
        except (Exception, SystemExit) as e:
            return json.dumps({"error": str(e)})

    @method()
    async def Query(self, query: "s") -> "s":  # noqa: F821
        """The results window's search-as-you-type: paths as a JSON list, no result folder
        (the folder is made only by the Search call, once the search is confirmed)."""
        self.act.touch()
        try:
            text, tags = sh.parse_tags(query)
            text = await asyncio.to_thread(sh.translate, text) if text else ""
            paths = await asyncio.to_thread(self.eng.search, text, tags, FOLDER_RESULTS)
            return json.dumps({"paths": paths})
        except (Exception, SystemExit) as e:
            return json.dumps({"error": str(e)})

    @method()
    async def Search(self, query: "s") -> "s":  # noqa: F821
        self.act.touch()
        try:
            text, tags = sh.parse_tags(query)
            text = await asyncio.to_thread(sh.translate, text) if text else ""
            paths = await asyncio.to_thread(self.eng.search, text, tags, FOLDER_RESULTS)
            return results_folder(query, paths)
        except (Exception, SystemExit) as e:
            return f"ERROR: {e}"


async def main() -> None:
    eng, act = Engine(), Activity()
    http = fetchhttp.start(eng, act.touch)   # for the VST3 plugin (127.0.0.1 only)
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    bus.export(PROVIDER_PATH, ShellProvider(eng, act))
    bus.export(PROVIDER_PATH, SampleSearch(eng, act))
    await bus.request_name(BUS_NAME)
    log(f"{BUS_NAME} ready (prefixes: {', '.join(sorted(PREFIXES))})")
    while True:
        await asyncio.sleep(30)
        if IDLE_MIN > 0 and time.time() - act.last > IDLE_MIN * 60:
            log("Idle, shutting down (starts again on the next search).")
            if http:
                http.shutdown()
            bus.disconnect()
            return


if __name__ == "__main__":
    asyncio.run(main())
