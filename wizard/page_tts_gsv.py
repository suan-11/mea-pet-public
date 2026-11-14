"""TTS 配置页 mixin"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from typing import Optional

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt5.QtGui import *

from wizard.styles import (
    STYLE_INPUT, STYLE_BTN_PRIMARY, STYLE_BTN_SECONDARY,
    COLOR_BG, COLOR_CARD, COLOR_ACCENT, COLOR_TEXT, COLOR_OK, COLOR_WARN, COLOR_ERR,
    MIN_TARGET_SIZE, apply_wizard_dialog_style, set_status,
    styled_open_directory, styled_open_file,
)
from wizard.platform_info import PLATFORM, CONFIG_PATH
from wizard.env_utils import pip_install, check_installed
from meapet.tts.common import prefix_python

# 探针跑的是 ``import GPT_SoVITS`` 这一句——包体 __init__ 很轻，本机实测 0.23 s；
# 超时只是"这台机器的解释器不是整合包那套"时的兜底，不是给正常路径留的余量。
GSV_PROBE_TIMEOUT_S = 5


def find_pack_python(base_dir: str) -> Optional[str]:
    """在整合包目录里找 runtime 下的解释器（Windows 是 python.exe，POSIX 是 bin/python）。"""
    candidate = prefix_python(os.path.join(base_dir, "runtime"))
    if os.path.isfile(candidate):
        return candidate
    # 也可能在 runtime 的下级
    for root, _dirs, _files in os.walk(base_dir):
        if os.path.basename(root).lower() != "runtime":
            continue
        nested = prefix_python(root)
        if os.path.isfile(nested):
            return nested
    return None


def gsv_root_for(py_path: str) -> str:
    """这解释器旁边那棵整合包树在哪；找不到返回空串。

    判据与子进程同一个（按 ``GPT_SoVITS/TTS_infer_pack`` 标记上溯），不在向导里
    另立一份"根在哪"的说法——否则页面和交付码会各自报自己的道理。
    """
    try:
        from meapet.tools.gsv_infer import find_gsv_root

        return find_gsv_root("", py_path) or ""
    except Exception:
        return ""


def _gsv_probe(py_path: str, root: str) -> bool:
    """这个解释器能不能真的 ``import GPT_SoVITS``。"""
    if not py_path or not os.path.isfile(py_path):
        return False
    # GPT_SoVITS 是仓库里的目录而非已安装的包：探针必须站在根上跑，
    # 否则一整套依赖齐全的整合包也会被报成「缺模块」（本机实测：站外面必失败）。
    try:
        r = subprocess.run(
            [py_path, "-c", "import GPT_SoVITS; print('ok')"],
            capture_output=True,
            text=True,
            timeout=GSV_PROBE_TIMEOUT_S,
            cwd=root or None,
        )
        return r.returncode == 0 and "ok" in r.stdout
    except Exception:
        return False


def resolve_gsv_python(
    typed: str,
    *,
    environ: Optional[dict] = None,
    find_in_dir=find_pack_python,
) -> tuple[str, str, str, str]:
    """返回 ``(解释器, 它是哪来的, 整合包根, 被丢掉的输入)``；没戏时四个空串。

    顺序照交付码 ``MeaTTS.__init__`` 那一串：配置/输入框 → ``MEA_PET_GSV_PYTHON``
    → 候选表自动检测。向导从前只认输入框那一格，于是 ``python_exe`` 留空、由候选表
    找到整合包的机器（Linux 的常态）打开页面看到的是「尚未配置语音」，而桌宠在另一头
    照样出声——2026-10-11 本机实测候选表命中家目录下那层带发布日期的解压子目录里的
    ``runtime/bin/python``。

    第四格讲交付码**不**讲的那件事：它解析不出配置那一格就静默改用候选表。页面得把
    "你填的那条没被采纳"说出来，否则填错的人看见的永远是一个绿灯。
    """
    from meapet.tts.common import resolve_external_python

    env = dict(os.environ if environ is None else environ)
    field = (typed or "").strip()
    dropped = ""
    if field:
        if os.path.isdir(field):
            found = find_in_dir(field)
            if found:
                return found, "选的文件夹", gsv_root_for(found), ""
            dropped = f"你选的文件夹里没有 runtime 解释器（{field}）"
        else:
            picked = resolve_external_python(field)
            if picked:
                return picked, "填的解释器", gsv_root_for(picked), ""
            dropped = f"输入框里的不是可用解释器（{field}）"

    env_py = resolve_external_python(env.get("MEA_PET_GSV_PYTHON", ""))
    if env_py:
        return env_py, "环境变量 MEA_PET_GSV_PYTHON", gsv_root_for(env_py), dropped

    # 候选表与排序都在交付码那一侧，向导不复制一份：复制的那份永远比原件窄。
    from meapet.tts.service import gsv_python_choices, select_gsv_python

    picked, layout_root = select_gsv_python(gsv_python_choices())
    if picked:
        return (
            picked,
            "自动检测",
            gsv_root_for(picked) or layout_root,
            dropped,
        )
    return "", "", "", dropped


def gsv_dir_field(python_exe: str) -> str:
    r"""把 ``<整合包>/runtime[/bin]/python`` 反推成整合包目录，填进"选文件夹"那一格。

    从前只认 ``python.exe``，POSIX 的 ``runtime/bin/python`` 于是原样躺在输入框里——
    标签说的是文件夹、框里是解释器。反推不出的（conda env 那种装在源码树之外）照原样
    填解释器路径：两种写法保存那头都认。
    """
    path = (python_exe or "").strip()
    if not path or not os.path.basename(path).lower().startswith("python"):
        return path
    if not os.path.isfile(path):
        return path
    parent = os.path.dirname(path)
    if os.path.basename(parent).lower() == "runtime":
        return os.path.dirname(parent)
    grand = os.path.dirname(parent)
    if os.path.basename(parent).lower() == "bin" and os.path.basename(grand).lower() == "runtime":
        return os.path.dirname(grand)
    return path


class TtsPageGsvMixin:
    def _browse_gsv_dir(self):
        """浏览选择整合包解压后的文件夹"""
        folder = styled_open_directory(self, "选整合包解压后的文件夹")
        if folder:
            self.gsv_dir_input.setText(folder)
            self._check_gsv()

    def _browse_gsv_ref_wav(self, language: str = "jp"):
        """选择固定 GPT-SoVITS 参考音频。"""
        inputs = getattr(self, "gsv_reference_inputs", {})
        target = inputs.get(language)
        if target is None:
            target = getattr(self, "gsv_ref_wav_input", None)
        current = ""
        if target is not None:
            current = target.text().strip()
        if current and os.path.isfile(current):
            start_dir = os.path.dirname(current)
        else:
            start_dir = os.path.join(
                os.path.dirname(CONFIG_PATH),
                "GPT-Sovits",
                "normal",
            )
        path = styled_open_file(
            self,
            "选择 GPT-SoVITS 参考音频",
            start_dir,
            "WAV Audio (*.wav);;All (*.*)",
        )
        if path and target is not None:
            target.setText(path)

    def _find_python_exe(self, base_dir):
        r"""在整合包目录中查找 runtime 下的解释器（Windows 是 python.exe，POSIX 是 bin/python）"""
        return find_pack_python(base_dir)

    def _check_gsv(self):
        """检测 GPT-SoVITS 环境状态——判据与交付码同一套，不只是看输入框"""
        py_path, origin, root, dropped = resolve_gsv_python(self.gsv_dir_input.text())

        if not py_path:
            if dropped:
                set_status(
                    self.gsv_status,
                    "warning",
                    dropped + "，程序自己也没检测到可用的 GPT-SoVITS 环境",
                )
            else:
                set_status(
                    self.gsv_status,
                    "muted",
                    "尚未配置语音；关闭语音也可以正常使用",
                )
            return

        tail = f"；{dropped}" if dropped else ""
        if not _gsv_probe(py_path, root):
            if root:
                set_status(
                    self.gsv_status,
                    "warning",
                    f"找到解释器（{origin}：{py_path}）和整合包树 {root}，"
                    "但它 import GPT_SoVITS 失败——这一般是那个解释器里没装依赖，"
                    "不是路径写错了" + tail,
                )
            else:
                set_status(
                    self.gsv_status,
                    "warning",
                    f"找到解释器（{origin}：{py_path}），"
                    "但按 GPT_SoVITS/TTS_infer_pack 标记上溯不到整合包根——"
                    "请选整合包解压后的整个文件夹；解释器装在源码树之外时，"
                    "只能在 config.json 里写 tts.gsv_root" + tail,
                )
            return

        # 「留空也会走它」只在框真为空时才说——框里写着一条没被采纳的路径时，
        # 这句会变成"你填的也算留空"，把人往错的方向带。
        note = ""
        if origin == "自动检测" and not self.gsv_dir_input.text().strip():
            note = "，配置里留空也会走它"
        set_status(
            self.gsv_status,
            "success",
            f"GPT-SoVITS 环境就绪，语音可用（{origin}：{py_path}{note}）" + tail,
        )

    def _guide_steps(self) -> str:
        """安装说明正文里那句"回到向导怎么选"——按平台给不同的文件名。

        Windows 整合包自带 runtime\\python.exe；Linux 那套是 runtime/bin/python，
        而且官方没有 Linux 整合包，多数人自己建 venv/conda，所以只能说"选文件夹或
        直接填解释器"，并把手动配置的去处指到 README。
        """
        if PLATFORM["is_windows"]:
            return (
                "点「浏览」，选中解压后的整合包文件夹。<br>"
                r"向导会自动找到其中的 runtime\python.exe，之后就能用了。</p>"
            )
        return (
            "点「浏览」，选中整合包（或你自建环境）所在的文件夹；"
            "也可以直接把解释器路径填进输入框——Linux 上是 "
            "<code>runtime/bin/python</code> 或 "
            "<code>~/.conda/envs/&lt;名&gt;/bin/python</code>。<br>"
            "没有 Linux 整合包，环境与依赖要自己建，逐步命令见 README 的"
            "「Linux 手动配置本地语音」。</p>"
        )

    def _guide_footer(self) -> str:
        if PLATFORM["is_windows"]:
            exe_hint = r"runtime\python.exe（打包版 MeaPet.exe 不能当 Python 用）"
        else:
            exe_hint = (
                "runtime/bin/python，或你自己建好的 venv/conda 解释器"
                "（打包版那个可执行文件不能当 Python 用）"
            )
        return (
            "GSV 模型权重可随项目分发；推理必须使用外部 GPT-SoVITS 环境里的 "
            + exe_hint
            + "。若只想开箱说话，请改用 ONNX 轻量离线（约 30 MB 模型）或 MiMo 云端语音；"
            "角色原声也可用 VITS + 离线语音包。</p>"
        )

    def _show_gsv_guide(self):
        """大白话安装指南（GPT-SoVITS 整合包版）"""
        guide = (
            "<h3>🎤 GPT-SoVITS：角色原声 · 音质最好</h3>"
            "<p>这个引擎需要 <b>GPT-SoVITS</b> 官方整合包（或自建环境），"
            "支持音色克隆，推理较慢。<br>"
            "只想开箱出声？ONNX 轻量离线（约 30 MB 模型）或 MiMo 云端更省事。</p>"
            "<hr>"
            "<h4>👇 跟着这几步做：</h4>"
            "<p><b>1. 下整合包</b><br>"
            "从 GPT-SoVITS 官方项目的发布说明中下载当前整合包。"
            "不要使用来历不明的网盘转存。</p>"
            "<p><b>2. 解压</b><br>"
            "解压到你喜欢的位置（例如一个单独的 GPT-SoVITS 文件夹）。</p>"
            "<p><b>3. 回到向导</b><br>"
            + self._guide_steps()
            + "<hr>"
            "<h4>💡 不开语音也能玩</h4>"
            "<p>不装语音完全不影响桌宠的其他功能。<br>"
            "以后想加声音了，随时回来装就行。</p>"
            "<hr>"
            "<p>"
            + self._guide_footer()
        )
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("语音功能怎么装")
        dialog.setMinimumSize(520, 520)
        apply_wizard_dialog_style(dialog)
        dialog.setAccessibleName("GPT-SoVITS 安装说明")
        dl = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.setObjectName("SummaryOutput")
        text.setOpenExternalLinks(True)
        text.setHtml(guide)
        text.setAccessibleName("GPT-SoVITS 安装步骤")
        dl.addWidget(text)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        c = QPushButton("明白了")
        c.setObjectName("PrimaryButton")
        c.setMinimumSize(104, MIN_TARGET_SIZE)
        c.setAccessibleName("关闭安装说明")
        c.clicked.connect(dialog.accept)
        btn_row.addWidget(c)
        dl.addLayout(btn_row)
        dialog.exec_()

    # ─── 跨线程信号槽（在主线程执行） ───
