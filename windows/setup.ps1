# Fetch portable - setup (run through Setup.cmd).
# Installs Python 3.12 and Fetch's packages into this folder, copies ffmpeg, downloads the CLAP
# model (~0.8 GB) and tells the VST3 plugin how to start the search service.
# Again = repair/update (downloads are cached in data\uv-cache).
#   -Quiet    no questions (CI, the installer): the plugin is not copied unless -InstallVst3
#   -InstallVst3  copy the plugin to the system VST3 folder without asking (one administrator prompt)
#   -Cpu      PyTorch for the CPU even if there is an NVIDIA graphics card
#   -Cuda     PyTorch with CUDA (NVIDIA) without detecting the card
# With an NVIDIA card (driver 580 or newer) indexing runs CLAP on the GPU (+~2.5 GB download).
param([switch]$Quiet, [switch]$Cpu, [switch]$Cuda, [switch]$InstallVst3)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$App = $PSScriptRoot
$Root = Split-Path -Parent $App
$Uv = Join-Path $Root "tools\uv.exe"
$PyVersion = "3.12.15"   # must be in the bundled uv's download list (uv 0.13.5 tops out at 3.12.15)

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Run($exe) {
    & $exe @args
    if ($LASTEXITCODE -ne 0) { throw "Failed ($LASTEXITCODE): $exe $args" }
}

$env:UV_CACHE_DIR = Join-Path $Root "data\uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root "python"
$env:UV_PYTHON_INSTALL_BIN = "0"          # no python.exe shims in the user's PATH
$env:UV_PYTHON_INSTALL_REGISTRY = "0"     # not registered with Windows (py launcher)
$env:UV_NO_PROGRESS = "1"

