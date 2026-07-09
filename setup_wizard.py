"""
MeaPet 配置向导 — 自动检测环境 + 一键配置桌宠
支持自动安装依赖、下载 Ollama、拉取模型
"""
import sys
import os
import json
import re
import subprocess
import threading
import time
import urllib.request
import io

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QLineEdit,
    QTextEdit, QStackedWidget, QFrame, QFileDialog,
    QCheckBox, QComboBox, QMessageBox, QProgressBar
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QPalette, QColor

# ─── 常量 ───
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
EXAMPLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.example.json")

COLOR_BG = "#1a1a2e"
COLOR_CARD = "rgba(30, 30, 60, 220)"
COLOR_ACCENT = "#FFB6C1"
COLOR_TEXT = "#F0F0F0"
COLOR_OK = "#7dffb3"
COLOR_WARN = "#ffd700"
COLOR_ERR = "#ff6b6b"

STYLE_INPUT = f"""
    QLineEdit {{
        background: rgba(0, 0, 0, 100);
        color: white;
        border: 1px solid rgba(255, 255, 255, 30);
        border-radius: 8px;
        padding: 10px 14px;
        font-size: 14px;
    }}
    QLineEdit:focus {{
        border: 1px solid {COLOR_ACCENT};
    }}
"""

STYLE_BTN_PRIMARY = f"""
    QPushButton {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #ff6b9d, stop:1 #ff9a56);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 12px 30px;
        font-size: 15px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #ff7bab, stop:1 #ffaa66);
    }}
    QPushButton:disabled {{
        background: #555;
        color: #999;
    }}
"""

STYLE_BTN_SECONDARY = f"""
    QPushButton {{
        background: rgba(255,255,255,15);
        color: {COLOR_TEXT};
        border: 1px solid rgba(255,255,255,30);
        border-radius: 8px;
        padding: 12px 30px;
        font-size: 15px;
    }}
    QPushButton:hover {{
        background: rgba(255,255,255,25);
    }}
"""


# ═══════════════════════════════════════
# 后台工作器（防止界面卡死）
# ═══════════════════════════════════════

class WorkerSignals(QObject):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(bool, str)


def pip_install(packages: list) -> bool:
    """安装 Python 包，返回是否成功"""
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install"] + packages,
            capture_output=True, text=True, timeout=300
        )
        return True
    except Exception:
        return False


def check_installed(package: str) -> bool:
    """检查 Python 包是否已安装"""
    try:
        __import__(package.replace("-", "_"))
        return True
    except ImportError:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "show", package],
                capture_output=True, timeout=10, check=True
            )
            return True
        except Exception:
            return False


def download_file(url: str, dest: str, progress_callback=None):
    """下载文件，可选进度回调（限频，兼顾无 Content-Length 的情况）"""
    import time as _time
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 65536  # 64KB，减少更新频率
            last_report = 0
            last_pct = -1
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = _time.time()
                    if progress_callback and now - last_report >= 0.2:
                        last_report = now
                        if total > 0:
                            pct = int(downloaded / total * 100)
                            if pct != last_pct:
                                last_pct = pct
                                progress_callback(pct)
                        else:
                            # 无 Content-Length 时给个脉冲效果（50% 表示正在下载）
                            progress_callback(-1)
            # 完成后确保 100%（无论是否已知 Content-Length）
            if progress_callback and (total == 0 or last_pct != 100):
                progress_callback(100)
        return True
    except Exception:
        return False


def check_ollama_running():
    """检查 Ollama 是否在运行"""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                return True, models
        return False, []
    except Exception:
        return False, []


