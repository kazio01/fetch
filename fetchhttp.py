#!/usr/bin/env python3
"""
fetchhttp – a small JSON API on 127.0.0.1 for clients that can't use D-Bus:
the Fetch VST3 plugin (Linux and Windows).

On Linux the search service (hakupalvelu.py) starts it next to its D-Bus interface.
On Windows (no D-Bus, no GNOME) run it on its own:

    python fetchhttp.py            # serves until stopped (or SAMPLEHAKU_IDLE_MIN minutes idle)

All requests are POST /v1/<name> with a JSON body; answers are JSON, errors {"error": "..."}.

    ping     {}                                          -> {ok, version, ready, error}
    browse   {text, filters, offset, limit, order, seed, folder} -> {total, paths, details}
             (folder: only the samples under that library folder, the Folders tree)
    folders  {parent} -> {folders: [{name, path, count}], files}: the library folder tree one level
             at a time; parent "" = the library roots
    addfolder   {path} -> indexstatus: add a library folder, analysed in the background (indeksoija.py)
    removefolder {path} -> {removed, folder}: a library folder leaves the index (files on disk stay)
    indexstatus {} -> {state, folder, step, steps, title, line, queue, error}
    similar  {path, conds: [timbre, key, bpm], n}        -> {paths, details, skipped}
    details  {paths: [...]}                              -> {path: details}
    catalog  {}                -> filter choices for the plugin's sidebar (types, sound tags, instrument
                                  tree, genres with counts, collections)
    folder   {path, filters}   -> {total, paths, details}: a collection folder, filtered like browse
    like     {paths, on}       -> {path: liked}: on true/false = like/unlike, missing = toggle
    recent   {add?}            -> {recent: [query, …]} (shared with the Fetch window, ikkuna.json)
    settags  {paths, tags}     -> {path: details}: "Edit tags…" ({type?, inst?, tags?}; null = automatic)

Every "details" entry also carries "liked" (in Liked Samples).

Only local clients are served: the socket is bound to 127.0.0.1, requests need the header
"X-Fetch-Client" and must not carry an "Origin" header (a web page in a browser can't call this).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = int(os.environ.get("SAMPLEHAKU_HTTP_PORT", 47811))
MAX_BODY = 1 << 20
MAX_LIMIT = 500


# short chip labels, as in the Fetch window (samplehaku_tulokset.py)
GROUP_SHORT = {"hiphop-rnb": "Hip Hop", "house-techno": "House", "pop-edm": "Pop/EDM", "live": "Live",
               "bass-music": "Bass music", "electronic": "Electronic", "global": "Global", "cinematic": "Cinematic"}
GENRE_SHORT = {"afropop-and-afrobeats": "Afrobeats", "progressive-house": "Progressive", "tearout": "Tearout",
               "big-room-house": "Big Room", "indie-electronic": "Indie Electronic", "latin-american": "Latin",
               "middle-eastern": "Middle East", "lo-fi-hip-hop": "Lo-Fi Hip Hop", "brazilian-funk": "Brazil Funk"}


def _version() -> str:
    try:
        return (Path(__file__).resolve().parent / "VERSION").read_text().strip()
    except OSError:
        return ""


class Api:
    """The request handlers; eng = hakupalvelu.Engine, touch() keeps the service alive."""

    def __init__(self, eng, touch=lambda: None):
        self.eng, self.touch = eng, touch

    def ping(self, a: dict) -> dict:
        torch = sys.modules.get("torch")             # which build the service runs (Windows: the CPU one)
        return {"ok": True, "version": _version(), "ready": self.eng.ready.is_set(),
                "error": self.eng.error, "torch": getattr(torch, "__version__", "")}

    def browse(self, a: dict) -> dict:
        if (a.get("text") or "").strip():
            self.eng.ready.wait(60)      # text search needs CLAP's text model
        out = self.eng.browse(str(a.get("filters", "")), max(0, int(a.get("offset", 0))),
                              max(1, min(MAX_LIMIT, int(a.get("limit", 100)))),
                              str(a.get("order", "path")), int(a.get("seed", 0)), str(a.get("text", "")),
                              str(a.get("folder", "")))
        return self._liked(out)

    def folders(self, a: dict) -> dict:
        return self.eng.folders(str(a.get("parent", "")))

    def addfolder(self, a: dict) -> dict:
        from hakupalvelu import add_folder
        return add_folder(str(a.get("path", "")))

    def removefolder(self, a: dict) -> dict:
        from hakupalvelu import remove_folder
        out = remove_folder(str(a.get("path", "")))
        if "removed" in out:
            self.eng.reload_soon(out["folder"])
        return out

    def indexstatus(self, a: dict) -> dict:
        from hakupalvelu import index_status
        return index_status()

    @staticmethod
    def _liked(out: dict) -> dict:
        from samplehaku_kokoelmat import LIKED
        for p, d in out.get("details", {}).items():
            d["liked"] = LIKED.has(p)
        return out

    def catalog(self, a: dict) -> dict:
        import genret
        import soittimet
        import samplehaku as sh
        from collections import Counter
        from samplehaku_kokoelmat import COLLECTIONS_DIR, LIKED_DIR
        idx = self.eng.index()
        counts = Counter(g for gs in idx.genres() for g in gs)
        inst_counts = Counter()
        for x in idx.instruments()[0]:
            parts = (x or "").split("/")
            for i in range(1, len(parts) + 1):
                inst_counts["/".join(parts[:i])] += 1 if x else 0

        def node(n: str) -> dict:
            kids = soittimet.children(n)
            return {"id": n, "label": soittimet.chip_label(n), "path": soittimet.label_path(n),
                    "count": inst_counts.get(n, 0), "children": [node(k) for k in kids]}

        insts = []
        for m in soittimet.MAINS:
            subs = []
            for label, kind, val in soittimet.SUBS.get(m, []):
                e = {"label": label, "kind": kind, "value": val}
                if kind == "inst":
                    e["children"] = [node(k) for k in soittimet.children(val)]
                subs.append(e)
            insts.append({"id": m, "label": soittimet.chip_label(m), "path": soittimet.LABEL[m],
                          "count": inst_counts.get(m, 0), "subs": subs})
        sound = []
        try:
            cfg = json.loads(sh.TAGS_PATH.read_text())
            for t in cfg.get("tags", cfg.get("tagit")):
                sound.append({"name": t.get("names", t.get("nimet"))[0].lower(),
                              "group": t.get("feature", t.get("piirre"))})
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            sound = [{"name": n, "group": g} for n, g in (
                ("dark", "centroid"), ("bright", "centroid"), ("bass", "low"), ("noisy", "flat"), ("tonal", "flat"),
                ("punchy", "attack"), ("soft", "attack"), ("tight", "tail"), ("long", "tail"))]
        colls = sorted(p for p in COLLECTIONS_DIR.iterdir() if p.is_dir()) if COLLECTIONS_DIR.is_dir() else []
        if LIKED_DIR not in colls:
            colls.insert(0, LIKED_DIR)
        else:
            colls.remove(LIKED_DIR)
            colls.insert(0, LIKED_DIR)
        return {
            "types": [{"name": "loop", "label": "Loop"}, {"name": "oneshot", "label": "One-shot"}],
            "sound": sound,
            "instruments": insts,
            "roles": [{"id": r, "label": l} for r, l in soittimet.ROLES],
            "tag_labels": dict(soittimet.TAG_LABEL),
            # for Edit tags: the whole instrument tree and the style tag groups
            "inst_tree": [node(m) for m in soittimet.MAINS],
            "tag_groups": [{"group": g, "tags": [{"id": t, "label": l} for t, l in ts]}
                           for g, ts in soittimet.TAG_GROUPS],
            "genres": [{"id": g, "label": lbl, "short": GROUP_SHORT.get(g, lbl), "count": counts.get(g, 0),
                        "genres": [{"id": gid, "label": gl, "short": GENRE_SHORT.get(gid, gl),
                                    "count": counts.get(gid, 0)}
                                   for gid, gl in genret.GENRES[g] if counts.get(gid)]}
                       for g, lbl in genret.GROUPS if counts.get(g)],
            "collections": [{"path": str(p), "name": p.name, "liked": p == LIKED_DIR} for p in colls],
            "total": len(idx.paths),
        }

    def folder(self, a: dict) -> dict:
        import numpy as np
        import samplehaku as sh
        folder = Path(str(a["path"]))
        audio = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".opus", ".m4a"}
        files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in audio),
                       key=lambda p: p.name.lower()) if folder.is_dir() else []
        _, tags = sh.parse_tags(str(a.get("filters", "")))
        idx = self.eng.index()
        mask = idx.tag_mask(tags) if tags else None
        paths, details = [], {}
        for p in files:
            i = self.eng.pos(str(p))
            if mask is not None and (i is None or not bool(mask[i])):
                continue
            paths.append(str(p))
            if i is not None:
                details[str(p)] = self.eng.detail(i)
        order = str(a.get("order", ""))
        if order.startswith("sort:") and paths:
            _, field, direction = (order.split(":") + ["asc"])[:3]
            keys = self.eng.sort_keys(field)
            k = [float(keys[i]) if (i := self.eng.pos(p)) is not None else np.inf for p in paths]
            o = sorted(range(len(paths)), key=lambda j: k[j], reverse=direction == "desc")
            paths = [paths[j] for j in o]
        return self._liked({"total": len(paths), "paths": paths, "details": details})

    def like(self, a: dict) -> dict:
        from samplehaku_kokoelmat import LIKED
        out = {}
        for p in [str(x) for x in a.get("paths", [])][:MAX_LIMIT]:
            on = a.get("on")
            if on is None:
                out[p] = LIKED.toggle(p)
            else:
                (LIKED.add if on else LIKED.remove)(p)
                out[p] = bool(on)
        return out

    def settags(self, a: dict) -> dict:
        import hakupalvelu
        tags = a.get("tags")
        if tags is not None and not isinstance(tags, dict):
            raise ValueError("tags must be an object or null")
        out = hakupalvelu.set_tags(self.eng, [str(p) for p in a.get("paths", [])][:MAX_LIMIT], tags)
        return self._liked({"details": out})["details"]

    def recent(self, a: dict) -> dict:
        cfg_path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "samplehaku/ikkuna.json"
        try:
            cfg = json.loads(cfg_path.read_text())
        except (OSError, ValueError):
            cfg = {}
        rec = cfg.get("recent", [])
        q = str(a.get("add") or "").strip()
        if q:
            rec = [{"q": q, "t": time.time()}] + [r for r in rec if r.get("q") != q]
            cfg["recent"] = rec[:20]
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cfg_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1))
            tmp.replace(cfg_path)
        return {"recent": [r["q"] for r in sorted(rec, key=lambda r: -r.get("t", 0))][:20]}

    def similar(self, a: dict) -> dict:
        conds = {c for c in a.get("conds", ["timbre"]) if c in ("timbre", "key", "bpm")} or {"timbre"}
        n = max(1, min(MAX_LIMIT, int(a.get("n", 100))))
        paths, skipped = self.eng.similar_combo(str(a["path"]), conds, n)
        return self._liked({"paths": paths, "skipped": skipped, "details": self._details(paths)})

    def details(self, a: dict) -> dict:
        return self._liked({"details": self._details([str(p) for p in a.get("paths", [])][:MAX_LIMIT])})["details"]

    def _details(self, paths: list[str]) -> dict:
        out = {}
        for p in paths:
            i = self.eng.pos(p)
            if i is not None:
                out[p] = self.eng.detail(i)
        return out


def make_handler(api: Api, port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Fetch"

        def log_message(self, fmt, *args):   # quiet; errors are answered as JSON
            pass

        def _send(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(405, {"error": "use POST"})

        def do_POST(self):
            if (self.headers.get("Origin") is not None or not self.headers.get("X-Fetch-Client")
                    or self.headers.get("Host", "") not in hosts):
                self._send(403, {"error": "forbidden"})
                return
            name = self.path.removeprefix("/v1/")
            fn = getattr(api, name, None) if name in ("ping", "browse", "similar", "details", "catalog", "folder", "folders", "addfolder", "removefolder", "indexstatus", "like", "recent", "settags") else None
            if fn is None:
                self._send(404, {"error": f"unknown request {self.path}"})
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > MAX_BODY or n < 0:               # a negative length would read until the client hangs up
                    raise ValueError("bad request length")
                a = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(a, dict):
                    raise ValueError("expected a JSON object")
            except (ValueError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
                return
            api.touch()
            try:
                self._send(200, fn(a))
            except (Exception, SystemExit) as e:   # samplehaku uses sys.exit for "index is empty"
                self._send(200, {"error": str(e) or e.__class__.__name__})

    return Handler


def start(eng, touch=lambda: None, port: int = PORT) -> ThreadingHTTPServer | None:
    """Serve in a background thread. None if disabled (port 0) or the port is taken."""
    if port <= 0:
        return None
    try:
        srv = ThreadingHTTPServer((HOST, port), make_handler(Api(eng, touch), port))
    except OSError as e:
        print(f"Fetch HTTP API not started ({HOST}:{port}): {e}", file=sys.stderr, flush=True)
        return None
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"Fetch HTTP API on http://{HOST}:{port}/v1/", file=sys.stderr, flush=True)
    return srv


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hakupalvelu as hp
    eng, act = hp.Engine(), hp.Activity()
    if start(eng, act.touch) is None:
        sys.exit(1)
    # standalone (Windows): keep running unless SAMPLEHAKU_IDLE_MIN is set explicitly
    idle = float(os.environ.get("SAMPLEHAKU_IDLE_MIN", 0))
    while True:
        time.sleep(30)
        if idle > 0 and time.time() - act.last > idle * 60:
            print("Idle, shutting down.", file=sys.stderr, flush=True)
            return


if __name__ == "__main__":
    main()
