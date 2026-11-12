#!/usr/bin/env bash
# Build the Fetch release package for Linux: the committed source tree + the built VST3 plugin.
#   bash linux/make_release.sh VST3_DIR OUT_DIR
# VST3_DIR contains Fetch.vst3 (the Linux build: CI artifact "Fetch-vst3-linux-x64").
# The user unpacks it and runs:  bash install.sh
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
VST3_DIR="${1:?VST3 folder (contains Fetch.vst3)}"
OUT="${2:?output folder}"
VERSION="$(cat "$R/VERSION")"

[ -d "$VST3_DIR/Fetch.vst3/Contents/x86_64-linux" ] || { echo "No Linux Fetch.vst3 in $VST3_DIR" >&2; exit 1; }
NAME="Fetch-$VERSION-linux-x64"
mkdir -p "$OUT"
P="$(cd "$OUT" && pwd)/$NAME"
rm -rf "$P" "$P.tar.gz"
mkdir -p "$P"
git -C "$R" archive HEAD | tar -C "$P" -xf -
mkdir -p "$P/VST3"
cp -r "$VST3_DIR/Fetch.vst3" "$P/VST3/"
tar -C "$OUT" -czf "$P.tar.gz" "$NAME"
echo "$P.tar.gz ($(du -h "$P.tar.gz" | cut -f1))"
