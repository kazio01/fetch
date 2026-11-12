#!/usr/bin/env bash
# asenna_gnome.sh – Fetch (samplehaku) for GNOME: results window, Super search and the Files extension.
#
# Run from the folder that has samplehaku.py and its .venv (run asenna.sh first):
#     bash asenna_gnome.sh
# Uninstall:  bash asenna_gnome.sh --uninstall
# Called by install.sh (any distribution): FETCH_SKIP_PACKAGES=1 (install.sh installed them), and
# --service-only installs just the search service the VST3 plugin needs (no window, Super search, Files).
set -euo pipefail
SERVICE_ONLY=0
[[ "${1:-}" == "--service-only" ]] && SERVICE_ONLY=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/.venv/bin/python"
NAME="fi.samplehaku.Search"
APP_ID="fi.samplehaku.Results"
ICONS="$HOME/.local/share/icons/hicolor"
UNIT="samplehaku-haku.service"
APPS="$HOME/.local/share/applications"
DBUS_DIR="$HOME/.local/share/dbus-1/services"
SYSTEMD_DIR="$HOME/.config/systemd/user"
NAUT_DIR="$HOME/.local/share/nautilus-python/extensions"
CONF_DIR="$HOME/.config/samplehaku"
# GNOME Shell reads search providers only from the system data folders, hence sudo.
# An earlier install uses the same folder; otherwise /usr/local/share if it is in XDG_DATA_DIRS (also with a trailing "/").
PROV_DIR="/usr/share/gnome-shell/search-providers"
if [[ ! -f "$PROV_DIR/fi.samplehaku.Search.ini" ]] \
   && [[ ":${XDG_DATA_DIRS:-/usr/local/share:/usr/share}:" =~ :/usr/local/share/?: ]]; then
    PROV_DIR="/usr/local/share/gnome-shell/search-providers"
fi

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()  { printf '   ✓ %s\n' "$*"; }
die() { printf '\n✗ %s\n' "$*" >&2; exit 1; }