def check_ollama_installed():
    """检查 Ollama 是否已安装（看能不能找到 ollama 命令）"""
    try:
        subprocess.run(["ollama", "--version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def pull_ollama_model(model: str, log_callback=None):
    """拉取 Ollama 模型"""
    try:
        proc = subprocess.Popen(
            ["ollama", "pull", model],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        for line in proc.stdout:
            if log_callback:
                log_callback(line.strip())
        proc.wait()
        return proc.returncode == 0
    except Exception as e:
        if log_callback:
            log_callback(f"错误：{e}")
        return False


# ═══════════════════════════════════════
# 页面：环境检测
# ═══════════════════════════════════════

class EnvCheckPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"""
            QFrame#card {{
                background: {COLOR_CARD};
                border: 1px solid rgba(255,255,255,20);
                border-radius: 12px;
                padding: 20px;
            }}
        """)
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(12)

        title = QLabel("🔧 环境检测")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #FFB6C1;")
        self.layout.addWidget(title)

        desc = QLabel("检查你的电脑上缺什么，一键补全")
        desc.setStyleSheet("font-size: 13px; color: #aaa; padding-bottom: 10px;")
        self.layout.addWidget(desc)

        # 检测结果列表
        self.items = {}  # name -> (label, status_label, btn)
        for name, hint in [
            ("Python 3.10+", "运行桌宠的基础"),
            ("pip", "Python 包管理器"),
            ("PyQt5", "窗口界面库"),
            ("pywin32", "Windows 窗口控制"),
            ("live2d-py", "Live2D 模型渲染（可选）"),
            ("PyOpenGL", "OpenGL 渲染（可选）"),
            ("Ollama", "本地 AI 后端（可选）"),
        ]:
            row = QHBoxLayout()
            name_label = QLabel(name)
            name_label.setStyleSheet("font-size: 14px; min-width: 120px;")
            row.addWidget(name_label)

            hint_label = QLabel(hint)
            hint_label.setStyleSheet("font-size: 11px; color: #666;")
            row.addWidget(hint_label)

            row.addStretch()

            status = QLabel("检测中…")
            status.setStyleSheet("font-size: 13px; color: #888;")
            row.addWidget(status)

            btn = QPushButton("安装")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,15);
                    color: {COLOR_TEXT};
                    border: 1px solid rgba(255,255,255,30);
                    border-radius: 6px;
                    padding: 4px 14px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background: rgba(255,255,255,25);
                }}
                QPushButton:disabled {{
                    background: transparent;
                    color: #555;
                    border: 1px solid transparent;
                }}
            """)
            btn.setFixedWidth(60)
            btn.hide()
            # 连接安装按钮
            if name == "Ollama":
                btn.clicked.connect(self.install_ollama)
            else:
                btn.clicked.connect(lambda checked, n=name: self.install_package(n))
            row.addWidget(btn)

            self.items[name] = (name_label, status, btn, hint)
            self.layout.addLayout(row)

        # 总体进度
        self.layout.addSpacing(10)
        self.total_bar = QProgressBar()
        self.total_bar.setRange(0, 100)
        self.total_bar.setValue(0)
        self.total_bar.setFixedHeight(6)
        self.total_bar.setTextVisible(False)
        self.total_bar.setStyleSheet("""
            QProgressBar {
                background: rgba(255,255,255,15);
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff6b9d, stop:1 #ff9a56);
                border-radius: 3px;
            }
        """)
        self.layout.addWidget(self.total_bar)

        self.total_status = QLabel("正在检测…")
        self.total_status.setStyleSheet("font-size: 12px; color: #888;")
        self.layout.addWidget(self.total_status)

        self.layout.addStretch()

        # 日志输出
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(120)
        self.log_area.setStyleSheet("""
            QTextEdit {
                background: rgba(0,0,0,80);
                color: #aaa;
                border: 1px solid rgba(255,255,255,10);
                border-radius: 6px;
                padding: 8px;
                font-size: 11px;
                font-family: Consolas, monospace;
            }
        """)
        self.log_area.hide()
        self.layout.addWidget(self.log_area)

        self._installing = False
        self._model_items = {}
        QTimer.singleShot(200, self._run_checks)

    def log(self, msg):
        self.log_area.show()
        self.log_area.append(msg)

    def _set_item_status(self, name, ok: bool, text: str = None):
        _, status, btn, _ = self.items[name]
        if ok:
            status.setText(text or "✅ 就绪")
            status.setStyleSheet("font-size: 13px; color: #7dffb3;")
            btn.hide()
        else:
            status.setText(text or "❌ 缺失")
            status.setStyleSheet("font-size: 13px; color: #ff6b6b;")
            btn.show()

    def _run_checks(self):
        self.log("开始检测环境…")

        # 1. Python 版本
        ver = sys.version_info
        ok = ver.major == 3 and ver.minor >= 10
        self._set_item_status("Python 3.10+", ok,
                              f"{'✅' if ok else '⚠️'} {ver.major}.{ver.minor}.{ver.micro}")
        self.log(f"Python: {sys.version}")
        self.total_bar.setValue(10)

        # 2. pip
        ok = check_installed("pip")
        self._set_item_status("pip", ok)
        self.log(f"pip: {'就绪' if ok else '缺失'}")
        self.total_bar.setValue(20)

        # 3. PyQt5
        ok = check_installed("PyQt5")
        self._set_item_status("PyQt5", ok)
        self.log(f"PyQt5: {'就绪' if ok else '缺失'}")
        self.total_bar.setValue(30)

        # 4. pywin32
        ok = check_installed("pywin32")
        self._set_item_status("pywin32", ok)
        self.total_bar.setValue(40)

        # 5-6. Live2D (optional)
        l2d_ok = check_installed("live2d")
        self._set_item_status("live2d-py", l2d_ok)
        gl_ok = check_installed("PyOpenGL")
        self._set_item_status("PyOpenGL", gl_ok)
        self.total_bar.setValue(60)

        # 7. Ollama
        ollama_ok = check_ollama_installed()
        running, models = check_ollama_running()
        if running:
            model_list = ", ".join(models[:3])
            self._set_item_status("Ollama", True, f"✅ 运行中 ({model_list})")
        elif ollama_ok:
            self._set_item_status("Ollama", True, "✅ 已安装，未运行")
        else:
            self._set_item_status("Ollama", False, "❌ 未安装")
        self.total_bar.setValue(80)

        # 8-9. Ollama 模型（仅在 Ollama 运行中时检测）
        self._model_items = {}
        if running:
            self._check_ollama_models(models)

        # 总结
        self.total_bar.setValue(100)
        self.total_status.setText("✅ 检测完成，缺失的项目可以点「安装」补上")
        self.total_status.setStyleSheet(f"font-size: 12px; color: {COLOR_OK};")
        self.log("环境检测完成")

    def _add_row(self, name: str, hint: str) -> tuple:
        """动态添加一行检测项"""
        row = QHBoxLayout()
        name_label = QLabel(name)
        name_label.setStyleSheet("font-size: 14px; min-width: 120px;")
        row.addWidget(name_label)
        hint_label = QLabel(hint)
        hint_label.setStyleSheet("font-size: 11px; color: #666;")
        row.addWidget(hint_label)
        row.addStretch()
        status = QLabel("检测中…")
        status.setStyleSheet("font-size: 13px; color: #888;")
        row.addWidget(status)
        btn = QPushButton("拉取")
        btn.setStyleSheet("""
            QPushButton { background: rgba(255,255,255,15); color: #F0F0F0;
                border: 1px solid rgba(255,255,255,30); border-radius: 6px;
                padding: 4px 14px; font-size: 12px; }
            QPushButton:hover { background: rgba(255,255,255,25); }
            QPushButton:disabled { background: transparent; color: #555;
                border: 1px solid transparent; }
        """)
        btn.setFixedWidth(60)
        btn.hide()
        row.addWidget(btn)
        # 追加到布局末尾（spacer 和进度条之前）
        insert_pos = self.layout.count() - 3  # 在 stretch 之前
        self.layout.insertLayout(insert_pos, row)
        self.items[name] = (name_label, status, btn, hint)
        return name_label, status, btn

    def _check_ollama_models(self, existing_models: list):
        """检测 Ollama 模型是否就绪"""
        needed = [
            ("qwen2.5:7b", "对话模型（约 4GB）", "对话用"),
            ("minicpm-v", "视觉模型（约 5.5GB）", "屏幕识图用"),
        ]
        has_qwen = any("qwen2.5" in m for m in existing_models)
        has_vision = any("minicpm" in m or "llava" in m or "vl" in m for m in existing_models)

        for model_name, hint, purpose in needed:
            is_chat = "qwen" in model_name
            ok = has_qwen if is_chat else has_vision
            _, status, btn = self._add_row(f"  📦 {model_name}", hint)
            if ok:
                status.setText("✅ 就绪")
                status.setStyleSheet("font-size: 13px; color: #7dffb3;")
                btn.hide()
            else:
                status.setText(f"❌ 未拉取（{purpose}）")
                status.setStyleSheet("font-size: 13px; color: #ff6b6b;")
                btn.show()
                btn.clicked.connect(lambda checked, m=model_name, s=status: self._pull_model(m, s))
            self._model_items[model_name] = (status, btn)

    def _set_installing(self, busy: bool):
        """安装中禁用/启用所有按钮"""
        self._installing = busy
        for name in self.items:
            _, _, btn, _ = self.items[name]
            btn.setEnabled(not busy)
        # 也禁用模型拉取按钮
        for mn in self._model_items:
            _, btn = self._model_items[mn]
            btn.setEnabled(not busy)
        self.total_bar.setVisible(busy)
        if busy:
            self.total_bar.setRange(0, 100)  # 重置可能被脉冲模式改过的范围
            self.total_bar.setValue(0)

    def install_package(self, name: str):
        """安装指定的包（后台线程）"""
        self._set_installing(True)
        pkg_map = {
            "PyQt5": ["PyQt5"],
            "pywin32": ["pywin32"],
            "live2d-py": ["live2d-py"],
            "PyOpenGL": ["PyOpenGL", "PyOpenGL-accelerate"],
        }
        packages = pkg_map.get(name, [name])
        self.log(f"📦 安装 {name} 中…")

        def task():
            ok = pip_install(packages)
            QTimer.singleShot(0, lambda: self._install_done(name, ok))

        threading.Thread(target=task, daemon=True).start()

    def install_ollama(self):
        """下载并安装 Ollama（后台线程）"""
        self._set_installing(True)
        self.total_bar.setValue(0)
        self.total_bar.setVisible(True)
        self.log("📦 正在下载 Ollama（约 300MB）…")

        def dl_progress(pct):
            QTimer.singleShot(0, lambda: self._set_dl_progress(pct))

        def task():
            dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "OllamaSetup.exe")
            ok = download_file(
                "https://ollama.com/download/OllamaSetup.exe",
                dest, dl_progress
            )
            if ok:
                self.log("下载完成，正在安装…")
                try:
                    subprocess.run([dest, "/S"], timeout=120)
                    QTimer.singleShot(0, lambda: self._set_item_status("Ollama", True, "✅ 已安装"))
                    self.log("✅ Ollama 安装成功！重启后生效")
                    self._install_done("Ollama", True)
                    return
                except Exception as e:
                    self.log(f"❌ 安装失败：{e}")
            else:
                self.log("❌ 下载失败，请手动从 ollama.com 下载安装")
            self._install_done("Ollama", False)

        threading.Thread(target=task, daemon=True).start()

    def _set_dl_progress(self, pct: int):
        """下载进度更新（主线程回调），支持无 Content-Length 的脉冲模式"""
        if pct < 0:
            # 无 Content-Length → 脉冲样式
            self.total_bar.setRange(0, 0)  # 不确定范围 → 自动脉冲动画
        else:
            self.total_bar.setRange(0, 100)
            self.total_bar.setValue(pct)

    def _install_done(self, name: str, ok: bool):
        """安装完成后恢复界面"""
        self._set_installing(False)
        if ok:
            self._set_item_status(name, True)
            self.total_status.setText(f"✅ {name} 安装成功")
        else:
            self.total_status.setText(f"❌ {name} 安装失败")
        self.total_bar.setVisible(False)

    def _pull_model(self, model: str, status_label):
        """拉取 Ollama 模型（后台线程，带进度）"""
        self._set_installing(True)
        self.total_bar.setValue(0)
        self.total_bar.setVisible(True)
        self.log(f"📦 正在拉取 {model}（这可能需要很久，取决于你的网速）…")

        def task():
            def on_log(line: str):
                QTimer.singleShot(0, lambda: self.log(f"  {line}"))
                # 尝试解析进度百分比
                import re
                m = re.search(r'(\d+)%', line)
                if m:
                    pct = int(m.group(1))
                    QTimer.singleShot(0, lambda: self.total_bar.setValue(pct))

            ok = pull_ollama_model(model, log_callback=on_log)
            QTimer.singleShot(0, lambda: self._pull_done(model, ok, status_label))

        threading.Thread(target=task, daemon=True).start()

    def _pull_done(self, model: str, ok: bool, status_label):
        """模型拉取完成"""
        self._set_installing(False)
        self.total_bar.setVisible(False)
        if ok:
            status_label.setText("✅ 就绪")
            status_label.setStyleSheet("font-size: 13px; color: #7dffb3;")
            self.log(f"✅ {model} 拉取完成")
            self.total_status.setText(f"✅ {model} 就绪")
        else:
            self.log(f"❌ {model} 拉取失败，稍后可以手动运行: ollama pull {model}")
            self.total_status.setText(f"❌ {model} 拉取失败")


# ═══════════════════════════════════════
# 页面：LLM 选择
# ═══════════════════════════════════════

class LLMPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"""
            QFrame#card {{
                background: {COLOR_CARD};
                border: 1px solid rgba(255,255,255,20);
                border-radius: 12px;
                padding: 20px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        title = QLabel("🧠 第 1 步：选 AI 大脑")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #FFB6C1;")
        layout.addWidget(title)
        desc = QLabel("桌宠要靠一个 AI 来对话，选一个你有的：")
        desc.setStyleSheet("font-size: 13px; color: #bbb;")
        layout.addWidget(desc)

        # Ollama
        self.radio_ollama = QRadioButton("Ollama（推荐 🎯 免费、本地运行）")
        self.radio_ollama.setStyleSheet("""
            QRadioButton { font-size: 15px; padding: 8px; spacing: 10px; }
            QRadioButton::indicator {
                width: 20px; height: 20px; border-radius: 10px;
                border: 2px solid #555;
            }
            QRadioButton::indicator:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff6b9d, stop:1 #ff9a56);
                border: 2px solid #FFB6C1;
            }
        """)
        self.radio_ollama.setChecked(True)
        layout.addWidget(self.radio_ollama)
        layout.addWidget(QLabel(
            "    • 完全免费，不需要 API Key\n"
            "    • 需要先装 Ollama 并下载模型\n"
            "    • 对话推荐模型：qwen2.5:7b\n"
            "    • 识图推荐模型：minicpm-v（桌宠会偷看你屏幕）",
            styleSheet="font-size: 12px; color: #888; padding-left: 35px;"
        ))

        # DeepSeek
        self.radio_ds = QRadioButton("DeepSeek API（在线、速度快）")
        self.radio_ds.setStyleSheet(self.radio_ollama.styleSheet())
        layout.addWidget(self.radio_ds)
        layout.addWidget(QLabel(
            "    • 需要注册 DeepSeek 获取 API Key\n"
            "    • 按量付费，不需要本地显卡\n"
            "    • 注：屏幕识图仍需要 Ollama（装 minicpm-v 即可）",
            styleSheet="font-size: 12px; color: #888; padding-left: 35px;"
        ))

        # Ollama 状态
        self.ollama_status = QLabel("")
        self.ollama_status.setStyleSheet("font-size: 12px; margin-top: 5px;")
        layout.addWidget(self.ollama_status)

        layout.addStretch()
        QTimer.singleShot(100, self._refresh_ollama_status)

    def _refresh_ollama_status(self):
        running, models = check_ollama_running()
        installed = check_ollama_installed()
        if running:
            m = ", ".join(models[:3])
            self.ollama_status.setText(f"✅ Ollama 运行中（模型：{m}）")
            self.ollama_status.setStyleSheet(f"font-size: 12px; color: {COLOR_OK};")
        elif installed:
            self.ollama_status.setText("ℹ️ Ollama 已安装但未运行，启动后再继续")
            self.ollama_status.setStyleSheet(f"font-size: 12px; color: {COLOR_WARN};")
        else:
            self.ollama_status.setText("ℹ️ 还没装 Ollama？可以先选 DeepSeek，或回头再装")
            self.ollama_status.setStyleSheet(f"font-size: 12px; color: #888;")

    def get_backend(self):
        if self.radio_ollama.isChecked():
            return "ollama"
        return "deepseek"


