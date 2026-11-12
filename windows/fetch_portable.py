r"""
Fetch portable (Windows): runs the search service and the indexer from one folder.

    <Fetch portable>\
        Setup.cmd, Index Samples.cmd, Fetch Service (console).cmd, README.txt
        app\            Fetch's Python code (this file is app\fetch_portable.py)
        python\         Python 3.12 with the packages (made by Setup.cmd)
        bin\ffmpeg.exe  (made by Setup.cmd)
        data\           index, settings, model and caches – everything Fetch writes, except
                        Documents\Fetch Collections (likes, renders) and %APPDATA%\Fetch (plugin)
        VST3\Fetch.vst3

    python fetch_portable.py serve            HTTP API for the VST3 plugin (127.0.0.1:47811)
    python fetch_portable.py index FOLDER… [--reuse OTHER.db]    CLAP (NVIDIA GPU if set up) + sound features (CPU, parallel);
                                              again = only new/changed files
    python fetch_portable.py setup-model      download the CLAP model (Setup.cmd)
    python fetch_portable.py check            versions, ffmpeg, model, index (JSON)

The folder can be moved: nothing outside it points into it except %APPDATA%\Fetch\server-command.txt
(Setup.cmd writes it again).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
DATA = ROOT / "data"
CFG = DATA / "config" / "samplehaku"


def setup_env() -> None:
    """Point every Fetch path into data\\ (before samplehaku & co. are imported: module constants)."""
    env = {
        "SAMPLEHAKU_DIR": DATA / "index",
        "SAMPLEHAKU_TAGS": CFG / "tagit.json",
        "SAMPLEHAKU_FIXES": CFG / "korjaukset.json",
        "SAMPLEHAKU_GENRES": CFG / "genret.json",
        "XDG_CONFIG_HOME": DATA / "config",          # ikkuna.json (recent searches)
        "XDG_CACHE_HOME": DATA / "cache",
        "HF_HOME": DATA / "huggingface",
        "NUMBA_CACHE_DIR": DATA / "cache" / "numba",
    }
    for k, v in env.items():
        os.environ.setdefault(k, str(v))
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ["FETCH_PORTABLE"] = "1"
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ["PATH"] = str(ROOT / "bin") + os.pathsep + os.environ.get("PATH", "")
    CFG.mkdir(parents=True, exist_ok=True)
    tags = Path(os.environ["SAMPLEHAKU_TAGS"])
    if not tags.exists() and (APP / "tagit.json").exists():
        tags.write_bytes((APP / "tagit.json").read_bytes())
    sys.path.insert(0, str(APP))


def _streams(log_name: str | None) -> None:
    """pythonw.exe has no console (sys.stderr is None): write to a log file instead.
    In a console: UTF-8 with replacement, so a sample name never stops the indexer."""
    if sys.stderr is None or sys.stdout is None:
        DATA.mkdir(parents=True, exist_ok=True)
        f = open(DATA / (log_name or "fetch.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def serve() -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")     # the model is on disk: no network check at start
    # The service needs few threads (a search is one matrix-vector product). With one per core, OpenBLAS
    # alone committed ~750 MB of thread buffers on a 24-thread CPU. Before numpy loads.
    # Only for this process: the indexer it starts (Settings > Add folder) must keep all cores.
    mine = [k for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS") if k not in os.environ]
    for k in mine:
        os.environ[k] = "4"
    # With an NVIDIA card, Setup installs the CPU build of the same torch into torch-cpu\ for the service:
    # the CUDA build would load its GPU libraries (~0.5 GB) for nothing. Indexing keeps the CUDA build.
    cpu_torch = ROOT / "torch-cpu"
    if (cpu_torch / "torch").is_dir():
        sys.path.insert(0, str(cpu_torch))
    import numpy  # noqa: F401 – the libraries read the thread counts when they load
    import torch  # noqa: F401
    for k in mine:
        del os.environ[k]
    import fetchhttp
    fetchhttp.main()


def index(folders: list[str]) -> None:
    import aanipiirteet
    import samplehaku as sh
    dirs, reuse = [], []
    it = iter(folders)
    for d in it:
        if d == "--reuse":
            db = next(it, "").strip().strip('"')
            if not Path(db).is_file():
                sys.exit(f"--reuse: not found: {db}")
            reuse += ["--reuse", db]
            continue
        p = Path(d.strip().strip('"'))
        if not p.is_dir():
            sys.exit(f"Not a folder: {d}")
        dirs.append(str(p))
    print("== 1/2 Audio embeddings (CLAP). Stop with Ctrl+C at any time: the next run continues.")
    sh.main(["index", *dirs, *reuse])
    print("\n== 2/2 Sound features, tempo and key (CPU, in parallel)")
    aanipiirteet.main(["--backend", "cpu"])
    print("\nDone. The plugin sees the new samples within a minute (or restart the service).")


def setup_model() -> None:
    import samplehaku as sh
    sh.Clap(sh.DEFAULT_MODEL)                 # downloads the full model (indexing)
    sh.TextClap(sh.DEFAULT_MODEL)             # and checks that the service's text part loads
    print(f"Model ready: {sh.DEFAULT_MODEL}")


def check() -> None:
    import shutil
    import sqlite3
    import samplehaku as sh
    out = {"python": sys.version.split()[0], "root": str(ROOT), "ffmpeg": shutil.which("ffmpeg"),
           "db": str(sh.DB_PATH)}
    try:
        import torch
        out["device"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    except Exception as e:           # noqa: BLE001 – reported, not raised
        out["device"] = f"ERROR {e}"
    for mod in ("numpy", "torch", "transformers", "librosa", "soundfile"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception as e:       # noqa: BLE001 – reported, not raised
            out[mod] = f"ERROR {e}"
    try:
        from huggingface_hub import try_to_load_from_cache
        out["model"] = bool(try_to_load_from_cache(sh.DEFAULT_MODEL, "config.json"))
    except Exception:                # noqa: BLE001
        out["model"] = False
    if sh.DB_PATH.exists():
        con = sqlite3.connect(sh.DB_PATH)
        out["files"] = con.execute("SELECT COUNT(*) FROM files WHERE audio_emb IS NOT NULL").fetchone()[0]
        try:
            out["features"] = con.execute("SELECT COUNT(*) FROM features WHERE centroid IS NOT NULL").fetchone()[0]
        except sqlite3.OperationalError:
            out["features"] = 0
    print(json.dumps(out, indent=1))


def main() -> None:
    setup_env()
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    _streams("service.log" if cmd == "serve" else None)
    if cmd == "serve":
        serve()
    elif cmd == "index" and args:
        index(args)
    elif cmd == "setup-model":
        setup_model()
    elif cmd == "check":
        check()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
