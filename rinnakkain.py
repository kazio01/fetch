"""
Ordered parallel map for the indexers: decoding and analysing files in worker processes while the
main process writes to the database (and, in samplehaku index, runs CLAP on the GPU).

Standard library only. Workers are started with "spawn" on every platform (the same as Windows; no
fork after torch has started its threads) and with one BLAS/OpenMP thread each, so N workers use
N cores instead of N x cores.
"""
from __future__ import annotations

import os
from collections import deque


def default_jobs() -> int:
    """SAMPLEHAKU_JOBS, otherwise all cores but two (at least 1)."""
    env = os.environ.get("SAMPLEHAKU_JOBS", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    return max(1, (os.cpu_count() or 2) - 2)


_ONE_THREAD = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS")


def ordered_map(fn, items, jobs: int, ahead: int | None = None):
    """Yield fn(item) for each item in order. At most `ahead` results wait in memory (decoded audio
    is large), so a slow consumer does not make the workers fill the RAM. jobs <= 1: in this process.
    fn must be a module-level function (pickled by name)."""
    if jobs <= 1:
        for it in items:
            yield fn(it)
        return
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    ahead = ahead or jobs * 3
    saved = {k: os.environ.get(k) for k in _ONE_THREAD}
    for k in _ONE_THREAD:
        os.environ[k] = "1"      # read by the workers when they import numpy (spawned on demand)
    try:
        with ProcessPoolExecutor(jobs, mp_context=mp.get_context("spawn")) as ex:
            it = iter(items)
            q = deque(ex.submit(fn, x) for _, x in zip(range(ahead), it))
            try:
                while q:
                    r = q.popleft().result()
                    for x in it:
                        q.append(ex.submit(fn, x))
                        break
                    yield r
            except BaseException:
                for f in q:
                    f.cancel()
                raise
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
