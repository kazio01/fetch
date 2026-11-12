#!/usr/bin/env bash
# Fetch (samplehaku) – base installation for Arch-based systems
# Run from the folder that contains samplehaku.py:  bash asenna.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "== System packages (ffmpeg for mp3/m4a files, pw-play for playback, wl-clipboard for copying)"
sudo pacman -S --needed ffmpeg pipewire wl-clipboard uv

echo "== Python environment (Python 3.12, so the PyTorch packages are sure to be available)"
cd "$DIR"
uv venv --python 3.12 .venv
# CPU build of PyTorch: about 200 MB, where the CUDA build would be several GB for nothing
uv pip install --python .venv torch --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv transformers librosa soundfile numpy

echo "== Command 'samplehaku' in ~/.local/bin"
mkdir -p ~/.local/bin
cat > ~/.local/bin/samplehaku <<EOF
#!/usr/bin/env bash
exec "$DIR/.venv/bin/python" "$DIR/samplehaku.py" "\$@"
EOF
chmod +x ~/.local/bin/samplehaku

echo
echo "Done. Next:"
echo "  samplehaku index ~/Samples      # the first run downloads the model (~0.8 GB)"
echo "  samplehaku                      # interactive search"
