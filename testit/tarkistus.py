"""Spot check: listen to random samples and mark what Fetch shows as right or wrong.
Gives an honest accuracy for the whole library (testit/tarkkuus.py section 6 reports it).

    .venv/bin/python -I testit/tarkistus.py              # continue (or start) the check
    .venv/bin/python -I testit/tarkistus.py --new 200    # draw a new sample (the old answers are kept in the file)
    .venv/bin/python -I testit/tarkistus.py --blind      # blind re-check: name the main class before seeing Fetch's

The sample: 150 random samples (at most 6 per pack series, not ones you have already corrected) + 50 more from
those whose instrument is only guessed from the audio. Answers: ~/.local/share/samplehaku/tarkistus.json,
saved after every sample, so q and continue later is fine.

Keys:  Enter = what is now shown is judged (unmarked = right) · i t b k = instrument / type / tempo / key wrong
       (press again: unsure, again: right; instrument: subclass wrong -> wrong -> unsure -> right)
       r = play again · space = stop · s = skip · u = back one · q = quit
A wrong instrument or type can be fixed at once: it is saved as an ordinary correction through the service
(the same as Edit tags…); tempo and key are only recorded here.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import termios
import time
import tty
from http.client import HTTPConnection
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

import samplehaku as sh  # noqa: E402
import soittimet as so  # noqa: E402
from tarkkuus import rel as rel_of, series_all, set_roots  # noqa: E402

STORE = Path(os.environ.get("SAMPLEHAKU_SPOTCHECK", sh.DATA_DIR / "tarkistus.json"))
FIELDS = ("inst", "type", "bpm", "key")
KEYS = {"i": "inst", "t": "type", "b": "bpm", "k": "key"}
CYCLE = {"inst": ["ok", "sub", "wrong", "unsure"], "type": ["ok", "wrong", "unsure"],
         "bpm": ["ok", "wrong", "unsure"], "key": ["ok", "wrong", "unsure"]}
MARK = {"ok": "\033[32m✓\033[0m", "sub": "\033[33m≈ subclass wrong\033[0m", "wrong": "\033[31m✗ wrong\033[0m",
        "unsure": "\033[2m? unsure\033[0m"}
PORT = int(os.environ.get("SAMPLEHAKU_HTTP_PORT", 47811))


# ---------------------------------------------------------------- data
def load_store() -> dict:
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return {}


def save_store(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    os.replace(tmp, STORE)


def shown(idx, i: int) -> dict:
    """What the window shows for sample i (same rules as hakupalvelu.Engine.detail)."""
    inst, est, _ = idx.instruments()
    p = idx.paths[i]
    fixed = idx.fixes.get(p, {})
    b = idx.bpm[i]
    return {
        "inst": inst[i], "inst_src": "user" if "inst" in fixed else ("audio" if est[i] else ("name" if inst[i] else "none")),
        "type": {1: "loop", 0: "oneshot"}[int(idx.stype[i])],
        "type_src": "user" if "type" in fixed else sh.sample_type_why(p, float(idx.dur[i]), b)[1],
        "bpm": round(float(b), 1) if np.isfinite(b) else None,
        "bpm_src": ("audio" if idx.bpm_est[i] else "name") if np.isfinite(b) else "none",
        "key": sh.key_name(int(idx.key_pc[i]), int(idx.key_mode[i])) if idx.key_pc[i] >= 0 else None,
        "key_src": ("audio" if idx.key_est[i] else "name") if idx.key_pc[i] >= 0 else "none",
        "dur": round(float(idx.dur[i]), 2),
    }


def draw(idx, n_uniform: int, n_audio: int, seed: int) -> list[dict]:
    inst, est, _ = idx.instruments()
    ser = series_all(idx.paths, idx.roots)
    rng = random.Random(seed)
    free = [i for i, p in enumerate(idx.paths) if p not in idx.fixes]
    rng.shuffle(free)
    per_series: dict[str, int] = {}
    picked, out = set(), []
    for i in free:
        if len(out) >= n_uniform:
            break
        s = ser[i]
        if per_series.get(s, 0) >= 6:
            continue
        if not os.path.exists(idx.paths[i]):          # checked only for the picked ones (network share)
            continue
        per_series[s] = per_series.get(s, 0) + 1
        picked.add(i)
        out.append({"path": idx.paths[i], "part": "uniform"})
    n_extra = 0
    for i in free:
        if n_extra >= n_audio:
            break
        if est[i] and i not in picked and os.path.exists(idx.paths[i]):
            out.append({"path": idx.paths[i], "part": "audio"})
            n_extra += 1
    rng.shuffle(out)                     # mixed: you can't tell which part a sample is from
    return out


# ---------------------------------------------------------------- service (corrections)
def ensure_service() -> None:
    subprocess.run(["gdbus", "call", "--session", "--dest", "fi.samplehaku.Search", "--object-path",
                    "/fi/samplehaku/SearchProvider", "--method", "fi.samplehaku.Search.Info", ""],
                   capture_output=True, timeout=120)


def set_tags(path: str, tags: dict) -> str:
    """-> '' or an error message."""
    for attempt in range(2):
        try:
            c = HTTPConnection("127.0.0.1", PORT, timeout=120)
            c.request("POST", "/v1/settags", json.dumps({"paths": [path], "tags": tags}),
                      {"X-Fetch-Client": "spotcheck", "Content-Type": "application/json"})
            r = json.loads(c.getresponse().read() or b"{}")
            c.close()
            return r.get("error", "")
        except OSError:
            if attempt == 0:
                ensure_service()
                time.sleep(1)
    return "the search service is not answering"


# ---------------------------------------------------------------- terminal
def getch() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        c = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if c == "\x03":
        raise KeyboardInterrupt
    return c


class Player:
    def __init__(self):
        self.p = None

    def play(self, path: str) -> None:
        self.stop()
        self.p = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(1)
            except subprocess.TimeoutExpired:
                self.p.kill()
        self.p = None


def pick_instrument(prompt: str) -> str | None:
    """Words typed -> matching instrument classes (all words are prefixes of words in the label path)."""
    nodes = list(so.LABEL)
    while True:
        try:
            s = input(prompt).strip().lower()
        except EOFError:
            return None
        if not s:
            return None
        words = s.split()
        hits = []
        for n in nodes:
            lw = re.findall(r"[a-z0-9]+", (so.label_path(n) + " " + n).lower())
            if all(any(x.startswith(w) for x in lw) for w in words):
                hits.append(n)
        exact = [n for n in hits if so.LABEL[n].lower() == s or n == s]
        if len(exact) == 1 or len(hits) == 1:
            return (exact or hits)[0]
        if not hits:
            print("  no match – try e.g. 'closed hat', 'perc shaker', 'synth lead' (Enter = skip)")
            continue
        hits.sort(key=lambda n: (n.count("/"), len(n)))
        for j, n in enumerate(hits[:12], 1):
            print(f"  {j:2d}  {so.label_path(n)}")
        try:
            c = input("  number (Enter = type again): ").strip()
        except EOFError:
            return None
        if c.isdigit() and 1 <= int(c) <= min(12, len(hits)):
            return hits[int(c) - 1]


def fmt(field: str, s: dict) -> str:
    v = s[field]
    if field == "inst":
        txt = so.label_path(v) if v else "–"
    elif field == "type":
        txt = {"loop": "Loop", "oneshot": "One-shot"}[v]
    elif field == "bpm":
        txt = f"{v:g}" if v is not None else "–"
    else:
        txt = v or "–"
    src = s[field + "_src"]
    est = "~" if src in ("audio", "length", "beats", "short", "default") else " "
    return f"{est}{txt:34s} \033[2m({src})\033[0m"


def screen(k: int, n: int, item: dict, s: dict, verdict: dict, root: str, done: int) -> None:
    rel = rel_of(item["path"])
    folder, name = os.path.split(rel)
    print("\033[2J\033[H", end="")
    print(f"\033[1mSpot check {k + 1}/{n}\033[0m  \033[2m({done} judged)\033[0m\n")
    print(f"  \033[1m{name}\033[0m")
    print(f"  \033[2m{folder.replace('/', ' / ')}   {s['dur']:g} s\033[0m\n")
    names = {"inst": "Instrument", "type": "Type", "bpm": "Tempo", "key": "Key"}
    for key, f in KEYS.items():
        print(f"  [{key}] {names[f]:11s} {fmt(f, s)} {MARK[verdict[f]]}")
    print("\n  \033[2mEnter = judged (unmarked = right) · i t b k = mark · r replay · space stop · s skip · "
          "u back · q quit\033[0m")


# ---------------------------------------------------------------- blind re-check
MAINS = [n for n in so.LABEL if "/" not in n]          # Drums … FX: keys 1–9, 0
MAIN_KEYS = "1234567890"


def blind_items(st: dict, n_control: int = 18, seed: int = 1) -> list[dict]:
    """The spot check's audio guesses + some name-based ones as a control (you can't tell which is which)."""
    done = [it for it in st.get("items", []) if it.get("verdict") and it.get("shown")]
    audio = [it for it in done if it["shown"]["inst_src"] == "audio"]
    named = [it for it in done if it["shown"]["inst_src"] == "name"]
    rng = random.Random(seed)
    out = [{"path": it["path"], "part": "audio", "fetch": it["shown"]["inst"]} for it in audio]
    out += [{"path": it["path"], "part": "control", "fetch": it["shown"]["inst"]}
            for it in rng.sample(named, min(n_control, len(named)))]
    rng.shuffle(out)
    return out


def blind(st: dict, root: str) -> None:
    b = st.setdefault("blind", {})
    if not b.get("items"):
        b["items"] = blind_items(st)
        b["created"] = time.strftime("%Y-%m-%d %H:%M")
        save_store(st)
    items = b["items"]
    player = Player()
    k = next((j for j, it in enumerate(items) if "answer" not in it), len(items))
    last = ""
    try:
        while k < len(items):
            it = items[k]
            if not os.path.exists(it["path"]):
                it["answer"] = None
                k += 1
                continue
            player.play(it["path"])
            while True:
                rel = rel_of(it["path"])
                folder, name = os.path.split(rel)
                print("\033[2J\033[H", end="")
                print(f"\033[1mBlind check {k + 1}/{len(items)}\033[0m  \033[2mwhat is it? (main class only)\033[0m")
                print(f"\033[2m{last}\033[0m\n")
                print(f"  \033[1m{name}\033[0m\n  \033[2m{folder.replace('/', ' / ')}\033[0m\n")
                for key, m in zip(MAIN_KEYS, MAINS):
                    print(f"  [{key}] {so.LABEL[m]}")
                print("\n  \033[2m? = can't tell · r replay · space stop · u back · q quit\033[0m")
                c = getch()
                if c in MAIN_KEYS or c == "?":
                    ans = MAINS[MAIN_KEYS.index(c)] if c != "?" else "unsure"
                    it.update(answer=ans, t=round(time.time()))
                    save_store(st)
                    f = it["fetch"].split("/")[0] if it["fetch"] else "-"
                    if ans == "unsure":
                        last = "previous: can't tell"
                    else:
                        same = "\033[32m✓ same\033[0m\033[2m" if f == ans else "\033[31m✗ differs\033[0m\033[2m"
                        last = f"previous: you {so.LABEL[ans]} · Fetch {so.label_path(it['fetch']) if it['fetch'] else '–'} {same}"
                    k += 1
                    break
                elif c == "r":
                    player.play(it["path"])
                elif c == " ":
                    player.stop()
                elif c == "u" and k > 0:
                    k -= 1
                    items[k].pop("answer", None)
                    last = ""
                    break
                elif c in ("q", "\x1b"):
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    finally:
        player.stop()
    done = sum(1 for it in items if it.get("answer"))
    print(f"\n{done}/{len(items)} answered, saved in {STORE}.")
    print("All done! Results: testit/aja.sh accuracy (section 6)." if k >= len(items)
          else "Continue later with the same command.")


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", type=int, metavar="N", help="draw a new sample of N (+ a quarter extra audio guesses)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--blind", action="store_true",
                    help="blind re-check: you name the main class first, Fetch's answer is shown only afterwards")
    a = ap.parse_args()
    if not sys.stdin.isatty():
        sys.exit("Run this in a terminal.")
    print("Loading the index…")
    idx = sh.Index(sh.db())
    pos = {p: i for i, p in enumerate(idx.paths)}
    set_roots(idx.paths, idx.roots)
    root = None                                        # rel_of() uses each path's own library root
    st = load_store()
    if a.blind:
        if not st.get("items"):
            sys.exit("Do the spot check first (without --blind).")
        return blind(st, root)
    if a.new or not st.get("items"):
        n = a.new or 200
        seed = a.seed if a.seed is not None else int(time.time())
        old = st.get("rounds", [])
        if st.get("items"):
            old.append({"seed": st.get("seed"), "items": st["items"]})
        st = {"seed": seed, "created": time.strftime("%Y-%m-%d %H:%M"), "rounds": old,
              "items": draw(idx, n - n // 4, n // 4, seed)}
        save_store(st)
    items = st["items"]
    player = Player()
    k = next((j for j, it in enumerate(items) if "verdict" not in it), len(items))
    try:
        while k < len(items):
            item = items[k]
            i = pos.get(item["path"])
            if i is None or not os.path.exists(item["path"]):
                item["verdict"] = None                  # gone from the library
                k += 1
                continue
            s = item.get("shown") or shown(idx, i)
            verdict = dict(item.get("verdict") or {f: "ok" for f in FIELDS})
            done = sum(1 for it in items if it.get("verdict"))
            player.play(item["path"])
            while True:
                screen(k, len(items), item, s, verdict, root, done)
                c = getch()
                if c in KEYS:
                    f = KEYS[c]
                    cyc = CYCLE[f]
                    verdict[f] = cyc[(cyc.index(verdict[f]) + 1) % len(cyc)]
                elif c == "r":
                    player.play(item["path"])
                elif c == " ":
                    player.stop()
                elif c in ("\r", "\n"):
                    fix = {}
                    if verdict["inst"] in ("sub", "wrong"):
                        node = pick_instrument("  right instrument (Enter = skip): ")
                        if node:
                            fix["inst"] = node
                    if verdict["type"] == "wrong":
                        fix["type"] = "oneshot" if s["type"] == "loop" else "loop"
                    if verdict["bpm"] == "wrong":
                        v = input("  right tempo (Enter = skip, 0 = has none): ").strip().replace(",", ".")
                        if re.fullmatch(r"\d+(\.\d+)?", v):
                            item["right_bpm"] = float(v)
                    if verdict["key"] == "wrong":
                        v = input("  right key, e.g. Am / F# (Enter = skip, - = has none): ").strip()
                        if v == "-" or (v and sh.parse_key(v, strict=False)):
                            item["right_key"] = v
                    if fix:
                        err = set_tags(item["path"], fix)
                        item["fix"] = fix
                        if err:
                            item["fix_error"] = err
                            print(f"  \033[31mcorrection not saved: {err}\033[0m (recorded here)")
                            time.sleep(1.5)
                    item.update(shown=s, verdict=verdict, t=round(time.time()))
                    save_store(st)
                    k += 1
                    break
                elif c == "s":
                    item.update(shown=s, verdict=None, t=round(time.time()))
                    save_store(st)
                    k += 1
                    break
                elif c == "u" and k > 0:
                    k -= 1
                    break
                elif c in ("q", "\x1b"):
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    finally:
        player.stop()
    done = sum(1 for it in items if it.get("verdict"))
    print(f"\n{done}/{len(items)} judged, saved in {STORE}.")
    if k >= len(items):
        print("All done! Results: testit/aja.sh accuracy (section 6).")
    else:
        print("Continue later with the same command.")


if __name__ == "__main__":
    main()
