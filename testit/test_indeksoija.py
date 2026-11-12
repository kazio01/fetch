"""1c) The background indexer (indeksoija.py) behind Settings > Add folder: queue, status, a
second folder while one runs, a failing step, a dead process. Runs in a temporary data folder
with stand-in steps (no model, no NPU): the real steps are tried by hand / in the window."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TMP = Path(tempfile.mkdtemp(prefix="fetch-indexer-"))
os.environ["SAMPLEHAKU_DIR"] = str(TMP / "data")
sys.path[:0] = [str(HERE), str(ROOT)]
from apu import check, otsikko, yhteenveto  # noqa: E402

import indeksoija as ix  # noqa: E402

lib = TMP / "lib"
(lib / "A").mkdir(parents=True)
(lib / "B").mkdir()
py = sys.executable


def fake_steps(folder: str):
    """Two quick steps that print progress like the real ones; a folder named FAIL fails."""
    code = ("import sys,time\nfor i in range(3):\n print(f'\\r  {i+1}/3 files', end='', flush=True); time.sleep(0.2)\n"
            "print()\nsys.exit(3 if sys.argv[1].endswith('FAIL') else 0)")
    return [("Audio embeddings", [py, "-c", code, folder]), ("Sound features", [py, "-c", code, folder])]


otsikko("Queue and status (in process)")
ix.steps = fake_steps
check("no status yet", ix.status().get("state") is None)
ix.main([str(lib / "A")])
st = ix.status()
check("one folder: done", st["state"] == "done" and st["done"] == [str(lib / "A")], str(st)[:120])
check("both steps ran", st["step"] == 2 and st["steps"] == 2)
check("queue empty afterwards", st["queue"] == [])
check("log has the progress lines", "3/3 files" in ix.LOG.read_text())

(lib / "FAIL").mkdir()
ix.main([str(lib / "FAIL"), str(lib / "B")])
st = ix.status()
check("a failing folder is reported", st["state"] == "error" and "FAIL" in st["error"] and "exit code 3" in st["error"],
      st.get("error", "")[:100])
check("the queue goes on after a failure", str(lib / "B") in st["done"] and st["queue"] == [])

otsikko("Dead process")
ix.write_json(ix.STATUS, {"state": "running", "pid": 999_999_999, "folder": "x"})
st = ix.status()
check("running with a gone pid -> error", st["state"] == "error" and "stopped" in st["error"])

otsikko("Detached start (the service's call)")
# the detached runner imports indeksoija afresh: give it the stand-in steps through a sitecustomize
site = TMP / "site"
site.mkdir()
(site / "sitecustomize.py").write_text(
    "import sys\nsys.path.insert(0, %r)\n" % str(ROOT)
    + "import indeksoija, time\n"
    + "def _steps(folder):\n"
    + "    return [('Step', [sys.executable, '-c', 'import time; print(\"1/1\"); time.sleep(1.5)'])]\n"
    + "indeksoija.steps = _steps\n")
os.environ["PYTHONPATH"] = str(site)
ix.write_json(ix.STATUS, {})
st = ix.start_detached([str(lib / "A")])
check("start: running at once", st["state"] == "running", str(st)[:100])
st2 = ix.start_detached([str(lib / "B")])
check("second folder while running: queued, same process", st2["state"] == "running" and st2["pid"] == st["pid"]
      and str(lib / "B") in st2["queue"], str(st2.get("queue")))
t0 = time.time()
while ix.status().get("state") == "running" and time.time() - t0 < 120:     # (idle-priority steps wait when the disk is busy)
    time.sleep(0.3)
st = ix.status()
check("both folders done by the one runner", st["state"] == "done" and st["queue"] == []
      and {str(lib / "A"), str(lib / "B")} <= set(st.get("done", [])), str(st)[:160])

sys.exit(yhteenveto("Background indexer"))
