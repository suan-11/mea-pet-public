"""
梅尔桌宠 - Live2D 渲染模块
基于 live2d-py (Cubism 3+) + QOpenGLWidget 透明窗口
"""
import os
import sys
import math
from PyQt5.QtWidgets import QOpenGLWidget
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
import live2d.v3 as live2d
from PyQt5.QtCore import QEvent
from PyQt5.QtGui import QSurfaceFormat

# 在 Windows 下可选导入 win32api（DLL 缺失时不阻塞启动）
if sys.platform == "win32":
    try:
        import win32api
        import win32con
    except Exception:
        win32api = None
        win32con = None
class Live2DModel:
    """Live2D 模型控制器，提供与 SpriteRenderer 兼容的接口"""
    
    def __init__(self, model_dir: str):
        """
        model_dir: 包含 .model3.json 的目录
        """
        self.model_dir = model_dir
        self.model = None
        self.widget = None  # Live2DWidget 引用
        self._loaded = False
        self._current_expression = "001"  # 兼容接口
        
        

        # 找 model3.json
        self._model_json = None
        for f in os.listdir(model_dir):
            if f.endswith('.model3.json') or f.endswith('.model.json'):
                self._model_json = os.path.join(model_dir, f)
                break
        if not self._model_json:
            raise FileNotFoundError(f"在 {model_dir} 中找不到 .model3.json")
        
        self._name = os.path.splitext(os.path.basename(self._model_json))[0]
    
    def create_widget(self, parent=None):
        """创建并返回 Live2DWidget"""
        self.widget = Live2DWidget(self, parent)
        return self.widget
    
    def get_model(self) -> live2d.LAppModel:
        return self.model
    
    def get_suggested_size(self) -> tuple:
        """返回建议显示尺寸（模型加载后）"""
        # 模型比例 5000:7000 = 5:7，目标宽度匹配 PNG 立绘
        return (525, 735)
    
    # ====== 兼容 SpriteRenderer 的接口 ======
    
    def set_mood(self, mood: str):
        """设置情绪表情"""
        self._current_expression = mood
        if self.model:
            # 根据 mood 播放对应 motion
            if mood in ("happy", "curious"):
                # 眯眼 motion
                self.model.StartMotion("Idle", 0, live2d.MotionPriority.NORMAL)
            elif mood in ("annoyed", "angry"):
                # 生气 motion
                self.model.StartMotion("Angry", 0, live2d.MotionPriority.NORMAL)
            elif mood == "sad" or mood == "melancholy":
                # 可扩展，暂无对应 motion
                pass
    
    def set_expression(self, expr: str):
        """设置差分表情（兼容接口）"""
        self._current_expression = expr
        # Live2D 没有差分表情，映射到 mood
        if expr == "011" or expr == "012":
            # 闭眼 → 保持当前，不额外动作
            pass
        elif expr == "001":
            # 默认睁眼
            pass
    
    def start_blink_animation(self):
        """眨眼由 Live2D SDK 自动处理，这里不需要做任何事"""
        pass
    
    def stop_blink_animation(self):
        pass
    
    def expression_changed(self):
        """无操作（Live2D 是连续的）"""
        pass
    
    def get_current_expression(self) -> str:
        return self._current_expression
    
    def get_current_pixmap(self):
        """无操作"""
        return None
    
    def set_size(self, width: int, height: int):
        if self.model:
            scale_w = width / self.model.GetCanvasSize()[0]
            scale_h = height / self.model.GetCanvasSize()[1]
            scale = min(scale_w, scale_h)
            self.model.SetScale(scale)


