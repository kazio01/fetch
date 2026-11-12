#!/usr/bin/env bash
# Regenerate windows/requirements-windows.txt (CPU) and requirements-windows-cuda.txt (NVIDIA):
# Windows wheels with hashes, pinned to the versions of the tested Linux environment (.venv) where
# the packages are the same. The CUDA file differs only in torch (+cu132: the only CUDA build of
# the same torch version for Windows; needs an NVIDIA driver 580 or newer).
set -euo pipefail
cd "$(dirname "$0")/.."
uv pip freeze --python .venv | grep -v '^-e\|@ file' > windows/constraints-linux.txt
common=(windows/requirements-windows.in --python-platform x86_64-pc-windows-msvc --python-version 3.12
        --generate-hashes)
uv pip compile "${common[@]}" -c windows/constraints-linux.txt --torch-backend cpu \
    -o windows/requirements-windows.txt
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
sed 's/^torch==\([0-9.]*\)+cpu$/torch==\1/' windows/constraints-linux.txt > "$tmp"
uv pip compile "${common[@]}" -c "$tmp" --torch-backend cu132 --no-annotate \
    -o windows/requirements-windows-cuda.txt
# Linux (install.sh): the same versions, hash-locked, CPU PyTorch
uv pip compile linux/requirements-linux.in --python-platform x86_64-manylinux_2_28 --python-version 3.12 \
    --generate-hashes -c windows/constraints-linux.txt --torch-backend cpu -o linux/requirements-linux.txt
