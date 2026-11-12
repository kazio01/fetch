"""
Path strings in the index: always written with "/" (on Windows too: "D:/Samples/Kick.wav",
"//nas/share/Samples/Kick.wav"). Windows accepts forward slashes, and the rules that read folder
names (instrument, genre, type, search text) then work the same on every platform.

Standard library only: imported by samplehaku, soittimet, genret, hakupalvelu and fetchhttp.
"""
from __future__ import annotations

import os

WINDOWS = os.name == "nt"


def norm(p) -> str:
    """A path as stored in the index. On Linux unchanged (a backslash is a legal file name character)."""
    s = os.fspath(p)
    return s.replace("\\", "/") if WINDOWS else s


def common_dir(paths) -> str:
    """The longest common folder of "/"-paths, like os.path.commonpath but on the strings as they are
    (keeps a "//server/share" prefix and "D:"). One path (or all equal) -> that path itself."""
    if not paths:
        return ""
    lo, hi = min(paths), max(paths)     # what the smallest and largest share, every path shares
    a, b = lo.split("/"), hi.split("/")
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return "/".join(a[:n])


def parent(p: str) -> str:
    """The folder of a "/"-path: "D:/Samples/Kick.wav" -> "D:/Samples"."""
    return p.rsplit("/", 1)[0] if "/" in p else ""


# ---------------------------------------------------------------- library roots
# A library root is a folder given to "samplehaku index" (or brought in with "samplehaku merge"),
# e.g. "/home/me/Samples" or "/mnt/winsamples". The first folder under the root is the
# sample pack. Several roots can live in one index (two libraries in different places); they are
# kept in the index's meta table (key "roots", a JSON list). An index without that key has one
# root: the common folder of all its paths (how Fetch worked before 0.4).

def fallback_root(paths) -> str:
    """The single root of an index without stored roots: the paths' common folder."""
    paths = list(paths)
    root = common_dir(paths)
    return parent(root) if root in set(paths) else root


def root_of(path: str, roots) -> str:
    """The deepest root that contains the path, or "" if none does."""
    best = ""
    for r in roots:
        if len(r) > len(best) and path.startswith(r + "/"):
            best = r
    return best


def roots_per_path(paths, roots=None) -> list[str]:
    """The root of every path. Paths outside the given roots (or all, without roots) get the
    common folder of those paths."""
    paths = list(paths)
    out = [root_of(p, roots) for p in paths] if roots else [""] * len(paths)
    left = [p for p, r in zip(paths, out) if not r]
    if left:
        fb = fallback_root(left)
        out = [r or fb for r in out]
    return out


def add_root(roots, new: str) -> list[str]:
    """Roots after indexing the folder `new`: a folder inside an existing root changes nothing,
    a folder that contains existing roots replaces them."""
    roots = list(roots)
    if any(new == r or new.startswith(r + "/") for r in roots):
        return roots
    return [r for r in roots if not r.startswith(new + "/")] + [new]


def write_atomic(path, text: str) -> None:
    """Write a settings file so that a crash or a full disk can't leave it half written (the user's
    corrections are thousands of hand-made entries): a temporary file beside it, then a rename."""
    path = os.fspath(path)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def replace_prefix(p: str, old: str, new: str) -> str | None:
    """p with the folder `old` swapped for `new`, or None if p is not under `old`."""
    if p == old:
        return new
    if p.startswith(old + "/"):
        return new + p[len(old):]
    return None
