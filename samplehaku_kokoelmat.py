"""
Fetch collections: liked samples (and later projects) as hard links.

~/Documents/Fetch Collections/
    Liked Samples/      hard links to liked samples
    (Liked Tracks/, projects … the same way later)

A hard link is the same file under another name: Ableton/Wine sees an ordinary file, it takes no
extra disk space, and the link keeps working even if the original is moved. Likes are recognised by
inode, so the same sample shows as liked in any folder. Removing a like removes only the link.

Used by: samplehaku_nautilus.py (Files menu) and samplehaku_tulokset.py (results window).
Standard library only: works with both the system Python and the .venv.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import polut

COLLECTIONS_DIR = Path(os.environ.get("SAMPLEHAKU_COLLECTIONS",
                                      Path.home() / "Documents/Fetch Collections"))
LIKED_DIR = Path(os.environ.get("SAMPLEHAKU_LIKED", COLLECTIONS_DIR / "Liked Samples"))
MANIFEST = ".samplehaku.json"   # result folder name -> original path
RESULTS_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "samplehaku/tulokset"


def _manifest_dir(d: Path) -> bool:
    """Only result folders and collections have manifests. Elsewhere (the library, possibly on a network
    share) no manifest is looked for: one failed open per sample over the network adds up."""
    s = polut.norm(d)
    return any(s == polut.norm(r) or s.startswith(polut.norm(r) + "/") for r in (RESULTS_DIR, COLLECTIONS_DIR))


def original_path(path: str) -> str:
    """Result folder entry '001 Kick.wav' -> original path in the library (otherwise the same path)."""
    p = Path(path)
    if not _manifest_dir(p.parent):
        return path
    try:
        return json.loads((p.parent / MANIFEST).read_text()).get(p.name, path)
    except (OSError, ValueError):
        return path


def _link_target(p) -> str:
    """A symlink's target as written. Windows returns an absolute target with the "\\\\?\\" prefix
    ("\\\\?\\D:\\x.wav", "\\\\?\\UNC\\server\\share\\x.wav"), which no indexed path has."""
    t = os.readlink(p)
    if t.startswith("\\\\?\\UNC\\"):
        return "\\\\" + t[8:]
    if t.startswith("\\\\?\\"):
        return t[4:]
    return t


def remember_original(path: str, orig: str) -> None:
    """A collection or result folder file found by inode: write its original into the folder's manifest,
    so the next lookup needs no inode table (a stat of every library file, seconds)."""
    p = Path(path)
    if not _manifest_dir(p.parent):
        return
    f = p.parent / MANIFEST
    try:
        man = json.loads(f.read_text())
    except (OSError, ValueError):
        man = {}
    if man.get(p.name) == orig:
        return
    man[p.name] = orig
    try:
        polut.write_atomic(f, json.dumps(man, ensure_ascii=False, indent=1))
    except OSError:
        pass


class Collection:
    """One folder of hard links. State is re-read only when the folder changes."""

    def __init__(self, folder: Path):
        self.dir = Path(folder)
        self.inodes: dict[tuple[int, int], list[Path]] = {}
        self.copies: dict[str, list[Path]] = {}   # copied likes (Windows, other drive): original -> copies
        self.targets: set[str] = set()           # symlinked likes (other drive): their originals
        self.links: dict[str, list[Path]] = {}    # symlinked likes: original -> links (for remove)
        self.dev = None                           # the collection's file system
        self.dir_dev: dict[str, int | None] = {}  # folder -> its file system (one stat per folder)
        self.mtime = None

    def _scan(self) -> None:
        try:
            m = self.dir.stat().st_mtime_ns
        except OSError:
            self.inodes, self.mtime = {}, None
            return
        if m == self.mtime:
            return
        self.inodes, self.copies, self.targets, self.dir_dev, self.links = {}, {}, set(), {}, {}
        try:
            self.dev = self.dir.stat().st_dev
        except OSError:
            self.dev = None
        try:
            man = json.loads((self.dir / MANIFEST).read_text())
        except (OSError, ValueError):
            man = {}
        for name, orig in man.items():
            if not (self.dir / name).is_symlink() and (self.dir / name).is_file():
                self.copies.setdefault(polut.norm(orig), []).append(self.dir / name)
        for p in self.dir.iterdir():
            if p.is_symlink():
                try:
                    t = polut.norm(_link_target(p))
                    self.targets.add(t)
                    self.links.setdefault(t, []).append(p)
                except OSError:
                    pass
                # Linux: a symlink is never a hard link, and following it may hit a slow or offline network
                # share (one round trip per like, on Nautilus's main thread): skip the stat. Windows keeps
                # the inode check as a fallback for link targets written differently from the index.
                if not polut.WINDOWS:
                    continue
            try:
                st = p.stat()
            except OSError:
                continue
            if p.is_file() and not p.name.endswith(".asd"):
                self.inodes.setdefault((st.st_dev, st.st_ino), []).append(p)
        self.mtime = m

    @staticmethod
    def _key(path: str):
        try:
            st = os.stat(path)
            return (st.st_dev, st.st_ino)
        except OSError:
            return None

    def _same_fs(self, path: str) -> bool:
        """Is path on the collection's file system (else it can't be a hard link)? Cached per folder,
        so a page of samples on a network share costs one stat per folder, not per file."""
        d = os.path.dirname(path)
        if d not in self.dir_dev:
            try:
                self.dir_dev[d] = os.stat(d).st_dev
            except OSError:
                self.dir_dev[d] = None
        return self.dir_dev[d] is not None and self.dir_dev[d] == self.dev

    def _inside(self, path) -> bool:
        """Is path a file of this collection itself (e.g. a row of the Liked view)?"""
        return polut.norm(Path(path).parent) == polut.norm(self.dir)

    def has(self, path: str) -> bool:
        if self._inside(path):                  # a file in Liked is liked: no stat (a link may point to a share)
            return os.path.lexists(path) and not str(path).endswith(".asd")
        self._scan()
        orig = polut.norm(original_path(path))
        if orig in self.copies or orig in self.targets:
            return True
        return self._same_fs(path) and self._key(path) in self.inodes

    def add(self, path: str) -> None:
        if self.has(path):
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        name = Path(original_path(path)).name     # from search results, without the "001 " prefix
        dst, n = self.dir / name, 2
        while dst.exists() or dst.is_symlink():
            dst = self.dir / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        try:
            os.link(path, dst)
            # the original by name: finding it again by inode means a stat of every file in the library
            self._manifest({dst.name: polut.norm(original_path(path))})
        except OSError:                           # different disk: a symlink is the only option
            try:
                dst.symlink_to(original_path(path))
            except OSError:                       # Windows without symlink rights (network share): a copy,
                shutil.copy2(path, dst)           # remembered in the manifest (Similar, liked state)
                self._manifest({dst.name: original_path(path)})
        self.mtime = None

    def _manifest(self, changes: dict) -> None:
        """Update the folder's manifest (name -> original path); a None value removes the name."""
        f = self.dir / MANIFEST
        try:
            man = json.loads(f.read_text())
        except (OSError, ValueError):
            man = {}
        for k, v in changes.items():
            if v is None:
                man.pop(k, None)
            else:
                man[k] = v
        polut.write_atomic(f, json.dumps(man, ensure_ascii=False, indent=1))

    def remove(self, path: str) -> None:
        self._scan()
        orig = polut.norm(original_path(path))
        victims = list(self.copies.get(orig, [])) + list(self.links.get(orig, []))
        if self._inside(path):                    # the like itself (a row of the Liked view)
            victims.append(Path(path))
        elif self._same_fs(path):                 # a hard link of a library file (no stat over a share)
            victims += self.inodes.get(self._key(path), [])
        for p in victims:
            p.unlink(missing_ok=True)
            Path(str(p) + ".asd").unlink(missing_ok=True)   # Ableton's analysis file
        if victims:
            self._manifest({p.name: None for p in victims})
        self.mtime = None

    def toggle(self, path: str) -> bool:
        """-> True if liked after the toggle."""
        if self.has(path):
            self.remove(path)
            return False
        self.add(path)
        return True


LIKED = Collection(LIKED_DIR)
