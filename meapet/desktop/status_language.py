"""桌宠统一状态文案。

角色在前：短句、可朗读、避免技术堆砌。系统状态用中文说明，
必要时在括号内补充隐私或恢复提示。
"""

from __future__ import annotations


def thinking() -> str:
    """LLM 请求进行中（可持久气泡）。"""
    return "梅尔正在思考…"


def thinking_busy() -> str:
    """用户在仍等待上一条回复时再次发送。"""
    return "还在想上一条…稍等喵"


def chat_timeout() -> str:
    return "唔…好像没响应喵。再试一次？"


def chat_error_prefix() -> str:
    return "出错啦："


def watching() -> str:
    return "偷偷看一眼…"


def watching_denied() -> str:
    return "好，这次不看了喵"


def cloud_vision_disabled() -> str:
    return "云端识图未授权：请在配置页允许云端识图"


def standby_on() -> str:
    return "梅尔待机中…右键可取消"


def standby_off() -> str:
    return "醒啦喵"


def watcher_enabled_local() -> str:
    return "屏幕观察已开启（本地识图）"


def watcher_enabled_cloud() -> str:
    return "屏幕观察已开启（云端，上传前会确认）"


def watcher_disabled() -> str:
    return "屏幕观察已关闭"


def tts_failed() -> str:
    return "语音合成失败，文字还在喵"


def empty_memories() -> str:
    return "还没有重要回忆。双击梅尔说说话，她会慢慢记住你喵"


def ready_hint() -> str:
    return "梅尔准备好啦～双击对话 · 右键打开菜单"


def wizard_progressive_hint() -> str:
    return "先完成「环境」和「对话」即可开玩；语音与屏幕识图可稍后设置"


def menu_watch_enable() -> str:
    return "开启屏幕观察（可能截屏）"


def menu_watch_disable() -> str:
    return "关闭屏幕观察"


def menu_standby_enter() -> str:
    return "待机（暂停识图）"


def menu_standby_leave() -> str:
    return "取消待机"


def menu_render_to_live2d() -> str:
    return "切换到 Live2D（当前 PNG）"


def menu_render_to_png() -> str:
    return "切回 PNG 立绘（当前 Live2D）"


def first_run_hint() -> str:
    """仅首次启动展示的一次性引导。"""
    return "双击说话 · 右键打开菜单 · 托盘可找回"


def reduced_motion_enabled_hint() -> str:
    return "已开启减少动画"


def tray_recover_standby() -> str:
    return "取消待机并显示"


def tray_standby_tooltip() -> str:
    return "梅尔待机中 · 点击托盘可恢复"


def tray_running_tooltip() -> str:
    return "梅尔桌宠 · MeaPet"


def menu_section_interaction() -> str:
    return "互动"


def menu_section_system() -> str:
    return "系统"
