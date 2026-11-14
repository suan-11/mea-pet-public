"""外置 VITS 运行时扩展包通道（meapet/vits_pack.py）的测试。

本文件能证明什么
----------------
* 下载→边车核验→解压→定位解释器全链在**假 opener + 内存 zip** 上跑通：
  API 资产路由与直链兜底各走一遍；sha256 不符 / 边车文件名不符 / expected
  钉住不符 / zip-slip / 缺解释器 / 缺契约件都有独立红测试。
* 重装覆盖旧包不留半成品；staging 目录无论成败都清理。

本文件不能证明什么
------------------
* 不证真 GitHub Release 可达（网络面由 fidus_channel 既有测试与真机取件覆盖）；
* 不证包内 torch 真能合成（那要真权重 + 真解释器，L3 验收）。
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from meapet import vits_pack  # noqa: E402

ZIP_NAME = "MeaPet-vits-runtime-win64-py311.zip"
DIRECT_URL = (
    "https://github.com/NekoHome-Studio/mea-pet-public/releases/download/"
    f"vits-runtime-v1/{ZIP_NAME}"
)
META_URL = (
    "https://api.github.com/repos/NekoHome-Studio/mea-pet-public/"
    "releases/tags/vits-runtime-v1"
)
ASSET_URL = "https://api.github.com/repos/NekoHome-Studio/mea-pet-public/releases/assets/1"
SIDECAR_ASSET_URL = (
    "https://api.github.com/repos/NekoHome-Studio/mea-pet-public/releases/assets/2"
)


def make_pack_zip(with_python: bool = True, slip: bool = False, drop_contract: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        if not drop_contract:
            archive.writestr("vits_core/__init__.py", "")
            archive.writestr("vits_models/G_latest.pth", b"fake-weights")
            archive.writestr("vits_models/finetune_speaker.json", "{}")
            archive.writestr("meapet/tools/vits_infer.py", "# script")
        if with_python:
            archive.writestr("python/python.exe", b"MZ-fake")
        if slip:
            archive.writestr("../evil.txt", "pwned")
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


def make_opener(zip_bytes: bytes, sidecar_bytes: bytes, *, api_route: bool = True):
    objects = {
        DIRECT_URL: zip_bytes,
        DIRECT_URL + ".sha256": sidecar_bytes,
    }
    if api_route:
        objects[META_URL] = json.dumps(
            {
                "assets": [
                    {"name": ZIP_NAME, "url": ASSET_URL},
                    {"name": ZIP_NAME + ".sha256", "url": SIDECAR_ASSET_URL},
                ]
            }
        ).encode("utf-8")
        objects[ASSET_URL] = zip_bytes
        objects[SIDECAR_ASSET_URL] = sidecar_bytes

    def opener(url, timeout, headers):
        if url not in objects:
            raise OSError(f"fake 404: {url}")
        return FakeResponse(objects[url])

    return opener


def sidecar_for(zip_bytes: bytes, name: str = ZIP_NAME) -> bytes:
    return f"{hashlib.sha256(zip_bytes).hexdigest()}  {name}".encode("utf-8")


def install(tmp_path, zip_bytes=None, sidecar_bytes=None, **kwargs):
    zip_bytes = make_pack_zip() if zip_bytes is None else zip_bytes
    sidecar_bytes = sidecar_for(zip_bytes) if sidecar_bytes is None else sidecar_bytes
    opener = kwargs.pop("opener", None) or make_opener(zip_bytes, sidecar_bytes)
    return vits_pack.install_vits_pack(
        DIRECT_URL, tmp_path / "addons", opener=opener, **kwargs
    )


def test_install_success_api_route(tmp_path):
    stages: list[str] = []
    zip_bytes = make_pack_zip()  # 钉住同一份字节：zip 条目时间戳随调用变
    info = install(tmp_path, zip_bytes=zip_bytes, progress=stages.append)
    assert info["route"] == "api 资产路由"
    assert Path(info["python_exe"]).is_file()
    pack = Path(info["pack_dir"])
    assert (pack / "vits_models" / "G_latest.pth").is_file()
    assert (pack / "meapet" / "tools" / "vits_infer.py").is_file()
    assert info["sha256"] == hashlib.sha256(zip_bytes).hexdigest()
    assert stages == ["下载", "核验", "解压", "就绪"]
    assert not (tmp_path / "addons" / ".vits-pack-download").exists()


def test_install_direct_route_fallback(tmp_path):
    zip_bytes = make_pack_zip()
    opener = make_opener(zip_bytes, sidecar_for(zip_bytes), api_route=False)
    info = install(tmp_path, zip_bytes=zip_bytes, opener=opener)
    assert info["route"] == "直链"


def test_hash_mismatch_rejected(tmp_path):
    zip_bytes = make_pack_zip()
    bad_sidecar = sidecar_for(b"different-bytes")
    with pytest.raises(vits_pack.VitsPackError, match="不符"):
        install(tmp_path, zip_bytes=zip_bytes, sidecar_bytes=bad_sidecar)
    assert not (tmp_path / "addons" / "vits-runtime").exists()


def test_expected_sha_pin_rejected(tmp_path):
    with pytest.raises(vits_pack.VitsPackError, match="钉住"):
        install(tmp_path, expected_sha256="0" * 64)


def test_sidecar_name_mismatch_rejected(tmp_path):
    zip_bytes = make_pack_zip()
    with pytest.raises(vits_pack.VitsPackError, match="边车写的是"):
        install(tmp_path, zip_bytes=zip_bytes, sidecar_bytes=sidecar_for(zip_bytes, name="other.zip"))


def test_zip_slip_rejected(tmp_path):
    zip_bytes = make_pack_zip(slip=True)
    with pytest.raises(vits_pack.VitsPackError, match="zip-slip"):
        install(tmp_path, zip_bytes=zip_bytes)
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "addons" / "evil.txt").exists()


def test_missing_python_rejected(tmp_path):
    zip_bytes = make_pack_zip(with_python=False)
    with pytest.raises(vits_pack.VitsPackError, match="解释器"):
        install(tmp_path, zip_bytes=zip_bytes)


def test_missing_contract_entry_rejected(tmp_path):
    zip_bytes = make_pack_zip(drop_contract=True)
    with pytest.raises(vits_pack.VitsPackError, match="契约件"):
        install(tmp_path, zip_bytes=zip_bytes)


def test_reinstall_replaces_old_pack(tmp_path):
    info1 = install(tmp_path)
    old_marker = Path(info1["pack_dir"]) / "old_marker.txt"
    old_marker.write_text("old", encoding="utf-8")
    info2 = install(tmp_path)
    assert info2["pack_dir"] == info1["pack_dir"]
    assert not old_marker.exists()
    assert Path(info2["python_exe"]).is_file()


def test_non_zip_url_rejected(tmp_path):
    with pytest.raises(vits_pack.VitsPackError, match="zip"):
        vits_pack.install_vits_pack(
            "https://github.com/o/r/releases/download/t/asset.tar.gz",
            tmp_path / "addons",
            opener=make_opener(b"", b""),
        )


def test_find_pack_python_layouts(tmp_path):
    root = tmp_path / "pack"
    (root / "runtime").mkdir(parents=True)
    (root / "runtime" / "python.exe").write_bytes(b"MZ")
    assert vits_pack.find_pack_python(root).endswith("python.exe")
    nested = tmp_path / "pack2" / "python-3.11.9-embed"
    nested.mkdir(parents=True)
    (nested / "python.exe").write_bytes(b"MZ")
    assert vits_pack.find_pack_python(tmp_path / "pack2").endswith("python.exe")
    assert vits_pack.find_pack_python(tmp_path / "empty") == ""
