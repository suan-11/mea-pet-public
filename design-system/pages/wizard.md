# Configuration wizard overrides

## Progressive disclosure

1. 环境 + 回复后端优先；模型服务商直连与 Agent 互斥
2. 语音总开关默认可用；引擎详细设置默认折叠
3. 屏幕识图默认关；先选关闭 / 继承 / 中转，再显示相应高级项
4. Companion MCP 仅在 Agent 模式并显式启用后显示网络与 Token 设置

## Backend

- 直连页明确选择 Ollama Chat、OpenAI Chat、OpenAI Responses 或 Anthropic Messages，不把 OpenAI-compatible 端点称为 Agent。
- Agent 页支持 Hermes 与 OpenClaw，恢复上次会话；新会话由「清除记忆」确认流程创建。
- 本地时间线默认 5 轮，可设 0–100；帮助文案说明按后端和 Agent 会话隔离。
- 配置保存后运行中热应用。失败时使用中性系统文案，不泄露响应正文。

## Companion MCP

- 默认回环地址；局域网必须填写本机具体 IP 与唯一允许的 Agent IP。
- 无证书的局域网 HTTP 需要显式勾选，并显示持续风险说明。
- Token 默认遮蔽，提供查看、复制、重新生成；重新生成后旧 Token 立即失效。
- 不自动修改 Windows 防火墙，界面只提供说明。

## TTS references

- GPT-SoVITS 参考音频按语言配置，每种语言一条固定音频并明确显示语言。
- 翻译只作为“不支持该输出语言”时的显式 TTS 路由，不作为请求失败兜底。

## Vision

- `inherit` 必须明确确认主回复模型支持图片。
- `relay` 在直连模式使用独立 Ollama / MiMo；Agent 模式只继承 Agent 视觉能力或关闭。
- 截图确认明确写出“本次有效”，默认全屏并允许改选区域/应用。

## Display

- 字体缩放即时预览
- 减少动画写入 `display.reduced_motion`