# ═══════════════════════════════════════
# 页面：API Key
# ═══════════════════════════════════════

class ApiKeyPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"""
            QFrame#card {{
                background: {COLOR_CARD};
                border: 1px solid rgba(255,255,255,20);
                border-radius: 12px;
                padding: 20px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        title = QLabel("🔑 第 2 步：API Key")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #FFB6C1;")
        layout.addWidget(title)
        layout.addWidget(QLabel(
            "在 platform.deepseek.com 注册获取API Key，要先充点额度，不过能用很久。",
            styleSheet="font-size: 13px; color: #bbb;"
        ))

        layout.addWidget(QLabel("DeepSeek API Key：", styleSheet="font-size: 14px; margin-top: 5px;"))
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self.key_input.setStyleSheet(STYLE_INPUT)
        self.key_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.key_input)

        show_btn = QPushButton("👁 显示")
        show_btn.setStyleSheet(STYLE_BTN_SECONDARY)
        show_btn.setFixedWidth(100)
        show_btn.clicked.connect(lambda: self.key_input.setEchoMode(
            QLineEdit.Normal if self.key_input.echoMode() == QLineEdit.Password else QLineEdit.Password
        ))
        layout.addWidget(show_btn)

        layout.addWidget(QLabel("API 地址（可选）：", styleSheet="font-size: 14px; margin-top: 5px;"))
        self.api_base = QLineEdit("https://api.deepseek.com/v1")
        self.api_base.setStyleSheet(STYLE_INPUT)
        layout.addWidget(self.api_base)

        layout.addStretch()


# ═══════════════════════════════════════
# 页面：TTS 设置
# ═══════════════════════════════════════

class TTSPage(QFrame):
    """语音设置页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"""
            QFrame#card {{
                background: {COLOR_CARD};
                border: 1px solid rgba(255,255,255,20);
                border-radius: 12px;
                padding: 20px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        title = QLabel("🎤 第 3 步：语音设置")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #FFB6C1;")
        layout.addWidget(title)

        self.enable_cb = QCheckBox("启用语音（梅尔会说话）")
        self.enable_cb.setStyleSheet("""
            QCheckBox { font-size: 15px; spacing: 10px; }
            QCheckBox::indicator {
                width: 22px; height: 22px; border-radius: 4px;
                border: 2px solid #555;
            }
            QCheckBox::indicator:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff6b9d, stop:1 #ff9a56);
                border: 2px solid #FFB6C1;
            }
        """)
        self.enable_cb.setChecked(True)
        self.enable_cb.toggled.connect(self._toggle)
        layout.addWidget(self.enable_cb)

        # GPT-SoVITS 状态 + 安装指南
        self.gsv_status = QLabel("")
        self.gsv_status.setStyleSheet("font-size: 12px; padding-left: 30px;")
        layout.addWidget(self.gsv_status)

        guide_btn = QPushButton("❓ 语音功能需要额外装一个东西，点我查看")
        guide_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,10);
                color: #aaa;
                border: 1px dashed rgba(255,255,255,30);
                border-radius: 8px;
                padding: 10px 16px;
                font-size: 12px;
                text-align: left;
            }
            QPushButton:hover {
                background: rgba(255,255,255,18);
                color: #FFB6C1;
                border: 1px dashed rgba(255,182,193,50);
            }
        """)
        guide_btn.clicked.connect(self._show_gsv_guide)
        layout.addWidget(guide_btn)

        # GPT-SoVITS 整合包目录选择
        gsv_label = QLabel("选整合包解压后的文件夹（会自动识别 python.exe）：")
        gsv_label.setStyleSheet("font-size: 12px; color: #888; margin-top: 8px;")
        layout.addWidget(gsv_label)

        path_row = QHBoxLayout()
        self.gsv_dir_input = QLineEdit()
        self.gsv_dir_input.setPlaceholderText("点「浏览」选整合包解压后的文件夹")
        self.gsv_dir_input.setStyleSheet(STYLE_INPUT)
        path_row.addWidget(self.gsv_dir_input)

        browse_btn = QPushButton("📂 选文件夹")
        browse_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,15); color: #F0F0F0;
                border: 1px solid rgba(255,255,255,30);
                border-radius: 8px; padding: 10px 16px; font-size: 13px;
            }
            QPushButton:hover { background: rgba(255,255,255,25); }
        """)
        browse_btn.setFixedWidth(100)
        browse_btn.clicked.connect(self._browse_gsv_dir)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        QTimer.singleShot(300, self._check_gsv)

        layout.addWidget(QLabel(
            "语音模型已打包，开箱即用。",
            styleSheet="font-size: 12px; color: #888; padding-left: 30px;"
        ))

        # 翻译提示（中文 → 日语翻译，使用阿里巴巴免费翻译 API，无需 Key）
        self.translate_frame = QFrame()
        tf = QVBoxLayout(self.translate_frame)
        tf.setContentsMargins(0, 5, 0, 0)
        tf.addWidget(QLabel(
            "📝 中文会自动翻译成日语再合成（先用免费翻译 API，加 Key 可备用 DeepSeek）",
            styleSheet="font-size: 12px; color: #aaa;"
        ))
        self.translate_key = QLineEdit()
        self.translate_key.setPlaceholderText("可选：DeepSeek API Key（免费翻译失效时备用）")
        self.translate_key.setStyleSheet(STYLE_INPUT)
        self.translate_key.setEchoMode(QLineEdit.Password)
        tf.addWidget(self.translate_key)
        layout.addWidget(self.translate_frame)

        layout.addStretch()
        self._tl_widgets = []


        QTimer.singleShot(500, self._check_gsv)

    def _browse_gsv_dir(self):
        """浏览选择整合包解压后的文件夹"""
        folder = QFileDialog.getExistingDirectory(
            self, "选整合包解压后的文件夹"
        )
        if folder:
            self.gsv_dir_input.setText(folder)
            self._check_gsv()

    def _find_python_exe(self, base_dir):
        """在整合包目录中查找 runtime\python.exe"""
        candidate = os.path.join(base_dir, "runtime", "python.exe")
        if os.path.isfile(candidate):
            return candidate
        # 也可能在 runtime 的下级
        for root, dirs, files in os.walk(base_dir):
            if "python.exe" in files and os.path.basename(root) == "runtime":
                return os.path.join(root, "python.exe")
        return None

    def _check_gsv(self):
        """检测 GPT-SoVITS 整合包环境状态"""
        saved = self.gsv_dir_input.text().strip()
        gsv_python = os.environ.get("GSV_PYTHON", "")

        def _has_gsv_module(py_path):
            if not py_path or not os.path.isfile(py_path):
                return False
            try:
                r = subprocess.run(
                    [py_path, "-c", "import GPT_SoVITS; print('ok')"],
                    capture_output=True, text=True, timeout=5
                )
                return r.returncode == 0 and 'ok' in r.stdout
            except Exception:
                return False

        # 如果输入的是文件夹，自动找 runtime\python.exe
        py_path = saved
        if saved and os.path.isdir(saved):
            py_path = self._find_python_exe(saved)
            if py_path:
                self.gsv_dir_input.setText(py_path)

        if py_path and os.path.isfile(py_path) and py_path.endswith("python.exe"):
            if _has_gsv_module(py_path):
                self.gsv_status.setText("✅ GPT-SoVITS 环境就绪，语音可用！")
                self.gsv_status.setStyleSheet("font-size: 12px; color: #7dffb3; padding-left: 30px;")
            else:
                self.gsv_status.setText("⚠️ 找到 python.exe 但缺少 GPT_SoVITS 模块，确认是官方整合包吗？")
                self.gsv_status.setStyleSheet("font-size: 12px; color: #ffd700; padding-left: 30px;")
        elif gsv_python and os.path.isfile(gsv_python):
            if _has_gsv_module(gsv_python):
                self.gsv_status.setText("✅ 已配置（GSV_PYTHON），语音可用")
                self.gsv_status.setStyleSheet("font-size: 12px; color: #7dffb3; padding-left: 30px;")
            else:
                self.gsv_status.setText("⚠️ GSV_PYTHON 指定了但缺少 GPT_SoVITS 模块")
                self.gsv_status.setStyleSheet("font-size: 12px; color: #ffd700; padding-left: 30px;")
        else:
            self.gsv_status.setText("⚠️ 还没装语音，但不开语音也能玩")
            self.gsv_status.setStyleSheet("font-size: 12px; color: #888; padding-left: 30px;")

    def _show_gsv_guide(self):
        """大白话安装指南（GPT-SoVITS 整合包版）"""
        guide = (
            "<h3>🎤 梅尔说话需要它</h3>"
            "<p>语音功能需要装 <b>GPT-SoVITS</b> 官方整合包。</p>"
            "<hr>"
            "<h4>👇 跟着这几步做：</h4>"
            "<p><b>1. 下整合包</b><br>"
            "去 <a href='https://www.yuque.com/baicai-1145/gpt-sovits/glvg99syb6q9mvtq'>GPT-SoVITS 发布页</a><br>"
            "下载最新版整合包（.7z 压缩包）</p>"
            "<p><b>2. 解压</b><br>"
            "解压到你喜欢的位置，比如：<br>"
            "<code>D:\GPT-SoVITS-v2pro-20250604\</code></p>"
            "<p><b>3. 回到向导</b><br>"
            "点「浏览」，选中解压后的整合包文件夹：<br>"
            "<code>D:\GPT-SoVITS-v2pro-20250604</code><br><br>"
            "向导会自动找到 runtime\python.exe，之后就能用了。</p>"
            "<hr>"
            "<h4>💡 不开语音也能玩</h4>"
            "<p>不装语音完全不影响桌宠的其他功能。<br>"
            "以后想加声音了，随时回来装就行。</p>"
            "<hr>"
            "<p style='color:#888; font-size:12px;'>"
            "模型文件已打包在项目里，不需要额外下载。</p>"
        )
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("语音功能怎么装")
        dialog.setMinimumSize(520, 520)
        dialog.setStyleSheet("background: #1a1a2e; color: #F0F0F0;")
        dl = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml(guide)
        text.setStyleSheet("""
            QTextEdit {
                background: rgba(30,30,60,200); color: #F0F0F0;
                border: 1px solid rgba(255,255,255,15);
                border-radius: 8px; padding: 16px; font-size: 13px;
            }
            QTextEdit a { color: #FFB6C1; }
            QTextEdit code {
                background: rgba(0,0,0,80); color: #7dffb3;
                padding: 2px 6px; border-radius: 3px;
                font-family: Consolas, monospace;
            }
        """)
        dl.addWidget(text)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        c = QPushButton("明白了")
        c.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff6b9d, stop:1 #ff9a56);
                color: white; border: none; border-radius: 8px;
                padding: 10px 30px; font-size: 14px; font-weight: bold;
            }
        """)
        c.clicked.connect(dialog.accept)
        btn_row.addWidget(c)
        dl.addLayout(btn_row)
        dialog.exec_()

    # ─── 跨线程信号槽（在主线程执行） ───

    def _toggle(self, on):
        self.translate_frame.setVisible(on and self.enable_cb.isChecked())


