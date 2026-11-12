#!/usr/bin/env bash
# Fetch for Linux – one-step install: semantic search for your sample library, the VST3 plugin, and on
# GNOME the Fetch window, Super-key search and the Files extension.
#
#   bash install.sh                 install (or update: run it again from a newer release)
#   bash install.sh --no-desktop    only the search service, the command line and the plugin
#   bash install.sh --npu           also sound features on the AMD Ryzen AI NPU (XDNA, Arch only)
#   bash install.sh --uninstall     remove Fetch (keeps your index, tags and corrections)
#
# Supported: Arch / CachyOS / Manjaro (pacman), Debian / Ubuntu (apt), Fedora (dnf); x86_64.
# Run as your normal user: sudo is asked for the system packages only.
#
# Where things go: the app in ~/.local/share/fetch/app (a git checkout is used in place), the index and
# caches in ~/.local/share/samplehaku, settings in ~/.config/samplehaku, the plugin in ~/.vst3.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="${FETCH_HOME:-$HOME/.local/share/fetch}/app"
UV_VERSION=0.13.5
UV_SHA256=f68d291c75783122dad58ab77def0e13f3233f358731db5d6d7e8715603b928f
DESKTOP=1
NPU=0
UNINSTALL=0
for a in "$@"; do
    case "$a" in
        --no-desktop) DESKTOP=0 ;;
        --npu) NPU=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "Unknown option: $a (see --help)" >&2; exit 2 ;;
    esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()  { printf '   ✓ %s\n' "$*"; }
die() { printf '\n✗ %s\n' "$*" >&2; exit 1; }

[[ $EUID -ne 0 ]] || die "Run as your normal user, not with sudo (sudo is asked when needed)."
[[ "$(uname -m)" == x86_64 ]] || die "Only x86_64 is supported for now (this is $(uname -m))."
[[ -f "$SRC/samplehaku.py" ]] || die "Run install.sh from the Fetch folder (samplehaku.py is missing)."

# A git checkout is a developer setup: install in place. A release folder is copied to $APP.
if [[ -d "$SRC/.git" ]]; then APP="$SRC"; fi

if [[ $UNINSTALL == 1 ]]; then
    say "Removing Fetch"
    if [[ -x "$APP/asenna_gnome.sh" ]]; then bash "$APP/asenna_gnome.sh" --uninstall || true; fi
    rm -rf "$HOME/.vst3/Fetch.vst3"
    rm -f "$HOME/.local/bin/samplehaku" "$HOME/.local/bin/fetch-index"
    if [[ "$APP" != "$SRC" || ! -d "$SRC/.git" ]]; then rm -rf "$APP"; fi
    ok "removed. Kept: ~/.local/share/samplehaku (index), ~/.config/samplehaku (tags, corrections),"
    ok "~/Documents/Fetch Collections (likes, renders). Delete them yourself if you want."
    exit 0
fi

# ---------------------------------------------------------------- 1. system packages
say "1/6 System packages (sudo)"
if [[ "${FETCH_NO_PACKAGES:-0}" == 1 ]]; then
    pkgs=("(skipped: FETCH_NO_PACKAGES=1)")
elif command -v pacman >/dev/null; then
    pkgs=(ffmpeg curl)
    [[ $DESKTOP == 1 ]] && pkgs+=(python-gobject gtk4 libadwaita gstreamer gst-plugins-base gst-plugins-good libnotify)
    [[ $DESKTOP == 1 ]] && command -v nautilus >/dev/null && pkgs+=(python-nautilus)
    sudo pacman -S --needed --noconfirm "${pkgs[@]}"
elif command -v apt-get >/dev/null; then
    pkgs=(ffmpeg curl ca-certificates)
    [[ $DESKTOP == 1 ]] && pkgs+=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base
                                 gstreamer1.0-plugins-good libnotify-bin)
    [[ $DESKTOP == 1 ]] && command -v nautilus >/dev/null && pkgs+=(python3-nautilus)
    sudo apt-get update -q
    sudo apt-get install -y -q "${pkgs[@]}"
elif command -v dnf >/dev/null; then
    pkgs=(ffmpeg-free curl)
    [[ $DESKTOP == 1 ]] && pkgs+=(python3-gobject gtk4 libadwaita gstreamer1-plugins-base gstreamer1-plugins-good libnotify)
    [[ $DESKTOP == 1 ]] && command -v nautilus >/dev/null && pkgs+=(nautilus-python)
    sudo dnf install -y "${pkgs[@]}"
else
    die "No pacman, apt or dnf found. Install ffmpeg (and for the window: Python GObject, GTK 4, libadwaita,
GStreamer good plugins) yourself, then run: FETCH_NO_PACKAGES=1 bash install.sh"
fi
ok "${pkgs[*]}"

# The Fetch window needs libadwaita 1.6 or newer (Debian 13, Ubuntu 25.04, Fedora 41, current Arch)
if [[ $DESKTOP == 1 ]]; then
    adw="$(/usr/bin/python3 -c 'import gi; gi.require_version("Adw", "1"); from gi.repository import Adw
print(Adw.get_major_version() * 100 + Adw.get_minor_version())' 2>/dev/null || echo 0)"
    if (( adw < 106 )); then
        echo "   ! libadwaita 1.6 or newer was not found: installing without the Fetch window and the GNOME parts."
        echo "     The search service, the command line and the VST3 plugin work as usual."
        DESKTOP=0
    fi
fi

