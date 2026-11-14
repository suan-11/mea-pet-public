#!/usr/bin/env bash
# MeaPet Linux onedir build (PyInstaller) — 与 scripts/build_windows.ps1 同一套闸。
#
# 用法（仓库根，venv 已激活）：bash scripts/build_linux.sh
# 环境变量：PYTHON=python          指定解释器（默认 PATH 上的 python）
#           SKIP_BRIDGE=1          复用仓库根已有的 .so，不重跑 cargo
#           SKIP_FIDUS=1           不自动取件（缺 fidus 时由 spec 响亮拦下）
#
# 为什么要有这个文件：README 的 Linux 打包路径此前是两行手敲命令、零防护。模型没水化时它
# 会把 134 B 的 Git LFS 指针当模型打进包（CONTRIBUTING.md 明令禁止的那件），而 spec 只拦
# 桥接层缺件与 fidus 缺件，不看指针——指针长得像文件，PyInstaller 照收。
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python}"
command -v "$PY" >/dev/null 2>&1 || {
  echo "[FAIL] 找不到解释器 '$PY'（用 PYTHON=/path/to/python 指定）" >&2
  exit 1
}
echo "== MeaPet Linux onedir build =="
echo "仓库根：$(pwd)"
echo "解释器：$("$PY" -c 'import sys; print(sys.executable)')"

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

# ---- Git LFS 指针闸 ----
# 指针文件只有 130 B 上下，特征就是头那一行。
is_lfs_pointer() {
  local f="$1"
  [ -f "$f" ] || return 1
  head -c 128 "$f" 2>/dev/null | grep -q '^version https://git-lfs\.github\.com/spec/v1$'
}

critical=(
  "vits_models/G_latest.pth"
  "models/GPT_weights/mea_pro-e50.ckpt"
  "models/SoVITS_weights/mea_pro_e24_s13704.pth"
  "vits_models/finetune_speaker.json"
  "config.example.json"
  "meapet/assets/fonts/LXGWWenKai-Regular.ttf"
)
for rel in "${critical[@]}"; do
  if [ ! -f "$rel" ]; then
    echo "[WARN] 缺资产：$rel" >&2
    continue
  fi
  is_lfs_pointer "$rel" && fail "拒绝打包 Git LFS 指针：$rel（先装 git-lfs，再 git lfs pull）"
done

# 覆盖面放宽：spec 整棵收 models/ 与 vits_models/，新增的 .pth/.ckpt 也须查得到，
# 否则「又加了一只模型没加闸」会静默进包。
if [ -d models ] || [ -d vits_models ]; then
  while IFS= read -r -d '' f; do
    is_lfs_pointer "$f" && fail "拒绝打包 Git LFS 指针：$f（先装 git-lfs，再 git lfs pull）"
  done < <(find models vits_models -type f \( -name '*.pth' -o -name '*.ckpt' \) -print0 2>/dev/null)
fi

# ---- 开发者配置不得进包 ----
# spec 只带 config.example.json。上一次构建若留下了真 config.json，它会被手动拷走再分发。
if [ -f "dist/MeaPet/_internal/config.json" ]; then
  echo "[WARN] dist/MeaPet/_internal/config.json 还在——里面可能有 API key，先清 dist/ 再打" >&2
fi

# ---- ① 桥接层：Linux 打包的硬前提 ----
if [ "${SKIP_BRIDGE:-0}" = "1" ]; then
  echo ">>> 跳过桥接层构建（SKIP_BRIDGE=1），复用仓库根产物"
else
  command -v cargo >/dev/null 2>&1 || fail "PATH 上没有 cargo，且未设 SKIP_BRIDGE=1"
  echo ">>> 构建 layer-shell 桥接层（Rust cdylib → 仓库根 liblayer_shell_shim.so）"
  bash build_layer_shell.sh
fi
[ -f liblayer_shell_shim.so ] ||
  fail "仓库根没有 liblayer_shell_shim.so ⇒ 这个包没有穿透模式（它是 ctypes 直读的裸 .so，依赖分析看不见）"

# ---- ② fidus：Linux 侧能力，走钉死的 Release 直链 ----
if [ "${SKIP_FIDUS:-0}" != "1" ] &&
  ! "$PY" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('fidus') else 1)"; then
  echo ">>> 环境里没有 fidus ⇒ 按渠道取件（钉 URL + 边车核验 + 回读 __git_commit__）"
  "$PY" -m meapet.bootstrap --install-fidus
fi

# ---- ③ 构建环境依赖门禁 ----
# 永不打包一个构建时就缺运行时依赖的 bundle：缺包会让 pre-GUI 依赖闸失败，而 console=False
# 时 stderr 是 None，产出的是一个安静的空壳（同 build_windows.ps1 的理由）。
if ! "$PY" -m meapet.bootstrap --check all; then
  fail "构建环境缺运行时依赖（见上）⇒ 先跑 \"$PY -m pip install -e '.[linux]'\"（VITS 面另加 .[vits]）"
fi

# ---- ④ 构建（P0-1：staging，半成品不落 dist/）----
"$PY" -c "import PyInstaller" 2>/dev/null ||
  fail "未装 PyInstaller：$PY -m pip install pyinstaller"
STAGE="build/stage_dist"
WORK="build/stage_work"
rm -rf "$STAGE"
echo ">>> $PY -m PyInstaller --noconfirm --distpath $STAGE --workpath $WORK MeaPet.spec"
"$PY" -m PyInstaller --noconfirm --distpath "$STAGE" --workpath "$WORK" MeaPet.spec

# ---- ⑤ 后置闸门（exe/关键资产/无泄露/shim/numpy/doctor JSON 冒烟/体积）----
# Linux 恒 full 档（瘦身是 Windows 包的事，见 MeaPet.spec 注释）。
"$PY" scripts/verify_dist.py --dist "$STAGE/MeaPet" --platform linux --profile full ||
  fail "构建后置闸门未通过——dist/MeaPet 保持原样（staging 留在 $STAGE 供排查）"

# ---- ⑥ 原子替换 dist/MeaPet ----
mkdir -p dist
[ ! -e dist/MeaPet.prev ] || rm -rf dist/MeaPet.prev
if [ -e dist/MeaPet ]; then mv dist/MeaPet dist/MeaPet.prev; fi
if ! mv "$STAGE/MeaPet" dist/MeaPet; then
  [ ! -e dist/MeaPet.prev ] || mv dist/MeaPet.prev dist/MeaPet
  fail "原子替换失败，已回滚旧产物"
fi
rm -rf dist/MeaPet.prev
bin="dist/MeaPet/MeaPet"
echo ">>> 构建 OK：$bin"
cat <<'TXT'
产物就绪。A 阶段那六项判据要在真机上逐条过（缺一条就不能说"Linux 过了"）：
  1 起得来     —— 在一台没装 Python 依赖的机器上双击/终端启动 $bin
  2 Live2D 出图—— 不是 PNG 回退
  3 have_engine() 为真（菜单「定位与穿透」不响亮拒绝）
  4 穿透能开关 —— 切进去再切回来，窗口不崩
  5 配置改动持久 —— 改一次配置、退出、重启，改动仍在
  6 出声面干净 —— stdout/stderr 无 ImportError、无降级以外的红字
TXT
