"""桌面 UI 统一使用 Qt 标准图标，避免 emoji 充当系统操作语义。"""

from __future__ import annotations

from functools import lru_cache

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QStyle


@lru_cache(maxsize=32)
def standard_icon(role: str) -> QIcon:
    """按角色名返回标准图标；无 QApplication 时返回空图标。"""
    app = QApplication.instance()
    if app is None:
        return QIcon()
    style = app.style()
    mapping = {
        "status": QStyle.SP_FileDialogInfoView,
        "watch": QStyle.SP_ComputerIcon,
        "settings": QStyle.SP_FileDialogDetailedView,
        "display": QStyle.SP_DesktopIcon,
        "expression": QStyle.SP_DirIcon,
        "quit": QStyle.SP_DialogCloseButton,
        "show": QStyle.SP_TitleBarNormalButton,
        "standby": QStyle.SP_MediaPause,
        "wake": QStyle.SP_MediaPlay,
        "autostart": QStyle.SP_BrowserReload,
        "reset": QStyle.SP_DialogResetButton,
        "apply": QStyle.SP_DialogApplyButton,
        "close": QStyle.SP_DialogCloseButton,
    }
    sp = mapping.get(role, QStyle.SP_FileIcon)
    try:
        return style.standardIcon(sp)
    except Exception:
        return QIcon()