if [[ "${1:-}" == "--uninstall" || "${1:-}" == "--poista" ]]; then
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$APPS/$NAME.desktop" "$APPS/$APP_ID.desktop" "$DBUS_DIR/$NAME.service" "$SYSTEMD_DIR/$UNIT" \
          "$NAUT_DIR/samplehaku_nautilus.py" \
          "$HOME/.local/bin/samplehaku-results" "$HOME/.local/bin/samplehaku-similar"
    rm -f "$ICONS"/*/apps/"$APP_ID".png
    gtk-update-icon-cache -f -t "$ICONS" 2>/dev/null || true
    sudo rm -f "$PROV_DIR/$NAME.ini"
    systemctl --user daemon-reload
    nautilus -q 2>/dev/null || true
    echo "Removed. (Tags and the index were kept: $CONF_DIR, ~/.local/share/samplehaku)"
    exit 0
fi

[[ $EUID -ne 0 ]] || die "Run as a normal user, not with sudo."
for f in samplehaku.py hakupalvelu.py samplehaku_nautilus.py samplehaku_tulokset.py \
         samplehaku_similar.py samplehaku_kokoelmat.py tagit.json fetch-icon.png \
         fetch-mark.png fetch-mark-dark.png; do
    [[ -f "$HERE/$f" ]] || die "$f is missing from $HERE"
done
[[ -x "$PY" ]] || die "Fetch's Python environment was not found ($PY). Run first: bash asenna.sh"

say "1/6 Packages"
if [[ "${FETCH_SKIP_PACKAGES:-0}" == 1 ]]; then
    ok "installed by install.sh"
else
    # Windows: system Python + GTK 4 + libadwaita; playback with GStreamer's playbin
    sudo pacman -S --needed python-nautilus libnotify python-gobject gtk4 libadwaita \
        gstreamer gst-plugins-base gst-plugins-good ffmpeg
    "$HERE/.venv/bin/python" -m pip --version >/dev/null 2>&1 \
        && "$PY" -m pip install -q dbus-fast \
        || uv pip install --python "$PY" -q dbus-fast
    ok "python-nautilus, GTK 4, libadwaita, GStreamer, notify-send, dbus-fast"
fi

say "2/6 Tags"
mkdir -p "$CONF_DIR"
if [[ -f "$CONF_DIR/tagit.json" ]]; then
    ok "exists, left untouched: $CONF_DIR/tagit.json"
else
    cp "$HERE/tagit.json" "$CONF_DIR/tagit.json"
    ok "$CONF_DIR/tagit.json (edit freely)"
fi

say "3/6 Search service (starts by itself on the first search)"
mkdir -p "$APPS" "$DBUS_DIR" "$SYSTEMD_DIR"
# Remove the old separate .desktop for the search provider: GNOME Shell skips a search provider whose
# DesktopId app is hidden (NoDisplay), so the provider uses the Fetch window's entry instead.
rm -f "$APPS/$NAME.desktop"
cat > "$SYSTEMD_DIR/$UNIT" <<EOF
[Unit]
Description=Fetch (samplehaku): GNOME search, Files add-on and results window service

[Service]
Type=dbus
BusName=$NAME
ExecStart="$PY" "$HERE/hakupalvelu.py"
# Stops by itself when idle (minutes, 0 = never):
Environment=SAMPLEHAKU_IDLE_MIN=10
# The model is already on disk (samplehaku index downloads it): no network check at startup
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_VERBOSITY=error
# Search prefixes in GNOME's search:
Environment=SAMPLEHAKU_PREFIX=fetch,ääni,sample,samplehaku
EOF
cat > "$DBUS_DIR/$NAME.service" <<EOF
[D-BUS Service]
Name=$NAME
Exec="$PY" "$HERE/hakupalvelu.py"
SystemdService=$UNIT
EOF
systemctl --user daemon-reload 2>/dev/null \
    || echo "   ! no systemd user session right now: the service starts after the next login"
ok "$SYSTEMD_DIR/$UNIT"

if [[ $SERVICE_ONLY == 1 ]]; then
    say "4-6/6 Window, Super search and Files extension: skipped (--service-only)"
elif ! command -v gnome-shell >/dev/null; then
    say "4/6 GNOME Shell search provider: skipped (not GNOME)"
else
say "4/6 GNOME Shell search provider"
sudo mkdir -p "$PROV_DIR"
sudo tee "$PROV_DIR/$NAME.ini" >/dev/null <<EOF
[Shell Search Provider]
DesktopId=$APP_ID.desktop
BusName=$NAME
ObjectPath=/fi/samplehaku/SearchProvider
Version=2
EOF
ok "$PROV_DIR/$NAME.ini"
fi

if [[ $SERVICE_ONLY == 0 ]]; then
say "5/6 Fetch window (app menu, icon)"
mkdir -p "$HOME/.local/bin" "$APPS"
printf '#!/usr/bin/env bash\nexec /usr/bin/python3 "%s/samplehaku_tulokset.py" "$@"\n' "$HERE" \
    > "$HOME/.local/bin/samplehaku-results"
chmod +x "$HOME/.local/bin/samplehaku-results"
cat > "$APPS/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Fetch
Comment=Search, audition and collect samples
Exec=$HOME/.local/bin/samplehaku-results
Icon=$APP_ID
Categories=AudioVideo;Audio;
Keywords=fetch;samplehaku;sample;search;audio;liked;
StartupNotify=true
DBusActivatable=false
EOF
# the app icon: white on black (fetch-icon-alt.png, black on white, is an unused alternative)
for px in 16 32 48 64 128 256 512; do
    mkdir -p "$ICONS/${px}x${px}/apps"
    if command -v magick >/dev/null; then
        magick "$HERE/fetch-icon.png" -resize "${px}x${px}" "$ICONS/${px}x${px}/apps/$APP_ID.png"
    elif [[ $px == 512 ]]; then
        cp "$HERE/fetch-icon.png" "$ICONS/512x512/apps/$APP_ID.png"
    fi
done
gtk-update-icon-cache -f -t "$ICONS" 2>/dev/null || true
update-desktop-database "$APPS" 2>/dev/null || true
ok "$HOME/.local/bin/samplehaku-results, $APPS/$APP_ID.desktop"

say "6/6 Files extension"
mkdir -p "$HOME/.local/bin"
# The "Similar sounds…" window (system Python + GTK 4 + libadwaita)
printf '#!/usr/bin/env bash\nexec /usr/bin/python3 "%s/samplehaku_similar.py" "$@"\n' "$HERE" \
    > "$HOME/.local/bin/samplehaku-similar"
chmod +x "$HOME/.local/bin/samplehaku-similar"
if command -v nautilus >/dev/null; then
    # A symlink (not a copy): the extension stays up to date and finds the project folder from the link target.
    mkdir -p "$NAUT_DIR"
    ln -sf "$HERE/samplehaku_nautilus.py" "$NAUT_DIR/samplehaku_nautilus.py"
    nautilus -q 2>/dev/null || true
    ok "$NAUT_DIR/samplehaku_nautilus.py"
else
    ok "skipped: Files (nautilus) is not installed"
fi
fi

say "Test: the search service responds"
echo "   (the first start loads the model, 5–20 s)"
if ! command -v gdbus >/dev/null || [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}${XDG_RUNTIME_DIR:-}" ]]; then
    echo "   (skipped: no desktop session)"
elif out=$(timeout 90 gdbus call --session --dest "$NAME" --object-path /fi/samplehaku/SearchProvider \
        --method org.gnome.Shell.SearchProvider2.GetInitialResultSet "['fetch','kick']" 2>&1); then
    n=$(grep -o "'/" <<<"$out" | wc -l)
    ok "the search 'kick' returned $n matches"
else
    echo "   ! no response: $out"
    echo "   ! see the log: journalctl --user -u $UNIT -e"
fi

[[ $SERVICE_ONLY == 1 ]] && exit 0
cat <<EOF

Done! Log out and back in so GNOME Shell finds the new search provider.

  Fetch window:  app menu > Fetch   or   samplehaku-results
  Super key, type:   fetch dark kick   or   +bass +long   (opens the Fetch window)
  Files: right-click an audio file > Similar sounds…
  Files, list view: columns Sound, Brightness, Tail, Folder (right-click the header row)
  Tags:   $CONF_DIR/tagit.json
  Log:    journalctl --user -u $UNIT -f
  If the search doesn't show up: Settings > Search > make sure Fetch is enabled.
  Check the installation:  testit/aja.sh testit
EOF
