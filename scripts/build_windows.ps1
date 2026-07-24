# MeaPet Windows onedir build (PyInstaller)
# Usage (from repo root, with venv activated):
#   powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== MeaPet PyInstaller build ==" -ForegroundColor Cyan
Write-Host "Root: $Root"

function Test-LfsPointer([string]$Path) {
    if (-not (Test-Path $Path)) { return $false }
    $fs = [System.IO.File]::OpenRead($Path)
    try {
        $buf = New-Object byte[] 64
        $n = $fs.Read($buf, 0, 64)
        $head = [System.Text.Encoding]::ASCII.GetString($buf, 0, $n)
        return $head.StartsWith("version https://git-lfs.github.com/spec/v1")
    } finally {
        $fs.Close()
    }
}

$critical = @(
    "vits_models\G_latest.pth",
    "vits_models\finetune_speaker.json",
    "config.example.json",
    "meapet\assets\fonts\LXGWWenKai-Regular.ttf"
)
foreach ($rel in $critical) {
    $p = Join-Path $Root $rel
    if (-not (Test-Path $p)) {
        Write-Warning "Missing optional/critical asset: $rel"
        continue
    }
    if (Test-LfsPointer $p) {
        throw "Refusing to package Git LFS pointer: $rel (run git lfs pull)"
    }
}

# Never ship developer secrets from a local config.json as datas (spec only
# includes config.example.json). If a previous dist left a config.json behind,
# warn the operator.
$distConfig = Join-Path $Root "dist\MeaPet\_internal\config.json"
if (Test-Path $distConfig) {
    Write-Warning "Existing dist config.json will be overwritten only if you wipe dist/ first."
}

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "python not on PATH" }

Write-Host "Python: $($py.Source)"
python -c "import PyInstaller; print('PyInstaller', PyInstaller.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller not installed. pip install pyinstaller"
}

Write-Host "Running pyinstaller MeaPet.spec ..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm MeaPet.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path $Root "dist\MeaPet\MeaPet.exe"
if (-not (Test-Path $exe)) { throw "MeaPet.exe missing after build" }

$assetsFont = Join-Path $Root "dist\MeaPet\_internal\meapet\assets\fonts\LXGWWenKai-Regular.ttf"
if (-not (Test-Path $assetsFont)) {
    Write-Warning "Bundled font missing in dist — UI will fall back to system fonts"
}

$vitsScript = Join-Path $Root "dist\MeaPet\_internal\meapet\tools\vits_infer.py"
if (-not (Test-Path $vitsScript)) {
    Write-Warning "vits_infer.py not extracted as data (in-process VITS still OK if vits_core/models present)"
}

Write-Host "Build OK: $exe" -ForegroundColor Green
Write-Host "Portable data root (runtime): dist\MeaPet\_internal"
Write-Host "Do not commit dist/ or ship a developer config.json with API keys."
