"""发布打包器的用户旅程与安全边界测试。

用户旅程：
1. 维护者双击一次即可得到可分享 ZIP 和 SHA-256 校验文件。
2. 接收者拿到运行代码、界面资源和配置模板，但拿不到维护者的密钥、
   截图、日志、数据库、缓存或虚拟环境。
3. 大型离线资源必须显式选择；Git LFS 指针永远只报告、不打包。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts import package_release
from scripts.package_release import (
    PackagingError,
    SensitiveContentError,
    build_release_archive,
    collect_release_files,
)


LFS_POINTER = (
    "version https://git-lfs.github.com/spec/v1\n"
    "oid sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
    "size 123456789\n"
)


def _write(root: Path, relative: str, content: str | bytes = "fixture") -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return relative


def _make_project(root: Path) -> list[str]:
    files = {
        ".python-version": "3.12\n",
        "pet.py": "print('pet')\n",
        "setup_wizard.py": "print('wizard')\n",
        "启动桌宠.bat": "@echo off\r\n",
        "打包发布.bat": "@echo off\r\n",
        "start.sh": "#!/usr/bin/env bash\n",
        "config.example.json": '{"llm": {"api_key": ""}}\n',
        "weight.json": "{}\n",
        "pyproject.toml": '[project]\nname = "mea-pet"\nversion = "1.2.3"\n',
        "uv.lock": "version = 1\n",
        "linux_requirements.txt": "PyQt5\n",
        "vits_requirements.txt": "torch\n",
        "README.md": "# MeaPet\n",
        "LICENSE": "MIT\n",
        "THIRD-PARTY-NOTICE.md": "notices\n",
        "meapet/__init__.py": "",
        "meapet/desktop/app.py": "class Pet: pass\n",
        "meapet/assets/fonts/font.ttf": b"font-data",
        "wizard/__init__.py": "",
        "wizard/app.py": "class Wizard: pass\n",
        "scripts/__init__.py": "",
        "scripts/package_release.py": "# packager\n",
        "sprites/mea01A_001.png": b"\x89PNG\r\nfixture",
        "live2d/model/mea_live2d/mea.model3.json": "{}\n",
        "GPT-Sovits/normal/jp_normal.wav": b"RIFFfixture",
        "vits_core/utils.py": "def load(): pass\n",
        "vits_models/finetune_speaker.json": "{}\n",
        # 默认排除、显式完整资源模式才纳入。
        "dic/open_jtalk/sys.dic": b"dictionary",
        "models/GPT_weights/local.ckpt": b"real-checkpoint",
        "vits_models/G_latest.pth": b"real-vits-weight",
        # LFS 指针即使在完整模式下也绝不能进入发布包。
        "models/SoVITS_weights/pointer.pth": LFS_POINTER,
        # 即便误进入候选清单，这些本地/开发文件仍必须被拒绝。
        "config.json": '{"api_key": "private"}\n',
        ".env": "API_KEY=private\n",
        "mea_memory.db": b"sqlite",
        "screenshots/private.png": b"private-screen",
        "audio_cache/private.wav": b"private-audio",
        "voice_cache/private.wav": b"private-voice",
        "tests/test_local.py": "def test_local(): pass\n",
        "design-system/MASTER.md": "dev docs\n",
        ".venv/Lib/site-packages/private.py": "secret = True\n",
        "meapet/__pycache__/app.pyc": b"compiled",
    }
    for relative, content in files.items():
        _write(root, relative, content)
    return sorted(files)


def test_standard_selection_contains_runtime_but_not_private_or_optional_files(
    tmp_path: Path,
) -> None:
    candidates = _make_project(tmp_path)

    selection = collect_release_files(tmp_path, candidates)

    included = set(selection.included_paths)
    assert "pet.py" in included
    assert "启动桌宠.bat" in included
    assert "打包发布.bat" in included
    assert "config.example.json" in included
    assert "meapet/assets/fonts/font.ttf" in included
    assert "sprites/mea01A_001.png" in included
    assert "live2d/model/mea_live2d/mea.model3.json" in included
    assert "GPT-Sovits/normal/jp_normal.wav" in included
    assert "vits_models/finetune_speaker.json" in included

    assert "config.json" not in included
    assert ".env" not in included
    assert "mea_memory.db" not in included
    assert "screenshots/private.png" not in included
    assert "audio_cache/private.wav" not in included
    assert "tests/test_local.py" not in included
    assert ".venv/Lib/site-packages/private.py" not in included
    assert "dic/open_jtalk/sys.dic" not in included
    assert "models/GPT_weights/local.ckpt" not in included
    assert "vits_models/G_latest.pth" not in included
    assert "models/SoVITS_weights/pointer.pth" not in included

    assert "models/SoVITS_weights/pointer.pth" in selection.lfs_pointer_paths
    assert "config.json" in selection.excluded_paths


def test_optional_asset_mode_includes_real_weights_but_never_lfs_pointers(
    tmp_path: Path,
) -> None:
    candidates = _make_project(tmp_path)

    selection = collect_release_files(
        tmp_path,
        candidates,
        include_optional_assets=True,
    )

    included = set(selection.included_paths)
    assert "dic/open_jtalk/sys.dic" in included
    assert "models/GPT_weights/local.ckpt" in included
    assert "vits_models/G_latest.pth" in included
    assert "models/SoVITS_weights/pointer.pth" not in included
    assert selection.lfs_pointer_paths == (
        "models/SoVITS_weights/pointer.pth",
    )


def test_build_release_creates_rooted_zip_manifest_guide_and_checksum(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    candidates = _make_project(project)

    result = build_release_archive(
        project,
        output,
        candidates=candidates,
        version="1.2.3",
        revision="abc1234",
        source_epoch=1_704_067_200,
    )

    assert result.zip_path.name == "MeaPet-1.2.3-abc1234.zip"
    assert result.checksum_path.name == "MeaPet-1.2.3-abc1234.zip.sha256"
    assert result.zip_path.is_file()
    expected_digest = hashlib.sha256(result.zip_path.read_bytes()).hexdigest()
    assert result.checksum_path.read_text(encoding="utf-8") == (
        f"{expected_digest}  {result.zip_path.name}\n"
    )

    with zipfile.ZipFile(result.zip_path) as archive:
        names = set(archive.namelist())
        prefix = "MeaPet-1.2.3/"
        assert f"{prefix}pet.py" in names
        assert f"{prefix}config.example.json" in names
        assert f"{prefix}快速开始.txt" in names
        assert f"{prefix}RELEASE-MANIFEST.json" in names
        assert f"{prefix}config.json" not in names
        assert f"{prefix}models/SoVITS_weights/pointer.pth" not in names
        assert all(name.startswith(prefix) for name in names)

        manifest = json.loads(
            archive.read(f"{prefix}RELEASE-MANIFEST.json").decode("utf-8")
        )
        assert manifest["schema_version"] == 1
        assert manifest["version"] == "1.2.3"
        assert manifest["revision"] == "abc1234"
        assert manifest["profile"] == "standard"
        assert manifest["file_count"] == len(result.selection.included_paths)
        assert manifest["lfs_pointers"] == [
            "models/SoVITS_weights/pointer.pth"
        ]
        assert all("config.json" != item["path"] for item in manifest["files"])

        guide = archive.read(f"{prefix}快速开始.txt").decode("utf-8")
        assert "启动桌宠.bat" in guide
        assert "config.json" in guide
        assert "不包含" in guide


def test_same_inputs_produce_byte_identical_archives(tmp_path: Path) -> None:
    project = tmp_path / "project"
    candidates = _make_project(project)

    first = build_release_archive(
        project,
        tmp_path / "first",
        candidates=candidates,
        version="1.2.3",
        revision="abc1234",
        source_epoch=1_704_067_200,
    )
    second = build_release_archive(
        project,
        tmp_path / "second",
        candidates=candidates,
        version="1.2.3",
        revision="abc1234",
        source_epoch=1_704_067_200,
    )

    assert first.zip_path.read_bytes() == second.zip_path.read_bytes()


def test_high_confidence_secret_aborts_without_creating_archive(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    candidates = _make_project(project)
    candidates.append(
        _write(project, "meapet/accidental_secret.py", "TOKEN = 'sk-" + "a" * 32 + "'\n")
    )

    with pytest.raises(SensitiveContentError, match="meapet/accidental_secret.py"):
        build_release_archive(
            project,
            output,
            candidates=candidates,
            version="1.2.3",
            revision="abc1234",
            source_epoch=1_704_067_200,
        )

    assert not list(output.glob("*.zip"))


def test_repeated_character_api_key_placeholder_is_not_a_secret(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    candidates = _make_project(project)
    candidates.append(
        _write(
            project,
            "meapet/key_placeholder.py",
            "KEY_PLACEHOLDER = 'sk-" + "x" * 32 + "'\n",
        )
    )

    result = build_release_archive(
        project,
        tmp_path / "output",
        candidates=candidates,
        version="1.2.3",
        revision="abc1234",
        source_epoch=1_704_067_200,
    )

    assert result.zip_path.is_file()


def test_candidate_path_must_not_escape_project_root(tmp_path: Path) -> None:
    _make_project(tmp_path)

    with pytest.raises(PackagingError, match="非法候选路径"):
        collect_release_files(tmp_path, ["../config.json"])


def test_missing_runtime_entrypoint_fails_fast(tmp_path: Path) -> None:
    _write(tmp_path, "config.example.json", "{}\n")

    with pytest.raises(PackagingError, match="缺少发布必需文件"):
        build_release_archive(
            tmp_path,
            tmp_path / "dist",
            candidates=["config.example.json"],
            version="1.2.3",
            revision="abc1234",
            source_epoch=1_704_067_200,
        )


def test_discover_tracked_files_reads_only_git_index(tmp_path: Path) -> None:
    tracked = _write(tmp_path, "meapet/tracked.py", "tracked = True\n")
    _write(tmp_path, "config.json", '{"api_key": "private"}\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", tracked], cwd=tmp_path, check=True)

    discovered = package_release.discover_tracked_files(tmp_path)

    assert discovered == (tracked,)


def test_discover_tracked_files_reports_git_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(package_release.subprocess, "run", missing_git)
    with pytest.raises(PackagingError, match="无法读取 Git 文件清单"):
        package_release.discover_tracked_files(tmp_path)


def test_discover_tracked_files_rejects_empty_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        package_release.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=b""),
    )
    with pytest.raises(PackagingError, match="文件清单为空"):
        package_release.discover_tracked_files(tmp_path)


def test_cli_dry_run_and_build_report_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "release"
    candidates = _make_project(project)
    monkeypatch.setattr(
        package_release,
        "discover_tracked_files",
        lambda _root: tuple(candidates),
    )

    dry_run = package_release.main(["--root", str(project), "--dry-run"])
    dry_output = capsys.readouterr().out
    assert dry_run == 0
    assert "dry-run 未生成任何文件" in dry_output
    assert "LFS 指针" in dry_output
    assert not output.exists()

    built = package_release.main(
        ["--root", str(project), "--output-dir", str(output)]
    )
    build_output = capsys.readouterr().out
    assert built == 0
    assert "打包完成" in build_output
    assert "SHA-256" in build_output
    assert len(list(output.glob("*.zip"))) == 1
    assert len(list(output.glob("*.sha256"))) == 1


def test_cli_returns_nonzero_for_packaging_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        package_release,
        "discover_tracked_files",
        lambda _root: (_ for _ in ()).throw(PackagingError("fixture failure")),
    )

    assert package_release.main(["--root", str(tmp_path)]) == 1
    assert "fixture failure" in capsys.readouterr().err


def test_cli_returns_interrupt_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        package_release,
        "discover_tracked_files",
        lambda _root: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    assert package_release.main(["--root", str(tmp_path)]) == 130
    assert "已取消打包" in capsys.readouterr().err


def test_invalid_source_date_epoch_fails_with_clear_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = _make_project(tmp_path)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "tomorrow")

    with pytest.raises(PackagingError, match="SOURCE_DATE_EPOCH 必须是整数秒"):
        build_release_archive(
            tmp_path,
            tmp_path / "dist",
            candidates=candidates,
            version="1.2.3",
            revision="abc1234",
        )


def test_symbolic_link_candidate_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("safe = True\n", encoding="utf-8")
    link = tmp_path / "meapet" / "linked.py"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("当前文件系统不允许创建符号链接")

    with pytest.raises(PackagingError, match="不能是符号链接"):
        collect_release_files(tmp_path, ["meapet/linked.py"])


def test_collection_handles_duplicate_missing_and_backup_candidates(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "pet.py", "print('pet')\n")
    _write(tmp_path, "config.local.bak", "private\n")

    selection = collect_release_files(
        tmp_path,
        [
            "pet.py",
            "pet.py",
            "meapet/missing.py",
            "config.local.bak",
            "unrelated.txt",
        ],
    )

    assert selection.included_paths == ("pet.py",)
    assert set(selection.excluded_paths) == {
        "config.local.bak",
        "meapet/missing.py",
        "unrelated.txt",
    }