# ═══════════════════════════════════════
# 页面：确认 + 保存
# ═══════════════════════════════════════

class SummaryPage(QFrame):
    def __init__(self, wizard, parent=None):
        super().__init__(parent)
        self.wizard = wizard
        self.setObjectName("card")
        self.setStyleSheet(f"""
            QFrame#card {{
                background: {COLOR_CARD};
                border: 1px solid rgba(255,255,255,20);
                border-radius: 12px;
                padding: 20px;
            }}
        """)
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(12)

        title = QLabel("📋 确认设置")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #FFB6C1;")
        self.layout.addWidget(title)

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setStyleSheet("""
            QTextEdit {
                background: rgba(0,0,0,80); color: #ddd;
                border: 1px solid rgba(255,255,255,15);
                border-radius: 8px; padding: 16px; font-size: 13px;
            }
        """)
        self.summary.setMinimumHeight(200)
        self.layout.addWidget(self.summary)
        self.layout.addStretch()

    def refresh(self):
        cfg = self.wizard.collect_config()
        lines = []
        b = cfg["llm"]["backend"]
        if b == "ollama":
            lines.append("🧠 AI 大脑：Ollama（本地免费）")
        elif b == "deepseek":
            k = cfg["llm"].get("api_key", "")
            lines.append(f"🧠 AI 大脑：DeepSeek API")
            lines.append(f"🔑 Key：{k[:8]}…{k[-4:]}" if len(k) > 12 else "⚠️ Key 未设置")

        t = cfg["tts"]
        if t["enabled"]:
            lines.append("🎤 语音：开启（🇯🇵 日语，免费翻译）")
        else:
            lines.append("🎤 语音：关闭")

        lines.append("")
        lines.append("📁 模型：./models/")
        lines.append("🖼️  立绘：./sprites/")
        lines.append("🐱 Live2D：./live2d/model/mea_live2d/")
        # 识图提醒
        if b != "ollama":
            lines.append("")
            lines.append("👀 屏幕识图需要 Ollama + minicpm-v 模型")
            lines.append("   如果没装，桌宠的偷看功能不会工作")
        self.summary.setText("\n".join(lines))


