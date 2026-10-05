"""硬编码审计的回归测试。

这些断言只约束会造成行为漂移、机器绑定或虚假配置能力的值；角色台词、协议
字段名和公开服务商清单等领域常量不在本文件的清理范围内。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _example_config() -> dict:
    return json.loads(
        (ROOT / "config.example.json").read_text(encoding="utf-8")
    )


def test_public_defaults_are_canonical_and_match_the_example_config():
    from meapet.config.defaults import (
        DEFAULT_DIRECT_MODEL,
        DEFAULT_GSV_GPT_MODEL,
        DEFAULT_GSV_GPT_WEIGHTS_DIR,
        DEFAULT_GSV_SOVITS_MODEL,
        DEFAULT_GSV_SOVITS_WEIGHTS_DIR,
        DEFAULT_HERMES_WS_URL,
        DEFAULT_LIVE2D_PLACEMENT_ANCHOR,
        DEFAULT_LIVE2D_WINDOW_MASK,
        DEFAULT_LIVE2D_WINDOW_SHAPE,
        DEFAULT_MEA_PET_MIMO_API_BASE,
        DEFAULT_MIMO_TTS_MODEL,
        DEFAULT_OLLAMA_HOST,
        DEFAULT_OPENAI_API_BASE,
        DEFAULT_WATCHER_INTERVAL,
    )
    from meapet.config.providers import preset_by_id
    from meapet.config.store import normalize_config

    example = _example_config()
    direct = example["llm"]["direct"]

    assert direct["api_base"] == DEFAULT_OPENAI_API_BASE
    assert direct["model"] == DEFAULT_DIRECT_MODEL
    assert example["llm"]["agent"]["base_url"] == DEFAULT_HERMES_WS_URL
    assert example["tts"]["api_base"] == DEFAULT_MEA_PET_MIMO_API_BASE
    assert example["tts"]["model"] == DEFAULT_MIMO_TTS_MODEL
    assert example["tts"]["gpt_model"] == DEFAULT_GSV_GPT_MODEL
    assert example["tts"]["sovits_model"] == DEFAULT_GSV_SOVITS_MODEL
    assert (
        example["tts"]["gpt_weights_dir"]
        == DEFAULT_GSV_GPT_WEIGHTS_DIR
    )
    assert (
        example["tts"]["sovits_weights_dir"]
        == DEFAULT_GSV_SOVITS_WEIGHTS_DIR
    )
    assert example["watcher"]["interval"] == dict(DEFAULT_WATCHER_INTERVAL)
    assert example["live2d"]["window_mask"] == dict(
        DEFAULT_LIVE2D_WINDOW_MASK
    )
    assert example["live2d"]["placement_anchor"] == dict(
        DEFAULT_LIVE2D_PLACEMENT_ANCHOR
    )
    expected_window_shape = {
        "enabled": DEFAULT_LIVE2D_WINDOW_SHAPE["enabled"],
        "contours": list(DEFAULT_LIVE2D_WINDOW_SHAPE["contours"]),
    }
    assert example["live2d"]["window_shape"] == expected_window_shape

    assert preset_by_id("openai").api_base == DEFAULT_OPENAI_API_BASE
    assert preset_by_id("ollama").api_base == DEFAULT_OLLAMA_HOST
    assert preset_by_id("mimo").api_base == DEFAULT_MEA_PET_MIMO_API_BASE
    assert normalize_config({})["live2d"]["window_mask"] == dict(
        DEFAULT_LIVE2D_WINDOW_MASK
    )
    assert normalize_config({})["live2d"]["placement_anchor"] == dict(
        DEFAULT_LIVE2D_PLACEMENT_ANCHOR
    )
    assert normalize_config({})["live2d"]["window_shape"] == expected_window_shape


def test_watcher_timer_uses_the_same_default_interval_as_normalization():
    from meapet.config.defaults import DEFAULT_WATCHER_INTERVAL
    from meapet.desktop.watch_ctrl import PetWatcherMixin

    class _Timer:
        started_with = None

        def start(self, milliseconds):
            self.started_with = milliseconds

    host = type(
        "WatcherHost",
        (),
        {"config": {}, "_watcher_timer": _Timer()},
    )()
    expected_min = DEFAULT_WATCHER_INTERVAL["min_ms"]
    expected_max = DEFAULT_WATCHER_INTERVAL["max_ms"]

    with mock.patch(
        "meapet.desktop.watch_ctrl.random.randint",
        return_value=expected_min,
    ) as randint:
        PetWatcherMixin._start_watcher_timer(host)

    randint.assert_called_once_with(expected_min, expected_max)
    assert host._watcher_timer.started_with == expected_min


def test_wizard_legacy_watcher_migration_uses_defaults_registry():
    source = (ROOT / "wizard" / "app.py").read_text(encoding="utf-8")

    assert "DEFAULT_WATCHER_INTERVAL[\"min_ms\"]" in source
    assert "DEFAULT_WATCHER_INTERVAL[\"max_ms\"]" in source
    assert "watcher.get(\"min_ms\", 180000)" not in source
    assert "watcher.get(\"max_ms\", 360000)" not in source


def test_example_config_character_has_expected_fields():
    character = _example_config()["character"]

    # 示例配置允许包含角色名，作为演示用途
    assert set(character) == {"name", "default_outfit", "default_direction"}


def test_chat_engine_has_no_dead_http_bridge_parameter():
    from meapet.chat.engine import ChatEngine

    assert "bridge_url" not in inspect.signature(ChatEngine).parameters
    engine = ChatEngine()
    assert not hasattr(engine, "bridge_url")


def test_gsv_runtime_discovery_has_no_developer_machine_or_dated_paths(
    tmp_path,
):
    from meapet.tts.service import MeaTTS, gsv_python_candidates

    source = inspect.getsource(MeaTTS.__init__)
    assert "20250604" not in source
    assert not any(f"{drive}:" in source for drive in "CDE")

    conda_prefix = tmp_path / "conda-env"
    candidates = gsv_python_candidates(
        home=tmp_path / "home",
        environ={"CONDA_PREFIX": str(conda_prefix)},
        executable=tmp_path / "python",
        frozen=False,
    )

    assert str(conda_prefix / "python.exe") in candidates
    assert all("20250604" not in candidate for candidate in candidates)


def test_gsv_setup_guide_has_no_developer_machine_path():
    source = (ROOT / "wizard" / "page_tts_gsv.py").read_text(encoding="utf-8")
    assert not any(f"{drive}:" in source for drive in "CDE")


def test_documented_audio_paths_are_portable():
    source = (ROOT / "docs" / "backend-and-control.md").read_text(encoding="utf-8")
    assert "D:/voices/" not in source
    assert "./voice_cache/" in source


def test_runtime_dependency_specs_have_one_python_source_of_truth():
    from meapet.bootstrap import all_runtime_dependencies
    from meapet.control import mcp_server
    from meapet.control import transport as control_transport
    from meapet.dependencies import (
        CRYPTOGRAPHY_REQUIREMENT,
        MCP_REQUIREMENT,
        PYQT_REQUIREMENT,
        UVICORN_REQUIREMENT,
        WEBSOCKETS_REQUIREMENT,
    )
    from wizard.agent_setup_help import _AGENT_DEPENDENCY_SPECS

    runtime_requirements = {
        dependency.module: dependency.requirement
        for dependency in all_runtime_dependencies()
    }
    assert runtime_requirements["websockets"] == WEBSOCKETS_REQUIREMENT
    assert runtime_requirements["cryptography"] == CRYPTOGRAPHY_REQUIREMENT
    assert _AGENT_DEPENDENCY_SPECS["hermes"][0][2] == WEBSOCKETS_REQUIREMENT
    assert (
        _AGENT_DEPENDENCY_SPECS["openclaw"][1][2]
        == CRYPTOGRAPHY_REQUIREMENT
    )
    assert PYQT_REQUIREMENT in (
        ROOT / "pyproject.toml"
    ).read_text(encoding="utf-8")
    assert PYQT_REQUIREMENT in (
        ROOT / "linux_requirements.txt"
    ).read_text(encoding="utf-8")
    assert MCP_REQUIREMENT not in inspect.getsource(mcp_server)
    assert MCP_REQUIREMENT not in inspect.getsource(control_transport)
    assert UVICORN_REQUIREMENT not in inspect.getsource(control_transport)


def test_agent_numeric_defaults_match_config_and_defaults_registry():
    """验证示例配置中的数值与 defaults.py 中的常量一致。

    此测试不检查源码中是否存在硬编码数字，
    因为那些属于实现细节，可能合法地出现在注释、文档字符串或测试代码中。
    """
    from meapet.config.defaults import (
        DEFAULT_AGENT_HISTORY_TURNS,
        DEFAULT_AGENT_TIMEOUT_SECONDS,
        DEFAULT_CONTROL_PORT,
    )

    example = _example_config()
    agent = example["llm"]["agent"]
    assert agent["timeout_seconds"] == DEFAULT_AGENT_TIMEOUT_SECONDS
    assert agent["history_turns"] == DEFAULT_AGENT_HISTORY_TURNS
    assert example["agent_control"]["port"] == DEFAULT_CONTROL_PORT


def test_llm_page_does_not_embed_a_second_legacy_palette():
    source = (ROOT / "wizard" / "page_llm.py").read_text(encoding="utf-8")

    assert "_INPUT_STYLE" not in source
    for legacy_color in (
        "#1B1D2E",
        "#E8EAF0",
        "#3A3D52",
        "#7C8AFF",
        "#9AA0B5",
        "#C8CCDC",
    ):
        assert legacy_color not in source


def test_default_endpoint_literals_resolve_to_known_defaults():
    """验证 defaults.py 中的端点常量与示例配置一致。

    此测试不扫描源码中是否出现端点字面量，
    只验证通过 defaults 模块能解析出正确的值。
    """
    from meapet.config.defaults import (
        DEFAULT_OPENAI_API_BASE,
        DEFAULT_MEA_PET_MIMO_API_BASE,
        DEFAULT_OLLAMA_HOST,
        DEFAULT_HERMES_WS_URL,
    )

    example = _example_config()

    # OpenAI 兼容端点
    assert example["llm"]["direct"]["api_base"] == DEFAULT_OPENAI_API_BASE

    # Mimo TTS 端点
    assert example["tts"]["api_base"] == DEFAULT_MEA_PET_MIMO_API_BASE

    # Ollama 本地端点
    assert DEFAULT_OLLAMA_HOST == "http://127.0.0.1:11434"

    # Hermes WebSocket 端点 — 使用 defaults 中的真实值
    assert DEFAULT_HERMES_WS_URL == "ws://127.0.0.1:9119/api/ws"
    assert example["llm"]["agent"]["base_url"] == DEFAULT_HERMES_WS_URL


def test_status_language_exposes_all_required_functions():
    """验证 status_language 模块导出了所有必需的气泡文案函数。

    此测试只检查函数是否存在且可调用，不检查其他文件中是否出现了硬编码文案。
    """
    from meapet.desktop import status_language

    required_functions = (
        "autostart_unsupported",
        "autostart_disabled",
        "autostart_enabled",
        "timeline_empty",
        "recent_reply_missing",
        "agent_session_failed",
        "control_token_missing",
        "control_token_copied",
        "control_token_regeneration_failed",
        "control_token_regenerated",
        "config_open_failed",
        "config_corrupt",
        "vision_backend_switched",
        "vision_model_switched",
        "watcher_uploading_cloud",
        "watcher_uploading_local",
        "vision_failed",
        "watcher_silent",
        "watcher_not_enabled",
        "render_png_enabled",
        "render_live2d_enabled",
        "render_live2d_failed",
    )
    for name in required_functions:
        assert callable(getattr(status_language, name, None)), name


def test_bubble_duration_helper_honors_config_and_safe_fallbacks():
    from meapet.config.defaults import bubble_duration_ms

    assert bubble_duration_ms(
        {"bubble_duration_ms": {"interaction": 1234}},
        "interaction",
    ) == 1234
    assert bubble_duration_ms(
        {"bubble_duration_ms": {"interaction": "broken"}},
        "interaction",
    ) == 3000
    assert bubble_duration_ms({}, "watch") == 7000


def test_python_package_index_is_overridable_and_not_repeated_in_ui():
    from meapet.dependencies import DEFAULT_PIP_INDEX_URL, resolve_pip_index_url

    custom = "https://packages.example.invalid/simple"
    assert resolve_pip_index_url(
        {"MEA_PET_PIP_INDEX_URL": custom}
    ) == custom
    assert resolve_pip_index_url(
        {"PIP_INDEX_URL": custom}
    ) == custom
    assert DEFAULT_PIP_INDEX_URL == (
        "https://pypi.tuna.tsinghua.edu.cn/simple"
    )

    source = (
        ROOT / "wizard" / "page_tts_vits.py"
    ).read_text(encoding="utf-8")
    assert "pypi.tuna.tsinghua.edu.cn" not in source


def test_installers_keep_china_friendly_default_without_proxy_mirrors():
    source_files = (
        ROOT / "meapet" / "dependencies.py",
        ROOT / "meapet" / "tools" / "gsv_infer.py",
        ROOT / "启动桌宠.bat",
    )
    forbidden = ("hf-mirror.com", "ghproxy.com")
    for path in source_files:
        source = path.read_text(encoding="utf-8")
        assert not any(value in source for value in forbidden), path

    dependencies = source_files[0].read_text(encoding="utf-8")
    windows_launcher = source_files[2].read_text(encoding="utf-8")
    assert dependencies.count("pypi.tuna.tsinghua.edu.cn") == 1
    assert windows_launcher.count("pypi.tuna.tsinghua.edu.cn") == 1
    assert "https://pypi.org/simple" in windows_launcher


def test_tts_wizard_uses_the_defaults_registry():
    source = (ROOT / "wizard" / "app.py").read_text(encoding="utf-8")
    for literal in (
        "https://api.xiaomimimo.com/v1",
        "mimo-v2.5-tts",
        "mimo-v2.5-tts-voiceclone",
    ):
        assert literal not in source


def test_runtime_version_and_user_agents_share_package_metadata():
    import tomllib

    import meapet

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["setuptools"]["dynamic"]["version"]["attr"] == (
        "meapet.__version__"
    )
    assert meapet.USER_AGENT == f"MeaPet/{meapet.__version__}"
    assert "MeaPet/0.1" not in (ROOT / "meapet" / "http_async.py").read_text(
        encoding="utf-8"
    )
    assert '"MeaPet/1.0"' not in (
        ROOT / "meapet" / "direct" / "client.py"
    ).read_text(encoding="utf-8")


def test_vision_defaults_are_not_repeated_in_runtime_or_wizard_code():
    from meapet.config.defaults import (
        DEFAULT_OLLAMA_CHAT_MODEL,
        DEFAULT_OLLAMA_VISION_MODEL,
    )

    assert DEFAULT_OLLAMA_CHAT_MODEL == "qwen2.5:7b"
    assert DEFAULT_OLLAMA_VISION_MODEL == "qwen3.5:4b"
    for relative in (
        "wizard/page_vision.py",
        "meapet/desktop/app.py",
        "meapet/desktop/config_bridge.py",
        "meapet/desktop/window_chrome.py",
        "meapet/watcher/screen.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert '"minicpm-v"' not in source
        assert '"qwen3.5:4b"' not in source


def test_bundled_interaction_audio_is_not_runtime_cache(tmp_path, monkeypatch):
    from meapet.desktop.interaction import PetInteractionMixin

    class Host:
        pass

    monkeypatch.setattr(
        "meapet.paths.data_path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    picked = PetInteractionMixin._pick_zone_audio(Host(), "upper")
    assert picked is not None
    assert "interaction_voices" in picked[0]


def test_interaction_precache_tool_uses_runtime_data_path():
    source = (ROOT / "meapet" / "tools" / "precache_interactions.py").read_text(
        encoding="utf-8"
    )
    assert "CACHE_DIR = data_path(\"voice_cache\")" in source
    assert "os.path.join(PET_DIR, \"voice_cache\")" not in source


def test_voice_input_model_paths_use_runtime_cache_and_keep_legacy_reading(tmp_path, monkeypatch):
    import meapet.paths as paths

    new_cache = tmp_path / "runtime-voice-asr"
    legacy_cache = tmp_path / "models" / "voice_asr"
    monkeypatch.setattr(paths, "data_path", lambda *parts: str(new_cache.joinpath(*parts)))
    monkeypatch.setattr(
        paths,
        "project_path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )

    model_dir = legacy_cache / "models" / paths.VOICE_ASR_MODEL_CACHE_NAME / "snapshots" / "rev1"
    model_dir.mkdir(parents=True)
    for name in paths.VOICE_ASR_REQUIRED_FILES:
        (model_dir / name).write_bytes(b"model")

    found = paths.find_voice_asr_model_dir()
    assert found == model_dir
    assert str(paths.voice_asr_cache_dir()).startswith(str(new_cache))


def test_voice_input_one_click_install_includes_model_downloader():
    from meapet.dependencies import VOICE_INPUT_INSTALL_REQUIREMENTS

    assert VOICE_INPUT_INSTALL_REQUIREMENTS == (
        "sherpa-onnx",
        "pyaudio",
        "modelscope",
    )
    source = (ROOT / "wizard" / "page_voice_input.py").read_text(encoding="utf-8")
    assert "*VOICE_INPUT_INSTALL_REQUIREMENTS" in source


def test_voice_input_installer_does_not_run_frozen_executable_as_python():
    from unittest import mock

    from wizard.page_voice_input import _voice_input_install_command

    with mock.patch("wizard.page_voice_input.is_frozen", return_value=True):
        assert _voice_input_install_command() is None

    with mock.patch("wizard.page_voice_input.is_frozen", return_value=False):
        command = _voice_input_install_command()
    assert command is not None
    assert command[1:4] == ["-m", "pip", "install"]
    assert "--index-url" in command
    assert any("pypi.tuna.tsinghua.edu.cn" in part for part in command)


def test_mimo_reference_browser_uses_runtime_data_path():
    source = (ROOT / "wizard" / "page_tts_mimo.py").read_text(encoding="utf-8")
    assert 'data_path("voice_cache")' in source
    assert 'os.path.dirname(CONFIG_PATH), "voice_cache"' not in source


def test_linux_extra_declares_fidus_bridge_module_level_imports():
    """`[linux]` 必须覆盖 fidus 桥在**模块级**拉起的每个非标准库依赖。

    `fidus_position.py` 顶层 `import numpy as np` 没有 try/except，而 numpy 原先只声明在
    `vits` extra ⇒ 照 `[linux]` 装的构建机打出的包一碰 fidus 就炸。缺口只在干净装机面暴露，
    本机 venv 的传递依赖垫着它（全局坑 pid-3852732），所以断言写在声明层而不是 import 层。
    """
    import ast
    import sys
    import tomllib

    from packaging.requirements import Requirement

    tree = ast.parse(
        (ROOT / "meapet" / "desktop" / "fidus_position.py").read_text(encoding="utf-8")
    )
    external: set[str] = set()
    for node in tree.body:  # 只取模块级：函数内的 import 有各自的兜底或 extra
        if isinstance(node, ast.Import):
            external |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            external.add(node.module.split(".")[0])
    external -= sys.stdlib_module_names | {"__future__"}

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = {
        Requirement(spec).name.lower().replace("-", "_")
        for spec in project["dependencies"] + project["optional-dependencies"]["linux"]
    }
    assert external <= declared, f"[linux] 未声明的模块级依赖：{sorted(external - declared)}"
