"""3) Collections (hard links) and the Files extension menu. In a temporary folder."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from apu import check, otsikko, yhteenveto  # noqa: E402

import samplehaku_kokoelmat as kk  # noqa: E402

otsikko("Collections (hard links)")
base = Path(tempfile.mkdtemp(dir=Path.home() / ".cache"))        # same disk as the library
try:
    src = base / "pack" / "Kick 01.wav"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"RIFF" + os.urandom(64))
    other = base / "pack2" / "Kick 01.wav"                       # same name, different file
    other.parent.mkdir(parents=True)
    other.write_bytes(b"RIFF" + os.urandom(64))
    col = kk.Collection(base / "Liked")
    check("not liked at first", not col.has(str(src)))
    col.add(str(src))
    lk = base / "Liked" / "Kick 01.wav"
    check("adding makes a hard link", lk.exists() and lk.stat().st_ino == src.stat().st_ino)
    check("recognised by inode", col.has(str(src)))
    col.add(str(src))
    check("adding twice makes no copy", len(list((base / "Liked").iterdir())) == 1)
    col.add(str(other))
    check("same name, different file -> '(2)'", (base / "Liked" / "Kick 01 (2).wav").exists())
    (base / "Liked" / "Kick 01.wav.asd").write_text("ableton")
    col.remove(str(src))
    check("removing deletes the link and the .asd", not lk.exists() and not (base / "Liked" / "Kick 01.wav.asd").exists())
    check("the original stays", src.exists())
    check("toggle on", col.toggle(str(src)) is True and col.has(str(src)))
    check("toggle off", col.toggle(str(src)) is False and not col.has(str(src)))
    check("a missing file doesn't crash has()", col.has("/ei/ole.wav") is False)
    # result folder: manifest -> original name
    kk.RESULTS_DIR = base / "tulokset"                       # result folders live there (hakupalvelu.results_folder)
    res = kk.RESULTS_DIR / "Similar"
    res.mkdir(parents=True)
    os.link(src, res / "001 Kick 01.wav")
    (res / kk.MANIFEST).write_text('{"001 Kick 01.wav": "%s"}' % src)
    check("original_path from the manifest", kk.original_path(str(res / "001 Kick 01.wav")) == str(src))
    col.add(str(res / "001 Kick 01.wav"))
    check("liked from a result gets the original name", (base / "Liked" / "Kick 01.wav").exists())
    (src.parent / kk.MANIFEST).write_text('{"Kick 01.wav": "/elsewhere/x.wav"}')
    check("library folders aren't searched for a manifest (one failed read per sample on a network share)",
          kk.original_path(str(src)) == str(src))
    # different disk: a symlink (tmpfs /tmp, if a different file system)
    tmp = Path(tempfile.mkdtemp())
    if os.stat(tmp).st_dev != os.stat(base).st_dev:
        far = tmp / "Far.wav"
        far.write_bytes(b"RIFF")
        c2 = kk.Collection(base / "Liked2")
        c2.add(str(far))
        check("different disk -> symlink", (base / "Liked2" / "Far.wav").is_symlink())
    shutil.rmtree(tmp, ignore_errors=True)
finally:
    shutil.rmtree(base, ignore_errors=True)

otsikko("Files extension menu")
repo = types.ModuleType("gi.repository")


class _O:
    pass


class MenuItem:
    def __init__(self, name, label):
        self.name, self.label, self.cb = name, label, None

    def connect(self, sig, cb, *a):
        self.cb = (cb, a)


repo.GObject = types.SimpleNamespace(GObject=type("GO", (), {}))
repo.Nautilus = types.SimpleNamespace(ColumnProvider=type("C", (), {}), InfoProvider=type("I", (), {}),
                                      MenuProvider=type("M", (), {}), MenuItem=MenuItem, Column=lambda **k: k)
repo.Gio = repo.GLib = types.SimpleNamespace()
saved = {k: sys.modules.get(k) for k in ("gi", "gi.repository")}
sys.modules["gi"] = types.ModuleType("gi")
sys.modules["gi.repository"] = repo
try:
    import samplehaku_nautilus as nx


    class F:
        def __init__(self, path, mime="audio/x-wav", scheme="file"):
            self.p, self.m, self.s = path, mime, scheme

        def get_uri_scheme(self):
            return self.s

        def get_mime_type(self):
            return self.m

        def get_location(self):
            return types.SimpleNamespace(get_path=lambda: self.p)

        def is_directory(self):
            return False

    ext = nx.SamplehakuExtension() if hasattr(nx, "SamplehakuExtension") else None
    cls = [v for v in vars(nx).values() if isinstance(v, type) and hasattr(v, "get_file_items")]
    ext = ext or (cls[0]() if cls else None)
    check("the extension loads", ext is not None)
    if ext:
        one = ext.get_file_items([F("/x/Kick.wav")])
        check("one audio file: Similar + Like", [i.name for i in one] == ["Samplehaku::similar", "Samplehaku::like"])
        many = ext.get_file_items([F("/x/a.wav"), F("/x/b.wav")])
        check("several: only Like (n)", [i.label for i in many] == ["Add to Liked (2)"])
        check("not audio -> no menu", ext.get_file_items([F("/x/a.txt", "text/plain")]) == [])
        check("mixed selection (audio + text) -> no menu", ext.get_file_items([F("/x/a.wav"), F("/x/b.txt", "text/plain")]) == [])
        check("not local -> no menu", ext.get_file_items([F("/x/a.wav", scheme="sftp")]) == [])
finally:
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

sys.exit(yhteenveto("Collections and Files"))
