"""
wayland_layer.py —— Niri (wlroots) 点击穿透的 layer-shell 后端。

原理：通过 liblayer_shell_shim.so 创建【裸】wl_surface（无 xdg_toplevel role），
     挂 layer-shell OVERLAY + 空 input region → pointer/touch 穿透。

该 `.so` 现在是 **Rust cdylib**（`native/layer_shell`，接口真值见 `~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md` §7.1）。
它**自己打开一条到 compositor 的 Wayland 连接**，并在内部跑一个自建的事件泵线程：
桥接层不从 Qt 借 `wl_display`，Qt 与它之间只有"这块 QImage 贴到那个矩形"这一件事。
失效模式（为什么必须自建泵，见 §6.4）：一条 Wayland 连接只能有一个读取者，若两处
都读同一个 socket 字节流 → 事件被互相吃掉，表现为随机花屏且不可复现。旧 C 实现把
这个前提外包给 Qt 主循环，本实现把它写进自己的线程模型。

调用流程：
  1. layer_shell_init()                    —— 建立连接 + 泵线程；0 = 成功，
                                             负数 = 无 Wayland 会话 / 全局绑定失败 / 内部建立失败
  2. layer_create_context(NULL, w, h, x, y) —— 创建 layer context（首参必须 NULL）
  3. layer_set_click_through(ctx, 1)       —— 穿透；0 = 恢复可点
  4. layer_update_pixels(ctx, rgba, w, h)  —— 每帧推送像素（Phase 2）
  5. layer_destroy_context(ctx)            —— 销毁

依赖：liblayer_shell_shim.so（在仓库根，由 `bash build_layer_shell.sh` 构建）
"""

import ctypes
from ctypes import (POINTER, c_char_p, c_int, c_uint32, c_ubyte, c_void_p)
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage

# shim 位于项目根目录：meapet/desktop/wayland_layer.py -> 上三级
_SHIM_PATH = str(
    Path(__file__).resolve().parent.parent.parent / "liblayer_shell_shim.so"
)

# wl_shm 格式常量（协议 fourcc，权威定义是 wayland.xml 的 wl_shm format 枚举）。
# 这张表**不是**"与某份 C 源保持一致"的副本：那正是两份真值靠约定对齐的老问题。
# 真正生效的只有 `set_pixel_format` 的值域（spec §6.2 的闭集 {0, ABGR8888}，
# 而 ARGB8888 的值本身就是 0），其字节布局由 Rust 侧的 T1 金样逐字节锁死；
# 其余条目只是把协议里存在、但本后端不使用的格式名列出来供查。
WL_SHM_FORMAT_ARGB8888 = 0
WL_SHM_FORMAT_XRGB8888 = 1
WL_SHM_FORMAT_ABGR8888 = 0x34324241
WL_SHM_FORMAT_XBGR8888 = 0x34324258
WL_SHM_FORMAT_RGBA8888 = 0x34324952
WL_SHM_FORMAT_RGBX8888 = 0x34325852
WL_SHM_FORMAT_BGRA8888 = 0x41524742
WL_SHM_FORMAT_BGRX8888 = 0x42315852


