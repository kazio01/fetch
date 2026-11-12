"""Stress and fuzz test of the search service's HTTP API (read-only requests; the user's settings are not
touched). Starts the service through D-Bus if needed.

    /usr/bin/python3 -I testit/rasitus.py [--seconds 60] [--clients 8]

1. Fuzz: malformed requests, wrong types, odd characters, half-typed filters -> always an answer in JSON,
   never a hang or a crash.
2. Load: several clients browse, search, open folders and ask for similar sounds at once, while the index
   file's time stamp is touched so the service reloads it in the background -> no failed request; reports
   the response times, and the service's memory, threads and open files before and after.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

PORT = int(os.environ.get("SAMPLEHAKU_HTTP_PORT", 47811))
DB = Path(os.environ.get("SAMPLEHAKU_DIR", Path.home() / ".local/share/samplehaku")) / "index.db"
bad: list[str] = []


def post(name: str, body, timeout: float = 120, raw: bytes | None = None, headers: dict | None = None):
    c = HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    h = {"X-Fetch-Client": "stress", "Content-Type": "application/json"} if headers is None else headers
    c.request("POST", "/v1/" + name, raw if raw is not None else json.dumps(body), h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data)


def service_pid() -> int:
    out = subprocess.run(["ss", "-ltnpH", f"sport = :{PORT}"], capture_output=True, text=True).stdout
    return int(out.split("pid=")[1].split(",")[0]) if "pid=" in out else 0


def usage(pid: int) -> dict:
    try:
        st = Path(f"/proc/{pid}/status").read_text()
        g = lambda k: int(next(ln.split()[1] for ln in st.splitlines() if ln.startswith(k)))   # noqa: E731
        return {"rss_mb": g("VmRSS:") // 1024, "anon_mb": g("RssAnon:") // 1024, "threads": g("Threads:"),
                "fds": len(os.listdir(f"/proc/{pid}/fd"))}
    except (OSError, StopIteration, ValueError):
        return {}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  \033[32m✓\033[0m " if ok else "  \033[31m✗\033[0m ") + name + (f"  \033[2m{detail[:110]}\033[0m" if detail else ""))
    if not ok:
        bad.append(name)


def fuzz(sample: list[str]) -> None:
    print("\033[1m== Fuzz\033[0m")
    cases = [
        ("browse", {"limit": "x"}), ("browse", {"offset": -5, "limit": 10 ** 9}), ("browse", {"order": "sort:nope:asc"}),
        ("browse", {"filters": "bpm:"}), ("browse", {"filters": "key:zz +loop"}), ("browse", {"filters": "+" * 500}),
        ("browse", {"text": "🥁 ' \" ; -- \\ %s {0} \x00 kick", "limit": 5}), ("browse", {"text": "-" * 50}),
        ("browse", {"text": "a " * 3000, "limit": 5}), ("browse", {"folder": "/nonexistent/x"}), ("browse", {"folder": "../.."}),
        ("browse", {"seed": "abc", "order": "shuffle"}), ("browse", {"text": None, "filters": None}),
        ("folders", {"parent": 5}), ("folders", {"parent": "/"}), ("details", {"paths": "notalist"}),
        ("details", {"paths": [1, None, {}, "/etc/passwd"]}), ("similar", {"path": "/etc/passwd"}), ("similar", {}),
        ("similar", {"path": sample[0], "conds": "timbre", "n": -3}), ("similar", {"path": sample[0], "conds": [1, 2], "n": "many"}),
        ("folder", {}), ("folder", {"path": "/proc"}), ("settags", {"paths": [], "tags": {"inst": "nope"}}),
        ("settags", {"paths": ["/nonexistent.wav"], "tags": []}), ("like", {"paths": "x"}), ("recent", {"add": None}),
        ("nosuch", {}), ("ping", []), ("ping", "text"),
    ]
    for name, body in cases:
        try:
            st, d = post(name, body, timeout=60)
            check(f"{name} {json.dumps(body)[:60]}", isinstance(d, (dict, list)), f"{st} {str(d)[:70]}")
        except Exception as e:  # noqa: BLE001
            check(f"{name} {json.dumps(body)[:60]}", False, f"{e.__class__.__name__}: {e}")
    for label, raw, hdr in (("not JSON", b"{oops", None), ("empty body", b"", None),
                            ("no client header", b"{}", {"Content-Type": "application/json"}),
                            ("Origin header (a browser)", b"{}", {"X-Fetch-Client": "x", "Origin": "http://evil.example"})):
        try:
            st, d = post("ping", None, raw=raw, headers=hdr)
            check(f"raw: {label}", isinstance(d, dict), f"{st} {d}")
        except Exception as e:  # noqa: BLE001
            check(f"raw: {label}", False, str(e))
    for label, req in (("negative Content-Length", b"POST /v1/ping HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Fetch-Client: s\r\nContent-Length: -1\r\n\r\n" % PORT),
                       ("huge Content-Length", b"POST /v1/ping HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Fetch-Client: s\r\nContent-Length: 99999999999\r\n\r\n" % PORT),
                       ("garbage", b"\x00\xff\x13GET\r\n\r\n")):
        s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
        try:
            s.sendall(req)
            s.settimeout(10)
            got = s.recv(4096)
            check(f"socket: {label} -> answered or closed", True, got[:40].decode("latin1"))
        except socket.timeout:
            check(f"socket: {label} -> answered or closed", False, "no answer in 10 s")
        except OSError as e:
            check(f"socket: {label} -> answered or closed", True, str(e))
        finally:
            s.close()
    st, d = post("ping", {})
    check("the service still answers after the fuzz", d.get("ok") is True, str(d))


def load(seconds: int, clients: int, sample: list[str], folders: list[str]) -> None:
    print(f"\033[1m== Load: {clients} clients, {seconds} s, index reloads in the background\033[0m")
    pid = service_pid()
    before = usage(pid)
    words = ["kick", "dark pad", "snare loop -kick", "warm analog bass", "vocal chop", "909", "techno hat", "dusty piano",
             "riser", "bright pluck", "deep sub", "latin percussion", "glitch", "punchy kick"]
    filters = ["", "+loop", "+oneshot", "inst:drums", "inst:drums/hat", "bpm:120-128", "key:Am~", "+dark", "tag:chords",
               "+loop bpm:124 inst:bass", "genre:house-techno"]
    orders = ["path", "shuffle", "sort:name:asc", "sort:bpm:desc", "sort:dur:asc", "sort:key:asc", "sort:inst:asc"]
    times: dict[str, list[float]] = {}
    errors: list[str] = []
    stop = time.time() + seconds
    lock = threading.Lock()

    def one(k: int) -> None:
        rng = random.Random(k)
        while time.time() < stop:
            kind = rng.choice(["page", "page", "search", "search", "folders", "similar", "details", "catalog", "folderpage"])
            try:
                t0 = time.perf_counter()
                if kind == "page":
                    st, d = post("browse", {"filters": rng.choice(filters), "offset": rng.randrange(0, 5000, 100), "limit": 100,
                                            "order": rng.choice(orders), "seed": rng.randrange(1 << 30)})
                elif kind == "search":
                    st, d = post("browse", {"text": rng.choice(words), "filters": rng.choice(filters[:6]), "limit": 100,
                                            "offset": rng.choice([0, 0, 100, 300]), "order": rng.choice(["path", "sort:name:asc"])})
                elif kind == "folders":
                    st, d = post("folders", {"parent": rng.choice([""] + folders)})
                elif kind == "folderpage":
                    st, d = post("browse", {"folder": rng.choice(folders), "limit": 100, "filters": rng.choice(filters[:4])})
                elif kind == "similar":
                    st, d = post("similar", {"path": rng.choice(sample), "conds": rng.choice([["timbre"], ["timbre", "key"], ["timbre", "bpm"]]), "n": 100})
                elif kind == "details":
                    st, d = post("details", {"paths": rng.sample(sample, 50)})
                else:
                    st, d = post("catalog", {})
                dt = time.perf_counter() - t0
                err = d.get("error") if isinstance(d, dict) else None
                # an unknown key/tempo of the source file is an answer, not a failure
                if st != 200 or (err and "unknown" not in err.lower()):
                    with lock:
                        errors.append(f"{kind}: {st} {err}")
                with lock:
                    times.setdefault(kind, []).append(dt)
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(f"{kind}: {e.__class__.__name__}: {e}")

    def toucher() -> None:              # a changed index.db time stamp = the service reloads the index in the background
        while time.time() < stop:
            time.sleep(20)
            try:
                os.utime(DB)
            except OSError:
                pass

    mtime = DB.stat().st_mtime_ns, DB.stat().st_atime_ns
    th = [threading.Thread(target=one, args=(k,)) for k in range(clients)] + [threading.Thread(target=toucher)]
    for t in th:
        t.start()
    peak = before
    while any(t.is_alive() for t in th):
        time.sleep(2)
        u = usage(pid)
        if u and u.get("rss_mb", 0) > peak.get("rss_mb", 0):
            peak = u
    os.utime(DB, ns=(mtime[1], mtime[0]))      # the time stamp back: the index caches stay valid
    n = sum(len(v) for v in times.values())
    check(f"{n} requests, none failed", not errors, "; ".join(errors[:3]))
    for kind, v in sorted(times.items()):
        v.sort()
        print(f"    {kind:11s} n={len(v):5d}  median {1000 * v[len(v) // 2]:7.0f} ms   p95 {1000 * v[int(len(v) * 0.95)]:7.0f} ms   max {1000 * v[-1]:7.0f} ms")
    check("the service is the same process (no crash, no restart)", service_pid() == pid, f"{pid} -> {service_pid()}")
    time.sleep(5)
    after = usage(pid)
    print(f"    before {before}\n    peak   {peak}\n    after  {after}")
    check("threads back to rest", after.get("threads", 0) <= before.get("threads", 0) + 4, f"{before.get('threads')} -> {after.get('threads')}")
    check("no leaked files", after.get("fds", 0) <= before.get("fds", 0) + 8, f"{before.get('fds')} -> {after.get('fds')}")
    st, d = post("ping", {})
    check("still answering", d.get("ok") is True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--clients", type=int, default=8)
    a = ap.parse_args()
    subprocess.run(["gdbus", "call", "--session", "--dest", "fi.samplehaku.Search", "--object-path", "/fi/samplehaku/SearchProvider",
                    "--method", "fi.samplehaku.Search.Info", "/"], capture_output=True, timeout=300)
    for _ in range(600):
        try:
            if post("ping", {}, timeout=5)[1].get("ready"):
                break
        except OSError:
            pass
        time.sleep(0.5)
    st, d = post("browse", {"limit": 300, "order": "shuffle", "seed": 7})
    sample = d["paths"]
    roots = [f["path"] for f in post("folders", {"parent": ""})[1]["folders"]]
    folders = roots + [f["path"] for r in roots for f in post("folders", {"parent": r})[1]["folders"][:8]]
    fuzz(sample)
    load(a.seconds, a.clients, sample, folders)
    print("\n\033[1;32mStress: all ok\033[0m" if not bad else f"\n\033[1;31mStress: {len(bad)} failed\033[0m: " + "; ".join(bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
