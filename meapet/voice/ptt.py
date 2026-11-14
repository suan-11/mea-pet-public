"""全局按住说话（P1-3）：系统级 PTT 热键。

为什么不是 QShortcut 了事：QShortcut 只在本应用窗口聚焦时有效；桌宠的常态恰恰是
「用户在全屏别的窗口时按住说话」——需要全局热键。Windows 上 RegisterHotKey 只报
按下、不报松开，所以按下后开一个高频 QTimer 轮询 GetAsyncKeyState 侦测松开；
不碰低级键盘钩子（WH_KEYBOARD_LL）——那东西被杀软按 keylogger 标，不值得。

非 Windows：GlobalPushToTalk.available() 返回 False，调用方回落应用内 QShortcut
或点按切换模式——不静默假装热键存在。
"""
from __future__ import annotations

import sys

from PyQt5.QtCore import QAbstractNativeEventFilter, QObject, QTimer, pyqtSignal

from meapet.log import get_color_logger

log = get_color_logger("voice")

# Windows 修饰键位（RegisterHotKey 与 GetAsyncKeyState 共用这一套）
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312

_MODIFIER_VK = {
    "ctrl": _MOD_CONTROL,
    "control": _MOD_CONTROL,
    "shift": _MOD_SHIFT,
    "alt": _MOD_ALT,
    "win": _MOD_WIN,
    "meta": _MOD_WIN,
}

_NAMED_KEY_VK = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "insert": 0x2D,
    "capslock": 0x14,
}