class WaylandLayerBackend:
    """单例式后端，供 click_through._enable_wayland 调用。"""

    def __init__(self):
        self._shim = None
        self._ctx = None
        self._pixel_format = 0  # 0 = 自动选择

    # ---------- 懒加载 shim ----------
    def _load(self) -> ctypes.CDLL:
        if self._shim is None:
            self._shim = ctypes.CDLL(_SHIM_PATH)

            # init / cleanup
            self._shim.layer_shell_init.restype = c_int
            self._shim.layer_shell_cleanup.restype = None

            # layer context API
            self._shim.layer_create_context.restype = c_void_p
            self._shim.layer_create_context.argtypes = [
                c_void_p,          # state：**必须传 None**——非 NULL 一律被拒（spec §4.7-8）
                c_int, c_int,      # width, height
                c_int, c_int,      # pos_x, pos_y
            ]
            self._shim.layer_set_click_through.argtypes = [c_void_p, c_int]
            self._shim.layer_destroy_context.argtypes = [c_void_p]

            # Phase 3: 双模切换（可选符号，缺失时降级而非整体失效）
            self._optional = set()
            for name, argtypes in (
                ("layer_clear", [c_void_p]),
                ("layer_set_position", [c_void_p, c_int, c_int]),
                ("layer_set_size", [c_void_p, c_int, c_int]),
            ):
                fn = getattr(self._shim, name, None)
                if fn is None:
                    print(f"[layer] ⚠ shim 缺少 {name}，该功能降级", flush=True)
                    continue
                fn.restype = None
                fn.argtypes = argtypes
                self._optional.add(name)

            # Phase 2: 像素上传
            self._shim.layer_update_pixels.restype = None
            self._shim.layer_update_pixels.argtypes = [
                c_void_p,
                POINTER(c_ubyte),
                c_int, c_int,
            ]
            self._shim.layer_update_pixels_with_format.restype = None
            self._shim.layer_update_pixels_with_format.argtypes = [
                c_void_p,
                POINTER(c_ubyte),
                c_int, c_int,
                c_uint32,
            ]
        return self._shim

    # ---------- 可用性 ----------
    def is_available(self) -> bool:
        try:
            shim = self._load()
            ret = shim.layer_shell_init()
            if ret != 0:
                print(f"[layer] ✗ layer_shell_init 返回 {ret}", flush=True)
            return ret == 0
        except Exception as exc:
            print(f"[layer] ✗ 探测异常: {type(exc).__name__}: {exc}", flush=True)
            return False

    # ---------- 生命周期 ----------
    def enable(self, qwindow, width: int, height: int, pos_x: int, pos_y: int):
        """创建 layer context，开启穿透。返回 ctx 句柄。"""
        shim = self._load()
        if shim.layer_shell_init() != 0:
            raise RuntimeError("layer-shell init 失败（compositor 不支持？）")

        # state 形参必须为 None：独立连接下没有"调用方自带 state"这回事（§4.7-8）
        self._ctx = shim.layer_create_context(None, width, height, pos_x, pos_y)
        if not self._ctx:
            raise RuntimeError("创建 layer context 失败")
        return self._ctx

    def set_click_through(self, enabled: bool):
        if self._ctx:
            self._shim.layer_set_click_through(self._ctx, 1 if enabled else 0)

    def clear(self):
        if self._ctx and "layer_clear" in getattr(self, "_optional", ()):
            self._shim.layer_clear(self._ctx)

    def set_position(self, x: int, y: int):
        if self._ctx and "layer_set_position" in getattr(self, "_optional", ()):
            self._shim.layer_set_position(self._ctx, x, y)

    def set_size(self, w: int, h: int):
        if self._ctx and "layer_set_size" in getattr(self, "_optional", ()):
            self._shim.layer_set_size(self._ctx, w, h)

    def disable(self):
        if self._ctx:
            self._shim.layer_destroy_context(self._ctx)
            self._ctx = None
            try:
                self._shim.layer_shell_cleanup()
            except Exception:
                pass

    # ---------- Phase 2: 像素上传 ----------
    def update_pixels(self, image) -> None:
        """把 QImage 推送到 layer surface（每帧调用）。

        image: PyQt5.QtGui.QImage，任意格式（内部自动转 RGBA8888）
        失败时静默返回，避免中断渲染循环。
        """
        if not self._ctx or not self._shim:
            return
        if not isinstance(image, QImage):
            return

        if image.format() != QImage.Format_RGBA8888:
            image = image.convertToFormat(QImage.Format_RGBA8888)

        w, h = image.width(), image.height()
        if w <= 0 or h <= 0:
            return

        # 关键：constBits() 返回 sip.voidptr，必须拷贝一份到 Python 管理的内存，
        # 否则 QImage 被 GC 后桥接层读到野指针 → 崩溃（桥接层只在本次调用内读它）
        bits = image.constBits()
        bits.setsize(image.byteCount())
        buf = (c_ubyte * image.byteCount()).from_buffer_copy(bits)

        if self._pixel_format:
            self._shim.layer_update_pixels_with_format(
                self._ctx, buf, w, h, self._pixel_format
            )
        else:
            self._shim.layer_update_pixels(self._ctx, buf, w, h)

    def set_pixel_format(self, fmt: int) -> None:
        """强制指定 wl_shm 格式（颜色错乱时才需要动；默认 0 = 自动）。

        值域是闭集 {0, WL_SHM_FORMAT_ABGR8888}（spec §6.2）：
          * 0  —— 自动：合成器广播 ABGR8888 就整体拷贝，否则回退 ARGB8888 交换 R/B。
            注意 ARGB8888 的枚举值本身就是 0，所以"强制 ARGB"与"自动"在这一层
            不可区分——要验证回退路径只能靠不支持 ABGR 的合成器，不是靠传这个值。
          * ABGR8888 —— 强制整体拷贝（内存布局 [R,G,B,A] 与 QImage RGBA8888 一致）。
          * 其他任何值 —— **该帧不提交** + 粘性错误。错误文本在桥接层的
            `layer_last_error()` 里（§7.1 #6/#11；注意本模块目前没有绑定那个符号，
            要读它得自己 `ctypes` 一次——该缺口登记为挂起项 gap#1）。这是有意的：
            调试通道要"响"，而不是悄悄替你选一个看起来对的格式（agents-rules §4）。
            旧 C 实现把任意 uint32 直接喂给 `wl_shm_pool_create_buffer`，代价是
            合成器发 fatal protocol error、**整条 Wayland 连接被断**（§4.7 第 11 行）。

        例：怀疑颜色通道错位时 `set_pixel_format(WL_SHM_FORMAT_ABGR8888)` 看是否变化；
        恢复正常渲染请显式传 0。
        """
        self._pixel_format = fmt

    def destroy_context(self):
        """只销毁当前 layer context，不调 `layer_shell_cleanup()`。

        这里不调 cleanup 的理由是**作用域**，不是"否则下次创建会失败"：cleanup 会
        连坐销毁**全部**存活 ctx 并停掉泵线程（§4.7 第 9 行把它做成状态机不变量，
        用来关掉旧 C 实现里那条可达的 use-after-free），而本方法的存在意义就是
        "这一只桌宠退场，后端留着给别人用"。

        cleanup 之后再 `enable()` 是合法的——`enable()` 自己先调 `layer_shell_init()`，
        状态机走 Uninitialized → Ready 重建（§4.3），所以 `disable()` 用 cleanup 也对。
        真正的失败形态只有一种：cleanup 后拿**旧句柄**继续调用，那会被句柄注册表按
        代际/纪元拒掉（§4.4），是定义良好的 no-op + 粘性错误，不是悬垂指针。
        """
        if self._ctx is None:
            return
        try:
            self._shim.layer_destroy_context(self._ctx)
        except Exception as exc:
            print(f"[layer] destroy_context 失败: {exc}", flush=True)
        self._ctx = None


# ---------- 单例 ----------
_backend = WaylandLayerBackend()


# ---------- 门面函数（供 click_through.py / render_host.py 调用）----------
def is_available() -> bool:
    return _backend.is_available()


def enable(qwindow, width: int, height: int, pos_x: int, pos_y: int):
    return _backend.enable(qwindow, width, height, pos_x, pos_y)


def set_click_through(enabled: bool) -> None:
    _backend.set_click_through(enabled)


def disable() -> None:
    _backend.disable()


def update_pixels(image) -> None:
    _backend.update_pixels(image)

def clear() -> None:
    _backend.clear()


def set_position(x: int, y: int) -> None:
    _backend.set_position(x, y)


def set_size(w: int, h: int) -> None:
    _backend.set_size(w, h)


def get_backend() -> WaylandLayerBackend:
    """返回后端单例，供 Live2DWidget 注入后每帧推像素。"""
    return _backend

