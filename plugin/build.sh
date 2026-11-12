#!/usr/bin/env bash
# Builds the Fetch VST3 plugin (Release) and installs it to ~/.vst3.
#   bash plugin/build.sh            build + install
#   bash plugin/build.sh --test     also run the offline tests (FETCH_TEST_SERVICE=1 for the service ones)
#   bash plugin/build.sh --no-install
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v cmake >/dev/null; then
    echo "cmake not found. Install it: sudo pacman -S cmake   (or: uv tool install cmake)" >&2
    exit 1
fi
gen=()
command -v ninja >/dev/null && gen=(-G Ninja)

cmake -S . -B build "${gen[@]}" -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j"$(nproc)"

if [[ " $* " == *" --test "* ]]; then
    ./build/FetchTests_artefacts/Release/FetchTests "$PWD/build/Fetch_artefacts/Release/VST3/Fetch.vst3"
fi

if [[ " $* " != *" --no-install "* ]]; then
    mkdir -p "$HOME/.vst3"
    rm -rf "$HOME/.vst3/Fetch.vst3"
    cp -r build/Fetch_artefacts/Release/VST3/Fetch.vst3 "$HOME/.vst3/"
    echo "Installed: $HOME/.vst3/Fetch.vst3 (rescan plugins in your DAW)"
fi