# ═══════════════════════════════════════
# 主向导窗口
# ═══════════════════════════════════════

class SetupWizard(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MeaPet 配置向导")
        self.setFixedSize(620, 680)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)

        self.setStyleSheet(f"""
            QWidget {{
                font-family: "Microsoft YaHei", "SimHei", sans-serif;
                color: {COLOR_TEXT};
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        container = QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background: {COLOR_BG};
                border: 1px solid rgba(255,255,255,25);
                border-radius: 16px;
            }}
        """)
        main = QVBoxLayout(container)
        main.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

        # 顶栏
        top = QHBoxLayout()
        top.setContentsMargins(20, 15, 20, 0)
        self.step_label = QLabel("环境检测")
        self.step_label.setStyleSheet("font-size: 12px; color: #888;")
        top.addWidget(self.step_label)
        top.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setStyleSheet("""
            QPushButton { background: rgba(255,255,255,10); color: #888;
                border: none; border-radius: 16px; font-size: 16px; }
            QPushButton:hover { background: rgba(255,80,80,150); color: white; }
        """)
        close_btn.clicked.connect(self.close)
        top.addWidget(close_btn)
        main.addLayout(top)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 4)
        self.progress.setValue(0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("""
            QProgressBar {
                background: rgba(255,255,255,15); border: none;
                border-radius: 2px; margin: 0 20px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff6b9d, stop:1 #ff9a56);
                border-radius: 2px;
            }
        """)
        main.addWidget(self.progress)

        # 页面
        from PyQt5.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        main.addWidget(scroll, 1)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")
        scroll.setWidget(self.stack)

        self.env_page = EnvCheckPage()
        self.llm_page = LLMPage()
        self.key_page = ApiKeyPage()
        self.tts_page = TTSPage()
        self.summary_page = SummaryPage(self)

        self.stack.addWidget(self.env_page)      # 0
        self.stack.addWidget(self.llm_page)       # 1
        self.stack.addWidget(self.key_page)       # 2
        self.stack.addWidget(self.tts_page)       # 3
        self.stack.addWidget(self.summary_page)   # 4

        # 底部按钮
        btns = QHBoxLayout()
        btns.setContentsMargins(20, 10, 20, 20)
        self.back_btn = QPushButton("← 上一步")
        self.back_btn.setStyleSheet(STYLE_BTN_SECONDARY)
        self.back_btn.clicked.connect(self._back)
        self.back_btn.setEnabled(False)
        btns.addWidget(self.back_btn)
        btns.addStretch()
        self.next_btn = QPushButton("下一步 →")
        self.next_btn.setStyleSheet(STYLE_BTN_PRIMARY)
        self.next_btn.clicked.connect(self._next)
        btns.addWidget(self.next_btn)
        main.addLayout(btns)

        # 窗口拖拽
        self._drag = None
        for w in [container, self.step_label]:
            w.mousePressEvent = lambda e: self._drag_start(e)
            w.mouseMoveEvent = lambda e: self._drag_move(e)
            w.mouseReleaseEvent = lambda e: setattr(self, '_drag', None)

        self._page = 0
        self._update()

    def _drag_start(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos()

    def _drag_move(self, e):
        if self._drag:
            self.move(self.pos() + e.globalPos() - self._drag)
            self._drag = e.globalPos()

    def _update(self):
        p = self._page
        self.progress.setValue(p)
        self.back_btn.setEnabled(p > 0)
        names = ["环境检测", "AI 大脑", "API Key", "语音设置", "确认"]
        self.step_label.setText(f"第 {p+1}/5 步  —  {names[p]}" if p < 5 else "完成")
        if p == 0:
            self.next_btn.setText("跳过 →")
        elif p == 4:
            self.next_btn.setText("✅ 保存配置")
        else:
            self.next_btn.setText("下一步 →")

    def _back(self):
        p = self._page
        if p == 0:
            return
        if p == 1:
            self._page = 0
        elif p == 2:
            self._page = 1
        elif p == 3:
            b = self.llm_page.get_backend()
            self._page = 2 if b == "deepseek" else 1
        elif p == 4:
            b = self.llm_page.get_backend()
            if b == "deepseek":
                self._page = 3
            else:
                self._page = 3  # TTS page
        self.stack.setCurrentIndex(self._page)
        self._update()

    def _next(self):
        p = self._page

        # 环境页 → 下一步
        if p == 0:
            self._page = 1
            self.stack.setCurrentIndex(1)
            self._update()
            return

        # LLM 页
        if p == 1:
            b = self.llm_page.get_backend()
            if b == "deepseek":
                self._page = 2
            else:
                self._page = 3
            self.stack.setCurrentIndex(self._page)
            self._update()
            return

        # API Key 页
        if p == 2:
            self._page = 3
            self.stack.setCurrentIndex(3)
            self._update()
            return

        # TTS 页 → 确认
        if p == 3:
            self.summary_page.refresh()
            self._page = 4
            self.stack.setCurrentIndex(4)
            self._update()
            return

        # 确认 → 保存
        if p == 4:
            self._save()

    def collect_config(self):
        config = {
            "llm": {"backend": self.llm_page.get_backend(), "temperature": 0.7},
            "vision": {"model": "minicpm-v"},
            "tts": {
                "engine": "gpt_sovits",
                "enabled": self.tts_page.enable_cb.isChecked(),
                "gpt_weights_dir": "./models/GPT_weights",
                "sovits_weights_dir": "./models/SoVITS_weights",
                "gpt_model": "mea_pro-e50.ckpt",
                "sovits_model": "mea_pro_e24_s13704.pth",
                "ref_dir": "./GPT-Sovits",
                "top_k": 15, "top_p": 0.8,
                "temperature": 0.6, "speed": 1.0,
                "translate_to_jp": True,
                "voice_lang": "jp",
                "translate_api_key": "",
                "translate_model": "deepseek-chat",
            },
            "display": {"scale": 0.5, "fps": 30},
            "character": {"name": "梅尔", "default_outfit": "01", "default_direction": "A"},
            "sprite_dir": "./sprites",
            "live2d": {
                "model_dir": "./live2d/model/mea_live2d",
                "enabled": True, "scale": 0.15
            }
        }

        b = self.llm_page.get_backend()
        if b == "ollama":
            config["llm"]["host"] = "http://127.0.0.1:11434"
            config["llm"]["model"] = "qwen2.5:7b"
            config["llm"]["api_key"] = ""
            config["llm"]["api_base"] = ""
            config["llm"]["bridge_url"] = ""
        elif b == "deepseek":
            config["llm"]["api_key"] = self.key_page.key_input.text().strip()
            config["llm"]["api_base"] = self.key_page.api_base.text().strip()
            config["llm"]["model"] = "deepseek-chat"

        # 翻译备用 Key（免费翻译 API 全失效时走 DeepSeek）
        if self.tts_page.enable_cb.isChecked():
            tk = self.tts_page.translate_key.text().strip()
            if tk:
                config["tts"]["translate_api_key"] = tk

        # GPT-SoVITS Python 路径
        gsv_path = self.tts_page.gsv_dir_input.text().strip()
        if gsv_path:
            config["tts"]["python_exe"] = gsv_path

        return config

    def _save(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.collect_config(), f, ensure_ascii=False, indent=2)
            QMessageBox.information(
                self, "✅ 完成",
                "配置已保存！\n\n"
                "现在双击「启动桌宠.bat」就能开玩啦 🐱\n\n"
                "提示：随时可以重新运行本向导修改配置。"
            )
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "❌ 保存失败", str(e))


# ═══════════════════════════════════════
# 入口
# ═══════════════════════════════════════

def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor("#1a1a2e"))
    p.setColor(QPalette.Base, QColor("#1a1a2e"))
    p.setColor(QPalette.Text, QColor("#F0F0F0"))
    app.setPalette(p)

    w = SetupWizard()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
