#!/usr/bin/env python3
"""P1-3 尾巴：PTT 全局热键的**真机**验证。

tests/ 里那几条只测解析与安全判定——RegisterHotKey 会改系统状态，
不能进 CI。这台探针是它的另一半：真注册、真模拟按下/松开、真卸载。

三件事必须都成立，缺一条 PTT 就是没做成：

1. **注册成功**：RegisterHotKey 返回非零。失败最常见的原因是热键被占用，
   探针要把「被占用」与「参数不合法」分开报。
2. **能收到按下与松开**：真发 WM_HOTKEY 进消息队列（PostThreadMessage 给自己），
   走 Qt 的 nativeEventFilter，触发 pressed；GetAsyncKeyState 那条松开侦测
   单独验——它读的是物理键态，模拟不出来，所以拆成两条独立读数。
3. **卸载干净**：unregister 之后同一个热键**还能再注册一次**。这一条最能
   证明「没留残留」——残留会让下次启动静默失败。

用法（需要真桌面，不能用 QT_QPA_PLATFORM=offscreen）::

    python scripts/ptt_probe.py
    python scripts/ptt_probe.py --hotkey "Ctrl+Alt+F9" --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def run(hotkey: str, *, json_out: bool = False) -> int:
    if not sys.platform.startswith("win"):
        print("[probe] 本探针只验 Windows 的 RegisterHotKey 通道", file=sys.stderr)
        return 2

    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from meapet.voice.ptt import GlobalPushToTalk, hotkey_is_safe, parse_hotkey

    app = QApplication.instance() or QApplication([])
    results = {}
    events = {"pressed": 0, "released": 0}

    # ---- 1. 安全判定 + 解析 ----
    results["hotkey_safe"] = hotkey_is_safe(hotkey)
    if not results["hotkey_safe"]:
        print(f"[probe] 热键 {hotkey} 不安全（缺修饰键或解析不过），拒绝继续", file=sys.stderr)
        return 1
    try:
        mods, vk = parse_hotkey(hotkey)
        results["parse"] = f"mods=0x{mods:04X} vk=0x{vk:02X}"
    except ValueError as exc:
        print(f"[probe] 解析失败：{exc}", file=sys.stderr)
        return 1

    # ---- 2. 真注册 ----
    ptt = GlobalPushToTalk()
    ptt.pressed.connect(lambda: events.__setitem__("pressed", events["pressed"] + 1))
    ptt.released.connect(lambda: events.__setitem__("released", events["released"] + 1))
    results["registered"] = ptt.register(hotkey)
    if not results["registered"]:
        # 断言「失败时状态是干净的」：没注册成功就不该留过滤器与热键
        results["clean_after_failed_register"] = not ptt.armed
        print("[probe] 注册失败（多半是热键被其它程序占用）", file=sys.stderr)
        if json_out:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        return 1

    # ---- 3. 真投递 WM_HOTKEY，验 filter → pressed ----
    import ctypes

    WM_HOTKEY = 0x0312
    thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
    ok = ctypes.windll.user32.PostThreadMessageW(
        thread_id, WM_HOTKEY, ptt._hotkey_id, 0
    )
    results["post_wm_hotkey"] = bool(ok)
    # 消息泵要转几圈才轮到 nativeEventFilter
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and events["pressed"] == 0:
        app.processEvents()
        time.sleep(0.02)
    results["pressed_received"] = events["pressed"] > 0

    # ---- 4. 松开侦测：物理键态模拟不出来，只验轮询真的起来了 ----
    #    按下回调会起一只 QTimer；这里确认它存在且间隔是约定的值。
    poll_timer = ptt._timer
    results["release_poll_armed"] = poll_timer is not None and poll_timer.isActive()
    results["release_poll_interval_ms"] = poll_timer.interval() if poll_timer else None

    # 收掉轮询，免得它在卸载之后还在读键态
    ptt._stop_poll()

    # ---- 5. 卸载，并且**再注册一次**证明没残留 ----
    ptt.unregister()
    results["armed_after_unregister"] = ptt.armed
    second = GlobalPushToTalk()
    results["re_registered_after_unregister"] = second.register(hotkey)
    second.unregister()

    ok_all = (
        results["registered"]
        and results["pressed_received"]
        and results["release_poll_armed"]
        and not results["armed_after_unregister"]
        and results["re_registered_after_unregister"]
    )
    results["ok"] = bool(ok_all)

    if json_out:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"[probe] PTT 真机验证 · 热键 {hotkey}")
        print(f"  解析              : {results['parse']}")
        print(f"  注册              : {'成功' if results['registered'] else '失败'}")
        print(f"  WM_HOTKEY 投递    : {'成功' if results['post_wm_hotkey'] else '失败'}")
        print(f"  pressed 收到      : {'是' if results['pressed_received'] else '否'}")
        print(
            f"  松开轮询已起      : {'是' if results['release_poll_armed'] else '否'}"
            f"（间隔 {results['release_poll_interval_ms']} ms）"
        )
        print(f"  卸载后 armed      : {results['armed_after_unregister']}（应为 False）")
        print(
            f"  卸载后可再注册    : "
            f"{'是' if results['re_registered_after_unregister'] else '否（有残留）'}"
        )
        print()
        print(f"  总判 = {'通过' if results['ok'] else '未过'}")
    return 0 if results["ok"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="P1-3 PTT 全局热键真机验证")
    parser.add_argument("--hotkey", default="Ctrl+Alt+F9", help="要验证的热键串")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    return run(args.hotkey, json_out=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
