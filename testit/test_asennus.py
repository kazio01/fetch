"""5) Installation, as GNOME and Files see it. Read-only (changes nothing).
Tests 1–4 load the code straight from the project folder, so they can't see installation problems. This checks
that Shell accepts the search provider, that D-Bus/systemd/launchers point to existing files, and that the
Files extension loads from where it is installed. Both of these real bugs would have shown up here:
a hidden DesktopId (Shell silently skips the provider) and an extension looking for the project in an old folder."""
from __future__ import annotations

import configparser
import os
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path[:0] = [str(HERE)]
from apu import check, otsikko, warn, yhteenveto  # noqa: E402

import gi  # noqa: E402

gi.require_version("GioUnix", "2.0")
from gi.repository import GioUnix  # noqa: E402

HOME = Path.home()
NAME = "fi.samplehaku.Search"
APP_ID = "fi.samplehaku.Results"
BIN = HOME / ".local/bin"
APPS = HOME / ".local/share/applications"
EXT = HOME / ".local/share/nautilus-python/extensions/samplehaku_nautilus.py"


def keyfile(path: Path, group: str) -> dict[str, str]:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    cp.read(path, encoding="utf-8")
    return dict(cp[group]) if cp.has_section(group) else {}


def exec_paths(line: str) -> list[Path]:
    """Absolute paths on an Exec/ExecStart line (quotes respected)."""
    try:
        parts = shlex.split(line)
    except ValueError:
        return []
    return [Path(p) for p in parts if p.startswith("/")]


def launcher_target(path: Path) -> Path | None:
    """The .py file a ~/.local/bin launcher runs (exec … "<path>.py" "$@")."""
    try:
        for line in path.read_text().splitlines():
            if line.startswith("exec "):
                return next((p for p in exec_paths(line[5:]) if p.suffix == ".py"), None)
    except OSError:
        pass
    return None


def desktop_app(desktop_id: str):
    """GioUnix.DesktopAppInfo or None (the constructor raises TypeError if the file doesn't exist)."""
    try:
        return GioUnix.DesktopAppInfo.new(desktop_id) if desktop_id else None
    except TypeError:
        return None


def in_repo(p: Path) -> bool:
    try:
        return p.resolve().is_relative_to(REPO.resolve())
    except OSError:
        return False


