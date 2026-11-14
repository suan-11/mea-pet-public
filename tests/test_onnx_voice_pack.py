"""轻量 ONNX 语音模型取件通道（meapet/onnx_voice_pack.py）的测试。

本文件能证明什么
----------------
* 下载→钉住哈希核验→安全解压→原子换入全链在**假 opener + 内存 tar.bz2** 上
  跑通：API 资产路由、直链兜底、gh-proxy 镜像兜底、自定义直链各走一遍；
  sha256 不符 / tar 越界 / 缺 tokens / 多份 .onnx 都有独立红测试。
* 重装覆盖旧目录不留半成品；staging 目录无论成败都清理。

本文件不能证明什么
------------------
* 不证真 GitHub Release 可达（网络面由真机取件覆盖：2026-11-14 实测档案
  sha256 与钉住值逐字一致，解包 8 件与真机验收副本逐字节相同）；
* 不证模型真能合成（那要 sherpa-onnx + 真权重，走 scripts/tts_engine_probe.py）。
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from meapet import onnx_voice_pack  # noqa: E402

ARCHIVE_NAME = onnx_voice_pack.ONNX_VOICE_MODEL_NAME + ".tar.bz2"
DIRECT_URL = onnx_voice_pack.DEFAULT_ONNX_VOICE_URL
META_URL = (
    "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/tts-models"
)
ASSET_URL = "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/assets/1"
MIRROR_URL = onnx_voice_pack.GITHUB_MIRROR_PREFIX + DIRECT_URL


def make_model_tar(
    *,
    wrapper: bool = True,
    tokens: bool = True,
    extra_onnx: bool = False,
    slip: bool = False,
    symlink_escape: bool = False,
) -> bytes:
    buf = io.BytesIO()
    prefix = f"{onnx_voice_pack.ONNX_VOICE_MODEL_NAME}/" if wrapper else ""
    with tarfile.open(fileobj=buf, mode="w:bz2") as archive:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

        add("zh_CN-chaowen-medium.onnx", b"fake-onnx")
        if tokens:
            add("tokens.txt", b"_ 0\n")
        add("lexicon.txt", "ni 你\n".encode("utf-8"))
        if extra_onnx:
            add("another.onnx", b"fake-2")
        if slip:
            # 不带包装前缀才是真正的越界（带前缀时 .. 会被前缀抵消）
            info = tarfile.TarInfo("../evil.txt")
            info.size = 5
            archive.addfile(info, io.BytesIO(b"pwned"))
        if symlink_escape:
            info = tarfile.TarInfo(prefix + "escape.lnk")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../outside.txt"
            archive.addfile(info)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def make_opener(tar_bytes: bytes, *, api_route: bool = True, direct: bool = True, mirror: bool = True):
    objects = {}
    if direct:
        objects[DIRECT_URL] = tar_bytes
    if mirror:
        objects[MIRROR_URL] = tar_bytes
    if api_route:
        objects[META_URL] = json.dumps(
            {"assets": [{"name": ARCHIVE_NAME, "url": ASSET_URL}]}
        ).encode("utf-8")
        objects[ASSET_URL] = tar_bytes

    def opener(url, timeout, headers):
        if url not in objects:
            raise OSError(f"fake 404: {url}")
        return FakeResponse(objects[url])

    return opener


def install(tmp_path, tar_bytes=None, **kwargs):
    tar_bytes = make_model_tar() if tar_bytes is None else tar_bytes
    opener = kwargs.pop("opener", None) or make_opener(tar_bytes)
    expected = kwargs.pop(
        "expected_sha256", hashlib.sha256(tar_bytes).hexdigest()
    )
    return onnx_voice_pack.install_onnx_voice(
        DIRECT_URL,
        tmp_path / "models" / "voice_onnx",
        opener=opener,
        expected_sha256=expected,
        **kwargs,
    )


def model_root(tmp_path) -> Path:
    return tmp_path / "models" / "voice_onnx"


# ─── 成功路径与路由 ─────────────────────────────────────────────────────


def test_install_success_api_route(tmp_path):
    stages: list[str] = []
    tar_bytes = make_model_tar()  # 钉住同一份字节
    info = install(tmp_path, tar_bytes=tar_bytes, progress=stages.append)
    assert info["route"] == "api 资产路由"
    root = model_root(tmp_path)
    assert info["model_dir"] == str(root)
    # 官方包的包装目录被剥掉：模型文件直接落在 model_root（引擎只认这一层）
    assert (root / "zh_CN-chaowen-medium.onnx").is_file()
    assert (root / "tokens.txt").is_file()
    assert not (root / onnx_voice_pack.ONNX_VOICE_MODEL_NAME).exists()
    assert info["sha256"] == hashlib.sha256(tar_bytes).hexdigest()
    assert stages == ["下载", "核验", "解压", "就绪"]
    assert not (tmp_path / "models" / ".onnx-voice-download").exists()
    assert not root.with_name("voice_onnx.new").exists()
    assert not root.with_name("voice_onnx.old").exists()


def test_install_falls_back_to_direct(tmp_path):
    tar_bytes = make_model_tar()
    opener = make_opener(tar_bytes, api_route=False)
    info = install(tmp_path, tar_bytes=tar_bytes, opener=opener)
    assert info["route"] == "直链"


def test_install_falls_back_to_mirror(tmp_path):
    """官方双路由都失败（本机实测形状：API 限流 + 直连 connect 失败）时走镜像。"""
    tar_bytes = make_model_tar()
    opener = make_opener(tar_bytes, api_route=False, direct=False)
    info = install(tmp_path, tar_bytes=tar_bytes, opener=opener)
    assert info["route"] == "gh-proxy 镜像"
    assert (model_root(tmp_path) / "tokens.txt").is_file()


def test_install_custom_url(tmp_path):
    tar_bytes = make_model_tar()
    url = "https://mirror.example.com/models/" + ARCHIVE_NAME

    def opener(u, timeout, headers):
        assert u == url
        return FakeResponse(tar_bytes)

    info = onnx_voice_pack.install_onnx_voice(
        url,
        model_root(tmp_path),
        opener=opener,
        expected_sha256=hashlib.sha256(tar_bytes).hexdigest(),
    )
    assert info["route"] == "自定义直链"


def test_flat_archive_without_wrapper(tmp_path):
    tar_bytes = make_model_tar(wrapper=False)
    info = install(tmp_path, tar_bytes=tar_bytes)
    assert (model_root(tmp_path) / "zh_CN-chaowen-medium.onnx").is_file()
    assert info["model_dir"] == str(model_root(tmp_path))


def test_reinstall_replaces_old_dir(tmp_path):
    root = model_root(tmp_path)
    root.mkdir(parents=True)
    (root / "stale.onnx").write_bytes(b"old")
    (root / "tokens.txt").write_text("old")
    install(tmp_path)
    assert not (root / "stale.onnx").exists()
    assert (root / "zh_CN-chaowen-medium.onnx").is_file()
    assert not root.with_name("voice_onnx.old").exists()
    assert not root.with_name("voice_onnx.new").exists()


# ─── 失败路径：每一格都要响亮 ──────────────────────────────────────────


def test_sha_mismatch_rejected(tmp_path):
    tar_bytes = make_model_tar()
    with pytest.raises(onnx_voice_pack.OnnxVoicePackError, match="钉住"):
        install(tmp_path, tar_bytes=tar_bytes, expected_sha256="0" * 64)
    assert not model_root(tmp_path).exists()
    assert not (tmp_path / "models" / ".onnx-voice-download").exists()


def test_tar_slip_rejected(tmp_path):
    tar_bytes = make_model_tar(slip=True)
    with pytest.raises(onnx_voice_pack.OnnxVoicePackError, match="越出"):
        install(tmp_path, tar_bytes=tar_bytes)
    assert not (tmp_path / "evil.txt").exists()
    assert not model_root(tmp_path).exists()


def test_symlink_escape_rejected(tmp_path):
    tar_bytes = make_model_tar(symlink_escape=True)
    with pytest.raises(onnx_voice_pack.OnnxVoicePackError, match="链接"):
        install(tmp_path, tar_bytes=tar_bytes)
    assert not model_root(tmp_path).exists()


def test_missing_tokens_rejected(tmp_path):
    tar_bytes = make_model_tar(tokens=False)
    with pytest.raises(onnx_voice_pack.OnnxVoicePackError, match="tokens.txt"):
        install(tmp_path, tar_bytes=tar_bytes)
    assert not model_root(tmp_path).exists()


def test_multiple_onnx_rejected(tmp_path):
    """与引擎同判据：多份 .onnx 拒绝挑——挑错的症状是音色莫名变了。"""
    tar_bytes = make_model_tar(extra_onnx=True)
    with pytest.raises(onnx_voice_pack.OnnxVoicePackError, match="不知道该用哪个"):
        install(tmp_path, tar_bytes=tar_bytes)
    assert not model_root(tmp_path).exists()


def test_all_routes_fail_reports_both(tmp_path):
    tar_bytes = make_model_tar()
    opener = make_opener(tar_bytes, api_route=False, direct=False, mirror=False)
    with pytest.raises(onnx_voice_pack.OnnxVoicePackError, match="镜像路由"):
        install(tmp_path, tar_bytes=tar_bytes, opener=opener)
    assert not model_root(tmp_path).exists()


def test_non_tar_bz2_url_rejected(tmp_path):
    with pytest.raises(onnx_voice_pack.OnnxVoicePackError, match="tar.bz2"):
        onnx_voice_pack.install_onnx_voice(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/model.zip",
            model_root(tmp_path),
        )


def test_non_https_url_rejected(tmp_path):
    with pytest.raises(onnx_voice_pack.OnnxVoicePackError, match="https"):
        onnx_voice_pack.install_onnx_voice(
            "http://example.com/model.tar.bz2", model_root(tmp_path)
        )


# ─── 钉住的身份本身 ────────────────────────────────────────────────────


def test_pinned_identity_shape():
    assert DIRECT_URL.endswith(ARCHIVE_NAME)
    assert DIRECT_URL.startswith("https://github.com/k2-fsa/sherpa-onnx/")
    assert len(onnx_voice_pack.ONNX_VOICE_SHA256) == 64
    int(onnx_voice_pack.ONNX_VOICE_SHA256, 16)  # 必须是合法十六进制
    assert onnx_voice_pack.ONNX_VOICE_ARCHIVE_BYTES > 1_000_000