# A running Fetch service (started by the plugin from this folder) holds its Python files open:
# stop it before packages are updated. The plugin starts it again when needed.
$Mine = Join-Path $Root "python"
Get-Process python, pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Mine, [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { Write-Host "Stopping the running Fetch service (pid $($_.Id))"; Stop-Process -Id $_.Id -Force }

Step "Python $PyVersion"
Run $Uv python install $PyVersion
$PyDir = Get-ChildItem (Join-Path $Root "python") -Directory -Filter "cpython-$PyVersion-*" | Select-Object -First 1
if (-not $PyDir) { throw "Python $PyVersion was not installed under $Root\python" }
$Py = Join-Path $PyDir.FullName "python.exe"
$PyW = Join-Path $PyDir.FullName "pythonw.exe"

# NVIDIA card -> CUDA PyTorch (the same torch version, built for CUDA 13.2: driver 580+)
$UseCuda = $false
if ($Cuda) { $UseCuda = $true }
elseif (-not $Cpu) {
    $Gpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
    if ($Gpu) {
        $Drv = $null
        $Smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if ($Smi) { $Drv = (& $Smi.Source --query-gpu=driver_version --format=csv,noheader 2>$null | Select-Object -First 1) }
        Write-Host "Graphics card: $($Gpu.Name), driver $Drv"
        if ($Drv -and ([version]($Drv.Trim()) -ge [version]"580.0")) { $UseCuda = $true }
        else {
            Write-Host "CUDA needs NVIDIA driver 580 or newer: update the driver (nvidia.com or the NVIDIA app)" -ForegroundColor Yellow
            Write-Host "and run Setup.cmd again. Installing the CPU version now." -ForegroundColor Yellow
        }
    }
}
$Torch = if ($UseCuda) { "cu132" } else { "cpu" }
$Req = if ($UseCuda) { "requirements-windows-cuda.txt" } else { "requirements-windows.txt" }

Step "Packages (PyTorch $Torch, transformers, librosa ...; first time ~1 GB, with CUDA ~3.5 GB)"
# Exact versions and hashes, the same versions as the tested Linux setup. Running Setup again with
# the other choice swaps torch (+cpu and +cu132 are different pinned versions).
Run $Uv pip install --python $Py --break-system-packages --require-hashes --torch-backend $Torch `
    -r (Join-Path $App $Req)
$Dev = (& $Py -c "import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')").Trim()
Write-Host "PyTorch device for indexing: $Dev"
if ($UseCuda -and $Dev -eq "CPU") {
    Write-Host "CUDA PyTorch was installed but does not see the card; indexing will use the CPU." -ForegroundColor Yellow
}

# The search service needs no GPU, but the CUDA build of PyTorch loads its GPU libraries anyway (~0.5 GB of
# memory in the service). So with CUDA, the same torch version is also installed for the
# CPU into torch-cpu\, which only the service puts first on its path (fetch_portable.py serve); indexing
# keeps using the GPU.
$TorchCpu = Join-Path $Root "torch-cpu"
if ($UseCuda) {
    Step "PyTorch for the search service (CPU build, ~0.7 GB on disk; uses ~0.5 GB less memory)"
    $Lines = Get-Content (Join-Path $App "requirements-windows.txt")
    $At = ($Lines | Select-String -Pattern '^torch==' | Select-Object -First 1).LineNumber - 1
    $Block = @($Lines[$At])
    for ($i = $At + 1; $i -lt $Lines.Count -and $Lines[$i] -match '^\s'; $i++) { $Block += $Lines[$i] }
    $TorchReq = Join-Path $env:TEMP "fetch-torch-cpu.txt"
    Set-Content -Encoding ASCII $TorchReq $Block
    Run $Uv pip install --python $Py --target $TorchCpu --no-deps --require-hashes --torch-backend cpu -r $TorchReq
    Remove-Item -Force $TorchReq
    $V = (& $Py -c "import sys; sys.path.insert(0, r'$TorchCpu'); import torch; print(torch.__version__)").Trim()
    Write-Host "Search service: torch $V"
    if ($V -notmatch '\+cpu$') { throw "The CPU build of PyTorch did not load from $TorchCpu ($V)" }
} elseif (Test-Path $TorchCpu) {
    Remove-Item -Recurse -Force $TorchCpu        # the main PyTorch is the CPU build already
}

Step "ffmpeg"
$Bin = Join-Path $Root "bin"
New-Item -ItemType Directory -Force $Bin | Out-Null
$Ff = (& $Py -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())").Trim()
Copy-Item -Force $Ff (Join-Path $Bin "ffmpeg.exe")

Step "CLAP model (~0.8 GB, first time only)"
Run $Py (Join-Path $App "fetch_portable.py") setup-model

Step "Plugin: how to start the search service"
$CmdDir = Join-Path $env:APPDATA "Fetch"
$CmdFile = Join-Path $CmdDir "server-command.txt"
New-Item -ItemType Directory -Force $CmdDir | Out-Null
$Line = "`"$PyW`" `"$(Join-Path $App 'fetch_portable.py')`" serve"
if ((Test-Path $CmdFile) -and ((Get-Content -Raw $CmdFile).Trim() -ne $Line)) {
    Copy-Item -Force $CmdFile "$CmdFile.bak"
    Write-Host "(previous command saved as $CmdFile.bak)"
}
[IO.File]::WriteAllText($CmdFile, $Line)
Write-Host $CmdFile
Write-Host $Line

Step "VST3 plugin"
$Vst = Join-Path $Root "VST3\Fetch.vst3"
$SysVst = Join-Path $env:CommonProgramFiles "VST3"
if (-not (Test-Path $Vst)) {
    Write-Host "No VST3\Fetch.vst3 in this package."
} elseif ($Quiet -and -not $InstallVst3) {
    Write-Host "Skipped (-Quiet). The plugin is in $Vst"
} else {
    $Yes = $InstallVst3
    if (-not $Yes) {
        Write-Host "Copy Fetch.vst3 to $SysVst (the standard VST3 folder, asks for administrator rights)?"
        Write-Host "Or answer n and add this folder to your DAW's plugin locations instead: $(Split-Path $Vst)"
        $Yes = (Read-Host "[Y/n]") -notmatch '^[nN]'
    }
    if ($Yes) {
        $Dst = Join-Path $SysVst "Fetch.vst3"
        $Cmd = "if (Test-Path '$Dst') { Remove-Item -Recurse -Force '$Dst' }; " +
               "New-Item -ItemType Directory -Force '$SysVst' | Out-Null; Copy-Item -Recurse -Force '$Vst' '$Dst'"
        try { Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile", "-Command", $Cmd }
        catch { Write-Host "Administrator rights were not given." -ForegroundColor Yellow }   # UAC cancelled
        if (Test-Path (Join-Path $Dst "Contents")) { Write-Host "Installed: $Dst" }
        else { Write-Host "Not copied. Add $(Split-Path $Vst) to your DAW's plugin locations instead." -ForegroundColor Yellow }
    }
}

Write-Host "`nSetup done." -ForegroundColor Green
Write-Host "Next: open your DAW, load the Fetch instrument and add your sample folder in its Settings"
Write-Host "(gear icon > Library folders > Add folder), or run 'Index Samples.cmd'. A network share works too."
