"""桌面端与配置中心共用的低误触 Qt 控件。"""

from __future__ import annotations

from PyQt5.QtWidgets import QComboBox


class WheelSafeComboBox(QComboBox):
    """忽略滚轮改值，让事件继续交给外层滚动区。"""

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        event.ignore()
