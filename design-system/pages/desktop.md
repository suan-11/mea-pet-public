# Desktop pet UI overrides

优先于 `MASTER.md` 中与桌宠浮层冲突的条目。

## Hierarchy

1. Live2D / PNG 角色本体
2. 回复气泡栈（可超过 3 条，旧条更透；不合并多分段回复）
3. 聊天输入面板
4. 右键菜单 / 托盘菜单
5. 模态确认

## Reply presentation

- TTS 关闭：分段文字随流式增量持续增长，完成后定稿。
- TTS 开启：音频就绪后再同时显示气泡并播放；时长至少覆盖音频。
- TTS 失败或语言不支持：立即回退文字，不阻塞后续分段。
- 点击绑定了轮次的气泡可查看「本轮完整回复」；右键菜单提供最近对话时间线。
- Agent 工具只展示经过清洗的开始、完成、失败状态，不显示原始工具名、参数和结果。

## Session and backend state

- 直连与 Agent 一次只启用一个；保存配置会取消旧 generation 并热切换，不自动兜底。
- 时间线标签应能区分模型服务、Agent 类型与会话，缓存按作用域隔离。
- Agent 新会话保留旧时间线只读；直连清除记忆同时清除直连时间线。

## Capture consent

- 每个截图请求都在本机单独确认，超时取消，授权不复用。
- 默认全屏；确认窗口允许改选区域或应用，本次结果不写磁盘。
- 确认窗口按当前范围自适应高度；全屏模式不为已隐藏的区域/应用表单保留空白占位。
- 云端路径必须同时满足 `watcher.allow_cloud`。

## Icons

系统操作使用 `meapet/desktop/icons.py` 的 Qt 标准图标；emoji 仅作心情点缀。

## Standby recovery

- 待机时托盘提示「点击可恢复」
- 托盘单击/菜单「取消待机并显示」可唤醒
- 右键菜单仍可取消待机

## Motion

`display.reduced_motion` + 环境变量 +（Linux）系统动画关闭启发式 → `MEAPET_REDUCED_MOTION`。
