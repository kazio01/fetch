"""
End-to-end test of the Fetch portable package (Windows CI; also runs on Linux).
Run with the package's own Python after Setup:

    python ci_test.py PACKAGE_DIR LIBRARY_DIR [--unc]

Makes a small sample library, indexes it through fetch_portable.py (as "Index Samples.cmd" does),
starts the service (as the plugin does) and checks the HTTP API the plugin uses.
--unc: index the library through \\\\localhost\\C$\\… (a network path), as with samples on a NAS.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

PORT = 47899
SR = 44100
fails = 0


def ok(cond: bool, what: str, extra: str = "") -> None:
    global fails
    print(("  ok    " if cond else "  FAIL  ") + what + (f"  ({extra})" if extra else ""), flush=True)
    fails += not cond


def make_library(lib: Path) -> dict[str, Path]:
    rng = np.random.default_rng(1)
    t = lambda s: np.arange(int(SR * s)) / SR               # noqa: E731
    files = {}

    def put(rel: str, y: np.ndarray) -> None:
        p = lib / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        sf.write(p, (0.8 * y / (np.abs(y).max() + 1e-9)).astype(np.float32), SR)
        files[rel] = p

    x = t(0.6)
    put("Test Pack/Drums/Kicks/Kick 01.wav", np.sin(2 * np.pi * (50 + 120 * np.exp(-x * 30)) * x) * np.exp(-x * 7))
    x = t(0.25)
    put("Test Pack/Drums/Hats/Closed Hat 01.wav", rng.normal(size=x.size) * np.exp(-x * 40))
    x = t(4.0)                                              # 8 beats at 120 bpm
    beat = (x * 2) % 1
    put("Test Pack/Bass/Bass Loop 120bpm Am.wav", np.sin(2 * np.pi * 55 * x) * np.exp(-beat * 6))
    x = t(3.0)
    put("Sämple Pack/FX/Riser 01.wav", rng.normal(size=x.size) * x / 3)
    x = t(2.0)
    chord = sum(np.sin(2 * np.pi * f * x) for f in (220.0, 261.63, 329.63))
    put("Sämple Pack/Keys/Piano Chord C.wav", chord * np.exp(-x * 1.5))
    # an mp3 too (decoded by ffmpeg / libsndfile)
    mp3 = lib / "Sämple Pack/Keys/Piano Chord C copy.mp3"
    try:      # the package's own ffmpeg (imageio-ffmpeg): the runner has none on PATH
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        exe = shutil.which("ffmpeg")
    r = subprocess.run([exe, "-nostdin", "-v", "error", "-y", "-i", str(files["Sämple Pack/Keys/Piano Chord C.wav"]),
                        str(mp3)]) if exe else None
    if r is not None and r.returncode == 0:
        files["Sämple Pack/Keys/Piano Chord C copy.mp3"] = mp3
    return files


def api(name: str, body: dict | None = None, timeout: float = 120) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/{name}", data=json.dumps(body or {}).encode(),
                                 headers={"X-Fetch-Client": "ci-test", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def to_unc(p: Path) -> Path:
    d = p.resolve().drive              # "C:"
    return Path(f"\\\\localhost\\{d[0]}$" + str(p.resolve())[len(d):])


def main() -> None:
    pkg, lib = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    unc = "--unc" in sys.argv
    app = pkg / "app"
    py = sys.executable
    env = dict(os.environ, SAMPLEHAKU_HTTP_PORT=str(PORT), PATH=str(pkg / "bin") + os.pathsep + os.environ["PATH"],
               SAMPLEHAKU_COLLECTIONS=str(pkg / "data" / "ci-collections"))

    print("== Library")
    files = make_library(lib)
    ok(len(files) == 6, f"{len(files)} test files")
    root = lib
    if unc:
        root = to_unc(lib)
        if root.is_dir():
            print(f"  ok    network path {root}")
        else:
            print(f"  skip  network path {root} not reachable, indexing the local path")
            root = lib

    print("== Index (as Index Samples.cmd)")
    t0 = time.time()
    # --index-cpu-torch (CI with Setup -Cuda): the runner has no GPU and the CUDA build of torch dies there while
    # indexing, so the index is made with the CPU build in torch-cpu\; the service test below is the point
    ienv = dict(env, PYTHONPATH=str(pkg / "torch-cpu")) if "--index-cpu-torch" in sys.argv else env
    r = subprocess.run([py, str(app / "fetch_portable.py"), "index", str(root)], env=ienv)
    ok(r.returncode == 0, "index finished", f"{time.time() - t0:.0f} s")
    out = subprocess.run([py, str(app / "fetch_portable.py"), "check"], env=env, capture_output=True, text=True)
    print(out.stdout, out.stderr)
    chk = json.loads(out.stdout)
    ok(chk.get("files") == len(files), "all files indexed", f"{chk.get('files')}")
    ok(chk.get("features") == len(files), "sound features for all", f"{chk.get('features')}")
    ok(bool(chk.get("ffmpeg")), "ffmpeg found")
    ok(chk.get("model") is True, "model cached in data\\huggingface")

    print("== Service (as the plugin starts it)")
    log = open(pkg / "data" / "ci-service.log", "w", encoding="utf-8")
    srv = subprocess.Popen([py, str(app / "fetch_portable.py"), "serve"], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        ready = False
        for _ in range(240):
            try:
                if api("ping", timeout=2).get("ready"):
                    ready = True
                    break
            except OSError:
                pass
            time.sleep(0.5)
        ok(ready, "service ready")
        tv = api("ping").get("torch", "")
        want = "+cpu" if (pkg / "torch-cpu" / "torch").is_dir() or "+cpu" in chk.get("torch", "") else ""
        ok(tv.endswith("+cpu") if want else bool(tv), "service runs the CPU build of torch" if want else "service torch", tv)

        b = api("browse", {"limit": 50})
        paths = b.get("paths", [])
        ok(b.get("total") == len(files), "browse: everything", f"{b.get('total')} {b.get('error', '')}")
        ok(all("\\" not in p for p in paths), "index paths use /", paths[0] if paths else "")
        det = b.get("details", {})
        kick = next((p for p in paths if p.endswith("Kick 01.wav")), None)
        ok(kick is not None and det[kick].get("inst", "").startswith("drums"), "kick: instrument from the folder",
           det.get(kick, {}).get("inst", "") if kick else "")
        loop = next((p for p in paths if "Bass Loop" in p), None)
        if loop:
            d = det[loop]
            ok(d.get("bpm") == 120 and d.get("key") == "Am" and d.get("type") == "Loop", "loop: bpm, key, type from the name",
               f"{d.get('bpm')} {d.get('key')} {d.get('type')}")
            ok(d.get("pack", "").split("/")[-2:] == ["Test Pack", "Bass"], "folder label", d.get("pack", ""))

        s = api("browse", {"text": "kick drum", "limit": 3})
        ok(bool(s.get("paths")) and s["paths"][0].endswith("Kick 01.wav"), "text search: kick", str(s.get("paths", [])[:1]))
        s = api("browse", {"filters": "+loop", "limit": 10})
        ok(loop in s.get("paths", []) and kick not in s.get("paths", []), "filter +loop", str(s.get("total")))

        if kick:
            sim = api("similar", {"path": kick, "conds": ["timbre"], "n": 5})
            ok(len(sim.get("paths", [])) >= 3, "similar", sim.get("error", ""))
            # the plugin may send the path back with backslashes (juce::File)
            if os.name == "nt":
                dd = api("details", {"paths": [kick.replace("/", "\\")]})
                ok(len(dd) == 1, "details with a backslash path")
            lk = api("like", {"paths": [kick], "on": True})
            cat = api("catalog")
            liked = next((c["path"] for c in cat.get("collections", []) if c.get("liked")), None)
            fol = api("folder", {"path": liked}) if liked else {}
            ok(lk.get(kick) is True and fol.get("total", 0) >= 1, "like -> Liked Samples", str(fol.get("total")))
            again = api("details", {"paths": [kick]})
            ok(again.get(kick, {}).get("liked") is True, "liked state")
            api("like", {"paths": [kick], "on": False})
            again = api("details", {"paths": [kick]})
            ok(again.get(kick, {}).get("liked") is False, "unlike")
    finally:
        srv.terminate()
        srv.wait(10)
        log.close()
        if fails:
            print((pkg / "data" / "ci-service.log").read_text(encoding="utf-8", errors="replace")[-4000:])

    print(f"\n{'ALL OK' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
