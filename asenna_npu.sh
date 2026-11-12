#!/usr/bin/env bash
# asenna_npu.sh – set up the Ryzen AI NPU for Fetch's sound features (Arch-based systems).
#
# Steps (each one is checked before anything is installed):
#   1. amdxdna driver (in the kernel since 6.14) and /dev/accel/accel0
#   2. permissions: render group, udev rule, memlock limit
#   3. XRT + the amdxdna plugin (pacman, or from source if the packages aren't available)
#   4. IRON (mlir_aie) + the Peano compiler in their own Python environment
#   5. the samplehaku-piirteet command and an NPU test
#
# Run as a normal user: bash asenna_npu.sh   (sudo is asked for when needed)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${SAMPLEHAKU_DIR:-$HOME/.local/share/samplehaku}"
IRON="$DATA/ironenv"
SRC="$DATA/mlir-aie"
BIN="$HOME/.local/bin"
WHEELS="https://github.com/Xilinx/mlir-aie/releases/expanded_assets/latest-wheels-4"
RELOGIN=0
# Note: no "command | grep -q" checks: with pipefail, grep -q exiting early kills the
# command before it (SIGPIPE) and the check looks like it failed.
# The window must not close before the messages have been read:
trap 'rc=$?; echo; [[ $rc -eq 0 ]] && echo "Script finished." || echo "Script stopped (error $rc), see the message above."; [[ -t 0 ]] && read -r -p "Press Enter to close… " _ || true' EXIT

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   ✓ %s\n' "$*"; }
warn() { printf '   ! %s\n' "$*"; }
die()  { printf '\n✗ %s\n' "$*" >&2; exit 1; }
ask()  { local a; read -r -p "   $* [y/N] " a; [[ "$a" =~ ^[yYkK] ]]; }

[[ $EUID -ne 0 ]] || die "Run as a normal user, not with sudo."
[[ -f "$HERE/aanipiirteet.py" ]] || die "aanipiirteet.py is missing from this folder."

# ------------------------------------------------------------------ 1. driver
say "1/5 NPU driver"
grep -q "Ryzen" /proc/cpuinfo && ok "$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)"
if ! grep -q '^amdxdna ' /proc/modules; then
    warn "amdxdna is not loaded, trying modprobe"
    sudo modprobe amdxdna || true
fi
grep -q '^amdxdna ' /proc/modules || die "Could not load the amdxdna module (kernel $(uname -r)). Linux 6.14+ is needed."
ok "amdxdna loaded (kernel $(uname -r))"
[[ -e /dev/accel/accel0 ]] || die "/dev/accel/accel0 is missing. Check in the BIOS that the NPU (IPU) is enabled, and see: sudo dmesg | grep -i amdxdna"
ok "/dev/accel/accel0 found"

# ------------------------------------------------------------------ 2. permissions
say "2/5 Permissions"
getent group render >/dev/null || sudo groupadd render
if grep -qw render <<<"$(id -nG "$USER")" || grep -qw "$USER" <<<"$(getent group render)"; then
    ok "$USER is in the render group"
else
    sudo usermod -aG render "$USER"; RELOGIN=1
    ok "added $USER to the render group (takes effect at the next login)"
fi
RULE=/etc/udev/rules.d/99-amdxdna.rules
if [[ ! -f $RULE ]]; then
    echo 'SUBSYSTEM=="accel", KERNEL=="accel*", GROUP="render", MODE="0660"' | sudo tee $RULE >/dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=accel --action=add
    sudo udevadm settle
fi
ok "udev rule: $(stat -c '%G %a' /dev/accel/accel0) /dev/accel/accel0"
LIM=/etc/security/limits.d/99-amdxdna.conf
if [[ ! -f $LIM ]]; then
    printf '@render - memlock unlimited\n' | sudo tee $LIM >/dev/null; RELOGIN=1
fi
ok "memlock limit: $LIM"

