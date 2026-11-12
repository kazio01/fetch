"""
Background indexer for Settings > Library folders > Add folder (the Fetch window and the plugin).

    python indeksoija.py FOLDER [FOLDER …]     add the folders to the queue and process it

The search service starts this as its own detached process (not a child of a console, so closing
one or Ctrl+C there doesn't stop it), one at a time: a second Add folder only adds to the queue.
Each folder runs the same steps as by hand, on the most efficient hardware available:
  1. audio embeddings (CLAP): NVIDIA/ROCm GPU if PyTorch sees one, otherwise the CPU
  2. sound features, tempo and key: the NPU (Linux, IRON) if it answers, otherwise the CPU in parallel
Progress goes to <data>/indeksointi.json, which the service's IndexStatus reports:
  {state: running|done|error, folder, step, steps, line, queue, started, finished, error, pid}
The service notices the changed index.db and reloads it in the background (hakupalvelu.Engine.index).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import samplehaku as sh  # noqa: E402

STATUS = sh.DATA_DIR / "indeksointi.json"
QUEUE = sh.DATA_DIR / "indeksointi-jono.json"
LOG = sh.DATA_DIR / "indeksointi.log"
WINDOWS = os.name == "nt"


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(p: Path, v) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if WINDOWS:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                      # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status() -> dict:
    """The current state; a "running" state whose process is gone counts as stopped."""
    st = read_json(STATUS, {})
    if st.get("state") == "running" and not pid_alive(int(st.get("pid") or 0)):
        st["state"] = "error"
        st["error"] = st.get("error") or "The indexer stopped unexpectedly."
    st["queue"] = read_json(QUEUE, [])
    return st


def enqueue(folders: list[str]) -> list[str]:
    q = read_json(QUEUE, [])
    for f in folders:
        if f not in q:
            q.append(f)
    write_json(QUEUE, q)
    return q


def steps(folder: str) -> list[tuple[str, list[str]]]:
    """The commands for one folder. Each runs as its own process (the models free their memory)."""
    py = sys.executable
    portable = HERE / "fetch_portable.py"
    if WINDOWS and portable.is_file():
        # the portable package sets its data folders itself; it runs CLAP (+CUDA) and the features
        return [("Analyzing samples", [py, str(portable), "index", folder])]
    venv = HERE / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    main_py = str(venv) if venv.is_file() else py
    feats = Path.home() / ".local/bin/samplehaku-piirteet"            # NPU launcher (asenna_npu.sh)
    feat_cmd = [str(feats)] if feats.is_file() and not WINDOWS else [main_py, str(HERE / "aanipiirteet.py")]
    return [("Audio embeddings", [main_py, str(HERE / "samplehaku.py"), "index", folder]),
            ("Sound features, tempo and key", feat_cmd)]


def run_one(folder: str, st: dict) -> None:
    plan = steps(folder)
    st.update(folder=folder, steps=len(plan), step=0, line="")   # an earlier folder's error stays
    write_json(STATUS, st)
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {folder}\n")
        for k, (title, cmd) in enumerate(plan, 1):
            st.update(step=k, title=title, line="")
            write_json(STATUS, st)
            log.write(f"$ {' '.join(cmd)}\n")
            log.flush()
            env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            buf, last_write = b"", 0.0
            while True:
                ch = p.stdout.read(1)
                if not ch:
                    break
                if ch in b"\r\n":
                    line = buf.decode("utf-8", "replace").strip()
                    buf = b""
                    if line:
                        if ch == b"\n":
                            log.write(line + "\n")
                        st["line"] = line[:200]
                        if time.time() - last_write > 0.5:
                            write_json(STATUS, st)
                            last_write = time.time()
                else:
                    buf += ch
            rc = p.wait()
            if rc != 0:
                raise RuntimeError(f"{title} stopped (exit code {rc}): {st.get('line', '')}")


def main(argv: list[str]) -> None:
    if argv:
        enqueue([str(Path(a).expanduser().resolve()).replace("\\", "/") for a in argv])
    cur = read_json(STATUS, {})
    if cur.get("state") == "running" and pid_alive(int(cur.get("pid") or 0)) and cur.get("pid") != os.getpid():
        return                                           # another indexer works through the queue
    st = {"state": "running", "pid": os.getpid(), "started": time.time(), "done": []}
    while True:
        q = read_json(QUEUE, [])
        if not q:
            break
        folder = q[0]
        try:
            run_one(folder, st)
            st.setdefault("done", []).append(folder)
        except Exception as e:  # noqa: BLE001 – reported in the status, the queue goes on
            st["error"] = f"{folder}: {e}"
            with open(LOG, "a", encoding="utf-8") as log:
                log.write(f"ERROR {st['error']}\n")
        write_json(QUEUE, [f for f in read_json(QUEUE, []) if f != folder])
    st.update(state="error" if st.get("error") else "done", finished=time.time(), line="")
    write_json(STATUS, st)


def start_detached(folders: list[str]) -> dict:
    """Called by the service: queue the folders and start the indexer unless one already runs."""
    enqueue([f.replace("\\", "/") for f in folders])
    st = status()
    if st.get("state") == "running":
        return st
    kw, cmd = {}, [sys.executable, str(Path(__file__).resolve())]
    # Low priority (the steps inherit it): analysis takes minutes to hours and must not starve audio
    # (a DAW, the preview) of CPU or disk time – dropouts were heard while a big job ran at normal priority.
    if WINDOWS:
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000 | 0x00004000   # DETACHED | NEW_GROUP | NO_WINDOW | BELOW_NORMAL
    else:
        kw["start_new_session"] = True
        if shutil.which("ionice"):
            cmd = ["ionice", "-c", "3"] + cmd                                       # disk: idle class
        kw["preexec_fn"] = lambda: os.nice(10)                                    # CPU
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, **kw)
    write_json(STATUS, {"state": "running", "pid": p.pid, "started": time.time(), "line": "Starting…",
                        "folder": read_json(QUEUE, [""])[0] if read_json(QUEUE, []) else ""})
    return status()


if __name__ == "__main__":
    main(sys.argv[1:])
