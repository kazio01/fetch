#!/usr/bin/env bash
# Build the Fetch portable package for Windows (a folder + zip; Python and packages are installed
# by Setup.cmd on the Windows machine, so the zip stays small).
#   bash windows/make_portable.sh VST3_DIR OUT_DIR
# VST3_DIR contains Fetch.vst3 (the Windows build: CI artifact "Fetch-vst3-windows-x64").
# Works on Linux and in Git Bash on Windows (CI).
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
VST3_DIR="${1:?VST3 folder (contains Fetch.vst3)}"
OUT="${2:?output folder}"
VERSION="$(cat "$R/VERSION")"
UV_VERSION=0.13.5
PY="$(command -v python3 || command -v python)"   # Git Bash on Windows has only "python"
UV_SHA256=a38b8d1902b7cfbc76b423ba1bb456f623f42064cdc72628b6075b89afd1019a

[ -d "$VST3_DIR/Fetch.vst3/Contents/x86_64-win" ] || { echo "No Windows Fetch.vst3 in $VST3_DIR" >&2; exit 1; }
NAME="Fetch-portable-$VERSION-windows-x64"
P="$OUT/$NAME"
rm -rf "$P" "$P.zip"
mkdir -p "$P/app" "$P/tools" "$P/VST3"

# Fetch's service code (no GNOME/D-Bus parts: hakupalvelu runs without dbus_fast)
for f in samplehaku.py aanipiirteet.py hakupalvelu.py fetchhttp.py genret.py soittimet.py polut.py pakkaukset.py rinnakkain.py siirto.py indeksoija.py \
         samplehaku_kokoelmat.py tagit.json savellaji_malli.npz tyyppi_malli.npz VERSION LICENSE; do
  cp "$R/$f" "$P/app/"
done
cp "$R/windows/fetch_portable.py" "$R/windows/requirements-windows.txt" \
   "$R/windows/requirements-windows-cuda.txt" "$P/app/"

# Windows scripts with CRLF line endings (cmd.exe misreads labels and blocks with LF only)
crlf() { sed 's/\r$//; s/$/\r/' "$1" > "$2"; }
crlf "$R/windows/setup.ps1" "$P/app/setup.ps1"
crlf "$R/windows/pick_folder.ps1" "$P/app/pick_folder.ps1"
for f in "Setup.cmd" "Index Samples.cmd" "Fetch Service (console).cmd" "README.txt"; do
  crlf "$R/windows/$f" "$P/$f"
done

cp -r "$VST3_DIR/Fetch.vst3" "$P/VST3/"

# uv (installs Python and the packages), pinned and checked
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/fetch-portable"
mkdir -p "$CACHE"
UVZIP="$CACHE/uv-$UV_VERSION-x86_64-pc-windows-msvc.zip"
if [ ! -f "$UVZIP" ]; then
  curl -fsSL -o "$UVZIP.part" \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-pc-windows-msvc.zip"
  mv "$UVZIP.part" "$UVZIP"
fi
echo "$UV_SHA256  $UVZIP" | sha256sum -c --quiet - || { rm -f "$UVZIP"; echo "uv checksum mismatch" >&2; exit 1; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
"$PY" -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extract("uv.exe", sys.argv[2])' "$UVZIP" "$TMP"
cp "$TMP/uv.exe" "$P/tools/uv.exe"

(cd "$OUT" && "$PY" -c '
import os, sys, zipfile
name = sys.argv[1]
with zipfile.ZipFile(name + ".zip", "w", zipfile.ZIP_DEFLATED) as z:
    for d, _, files in os.walk(name):
        for f in sorted(files):
            z.write(os.path.join(d, f))
' "$NAME")
echo "$P"
echo "$P.zip ($(du -h "$P.zip" | cut -f1))"