# ---------------------------------------------------------------- 2. the app files
say "2/6 Fetch"
if [[ "$APP" != "$SRC" ]]; then
    mkdir -p "$APP"
    # the release folder's contents; the Python environment and the plugin build are made here, not copied
    tar -C "$SRC" --exclude=./.venv --exclude=./plugin/build --exclude=./tools -cf - . | tar -C "$APP" -xf -
fi
ok "$APP"

# uv: installs Python 3.12 and the packages (pinned release, checked)
UV="$(command -v uv || true)"
if [[ -z "$UV" ]]; then
    mkdir -p "$APP/tools"
    UV="$APP/tools/uv"
    if [[ ! -x "$UV" ]]; then
        tmp="$(mktemp -d)"
        trap 'rm -rf "$tmp"' EXIT
        curl -fsSL -o "$tmp/uv.tgz" \
            "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz"
        echo "$UV_SHA256  $tmp/uv.tgz" | sha256sum -c --quiet - || die "uv download: checksum mismatch"
        tar -C "$tmp" -xzf "$tmp/uv.tgz"
        install -m 755 "$tmp/uv-x86_64-unknown-linux-gnu/uv" "$UV"
    fi
fi
ok "uv: $UV"

# ---------------------------------------------------------------- 3. Python environment
say "3/6 Python 3.12 and packages (PyTorch for the CPU, transformers, librosa …; first time ~1 GB)"
cd "$APP"
[[ -x .venv/bin/python ]] || "$UV" venv -q --python 3.12 .venv
# exact versions with hashes: the same as the tested setup (windows/make_requirements.sh)
"$UV" pip install -q --python .venv/bin/python --require-hashes --torch-backend cpu -r linux/requirements-linux.txt
ok "$(.venv/bin/python -c 'import torch, transformers; print(f"torch {torch.__version__}, transformers {transformers.__version__}")')"

mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/samplehaku" <<EOF
#!/usr/bin/env bash
exec "$APP/.venv/bin/python" "$APP/samplehaku.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/samplehaku"
# fetch-index FOLDER…: both analysis steps, as the plugin's Settings > Add folder does
cat > "$HOME/.local/bin/fetch-index" <<EOF
#!/usr/bin/env bash
set -e
[ \$# -gt 0 ] || { echo "usage: fetch-index FOLDER…  (again = only new or changed files)"; exit 2; }
echo "== 1/2 Audio embeddings (CLAP). Ctrl+C stops; the next run continues."
"$APP/.venv/bin/python" "$APP/samplehaku.py" index "\$@"
echo "== 2/2 Sound features, tempo and key"
if [ -x "\$HOME/.local/bin/samplehaku-piirteet" ]; then "\$HOME/.local/bin/samplehaku-piirteet"
else "$APP/.venv/bin/python" "$APP/aanipiirteet.py"; fi
echo "Done. Fetch sees the new samples within a minute."
EOF
chmod +x "$HOME/.local/bin/fetch-index"
ok "commands: fetch-index FOLDER (add samples), samplehaku (search / stats)"

# ---------------------------------------------------------------- 4. the CLAP model
say "4/6 CLAP model (~0.8 GB, first time only)"
.venv/bin/python - <<'EOF'
import samplehaku as sh
sh.Clap(sh.DEFAULT_MODEL)        # the full model: indexing
sh.TextClap(sh.DEFAULT_MODEL)    # the search service's text part (also makes its small copy)
print(f"   ✓ {sh.DEFAULT_MODEL}")
EOF

# ---------------------------------------------------------------- 5. service, window, GNOME
say "5/6 Search service$([[ $DESKTOP == 1 ]] && echo ", Fetch window, Super search, Files extension")"
if [[ $DESKTOP == 1 ]]; then
    FETCH_SKIP_PACKAGES=1 bash "$APP/asenna_gnome.sh"
else
    FETCH_SKIP_PACKAGES=1 bash "$APP/asenna_gnome.sh" --service-only
fi

# ---------------------------------------------------------------- 6. the VST3 plugin
say "6/6 VST3 plugin"
VST=""
for c in "$APP/VST3/Fetch.vst3" "$APP/plugin/build/Fetch_artefacts/Release/VST3/Fetch.vst3"; do
    [[ -d "$c/Contents/x86_64-linux" ]] && { VST="$c"; break; }
done
if [[ -n "$VST" ]]; then
    mkdir -p "$HOME/.vst3"
    rm -rf "$HOME/.vst3/Fetch.vst3"
    cp -r "$VST" "$HOME/.vst3/"
    ok "$HOME/.vst3/Fetch.vst3 (rescan plugins in your DAW)"
else
    ok "skipped: no built plugin here (a release folder has VST3/Fetch.vst3; from source: bash plugin/build.sh)"
fi

if [[ $NPU == 1 ]]; then
    say "NPU (AMD Ryzen AI, XDNA)"
    command -v pacman >/dev/null || die "The NPU setup is for Arch-based systems only."
    bash "$APP/asenna_npu.sh"
fi

cat <<EOF

Fetch is installed.

  1. Add your samples:   fetch-index ~/Samples        (or in the plugin / window: Settings > Add folder)
     The first run analyses every file (CPU: roughly 5–20 files a second); running it again only
     adds new or changed files.
  2. Open your DAW and load the "Fetch" instrument, or the Fetch window from the app menu (GNOME).

Update: run install.sh from a newer release.   Remove: bash install.sh --uninstall
EOF