# ---------------------------------------------------------------- Super search
otsikko("Super search: GNOME Shell search provider")
data_dirs = [d.rstrip("/") for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
inis = [Path(d) / "gnome-shell/search-providers" / f"{NAME}.ini" for d in data_dirs]
found = [p for p in inis if p.is_file()]
check("the provider .ini is in a folder Shell reads (XDG_DATA_DIRS)", found, ", ".join(map(str, found)) or
      "not found: " + ", ".join(str(p.parent) for p in inis))
if len(found) > 1:
    warn("several .ini files: Shell uses the first", ", ".join(map(str, found)))
if found:
    ini = keyfile(found[0], "Shell Search Provider")
    check(".ini: BusName and ObjectPath", ini.get("BusName") == NAME and ini.get("ObjectPath") == "/fi/samplehaku/SearchProvider",
          f"{ini.get('BusName')} {ini.get('ObjectPath')}")
    check(".ini: Version=2", ini.get("Version") == "2")
    did = ini.get("DesktopId", "")
    app = desktop_app(did)
    check(f".ini: the DesktopId app exists ({did})", app is not None)
    check("the DesktopId app is visible (should_show; hidden = Shell silently skips the provider)",
          app is not None and app.should_show(), "" if app is None or app.should_show() else "NoDisplay/Hidden/OnlyShowIn")

otsikko("Search service: D-Bus and systemd")
svc = HOME / ".local/share/dbus-1/services" / f"{NAME}.service"
check("D-Bus service file", svc.is_file(), str(svc))
if svc.is_file():
    d = keyfile(svc, "D-BUS Service")
    check("D-Bus: Name", d.get("Name") == NAME)
    ex = exec_paths(d.get("Exec", ""))
    check("D-Bus: Exec paths exist", ex and all(p.exists() for p in ex), " ".join(map(str, ex)))
    check("D-Bus: Exec runs this project's hakupalvelu.py", any(p.name == "hakupalvelu.py" and in_repo(p) for p in ex))
    unit_name = d.get("SystemdService", "")
    unit = HOME / ".config/systemd/user" / unit_name
    check(f"systemd unit {unit_name}", unit_name and unit.is_file())
    if unit.is_file():
        u = keyfile(unit, "Service")
        ex = exec_paths(u.get("ExecStart", ""))
        check("systemd: ExecStart paths exist and are in this project",
              ex and all(p.exists() for p in ex) and any(in_repo(p) for p in ex), " ".join(map(str, ex)))
        check("systemd: BusName", u.get("BusName") == NAME)

# ---------------------------------------------------------------- launchers and the Fetch window
otsikko("Launchers (~/.local/bin)")
for name, need in (("samplehaku", True), ("samplehaku-results", True), ("samplehaku-similar", True),
                   ("samplehaku-piirteet", False)):
    path = BIN / name
    if not path.exists() and not need:
        warn(f"{name} is missing (optional: asenna_npu.sh)")
        continue
    target = launcher_target(path)
    check(f"{name}: executable and runs an existing file in this project",
          os.access(path, os.X_OK) and target is not None and target.is_file() and in_repo(target), str(target))

otsikko("Fetch window: app menu and icon")
app = desktop_app(f"{APP_ID}.desktop")
check(f"{APP_ID}.desktop exists and is visible", app is not None and app.should_show())
if app:
    ex = exec_paths(app.get_commandline() or "")
    check(".desktop: Exec exists", ex and all(p.exists() for p in ex), " ".join(map(str, ex)))
icons = HOME / ".local/share/icons/hicolor"
pngs = sorted(icons.glob(f"*/apps/{APP_ID}.png"))
check("icon installed (hicolor)", pngs, f"{len(pngs)} sizes")
cache = icons / "icon-theme.cache"
if pngs and cache.exists():
    stale = [p for p in pngs if p.stat().st_mtime > cache.stat().st_mtime]
    check("icon-theme.cache up to date (otherwise a new icon doesn't show)", not stale,
          f"run: gtk-update-icon-cache -f -t {icons}" if stale else "")
if (APPS / f"{NAME}.desktop").exists():
    warn(f"the old {NAME}.desktop still exists (asenna_gnome.sh removes it)")

# ---------------------------------------------------------------- Files extension
otsikko("Files extension (installed, as Files loads it)")
check("extension installed", EXT.exists(), str(EXT))
if EXT.exists():
    if EXT.is_symlink():
        check("the extension is a link to this project's file", EXT.resolve() == (REPO / EXT.name).resolve(), str(EXT.resolve()))
    else:
        same = EXT.read_bytes() == (REPO / EXT.name).read_bytes()
        (check if same else warn)("the extension is a copy (asenna_gnome.sh now makes a link)",
                                  *(() if same else ("the copy differs from the project version",)))
    # A separate process: only the extensions folder on the path, working directory /, no project on sys.path
    # (the same situation as inside Files). The extension must find the project folder by itself.
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "import gi; gi.require_version('Nautilus', '4.1'); "
            "import samplehaku_nautilus as x; "
            "print(x._project_dir() if hasattr(x, '_project_dir') else '?')")
    r = subprocess.run(["/usr/bin/python3", "-I", "-c", code, str(EXT.parent)], cwd="/",
                       capture_output=True, text=True, timeout=60)
    err = (r.stderr.strip().splitlines() or [""])[-1]
    check("the extension loads from where it is installed (system Python)", r.returncode == 0, err[:120])
    if r.returncode == 0:
        check("the extension finds the project folder", Path(r.stdout.strip()).resolve() == REPO.resolve(), r.stdout.strip())

    # Files' own log since its last start: did the extension fail inside the real Files?
    try:
        log = subprocess.run(["journalctl", "--user", "-b", "-o", "cat", "--no-pager", "-n", "20000"],
                             capture_output=True, text=True, timeout=30).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        log = []
    starts = [i for i, line in enumerate(log) if "Connecting to org.freedesktop.Tracker3.Miner.Files" in line]
    if not starts:
        warn("no Files start found in the log (this boot)")
    else:
        since = log[starts[-1]:]
        bad = [line for line in since if "samplehaku_nautilus" in line or "samplehaku_kokoelmat" in line]
        check("Files log: no extension errors since its last start", not bad,
              (bad[-1][:120] if bad else f"{len(since)} lines"))

otsikko("Data")
db = Path(os.environ.get("SAMPLEHAKU_DIR", HOME / ".local/share/samplehaku")) / "index.db"
check("the index exists", db.is_file() and db.stat().st_size > 0, str(db))

sys.exit(yhteenveto("Installation"))
