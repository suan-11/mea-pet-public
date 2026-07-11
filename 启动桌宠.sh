#!/usr/bin/env bash
set -euo pipefail

# ======== 0. 初始化 ========
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- 编码 ---
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1

# --- X11 (Linux 桌面必须) ---
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
if [[ -z "${DISPLAY:-}" ]]; then
    echo "[MeaPet] ⚠️  DISPLAY 未设置，请确认你在 X11/Wayland 桌面环境中运行"
fi

# --- XDG Runtime (PulseAudio/PipeWire 需要) ---
if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi

# --- Qt 相关 ---
export QTWEBENGINE_DISABLE_SANDBOX=1
# 输入法: 留空让 Qt 自动检测；可手动指定 fcitx / ibus / xim
# export QT_IM_MODULE=

# --- TTS (GPT-SoVITS) ---
# 如果你有独立的 GPT-SoVITS Python 环境，设置此变量指向其 python 解释器
# export GSV_PYTHON="$HOME/GPT-SoVITS/.venv/bin/python3"

# --- HuggingFace 镜像 (国内用户) ---
# export HF_ENDPOINT="https://hf-mirror.com"
# export TOKENIZERS_PARALLELISM=false

# --- 清理可能污染子进程的变量 ---
unset PYTHONPATH PYTHONHOME 2>/dev/null || true

VENV_DIR="$SCRIPT_DIR/.venv"
PY_CMD=""

echo "[MeaPet] 工作目录: $SCRIPT_DIR"

# ======== 1. 检测已有 Python ========
find_python() {
    # 1a. 检查项目内 venv
    if [[ -x "$VENV_DIR/bin/python3" ]]; then
        PY_CMD="$VENV_DIR/bin/python3"
        return 0
    fi

    # 1b. 检查 Hermes venv (Linux 常见路径)
    local hermes_paths=(
        "$HOME/.local/share/hermes/hermes-agent/venv/bin/python3"
        "$HOME/.hermes/hermes-agent/venv/bin/python3"
    )
    for hp in "${hermes_paths[@]}"; do
        if [[ -x "$hp" ]]; then
            PY_CMD="$hp"
            return 0
        fi
    done

    # 1c. 系统 PATH 中的 python3 / python
    if command -v python3 &>/dev/null; then
        PY_CMD="python3"
        return 0
    elif command -v python &>/dev/null; then
        PY_CMD="python"
        return 0
    fi

    return 1
}

if find_python; then
    echo "[MeaPet] 检测到 Python: $PY_CMD ($($PY_CMD --version 2>&1))"
else
    echo "[MeaPet] ❌ 未检测到 Python3！"
    echo ""
    echo "请通过系统包管理器安装 Python 3.10+："
    echo "  Debian/Ubuntu: sudo apt install python3 python3-pip python3-venv"
    echo "  Fedora/RHEL:   sudo dnf install python3 python3-pip"
    echo "  Arch Linux:    sudo pacman -S python python-pip"
    echo "  macOS:         brew install python3"
    echo ""
    echo "安装后请重新运行此脚本。"
    exit 1
fi

# ======== 2. 创建/激活虚拟环境 ========
if [[ ! -d "$VENV_DIR" ]]; then
    echo "[MeaPet] 正在创建虚拟环境 ..."
    $PY_CMD -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
PY_CMD="$VENV_DIR/bin/python3"

echo "[MeaPet] 使用虚拟环境: $VENV_DIR"
echo "[MeaPet] Python 版本: $($PY_CMD --version 2>&1)"

# ======== 3. 确保 pip 可用 ========
if ! $PY_CMD -m pip --version &>/dev/null; then
    echo "[MeaPet] 正在安装 pip ..."
    $PY_CMD -m ensurepip --upgrade 2>/dev/null || {
        echo "[MeaPet] ensurepip 失败，尝试 get-pip.py ..."
        curl -sSL https://bootstrap.pypa.io/get-pip.py | $PY_CMD
    }
fi

# ======== 4. 选择 pip 镜像 ========
MIRROR_URL=""
MIRROR_CHOICE="1"
if [[ -t 0 ]]; then
    echo ""
    echo "请选择 pip 安装源:"
    echo "  1) PyPI 默认源 (国际)"
    echo "  2) 清华 TUNA 镜像 (国内)"
    echo "  3) 跳过依赖安装"
    read -r -p "请输入选项 [1-3] (默认: 1): " MIRROR_CHOICE
    MIRROR_CHOICE="${MIRROR_CHOICE:-1}"
    case "$MIRROR_CHOICE" in
        2) MIRROR_URL="https://pypi.tuna.tsinghua.edu.cn/simple" ;;
        3) echo "[MeaPet] ⏭️  跳过依赖安装"; ;;
        *) MIRROR_URL="" ;;
    esac
fi

# ======== 5. 安装基础依赖 ========
REQ_FILE="$SCRIPT_DIR/linux_requirements.txt"
if [[ ! -f "$REQ_FILE" ]]; then
    echo "[MeaPet] ⚠️  未找到 linux_requirements.txt，尝试使用 requirements.txt ..."
    REQ_FILE="$SCRIPT_DIR/requirements.txt"
fi

if [[ "$MIRROR_CHOICE" == "3" ]]; then
    echo "[MeaPet] 跳过依赖安装"
elif [[ -f "$REQ_FILE" ]]; then
    echo "[MeaPet] 正在安装基础依赖 ..."
    echo "[MeaPet] 💡 Live2D 模型支持需手动配置，下载地址及说明请参阅项目 README"
    PIP_ARGS=(-r "$REQ_FILE" -q)
    if [[ -n "$MIRROR_URL" ]]; then
        PIP_ARGS+=(--index-url "$MIRROR_URL" --trusted-host pypi.tuna.tsinghua.edu.cn)
    fi
    $PY_CMD -m pip install "${PIP_ARGS[@]}" || {
        echo "[MeaPet] ❌ 基础依赖安装失败"
        exit 1
    }
else
    echo "[MeaPet] ⚠️  未找到任何 requirements 文件，跳过依赖安装"
fi

# ======== 6. 检查 Qt 平台插件 ========
if ! $PY_CMD -c "from PyQt5.QtCore import QT_VERSION_STR; print(QT_VERSION_STR)" 2>/dev/null; then
    echo "[MeaPet] ⚠️  PyQt5 未安装或 Qt 库缺失"
    echo "    Ubuntu/Debian: sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1 libopengl0"
    echo "    Fedora:        sudo dnf install libxcb xcb-util-cursor libxkbcommon-x11 libglvnd-glx"
    echo "    Arch:          sudo pacman -S libxcb xcb-util-cursor libxkbcommon libglvnd"
fi

# ======== 7. 启动 ========
if [[ ! -f "$SCRIPT_DIR/config.json" ]]; then
    echo "[MeaPet] 首次运行，启动配置向导 ..."
    "$PY_CMD" "$SCRIPT_DIR/setup_wizard.py"
    if [[ ! -f "$SCRIPT_DIR/config.json" ]]; then
        echo "[MeaPet] 配置未完成，退出。"
        exit 0
    fi
fi

echo "[MeaPet] 启动桌宠 ..."
"$PY_CMD" "$SCRIPT_DIR/pet.py"