# ------------------------------------------------------------------ 3. XRT
say "3/5 XRT and the amdxdna plugin"
xrt_env() { [[ -f /opt/xilinx/xrt/setup.sh ]] && source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1 || true; }
xrt_env
build_xrt() {
    local b="$DATA/build-xdna"
    warn "Building XRT and the plugin from source ($b). This takes 15–40 min."
    sudo pacman -S --needed base-devel git cmake boost openssl protobuf rapidjson curl json-c ncurses \
        util-linux-libs libdrm elfutils opencl-headers opencl-clhpp ocl-icd pybind11 systemtap python
    [[ -d "$b" ]] || git clone --recursive https://github.com/amd/xdna-driver.git "$b"
    (cd "$b/xrt/build" && ./build.sh -npu -opt -disable-werror -j "$(nproc)" && sudo make -C Release install)
    source /opt/xilinx/xrt/setup.sh >/dev/null
    # -nokmod: does NOT touch the kernel's own amdxdna module
    (cd "$b/build" && ./build.sh -release -nokmod -j "$(nproc)" && sudo tar xzf Release/xrt_plugin.*-amdxdna.tar.gz -C /)
}
if command -v xrt-smi >/dev/null; then
    ok "xrt-smi found: $(command -v xrt-smi)"
elif pacman -Si xrt-plugin-amdxdna >/dev/null 2>&1; then
    sudo pacman -S --needed xrt xrt-plugin-amdxdna
    xrt_env
else
    warn "The xrt / xrt-plugin-amdxdna packages are not in the enabled repositories."
    warn "They are in Arch's extra-testing repository. Options: enable [extra-testing]"
    warn "in /etc/pacman.conf and run this script again, OR build from source now."
    ask "Build from source?" && build_xrt || die "XRT is missing."
fi
command -v xrt-smi >/dev/null || die "xrt-smi not found after installation."
# the render group takes effect only after logging in again (not every distro has the sg command)
if [[ ! -r /dev/accel/accel0 || ! -w /dev/accel/accel0 ]]; then
    die "XRT and the NPU plugin are installed, but the render group is not active in this session yet.
  Log out and back in, then run again:  bash \"$HERE/asenna_npu.sh\"
  (steps already done are skipped)"
fi
NPU_LINE="$(xrt-smi examine 2>&1 | grep -E 'npu|NPU|Phoenix|RyzenAI' | head -3 || true)"
[[ -n "$NPU_LINE" ]] || { xrt-smi examine || true; die "XRT doesn't see the NPU. If a log-out message was shown, log out and run again."; }
ok "XRT sees the NPU:"; echo "$NPU_LINE" | sed 's/^/      /'

# ------------------------------------------------------------------ 4. IRON
say "4/5 IRON (mlir_aie) and Peano"
command -v llvm-objcopy >/dev/null || sudo pacman -S --needed llvm   # IRON needs llvm-objcopy
command -v uv >/dev/null || sudo pacman -S --needed uv
command -v git >/dev/null || sudo pacman -S --needed git
command -v ffmpeg >/dev/null || sudo pacman -S --needed ffmpeg

# pyxrt is built for one Python version; the IRON environment must use the same one.
PYXRT_SO="$(find /opt/xilinx/xrt /usr/lib -maxdepth 4 -name 'pyxrt*.so' 2>/dev/null | head -1 || true)"
[[ -n "$PYXRT_SO" ]] || die "The pyxrt library was not found in the XRT installation."
PYV="$(basename "$PYXRT_SO" | sed -nE 's/.*cpython-3([0-9]+).*/3.\1/p')"
PYV="${PYV:-$(python3 -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')}"
# A link in its own folder, so the whole system site-packages doesn't leak into the IRON environment.
PYXRT_DIR="$DATA/pyxrt"; mkdir -p "$PYXRT_DIR"; ln -sf "$PYXRT_SO" "$PYXRT_DIR/"
ok "pyxrt: $PYXRT_SO (Python $PYV)"

