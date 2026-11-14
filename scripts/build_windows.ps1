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
    "models\GPT_weights\mea_pro-e50.ckpt",
    "models\SoVITS_weights\mea_pro_e24_s13704.pth",
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

# Every model included by the spec must be hydrated. Keep this check broad so
# adding another .pth/.ckpt asset cannot silently ship a tiny LFS pointer.
foreach ($assetName in @("models", "vits_models")) {
    $assetRoot = Join-Path $Root $assetName
    if (-not (Test-Path $assetRoot)) { continue }
    Get-ChildItem -LiteralPath $assetRoot -Recurse -File -Include *.pth,*.ckpt |
        ForEach-Object {
            if (Test-LfsPointer $_.FullName) {
                throw "Refusing to package Git LFS pointer: $($_.FullName) (run git lfs pull)"
            }
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

# Never ship a bundle whose runtime dependencies were missing at build time.
# A missing package here produces a silent no-op exe: the pre-GUI dependency
# gate fails, and with console=False stderr is None so nothing is ever printed.
Write-Host "Checking build environment dependencies ..." -ForegroundColor Cyan
python -m meapet.bootstrap --check all
if ($LASTEXITCODE -ne 0) {
    throw "Build environment is missing runtime dependencies (see above). Run: python -m pip install -e "".[all]"""
}

# ── P0-1 构建后置闸门：staging 构建 → 全量断言 → 原子替换 dist ──────────────
# 半成品绝不污染 dist/：PyInstaller 先落 build/stage_dist，scripts/verify_dist.py
# 全过（exe/旧CRT归零/关键资产/无开发者数据泄露/doctor JSON 冒烟/体积）才替换。
# 档位：MEAPET_PROFILE=core|full（Windows 默认 core；=full 回整包对照；Linux 恒 full）。
$Profile = if ($env:MEAPET_PROFILE) { $env:MEAPET_PROFILE } else { "core" }
$Stage = Join-Path $Root "build\stage_dist"
$Work = Join-Path $Root "build\stage_work"
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }

Write-Host "Running pyinstaller MeaPet.spec (staging=$Stage profile=$Profile) ..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm --distpath $Stage --workpath $Work MeaPet.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

python scripts/verify_dist.py --dist (Join-Path $Stage "MeaPet") --platform windows --profile $Profile
if ($LASTEXITCODE -ne 0) {
    throw "构建后置闸门未通过——dist/MeaPet 保持原样（staging 留在 $Stage 供排查）"
}

# 原子替换：旧包先改名 .prev，新包移入；移入失败回滚旧包。.prev 随后清理。
$Final = Join-Path $Root "dist\MeaPet"
$Prev = Join-Path $Root "dist\MeaPet.prev"
if (Test-Path $Prev) { Remove-Item -Recurse -Force $Prev }
if (Test-Path $Final) { Move-Item -LiteralPath $Final -Destination $Prev }
try {
    Move-Item -LiteralPath (Join-Path $Stage "MeaPet") -Destination $Final
} catch {
    if (Test-Path $Prev) { Move-Item -LiteralPath $Prev -Destination $Final }
    throw
}
if (Test-Path $Prev) { Remove-Item -Recurse -Force $Prev }

Write-Host "Build OK: $Final" -ForegroundColor Green
Write-Host "Portable data root (runtime): dist\MeaPet\_internal"
Write-Host "Do not commit dist/ or ship a developer config.json with API keys."
