# Desktop pet UI overrides

优先于 `MASTER.md` 中与桌宠浮层冲突的条目。

## Hierarchy

1. Live2D / PNG 角色本体
2. 语音气泡栈（最多约 3 条，旧条更透）
3. 聊天输入面板
4. 右键菜单 / 托盘菜单
5. 模态确认

## Icons

系统操作使用 `meapet/desktop/icons.py` 的 Qt 标准图标；emoji 仅作心情点缀。

## Standby recovery

- 待机时托盘提示「点击可恢复」
- 托盘单击/菜单「取消待机并显示」可唤醒
- 右键菜单仍可取消待机

## Motion

`display.reduced_motion` + 环境变量 +（Linux）系统动画关闭启发式 → `MEAPET_REDUCED_MOTION`。