class Live2DWidget(QOpenGLWidget):
    """透明 Live2D 渲染窗口"""
    
    # 信号：摸头(head) / 摸尾巴(tail)
    head_patted = pyqtSignal()
    tail_patted = pyqtSignal()
    
    def __init__(self, l2d_model: Live2DModel, parent=None):
        super().__init__(parent)

        # 必须在初始化 QOpenGLWidget 之前设置
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)       # 分配 8 位 Alpha 通道
        fmt.setRenderableType(QSurfaceFormat.OpenGL)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        QSurfaceFormat.setDefaultFormat(fmt)
        self.setFormat(fmt)

        
        self.l2d = l2d_model
        
        # 1. Qt 自身的透明设置
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True) # 确保在最上层渲染
        self.setStyleSheet("background: transparent; border: none;")

        # 2. 【删除】手动设置 Win32 分层窗口的代码（这会导致 OpenGL 黑底）
        # if sys.platform == "win32": ... (全部删除)

        # 鼠标追踪
        self.setMouseTracking(True)
        self._ready = False
        self._drag_target = (0.0, 0.0)  # 眼球追踪坐标（每帧更新）
        self._global_filter_installed = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        
        # 鼠标位置与拖拽
        self._mouse_x = 0
        self._mouse_y = 0
        self._press_pos = None  
        
        # 【新增】窗口拖拽状态
        self._dragging_window = False
        self._drag_start_global_pos = None

        self.resize(400, 660)
        
        # 3. 【删除】这行代码，否则鼠标事件无法触发
        # self.setAttribute(Qt.WA_TransparentForMouseEvents, True) 
        self.installEventFilter(self)


    def eventFilter(self, obj, event):
        # 仅处理鼠标按下事件（穿透判定）
        if obj == self and event.type() == QEvent.MouseButtonPress:
            # 如果是右键，不拦截，用于呼出菜单
            if event.button() == Qt.RightButton:
                return False  
            # 如果是左键，判断是否点击在模型的非透明区域
            # （这里可以简单判断坐标，或者通过读取像素判断是否透明）
            x, y = event.x(), event.y()
            w, h = self.width(), self.height()
            # 简单的矩形碰撞检测 — 扩大范围以适配非100%缩放
            if w * 0.15 < x < w * 0.85 and 0 < y < h * 0.9:
                return False  # 在模型区域内，不拦截，允许触发 mousePressEvent
            
            # 在模型区域外，返回 True 拦截事件，让操作系统将其传递给底层窗口
            return True  
        
        return super().eventFilter(obj, event)
    
    def initializeGL(self):
        live2d.glInit()
        self._ready = True
        
        from OpenGL.GL import glClearColor, glEnable, glBlendFunc, GL_BLEND, GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA
        
        # 1. 设置透明清屏颜色
        glClearColor(0.0, 0.0, 0.0, 0.0)  
        
        # 2. 显式开启 Alpha 混合，防止 Live2D 渲染出黑边
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        
        # 3. 加载模型
        model = live2d.LAppModel()
        model.LoadModelJson(self.l2d._model_json)
        model.SetAutoBlinkEnable(True)
        model.SetAutoBreathEnable(True)
        self._fit_model_to_window(model)
        self.l2d.model = model
        self.l2d._loaded = True
        self._timer.start(16)

    def _fit_model_to_window(self, model):
        """用 Resize（max 逻辑填满窗口）+ 补偿透明边距"""
        model.Resize(self.width(), self.height())
    
    def resizeGL(self, w, h):
        from OpenGL.GL import glViewport
        # 高DPI下需要用物理像素设置视口
        dpr = self.devicePixelRatio()
        glViewport(0, 0, int(w * dpr), int(h * dpr))
        if self.l2d.model and w > 0 and h > 0:
            self._fit_model_to_window(self.l2d.model)
    
    def paintGL(self):
        if not self._ready or not self.l2d.model:
            # 记录为什么没渲染
            if not hasattr(self, '_dbg_skip'):
                self._dbg_skip = 0
            self._dbg_skip += 1
            if self._dbg_skip <= 3:
                import sys as _s
                _s.stderr.write(f"[PAINT] SKIP _ready={self._ready} model={self.l2d.model is not None}\n")
                _s.stderr.flush()
            return
        
        # 每 2400 帧输出一次心跳
        if not hasattr(self, '_dbg_frame'):
            self._dbg_frame = 0
        self._dbg_frame += 1
        if self._dbg_frame % 2400 == 0:
            import sys as _s
            _s.stderr.write(f"[PAINT] frame={self._dbg_frame} alive\n")
            _s.stderr.flush()
        
        from OpenGL.GL import glClear, GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT
        # 强制清空颜色缓冲，确保没有残留的黑色像素
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT) 
        
        live2d.clearBuffer()
        
        # 每帧从系统获取光标全局坐标，映射后驱动眼球+身体追踪
        from PyQt5.QtGui import QCursor
        gp = QCursor.pos()
        wp = self.mapToGlobal(self.rect().topLeft())
        w, h = self.width(), self.height()
        if w > 0 and h > 0 and self.l2d.model:
            # 鼠标相对窗口中心，归一化到[-1,1]
            cx = (gp.x() - wp.x() - w / 2) / (w / 2)
            cy = (gp.y() - wp.y() - h / 2) / (h / 2)
            cx = max(-1.0, min(1.0, cx))
            cy = max(-1.0, min(1.0, cy))
            # 直接用 SetParameterValue 驱动追踪参数（权重1.0=立即生效）
            self.l2d.model.SetParameterValue("ParamAngleX", cx * 30, 1.0)
            self.l2d.model.SetParameterValue("ParamAngleY", -cy * 30, 1.0)
            self.l2d.model.SetParameterValue("ParamBodyAngleZ", cx * 10, 1.0)
            self.l2d.model.SetParameterValue("ParamAngleZ", cx * 10, 1.0)
        
        self.l2d.model.Update()
        self.l2d.model.Draw()

    def _on_timer(self):
        self.update()
    
    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        # 仅当左键按下时，记录初始位置
        if event.button() == Qt.LeftButton:
            self._press_pos = (event.x(), event.y())
            # 记录全局坐标（用于移动窗口）
            self._drag_start_global_pos = event.globalPos()
            self._dragging_window = False
        else:
            self._press_pos = None

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        
        # 1. 处理窗口拖拽逻辑
        if event.buttons() & Qt.LeftButton and self._drag_start_global_pos is not None:
            delta = event.globalPos() - self._drag_start_global_pos
            if delta.manhattanLength() > 5:
                self._dragging_window = True
                if self.parent():
                    self.parent().move(self.parent().pos() + delta)
                    self._drag_start_global_pos = event.globalPos()
                return

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        
        # 1. 释放左键时，重置拖拽状态
        if event.button() == Qt.LeftButton:
            self._drag_start_global_pos = None
            
            # 2. 【核心逻辑】如果刚才发生了窗口拖拽，直接清空状态，绝对不触发互动
            if self._dragging_window:
                self._dragging_window = False
                self._press_pos = None
                return
            
            # 3. 如果没有拖拽窗口，且是有效的点击，才判断是否触发互动
            if self.l2d.model and self._press_pos is not None:
                px, py = self._press_pos
                dist = math.sqrt((event.x() - px)**2 + (event.y() - py)**2)
                
                # 移动距离小于 8px 视为点击
                if dist < 8:
                    if py < self.height() * 0.5:
                        self.l2d.model.StartMotion("Idle", 0, live2d.MotionPriority.FORCE)
                        self.head_patted.emit()
                    else:
                        self.l2d.model.StartMotion("Angry", 0, live2d.MotionPriority.FORCE)
                        self.tail_patted.emit()
            
            # 4. 清空按下位置
            self._press_pos = None

        self._press_pos = None
    
    def play_motion(self, motion_name: str, priority=3):
        """播放指定 motion"""
        if self.l2d.model:
            self.l2d.model.StartMotion(motion_name, 0, priority)
    
    def shutdown(self):
        self._timer.stop()
        self._ready = False


# ====== 工具函数 ======

def init_live2d():
    """在 QApplication 创建之前初始化 Live2D"""
    live2d.init()


def dispose_live2d():
    """程序退出时释放 Live2D"""
    try:
        live2d.dispose()
    except Exception:
        pass
