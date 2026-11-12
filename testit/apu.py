"""Shared helpers for the tests: recording results and the summary."""
from __future__ import annotations

import sys
import time
import traceback

TULOS = {"ok": 0, "virhe": 0, "varoitus": 0}
_VIRHEET: list[str] = []


def otsikko(teksti: str) -> None:
    print(f"\n\033[1m== {teksti}\033[0m", flush=True)


def check(nimi: str, ehto, lisatieto: str = "") -> bool:
    ok = bool(ehto)
    TULOS["ok" if ok else "virhe"] += 1
    merkki = "\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m"
    print(f"  {merkki} {nimi}" + (f"  \033[2m{lisatieto}\033[0m" if lisatieto else ""), flush=True)
    if not ok:
        _VIRHEET.append(f"{nimi}  {lisatieto}")
    return ok


def warn(nimi: str, lisatieto: str = "") -> None:
    TULOS["varoitus"] += 1
    print(f"  \033[33m!\033[0m {nimi}  \033[2m{lisatieto}\033[0m", flush=True)


def raises(nimi: str, fn, *args, exc=Exception) -> bool:
    try:
        fn(*args)
    except exc:
        return check(nimi, True)
    except Exception as e:                       # wrong exception
        return check(nimi, False, f"wrong exception {type(e).__name__}: {e}")
    return check(nimi, False, "no exception")


def safe(nimi: str, fn, *args):
    """Run fn; a crash = a failure (the test continues)."""
    try:
        return fn(*args)
    except Exception as e:
        check(nimi, False, f"{type(e).__name__}: {e}")
        traceback.print_exc(limit=3)
        return None


class Ajastin:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.s = time.perf_counter() - self.t


def yhteenveto(osa: str) -> int:
    print(f"\n\033[1m{osa}: {TULOS['ok']} ok, {TULOS['virhe']} failed, {TULOS['varoitus']} warnings\033[0m")
    for v in _VIRHEET:
        print(f"  \033[31m✗\033[0m {v}")
    return 1 if TULOS["virhe"] else 0


if __name__ == "__main__":
    sys.exit(0)