[[ -x "$IRON/bin/python" ]] || uv venv --seed --python "$PYV" "$IRON"
"$IRON/bin/python" -m pip install -q --upgrade pip
"$IRON/bin/python" -m pip install -q mlir_aie -f "$WHEELS"
VER="$("$IRON/bin/python" -c 'import importlib.metadata as m; print(m.version("mlir_aie"))')"
ok "mlir_aie $VER"

# The example code (whole_array.py) must match the installed version.
[[ -d "$SRC/.git" ]] || git clone --filter=blob:none https://github.com/Xilinx/mlir-aie.git "$SRC"
SHA="$(sed -nE 's/.*\+g([0-9a-f]+).*/\1/p' <<<"$VER")"
git -C "$SRC" fetch -q origin
git -C "$SRC" fetch -q --tags origin
# The source (whole_array.py) must match the installed wheel: the main branch won't do
# (tested: mlir_aie 1.3.4 + main -> ModuleNotFoundError aie.helpers.npdtypes).
if [[ -n "$SHA" ]]; then git -C "$SRC" checkout -q "$SHA"; ok "mlir-aie source: commit $SHA";
elif git -C "$SRC" rev-parse -q --verify "refs/tags/v${VER%%+*}" >/dev/null; then
    git -C "$SRC" checkout -q "v${VER%%+*}"; ok "mlir-aie source: tag v${VER%%+*}"
else git -C "$SRC" checkout -q origin/main; warn "no tag found for version $VER, using the main branch"; fi
"$IRON/bin/python" -m pip install -q -r "$SRC/utils/peano-requirements.txt"
SITE="$("$IRON/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
[[ -d "$SITE/mlir_aie" && -d "$SITE/llvm-aie" ]] || die "mlir_aie or llvm-aie is missing: $SITE"
ok "Peano: $SITE/llvm-aie"

# ------------------------------------------------------------------ 5. command + test
say "5/5 The samplehaku-piirteet command"
mkdir -p "$BIN"
cat > "$BIN/samplehaku-piirteet" <<EOF
#!/usr/bin/env bash
# Computes Fetch's sound features on the NPU (or on the CPU if the NPU doesn't respond).
[[ -f /opt/xilinx/xrt/setup.sh ]] && source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1
export MLIR_AIE_INSTALL_DIR="$SITE/mlir_aie"
export PEANO_INSTALL_DIR="$SITE/llvm-aie"
export MLIR_AIE_DIR="$SRC"
export PATH="\$MLIR_AIE_INSTALL_DIR/bin:\$PATH"
export PYTHONPATH="\$MLIR_AIE_INSTALL_DIR/python:$PYXRT_DIR:\${PYTHONPATH:-}"
export LD_LIBRARY_PATH="\$MLIR_AIE_INSTALL_DIR/lib:\${LD_LIBRARY_PATH:-}"
export NPU2=0
# Before logging in again, the render group is not active (not every distro has the sg command).
if [[ ! -w /dev/accel/accel0 ]]; then
    echo "(No access to the NPU in this session: log out and back in. Computing on the CPU for now.)" >&2
    set -- --backend cpu "\$@"
fi
exec "$IRON/bin/python" "$HERE/aanipiirteet.py" "\$@"
EOF
chmod +x "$BIN/samplehaku-piirteet"
ok "$BIN/samplehaku-piirteet"

say "NPU test (the first run compiles the NPU program, 1–3 min)"
if "$BIN/samplehaku-piirteet" --selftest --backend npu; then
    say "Done! The NPU now computes Fetch's sound features."
    echo "   samplehaku-piirteet             compute the library's features"
    echo "   samplehaku search \"kick +dark +tight\""
else
    warn "The NPU test failed. Features can still be computed on the CPU: samplehaku-piirteet --backend cpu"
    warn "Please include the error message above in a bug report."
fi
[[ $RELOGIN -eq 0 ]] || warn "Log out and back in so the render group and the memlock limit take effect."
