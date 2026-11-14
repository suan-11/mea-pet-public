# Third-Party Notices

本项目（MeaPet — 梅尔桌宠）使用了以下开源项目的代码、资源或设计参考。
各项目版权归其各自所有者所有，并遵循对应的许可证条款。

---

## 一、包含或修改了代码的开源项目

### VITS

- **仓库**: https://github.com/jaywalnut310/vits
- **许可证**: MIT License
- **使用方式**: `vits_core/` 目录中的模型核心代码基于 VITS 的 MIT 许可实现修改而来
- **版权声明**: Copyright (c) 2021 Jaehyeon Kim

### VITS-fast-fine-tuning

- **仓库**: https://github.com/Plachtaa/VITS-fast-fine-tuning
- **许可证**: Apache License 2.0
- **使用方式**: 模型结构、推理逻辑参考自 VITS-fast-fine-tuning；`vits_core/` 包含其派生代码
- **版权声明**: Copyright (c) 2023 Plachta

### Keith Ito — tacotron text frontend (cmudict)

- **仓库**: https://github.com/keithito/tacotron
- **许可证**: MIT License
- **使用方式**: `vits_core/text/` 中的文本前端代码（音素转换、符号表）源自 Keith Ito 的实现
- **版权声明**: Copyright (c) 2017 Keith Ito
- **许可证全文**: 见 `vits_core/text/LICENSE`

---

## 二、通过子进程或库接口调用的开源项目

### GPT-SoVITS

- **仓库**: https://github.com/RVC-Boss/GPT-SoVITS
- **许可证**: MIT License
- **使用方式**: `gsv_infer.py` 通过子进程调用 GPT-SoVITS 整合包的 TTS 流水线
- **版权声明**: Copyright (c) 2024 RVC-Boss

### GPT-SoVITS-CPUFast

- **仓库**: https://github.com/baicai-1145/GPT-SoVITS-CPUFast
- **许可证**: MIT License
- **使用方式**: GPT-SoVITS 推理加速引擎，作为独立仓库被参考/调用
- **版权声明**: Copyright (c) 2024 baicai-1145

### Live2D Cubism SDK (Core)

- **仓库**: https://www.live2d.com/
- **许可证**: Live2D 专有软件许可协议
- **使用方式**: `live2d_widget.py` 通过 `live2d-py` （一个 Live2D Cubism C API 的 Python 绑定）加载并渲染 Live2D 模型
- **注意**: 使用 Live2D Cubism Core 需遵守 Live2D Inc. 的 [软件许可协议](https://www.live2d.com/legal/license/)。`live2d-py` 为独立维护的第三方绑定库
- **版权声明**: Copyright (c) Live2D Inc.

### Ollama

- **仓库**: https://github.com/ollama/ollama
- **许可证**: MIT License
- **使用方式**: `chat.py` 通过 REST API 调用 Ollama 本地 LLM 服务
- **版权声明**: Copyright (c) 2024 Ollama

### Sakura

- **仓库**: https://github.com/Rvosy/sakura
- **许可证**: Apache License 2.0
- **使用方式**: `watcher.py` 中的主动搭话三层决策 prompt 架构参考了 Sakura 的设计思路（仅参考，无直接代码包含）
- **版权声明**: Copyright (c) 2024 Rvosy

---

## 三、随项目分发的字体资源

### LXGW WenKai（霞鹜文楷）

- **仓库**: https://github.com/lxgw/LxgwWenKai
- **版本**: v1.522
- **许可证**: SIL Open Font License 1.1
- **使用方式**: `meapet/assets/fonts/LXGWWenKai-Regular.ttf` 作为桌宠及配置界面的全局中文字体
- **版权声明**: Copyright 2021–2026 LXGW；Copyright 2020 The Klee Project Authors
- **许可证全文**: 见 `meapet/assets/fonts/OFL-LXGWWenKai.txt`

---

## 四、按需下载的语音模型（不随包分发）

### AISHELL-3 / vits-icefall-zh-aishell3

- **数据集**: https://www.openslr.org/93/ （AISHELL-3 多说话人普通话 TTS 语料库）
- **模型**: https://github.com/k2-fsa/sherpa-onnx （tts-models Release 资产 `vits-icefall-zh-aishell3`）
- **许可证**: Apache License 2.0（数据集与 sherpa-onnx 模型）
- **使用方式**: ONNX 轻量离线兜底引擎的默认嗓音，由配置向导按需下载至 `models/voice_onnx/`，不随代码仓库与安装包分发；174 个说话人，多数女声
- **版权声明**: Copyright AISHELL Foundation；模型由 k2-fsa/sherpa-onnx 团队训练发布

---

## 五、运行时 Python 包依赖

以下为项目运行时通过 pip 安装的 Python 包依赖（仅列出核心依赖，完整清单见各 `requirements.txt`）。各包遵循其各自的许可证条款：

| 包名 | 许可证 |
|------|--------|
| PyQt5 | GPL v3 / Commercial |
| live2d-py | MIT |
| PyTorch / torchaudio | BSD-style |
| requests | Apache 2.0 |
| Pillow | Historical (HPND) |
| numpy | BSD 3-Clause |
| soundfile | BSD 2-Clause |
| scipy | BSD 3-Clause |
| librosa | ISC |
| PyOpenGL | BSD-style |
| transformers | Apache 2.0 |
| tokenizers | Apache 2.0 |
| huggingface_hub | Apache 2.0 |
| PyYAML | MIT |
| tqdm | MIT / MPL 2.0 |
| sounddevice | MIT |
| pywin32 | PSF |
| setuptools | MIT |
| Cython | Apache 2.0 |
| pyopenjtalk-prebuilt | MIT |
| unidecode | GPL 2+ |
| jieba | MIT |
| cn2an | MIT |
| opencc | Apache 2.0 |
| pypinyin | MIT |
| scipy | BSD 3-Clause |
| av | BSD 3-Clause / LGPL |
| gradio | Apache 2.0 |
| pydantic | MIT |

---

## 六、致谢

- **DeepSeek** — AI 对话 API 服务 (https://deepseek.com/)
- **CMU Pronouncing Dictionary** — 英语音素词典，`vits_core/text/` 使用其数据 (BSD-like)
- **清华 TUNA 镜像站** — 为国内用户提供 Python 包和模型下载加速

---

*本文件部分信息由 AI 辅助整理。如发现许可证信息不准确，欢迎提交 Issue 或 PR 修正。*