def _key_vk(name: str):
    """主键名 → Windows 虚拟键码；不认得返回 None。"""
    if not name:
        return None
    if len(name) == 1:
        ch = name.lower()
        if "a" <= ch <= "z":
            return ord(ch.upper())
        if "0" <= ch <= "9":
            return ord(ch)
        return None
    if name.startswith("f") and name[1:].isdigit():
        n = int(name[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1
        return None
    return _NAMED_KEY_VK.get(name)


def parse_hotkey(text) -> tuple:
    """把 'Ctrl+Shift+Space' 解析成 (修饰键位掩码, 主键 VK)。

    解析不了抛 ValueError——配置里的热键串是用户写的，报错要把原文带上，
    别吞成「热键无效」四个字。
    """
    parts = [p.strip().lower() for p in str(text or "").split("+") if p.strip()]
    mods = 0
    key = ""
    for part in parts:
        if part in _MODIFIER_VK:
            mods |= _MODIFIER_VK[part]
        elif not key:
            key = part
        else:
            raise ValueError(f"热键 '{text}' 里有多个主键：{part}")
    if not key:
        raise ValueError(f"热键 '{text}' 缺主键")
    vk = _key_vk(key)
    if vk is None:
        raise ValueError(f"热键 '{text}' 的主键不认得：{key}")
    return mods, vk


def hotkey_is_safe(text) -> bool:
    """不带任何修饰键的单主键当全局 PTT 太危险（按住 A 全系统都在录音）——拦。"""
    try:
        mods, _vk = parse_hotkey(text)
    except ValueError:
        return False
    return mods != 0


class _WinHotkeyFilter(QAbstractNativeEventFilter):
    """把 WM_HOTKEY 翻译成一次 on_press() 回调（GUI 线程上跑，安全发 Qt 信号）。"""

    def __init__(self, hotkey_id: int, on_press):
        super().__init__()
        self._hotkey_id = int(hotkey_id)
        self._on_press = on_press

    def nativeEventFilter(self, eventType, message):  # noqa: N802 - Qt 定名
        try:
            if eventType == b"windows_generic_MSG":
                import ctypes

                msg = ctypes.cast(int(message), ctypes.POINTER(_WinMsg)).contents
                if msg.message == _WM_HOTKEY and msg.wParam == self._hotkey_id:
                    self._on_press()
        except Exception as exc:  # noqa: BLE001 - 过滤器炸不得带走消息泵
            log.warning(f"[voice] 全局热键过滤器异常：{type(exc).__name__}: {exc}")
        return False, 0


class _WinMsg(__import__("ctypes").Structure):
    _fields_ = [
        ("hwnd", __import__("ctypes").c_void_p),
        ("message", __import__("ctypes").c_uint),
        ("wParam", __import__("ctypes").c_size_t),
        ("lParam", __import__("ctypes").c_ssize_t),
        ("time", __import__("ctypes").c_uint),
        ("pt_x", __import__("ctypes").c_long),
        ("pt_y", __import__("ctypes").c_long),
    ]


class GlobalPushToTalk(QObject):
    """Windows 全局 PTT：pressed 在按下时发，released 在松开时发。

    松开侦测：RegisterHotKey 没有 release 事件，按下后轮询
    GetAsyncKeyState（40 ms 一格），高位清零即松开。
    """

    pressed = pyqtSignal()
    released = pyqtSignal()

    #: 轮询松开的间隔（毫秒）。40 ms 之下收益感觉不到，之上"松手还录"的尾巴开始明显。
    POLL_MS = 40

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._mods = 0
        self._vk = 0
        self._hotkey_id = (id(self) & 0x7FFF) or 1
        self._filter = None
        self._timer = None
        self._armed = False

    @staticmethod
    def available() -> bool:
        return sys.platform.startswith("win")

    @property
    def armed(self) -> bool:
        return self._armed

    def register(self, hotkey: str) -> bool:
        """注册热键；失败（非 Windows / 被占用 / 解析不过）返回 False 并写日志。"""
        if not self.available():
            log.info("[voice] 非 Windows 平台，全局 PTT 不可用，回落应用内快捷键")
            return False
        self.unregister()
        try:
            mods, vk = parse_hotkey(hotkey)
        except ValueError as exc:
            log.warning(f"[voice] {exc}")
            return False
        import ctypes

        user32 = ctypes.windll.user32
        ok = user32.RegisterHotKey(None, self._hotkey_id, mods | _MOD_NOREPEAT, vk)
        if not ok:
            log.warning(f"[voice] RegisterHotKey 失败（热键被占用？）：{hotkey}")
            return False
        self._mods, self._vk = mods, vk
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            # 没有应用实例就没处装过滤器；注册已生效但永远不会回调，按失败处理并回滚。
            user32.UnregisterHotKey(None, self._hotkey_id)
            log.warning("[voice] 无 QApplication 实例，全局热键装不上过滤器")
            return False
        self._filter = _WinHotkeyFilter(self._hotkey_id, self._on_press)
        app.installNativeEventFilter(self._filter)
        self._armed = True
        log.info(f"[voice] 全局 PTT 已注册：{hotkey}")
        return True

    def unregister(self) -> None:
        if sys.platform.startswith("win"):
            try:
                import ctypes

                ctypes.windll.user32.UnregisterHotKey(None, self._hotkey_id)
            except Exception as exc:  # noqa: BLE001 - 卸载失败最坏是热键残留到进程退出
                # 留痕：热键残留会导致下次 RegisterHotKey「被占用」，而那条日志
                # 会指向错误的方向（以为别的软件抢了）。
                log.warning(f"[voice] UnregisterHotKey 失败：{type(exc).__name__}: {exc}")
        if self._filter is not None:
            from PyQt5.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._filter)
            self._filter = None
        self._armed = False
        self._stop_poll()

    def _on_press(self) -> None:
        if self._timer is not None:
            return  # MOD_NOREPEAT 已挡系统连发；真来了也别叠第二只计时器
        self.pressed.emit()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_release)
        self._timer.start(self.POLL_MS)

    def _poll_release(self) -> None:
        import ctypes

        state = ctypes.windll.user32.GetAsyncKeyState(self._vk)
        if not (state & 0x8000):
            self._stop_poll()
            self.released.emit()

    def _stop_poll(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
