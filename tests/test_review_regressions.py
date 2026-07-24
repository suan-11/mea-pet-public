"""项目审查问题的回归测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import asyncio
from types import SimpleNamespace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_SECRET_ENV_KEYS = {
    "MIMO_API_KEY": "",
    "XIAOMIMIMO_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
    "MEAPET_API_KEY": "",
    "OPENAI_API_KEY": "",
    "TRANSLATE_API_KEY": "",
}


class TestProviderKeyIsolation(unittest.TestCase):
    def test_mimo_tts_does_not_reuse_deepseek_llm_key(self):
        from meapet.config.store import resolve_tts_api_key

        with mock.patch.dict(os.environ, _SECRET_ENV_KEYS, clear=False):
            key = resolve_tts_api_key(
                {"api_key": ""},
                {"backend": "deepseek", "api_key": "deepseek-test-key"},
            )
        self.assertEqual(key, "")

    def test_deepseek_env_does_not_override_explicit_mimo_tts_key(self):
        from meapet.config.store import resolve_tts_api_key

        env = dict(_SECRET_ENV_KEYS)
        env["DEEPSEEK_API_KEY"] = "deepseek-env-test-key"
        with mock.patch.dict(os.environ, env, clear=False):
            key = resolve_tts_api_key({"api_key": "mimo-file-test-key"}, {})
        self.assertEqual(key, "mimo-file-test-key")

    def test_translation_does_not_reuse_mimo_llm_key(self):
        from meapet.config.store import resolve_translate_api_key

        with mock.patch.dict(os.environ, _SECRET_ENV_KEYS, clear=False):
            key = resolve_translate_api_key(
                {},
                {"backend": "mimo", "api_key": "mimo-test-key"},
            )
        self.assertEqual(key, "")

    def test_translation_never_reuses_deepseek_llm_key(self):
        from meapet.config.store import resolve_translate_api_key

        with mock.patch.dict(os.environ, _SECRET_ENV_KEYS, clear=False):
            key = resolve_translate_api_key(
                {},
                {"backend": "deepseek", "api_key": "deepseek-test-key"},
            )
        self.assertEqual(key, "")

    def test_deepseek_backend_does_not_consume_openai_key(self):
        from meapet.config.store import resolve_llm_api_key

        env = dict(_SECRET_ENV_KEYS)
        env["OPENAI_API_KEY"] = "openai-test-key"
        with mock.patch.dict(os.environ, env, clear=False):
            key = resolve_llm_api_key({"backend": "deepseek", "api_key": ""})
        self.assertEqual(key, "")

    def test_mimo_vision_does_not_reuse_deepseek_llm_key(self):
        from meapet.config.store import resolve_vision_api_key

        with mock.patch.dict(os.environ, _SECRET_ENV_KEYS, clear=False):
            key = resolve_vision_api_key(
                {"backend": "mimo", "api_key": ""},
                {"backend": "deepseek", "api_key": "deepseek-test-key"},
            )
        self.assertEqual(key, "")

    def test_mimo_vision_may_reuse_mimo_llm_key(self):
        from meapet.config.store import resolve_vision_api_key

        with mock.patch.dict(os.environ, _SECRET_ENV_KEYS, clear=False):
            key = resolve_vision_api_key(
                {"backend": "mimo", "api_key": ""},
                {"backend": "mimo", "api_key": "mimo-test-key"},
            )
        self.assertEqual(key, "mimo-test-key")

    def test_ollama_vision_does_not_reuse_cloud_llm_key(self):
        from meapet.config.store import resolve_vision_api_key

        with mock.patch.dict(os.environ, _SECRET_ENV_KEYS, clear=False):
            key = resolve_vision_api_key(
                {"backend": "ollama", "api_key": ""},
                {"backend": "mimo", "api_key": "mimo-test-key"},
            )
        self.assertEqual(key, "")

    def test_mimo_vision_does_not_reuse_deepseek_api_base(self):
        from meapet.config.store import resolve_vision_api_base

        base = resolve_vision_api_base(
            {"backend": "mimo", "api_base": ""},
            {"backend": "deepseek", "api_base": "https://api.deepseek.example/v1"},
        )
        self.assertEqual(base, "https://api.xiaomimimo.com/v1")


class TestRuntimeConfigurationSwitch(unittest.TestCase):
    def test_saved_configuration_cancels_old_generation_and_rebuilds_one_backend(self):
        from meapet.desktop.config_bridge import PetConfigBridgeMixin

        class Timer:
            def __init__(self, name, events):
                self.name = name
                self.events = events

            def stop(self):
                self.events.append(f"stop:{self.name}")

        class Worker:
            def __init__(self, events):
                self.events = events
                self.wait_calls = 0

            def terminate(self):
                self.events.append("terminate:worker")

            def wait(self, _timeout):
                self.wait_calls += 1
                self.events.append("wait:worker")
                return True

            def deleteLater(self):
                self.events.append("delete:worker")

        class Host(PetConfigBridgeMixin):
            def __init__(self):
                self.config = {"llm": {"mode": "direct"}}
                self.events = []
                self._chat_worker = Worker(self.events)
                self._chat_poll = Timer("poll", self.events)
                self._chat_timeout = Timer("timeout", self.events)

            def _invalidate_active_conversation(self):
                self.events.append("invalidate")

            def _stop_control(self):
                self.events.append("stop:control")

            def _disconnect_watcher_signals(self):
                self.events.append("stop:watcher")

            def _apply_motion_preference(self):
                self.events.append("motion")

            def _init_tts(self):
                self.events.append("init:tts")

            def _init_chat(self):
                self.events.append("init:chat")

            def _init_watcher(self):
                self.events.append("init:watcher")

            def _init_control(self):
                self.events.append("init:control")

            def _show_bubble(self, text, duration, mood=None):
                self.events.append((text, duration, mood))

        host = Host()
        worker = host._chat_worker
        applied = host._apply_runtime_config(
            {
                "llm": {
                    "mode": "agent",
                    "agent": {
                        "kind": "hermes",
                        "base_url": "http://127.0.0.1:8642",
                    },
                }
            }
        )

        self.assertTrue(applied)
        self.assertEqual(host.config["llm"]["mode"], "agent")
        self.assertLess(host.events.index("invalidate"), host.events.index("init:chat"))
        self.assertEqual(
            [event for event in host.events if event in {"init:chat", "init:control"}],
            ["init:chat", "init:control"],
        )
        self.assertIn(("新配置已应用。", 3500, None), host.events)
        # GUI must not join the old worker Future (would freeze the event loop).
        self.assertEqual(worker.wait_calls, 0)
        self.assertNotIn("wait:worker", host.events)
        self.assertIn("terminate:worker", host.events)
        self.assertIn("delete:worker", host.events)

    def test_unsupported_follow_backend_falls_back_to_local_vision(self):
        from meapet.config.store import (
            resolve_vision_backend,
            resolve_vision_host,
        )

        vision = {"backend": "", "host": ""}
        llm = {
            "backend": "deepseek",
            "host": "https://api.deepseek.example",
        }
        self.assertEqual(resolve_vision_backend(vision, llm), "ollama")
        self.assertEqual(resolve_vision_host(vision, llm), "http://127.0.0.1:11434")


class TestModelArtifactValidation(unittest.TestCase):
    @staticmethod
    def _write_lfs_pointer(path: Path) -> None:
        path.write_text(
            "version https://git-lfs.github.com/spec/v1\n"
            "oid sha256:" + "a" * 64 + "\n"
            "size 123456789\n",
            encoding="utf-8",
        )

    def _make_gsv_health_stub(self, root: Path, *, pointer: bool):
        from meapet.tts.service import MeaTTS

        gpt = root / "gpt.ckpt"
        sovits = root / "sovits.pth"
        if pointer:
            self._write_lfs_pointer(gpt)
        else:
            gpt.write_bytes(b"real-model-placeholder")
        sovits.write_bytes(b"real-model-placeholder")
        infer_script = root / "infer.py"
        infer_script.write_text("# test\n", encoding="utf-8")
        refs = root / "refs"
        for name in ("normal", "soft", "clam"):
            (refs / name).mkdir(parents=True, exist_ok=True)

        tts = MeaTTS.__new__(MeaTTS)
        tts._mimo_mode = False
        tts._vits_mode = False
        tts.gpt_path = str(gpt)
        tts.sovits_path = str(sovits)
        tts.python_exe = sys.executable
        tts.infer_script = str(infer_script)
        tts.ref_dir = str(refs)
        tts._deps_attempted = False
        tts._deps_ready = False
        tts.config = {}
        return tts, gpt

    def test_lfs_pointer_is_detected_without_downloading(self):
        from meapet.tts.common import is_git_lfs_pointer

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "model.pth"
            self._write_lfs_pointer(path)
            with mock.patch("subprocess.run") as run:
                self.assertTrue(is_git_lfs_pointer(str(path)))
            run.assert_not_called()

    def test_gsv_health_rejects_lfs_pointer(self):
        with tempfile.TemporaryDirectory() as td:
            tts, _ = self._make_gsv_health_stub(Path(td), pointer=True)
            with mock.patch("meapet.tts.service.log.warning"), mock.patch(
                "meapet.tts.service.auto_install_gsv_deps", return_value=True
            ) as install:
                self.assertFalse(tts.health_check())
            install.assert_not_called()

    def test_gsv_health_requires_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            tts, _ = self._make_gsv_health_stub(Path(td), pointer=False)
            with mock.patch("meapet.tts.service.log.warning"), mock.patch(
                "meapet.tts.service.auto_install_gsv_deps", return_value=False
            ):
                self.assertFalse(tts.health_check())
            self.assertFalse(tts._deps_ready)


class TestConfigSafety(unittest.TestCase):
    def test_startup_config_path_is_independent_of_cwd(self):
        from meapet.config.store import resolve_startup_config_path

        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as cwd:
            project_path = Path(project)
            config = project_path / "config.json"
            example = project_path / "config.example.json"
            config.write_text("{}", encoding="utf-8")
            example.write_text("{}", encoding="utf-8")
            old_cwd = os.getcwd()
            try:
                os.chdir(cwd)
                self.assertEqual(
                    resolve_startup_config_path(project_path),
                    str(config),
                )
            finally:
                os.chdir(old_cwd)

    def test_startup_config_falls_back_to_project_example(self):
        from meapet.config.store import resolve_startup_config_path

        with tempfile.TemporaryDirectory() as project:
            project_path = Path(project)
            example = project_path / "config.example.json"
            example.write_text("{}", encoding="utf-8")
            self.assertEqual(
                resolve_startup_config_path(project_path),
                str(example),
            )

    def test_atomic_save_preserves_all_key_values(self):
        from meapet.config.store import save_json

        data = {
            "llm": {"api_key": "llm-existing-test-key"},
            "vision": {"api_key": "vision-existing-test-key"},
            "tts": {
                "api_key": "tts-existing-test-key",
                "translate_api_key": "translate-existing-test-key",
            },
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            original_replace = os.replace
            with mock.patch(
                "meapet.config.store.os.replace", wraps=original_replace
            ) as replace:
                save_json(str(path), data)
            self.assertTrue(replace.called)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), data)
            self.assertEqual(list(Path(td).iterdir()), [path])

    def test_normalize_and_save_preserve_existing_fields_and_keys(self):
        from meapet.config.store import normalize_config, save_config

        data = {
            "llm": {"backend": "deepseek", "api_key": "existing-test-key"},
            "watcher": {
                "enabled": False,
                "custom_field": "keep-me",
                "interval": {
                    "min_ms": 1234,
                    "max_ms": 5678,
                    "custom_interval_field": "keep-me-too",
                },
            },
            "watcher_interval": {"legacy_field": "keep-legacy"},
            "custom_top_level": {"nested": True},
        }
        normalized = normalize_config(data)
        self.assertEqual(normalized["llm"]["api_key"], "existing-test-key")
        self.assertEqual(normalized["watcher"]["custom_field"], "keep-me")
        self.assertEqual(
            normalized["watcher"]["interval"]["custom_interval_field"],
            "keep-me-too",
        )
        self.assertEqual(
            normalized["watcher_interval"]["legacy_field"],
            "keep-legacy",
        )
        self.assertEqual(normalized["custom_top_level"], {"nested": True})

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            save_config(data, str(path))
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["api_key"], "existing-test-key")
        self.assertEqual(saved["watcher"]["custom_field"], "keep-me")
        self.assertIn("watcher_interval", saved)

    def test_failed_startup_closes_keepalive_and_quits(self):
        from meapet.desktop.app import _abort_failed_startup

        app = mock.Mock()
        keepalive = mock.Mock()
        splash = mock.Mock()
        _abort_failed_startup(app, keepalive, splash)
        keepalive.close.assert_called_once_with()
        splash.close.assert_called_once_with()
        app.quit.assert_called_once_with()

    def test_resolve_resource_path_is_independent_of_cwd(self):
        from meapet.config.store import resolve_resource_path

        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as cwd:
            project_path = Path(project)
            model = project_path / "live2d" / "model" / "mea"
            model.mkdir(parents=True)
            (model / "mea.model3.json").write_text("{}", encoding="utf-8")
            old_cwd = os.getcwd()
            try:
                os.chdir(cwd)
                resolved = resolve_resource_path(
                    "./live2d/model/mea",
                    root=project_path,
                )
                self.assertTrue(os.path.isdir(resolved))
                self.assertEqual(
                    Path(resolved).resolve(),
                    model.resolve(),
                )
            finally:
                os.chdir(old_cwd)

    def test_writable_config_path_maps_example_to_config_json(self):
        from meapet.config.store import resolve_writable_config_path

        with tempfile.TemporaryDirectory() as project:
            project_path = Path(project)
            example = project_path / "config.example.json"
            self.assertEqual(
                resolve_writable_config_path(str(example), root=project_path),
                str(project_path / "config.json"),
            )
            self.assertEqual(
                resolve_writable_config_path(None, root=project_path),
                str(project_path / "config.json"),
            )

    def test_save_config_merges_with_existing_disk_fields(self):
        from meapet.config.store import save_config

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "llm": {
                            "backend": "ollama",
                            "api_key": "disk-key",
                            "model": "keep-me",
                        },
                        "custom_top": {"nested": True},
                        "display": {"size_factor": 2.0},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            save_config(
                {
                    "llm": {"backend": "deepseek", "api_key": "new-key"},
                    "display": {"size_factor": 1.25},
                },
                str(path),
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["llm"]["backend"], "deepseek")
            self.assertEqual(saved["llm"]["api_key"], "new-key")
            self.assertEqual(saved["llm"]["model"], "keep-me")
            self.assertEqual(saved["custom_top"], {"nested": True})
            self.assertEqual(saved["display"]["size_factor"], 1.25)

    def test_config_bridge_saves_to_remembered_writable_path(self):
        from meapet.desktop.config_bridge import PetConfigBridgeMixin

        class Host(PetConfigBridgeMixin):
            pass

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            example = project / "config.example.json"
            example.write_text(
                json.dumps(
                    {
                        "llm": {"backend": "ollama", "api_key": "from-example"},
                        "live2d": {
                            "enabled": True,
                            "model_dir": "./live2d/model/mea",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            host = Host()
            host.config = host._load_config(str(example))
            host.config.setdefault("ui", {})["first_run_hint_shown"] = True
            host._save_config()

            written = project / "config.json"
            self.assertTrue(written.is_file())
            saved = json.loads(written.read_text(encoding="utf-8"))
            self.assertTrue(saved["ui"]["first_run_hint_shown"])
            self.assertEqual(saved["llm"]["api_key"], "from-example")
            # example template must not be rewritten
            example_data = json.loads(example.read_text(encoding="utf-8"))
            self.assertNotIn("ui", example_data)


class TestRepositoryIgnoreRules(unittest.TestCase):
    def test_example_config_has_unique_keys_and_timeline_default(self):
        duplicates = []

        def reject_duplicate_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    duplicates.append(key)
                result[key] = value
            return result

        example = json.loads(
            (ROOT / "config.example.json").read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )

        self.assertEqual(duplicates, [])
        self.assertEqual(example["ui"]["timeline_turns"], 5)

    def test_sensitive_runtime_artifacts_are_ignored(self):
        patterns = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for expected in (
            "config*.bak*",
            "*.db-wal",
            "*.db-shm",
            "audio_cache/",
            ".coverage*",
            ".pytest_cache/",
            ".mypy_cache/",
            ".ruff_cache/",
            "*.log.*",
        ):
            self.assertIn(expected, patterns)

    def test_setuptools_discovers_runtime_subpackages(self):
        import tomllib

        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        setuptools = project["tool"]["setuptools"]
        package_find = setuptools["packages"]["find"]

        self.assertIn("meapet*", package_find["include"])
        self.assertIn("wizard*", package_find["include"])


class TestInstallerReliability(unittest.TestCase):
    def test_pip_install_checks_process_return_code(self):
        from wizard.env_utils import pip_install

        with mock.patch(
            "wizard.env_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=1),
        ):
            self.assertFalse(pip_install(["example-package"]))
        with mock.patch(
            "wizard.env_utils.subprocess.run",
            return_value=SimpleNamespace(returncode=0),
        ):
            self.assertTrue(pip_install(["example-package"]))

    def test_download_rejects_plain_http(self):
        from wizard.env_utils import download_file

        with tempfile.TemporaryDirectory() as td, mock.patch(
            "wizard.env_utils.urllib.request.urlopen"
        ) as urlopen:
            result = download_file(
                "http://example.invalid/tool.exe",
                str(Path(td) / "tool.exe"),
            )
        self.assertFalse(result)
        urlopen.assert_not_called()

    def test_failed_download_keeps_existing_destination(self):
        from wizard.env_utils import download_file

        class BrokenResponse:
            headers = {"Content-Length": "100"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                if not hasattr(self, "_read_once"):
                    self._read_once = True
                    return b"partial"
                raise OSError("connection lost")

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "tool.exe"
            dest.write_bytes(b"existing-good-file")
            with mock.patch(
                "wizard.env_utils.urllib.request.urlopen",
                return_value=BrokenResponse(),
            ):
                self.assertFalse(
                    download_file("https://example.invalid/tool.exe", str(dest))
                )
            self.assertEqual(dest.read_bytes(), b"existing-good-file")
            self.assertEqual(list(Path(td).iterdir()), [dest])

    def test_truncated_download_keeps_existing_destination(self):
        from wizard.env_utils import download_file

        class TruncatedResponse:
            headers = {"Content-Length": "100"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                if not hasattr(self, "_read_once"):
                    self._read_once = True
                    return b"short"
                return b""

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "tool.exe"
            dest.write_bytes(b"existing-good-file")
            with mock.patch(
                "wizard.env_utils.urllib.request.urlopen",
                return_value=TruncatedResponse(),
            ):
                self.assertFalse(
                    download_file("https://example.invalid/tool.exe", str(dest))
                )
            self.assertEqual(dest.read_bytes(), b"existing-good-file")
            self.assertEqual(list(Path(td).iterdir()), [dest])

    def test_download_rejects_redirect_downgrade_to_http(self):
        from wizard.env_utils import download_file

        class DowngradedResponse:
            headers = {"Content-Length": "0"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "http://mirror.example.invalid/tool.exe"

            def read(self, _size):
                return b""

        with tempfile.TemporaryDirectory() as td, mock.patch(
            "wizard.env_utils.urllib.request.urlopen",
            return_value=DowngradedResponse(),
        ):
            dest = Path(td) / "tool.exe"
            self.assertFalse(
                download_file("https://example.invalid/tool.exe", str(dest))
            )
            self.assertFalse(dest.exists())

    def test_skipped_platform_install_restores_ui_state(self):
        from PyQt5.QtWidgets import QMessageBox
        from wizard.page_env import EnvCheckPage

        fake_page = SimpleNamespace(
            _set_installing=mock.Mock(),
            log=mock.Mock(),
        )
        platform = {"is_windows": False, "os_label": "Linux", "display": "Linux"}
        with mock.patch(
            "wizard.page_env.styled_message_box",
            return_value=QMessageBox.Yes,
        ), mock.patch("wizard.page_env.PLATFORM", platform):
            EnvCheckPage.install_package(fake_page, "pywin32")
        self.assertEqual(
            fake_page._set_installing.call_args_list,
            [mock.call(True), mock.call(False)],
        )


class TestWatcherPrivacyAndLifecycle(unittest.TestCase):
    def test_loopback_url_classification(self):
        from meapet.utils import is_loopback_url

        for url in (
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "http://127.9.8.7:11434",
            "http://[::1]:11434",
        ):
            self.assertTrue(is_loopback_url(url), msg=url)
        for url in (
            "https://ollama.example.com",
            "http://192.168.1.10:11434",
            "not-a-url",
            "",
        ):
            self.assertFalse(is_loopback_url(url), msg=url)

    def test_remote_ollama_is_treated_as_cloud(self):
        from meapet.desktop.watch_ctrl import PetWatcherMixin

        class WatcherConfig(PetWatcherMixin):
            pass

        pet = WatcherConfig()
        pet.config = {
            "vision": {
                "backend": "ollama",
                "host": "https://ollama.example.com",
            },
            "llm": {"backend": "ollama", "host": "http://127.0.0.1:11434"},
        }
        self.assertTrue(pet._is_cloud_vision())
        pet.config["vision"]["host"] = "http://localhost:11434"
        self.assertFalse(pet._is_cloud_vision())

    def test_watcher_can_be_prepared_after_stop(self):
        from meapet.watcher.screen import ScreenWatcher

        watcher = ScreenWatcher()
        # Default stop is non-blocking (no QThread.wait) so GUI callers stay responsive.
        self.assertTrue(watcher.stop())
        self.assertTrue(watcher._stop)
        self.assertTrue(watcher.prepare_start())
        self.assertFalse(watcher._stop)

    def test_memory_defers_embedding_cache_until_search(self):
        """MeaMemory() must not preload every embedding on the GUI startup path."""
        import tempfile
        from pathlib import Path
        from unittest import mock

        from meapet import memory as memory_pkg
        from meapet.memory import db as memory_db

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "mea_memory.db")
            with mock.patch.object(memory_db, "DB_PATH", db_path), mock.patch.object(
                memory_pkg.db, "DB_PATH", db_path
            ):
                mem = memory_db.MeaMemory()
                self.assertFalse(mem._emb_cache_loaded)
                mem.add_memory("主人喜欢红茶", importance=3)
                self.assertFalse(mem._emb_cache_loaded)
                hits = mem.search_memories("红茶", limit=3)
                self.assertTrue(mem._emb_cache_loaded)
                self.assertTrue(any("红茶" in (h.get("content") or "") for h in hits))
                mem.close()

    def test_watch_output_parser_has_no_backend_probe(self):
        from meapet.chat.engine import ChatEngine
        from meapet.watcher.screen import parse_watch_output

        with mock.patch.object(ChatEngine, "_deferred_check") as probe:
            parsed = parse_watch_output("说\n[happy]你好喵\nこんにちはにゃ")
        self.assertEqual(parsed[:4], (True, "你好喵", "こんにちはにゃ", "happy"))
        probe.assert_not_called()


class TestPrivacySafeLogging(unittest.TestCase):
    def test_key_value_secrets_are_redacted_even_without_sk_prefix(self):
        from meapet.utils import redact_text

        for raw, secret in (
            ("api_key=plain-provider-key-123456", "plain-provider-key-123456"),
            ("password: correct-horse-battery-staple", "correct-horse-battery-staple"),
            ("token=session-token-abcdef123456", "session-token-abcdef123456"),
        ):
            redacted = redact_text(raw)
            self.assertNotIn(secret, redacted)

    def test_chat_debug_dump_is_opt_in(self):
        from meapet.chat.engine import ChatEngine

        engine = ChatEngine.__new__(ChatEngine)
        marker = "private-conversation-marker"
        with mock.patch.dict(os.environ, {"MEAPET_DEBUG": ""}, clear=False), mock.patch(
            "meapet.chat.engine._safe_print"
        ) as printer:
            engine._debug_dump("request", {"messages": [marker]})
        printer.assert_not_called()

        with mock.patch.dict(os.environ, {"MEAPET_DEBUG": "1"}, clear=False), mock.patch(
            "meapet.chat.engine._safe_print"
        ) as printer:
            engine._debug_dump("request", {"messages": [marker]})
        rendered = " ".join(str(call) for call in printer.call_args_list)
        self.assertIn(marker, rendered)

    def test_deepseek_error_body_is_hidden_without_debug(self):
        from meapet.chat.engine import ChatEngine

        engine = ChatEngine.__new__(ChatEngine)
        engine.model = "test-model"
        engine.api_base = "https://api.example.invalid"
        engine.api_key = "test-key"
        engine.temperature = 0.1
        engine._post_json = mock.AsyncMock(
            return_value=SimpleNamespace(
                status_code=500,
                text="private-response-body-marker",
            )
        )
        engine._fallback_reply = mock.Mock(return_value="fallback")
        with mock.patch.dict(os.environ, {"MEAPET_DEBUG": ""}, clear=False), mock.patch(
            "meapet.chat.engine._safe_print"
        ) as printer:
            result = asyncio.run(
                engine._chat_deepseek_async([{"role": "user", "content": "hello"}])
            )
        self.assertEqual(result, "fallback")
        rendered = " ".join(str(call) for call in printer.call_args_list)
        self.assertNotIn("private-response-body-marker", rendered)

    def test_chat_flow_logs_only_input_length_by_default(self):
        from meapet.desktop.chat_flow import PetChatFlowMixin

        marker = "private-user-input-marker"
        fake_pet = SimpleNamespace(
            _record_interaction=mock.Mock(),
            _show_bubble=mock.Mock(),
            _position_bubble=mock.Mock(),
            _do_chat=mock.Mock(),
        )
        with mock.patch.dict(os.environ, {"MEAPET_DEBUG": ""}, clear=False), mock.patch(
            "meapet.desktop.chat_flow.safe_print"
        ) as printer, mock.patch("meapet.desktop.chat_flow.QTimer.singleShot"):
            PetChatFlowMixin._on_input_submit(fake_pet, marker)
        rendered = " ".join(str(call) for call in printer.call_args_list)
        self.assertNotIn(marker, rendered)
        self.assertIn(str(len(marker)), rendered)

    def test_chat_flow_logs_only_input_length_by_default(self):
        from meapet.desktop.chat_flow import PetChatFlowMixin

        marker = "private-user-input-marker"
        fake_pet = SimpleNamespace(
            _record_interaction=mock.Mock(),
            _show_bubble=mock.Mock(),
            _position_bubble=mock.Mock(),
            _do_chat=mock.Mock(),
        )
        with mock.patch.dict(os.environ, {"MEAPET_DEBUG": ""}, clear=False), mock.patch(
                "meapet.desktop.chat_flow.log.debug"
        ) as mock_debug, mock.patch("meapet.desktop.chat_flow.QTimer.singleShot"):
            PetChatFlowMixin._on_input_submit(fake_pet, marker)

        # 验证日志只记录了长度，没有暴露私密文本
        mock_debug.assert_called_once()
        args, _ = mock_debug.call_args
        self.assertIn("chars=", args[0])  # 包含长度信息
        self.assertNotIn(marker, args[0])  # 不包含原始私密内容


class TestTtsOutputPaths(unittest.TestCase):
    def test_audio_cache_key_is_deterministic_and_does_not_expose_text(self):
        from meapet.utils import audio_cache_key

        text = "../../private conversation / 密码内容"
        first = audio_cache_key(text)
        second = audio_cache_key(text)
        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertNotIn("private", first)
        self.assertNotIn("密码", first)
        self.assertNotIn("/", first)
        self.assertNotIn("..", first)

    def test_output_paths_are_unique_even_when_created_immediately(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS.__new__(MeaTTS)
            tts.output_dir = td
            paths = {tts._new_output_wav_path() for _ in range(100)}
        self.assertEqual(len(paths), 100)
        self.assertTrue(all(Path(path).parent == Path(td) for path in paths))


class TestUnsupportedBackends(unittest.TestCase):
    def test_openclaw_is_not_advertised_as_available(self):
        from meapet.chat.engine import ChatEngine

        with mock.patch("meapet.chat.engine._safe_print") as printer:
            engine = ChatEngine(backend="openclaw")
        self.assertFalse(engine.available)
        self.assertTrue(engine._backend_ready)
        rendered = " ".join(str(call) for call in printer.call_args_list)
        self.assertIn("未实现", rendered)


if __name__ == "__main__":
    unittest.main()
