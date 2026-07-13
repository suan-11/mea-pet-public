# MeaPet UI Design System

本文件由 UI/UX Pro Max 的 “AI virtual companion desktop app / modern dark / playful / glass” 推荐结果整理而成，并针对 PyQt5 桌面环境做了实现映射。

## 产品与视觉方向

- 产品：面向个人用户的 AI 桌宠、设置向导与轻量状态工具。
- 气质：温暖、陪伴、克制的二次元感；暗色为主，不使用纯黑。
- 风格：Modern Dark + 少量半透明层次。模糊仅在平台稳定支持时使用，默认以不透明表面和边框保证可读性。
- 动效：只保留有状态含义的淡入/淡出；150–300ms；不得阻塞输入。
- 图标：系统操作使用文字或统一矢量语言；emoji 只可作为角色表情或内容，不承担唯一操作含义。

## 语义色

| 角色 | 颜色 | 用途 |
|---|---:|---|
| Canvas | `#0E1020` | 窗口底色 |
| Surface | `#17192D` | 主卡片 |
| Elevated | `#20233D` | 浮层、次级卡片 |
| Input | `#111326` | 输入区域 |
| Primary | `#FF91B4` | 主操作与品牌强调 |
| Secondary | `#FFB36B` | 渐变终点与温暖强调 |
| Accent | `#A69BFF` | 焦点之外的辅助强调 |
| Text Primary | `#F8F8FC` | 标题和正文 |
| Text Secondary | `#CACCE0` | 说明文字 |
| Text Muted | `#9FA3BC` | 次要提示，仍满足 AA 正文对比度 |
| Border | `#3B3E5B` | 默认边界 |
| Focus | `#C0B9FF` | 键盘焦点环 |
| Success | `#70DDB0` | 成功状态，同时配合文字 |
| Warning | `#F4CC75` | 警告状态，同时配合文字 |
| Danger | `#FF8892` | 错误/危险状态，同时配合文字 |

## 字体、尺寸与间距

- 字体：标题、角色名、按钮与正文统一使用随项目分发的 LXGW WenKai（霞鹜文楷）；仅在字体资产加载失败时回退到平台原生中文 UI 字体，避免回退到古早系统字体。
- 字阶：12 / 13 / 14 / 16 / 20 / 24 / 28px。
- 正文行高：约 1.5；长文本优先换行，不用截断隐藏关键信息。
- 间距：4 / 8 / 12 / 16 / 20 / 24 / 32px。
- 圆角：8 / 12 / 18px。
- 表单输入、主按钮和图标按钮的交互区域至少为 44×44px；桌面右键菜单遵循紧凑原生密度，单项总高度至少 32px。

## 组件规则

- 每页只保留一个主按钮；返回、取消、浏览等使用次级样式。
- 输入框必须有可见标签、可访问名称和 2px 高对比焦点边框。
- 桌宠回复使用带方向性尾巴的紧凑语音气泡，只承载正文，不显示角色名、标题栏或装饰分隔线；聊天输入框保持规则操作面板形态，两者不能混用同一框体。
- 禁用态降低对比并保持不可交互；错误信息放在对应字段附近并提供恢复方向。
- 配置中心使用四个固定标签页（环境、对话、语音、屏幕识图）；缺少必要配置时显示红点，并同时给出可访问的文字原因。
- 异步检测超过 300ms 时显示状态文字或进度，进行中按钮必须禁止重复触发。
- 浮窗必须支持 Escape 关闭，并提供可见关闭操作；状态不能只靠颜色表达。

## PyQt5 映射

- 令牌来源：`meapet/ui_theme.py`。
- 向导主题：`wizard/styles.py`，页面通过 `objectName` 和动态属性获得角色样式。
- 桌面浮窗主题：`meapet/desktop/theme.py`。
- 使用原生 `QPushButton`、`QLineEdit`、`QComboBox`、`QCheckBox`、`QRadioButton`，保持键盘与辅助技术语义。
- 避免宽泛的 `QFrame { ... }` 选择器；`QLabel` 继承自 `QFrame`，宽泛边框会误伤所有文本。必须使用 `QFrame#SpecificName`。

## 验收清单

- [ ] 正文对比度至少 4.5:1，UI 图形至少 3:1。
- [ ] 所有核心路径可只用键盘完成，Tab 顺序符合视觉顺序。
- [ ] 焦点清晰可见，按钮与输入至少 44px。
- [ ] 小窗口或高 DPI 下可滚动、可缩放，不裁切主要操作。
- [ ] 加载、成功、警告、错误均有文字反馈。
- [ ] 配置保存行为与全部既有配置键保持不变。

## Page overrides

- Desktop pet: `design-system/pages/desktop.md`
- Wizard: `design-system/pages/wizard.md`
- Status copy source: `meapet/desktop/status_language.py`
