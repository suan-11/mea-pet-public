"""外置 VITS 运行时扩展包的取件通道（P0-2，纯标准库）。

为什么不走 pip
--------------
Windows 冻结包基于 Python 3.13，而 pyopenjtalk-prebuilt 没有 cp312+ 轮子
（docs/troubleshooting.zh-CN.md 实测钉死），且冻结态 wizard/env_utils.py:pip_install()
直接返回 False。所以本地 VITS 的唯一可行形态是**独立扩展包**：便携 Python 3.11 +
torch + vits_core + 权重 + dic 的 zip，解压到 addons/ 后把 tts.vits_python 指向包内
解释器——天然绕过 3.13 撕裂，也绕开冻结态 pip。

包布局契约（与 _internal 镜像，vits_infer.py::_bootstrap 按 parents[2] 定位）::

    <pack>/python/python.exe          便携解释器（Windows）或 bin/python（POSIX）
    <pack>/vits_core/                 文本前端与推理代码
    <pack>/vits_models/G_latest.pth   权重
    <pack>/vits_models/finetune_speaker.json
    <pack>/dic/open_jtalk_dic_utf_8-1.11/   日语词典（可选，ja 需要）
    <pack>/meapet/tools/vits_infer.py 推理脚本（base 解析到包根）

取件复用 meapet/fidus_channel.py 的 GitHub Release 双路由（API 资产地址优先、
直链兜底——本机实测直连 github.com 会在 connect 阶段失败）与 .sha256 边车校验。
边车声明即身份；调用方还可通过 expected_sha256 再钉一层（fidus 三步钉法的同款）。

本模块不 import meapet.* 之外的产品模块，也不碰 Qt——向导线程直接调。
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path
from typing import Callable

from meapet import fidus_channel

# 官方包尚未发布（P0-3 Release 流水线落地后随 tag 产出）。URL 是声明不是猜测：
# 发布时资产名与 tag 必须逐字对上，配置 tts.vits_pack_url 可覆盖。
DEFAULT_VITS_PACK_TAG = "vits-runtime-v1"
DEFAULT_VITS_PACK_NAME = "MeaPet-vits-runtime-win64-py311.zip"
DEFAULT_VITS_PACK_URL = (
    "https://github.com/NekoHome-Studio/mea-pet-public/releases/download/"
    f"{DEFAULT_VITS_PACK_TAG}/{DEFAULT_VITS_PACK_NAME}"
)

# 包内必须存在的契约件（缺任何一件，这个包就不是"能出声的运行时"）。
PACK_REQUIRED_ENTRIES = (
    ("vits_core",),
    ("vits_models", "G_latest.pth"),
    ("vits_models", "finetune_speaker.json"),
    ("meapet", "tools", "vits_infer.py"),
)

_CHUNK = 1024 * 1024


class VitsPackError(RuntimeError):
    """取件、核验、解压任一步没成。调用方按"这一格没成"处理，不是"装上了"。"""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_pack(
    zip_path: Path,
    sidecar_path: Path,
    *,
    expected_sha256: str | None = None,
) -> str:
    """边车两段式 + 逐字节 sha256；expected 给定时边车声明必须等于它。"""
    declared, name = fidus_channel.parse_sidecar(sidecar_path)
    if name != zip_path.name:
        raise VitsPackError(f"边车写的是 '{name}'，取回的却是 '{zip_path.name}'")
    if expected_sha256 and declared != expected_sha256.lower().strip():
        raise VitsPackError(
            f"边车声明 '{declared}' ≠ 钉住的 '{expected_sha256}' ⇒ "
            "这不是声明的那一次构建"
        )
    actual = sha256_of(zip_path)
    if actual != declared:
        raise VitsPackError(f"落盘字节 '{actual}' 与边车声明 '{declared}' 不符")
    return actual


def safe_extract(zip_path: Path, dest_dir: Path) -> Path:
    """解压并拒绝 zip-slip：任何条目解析后必须仍在 dest_dir 内。"""
    dest_dir = Path(dest_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (dest_dir / member.filename).resolve()
            if target != dest_dir and dest_dir not in target.parents:
                raise VitsPackError(
                    f"包内条目 '{member.filename}' 试图越出解压目录（zip-slip），已拒绝"
                )
        archive.extractall(dest_dir)
    return dest_dir


def find_pack_python(pack_root: Path) -> str:
    """在包内定位便携解释器；找不到返回空串（调用方响亮报错）。"""
    pack_root = Path(pack_root)
    candidates = (
        "python.exe",
        "python/python.exe",
        "runtime/python.exe",
        "bin/python",
        "python/bin/python",
        "runtime/bin/python",
    )
    for rel in candidates:
        probe = pack_root / rel
        if probe.is_file():
            return str(probe)
    # 再扫一层：便携发行版常把解释器放在带版本号的子目录里。
    try:
        for child in sorted(pack_root.iterdir()):
            if not child.is_dir():
                continue
            for rel in ("python.exe", "bin/python", "python/bin/python"):
                probe = child / rel
                if probe.is_file():
                    return str(probe)
    except OSError:
        pass
    return ""


def validate_pack_layout(pack_root: Path) -> None:
    missing = [
        "/".join(rel)
        for rel in PACK_REQUIRED_ENTRIES
        if not (Path(pack_root).joinpath(*rel)).exists()
    ]
    if missing:
        raise VitsPackError(f"扩展包缺契约件: {missing}（布局见 meapet/vits_pack.py 文档）")


def install_vits_pack(
    url: str,
    addons_root: Path | str,
    *,
    expected_sha256: str | None = None,
    timeout: float = 1800.0,
    opener: fidus_channel.Opener = fidus_channel._urlopen,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """下载 → 核验 → 解压 → 定位解释器，返回落盘事实。

    返回 {pack_dir, python_exe, sha256, route, url}；任一步失败抛
    VitsPackError，不留半成品目录。进度回调收阶段名（下载/核验/解压/
    就绪）——字节级进度受 fidus 双路由传输形状限制，暂不提供，不谎报百分比。
    """
    addons_root = Path(addons_root)
    staging = addons_root / ".vits-pack-download"
    staging.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if not name.endswith(".zip"):
        raise VitsPackError(f"'{url}' 不像 zip 资产直链（文件名须以 .zip 结尾）")
    zip_path = staging / name
    sidecar_path = staging / (name + ".sha256")

    def report(stage: str) -> None:
        if progress is not None:
            try:
                progress(stage)
            except Exception:
                # 进度回调（UI）失败不得影响安装本身——阶段读数丢一格而已。
                pass

    try:
        report("下载")
        route = fidus_channel.fetch_release_object(
            url, zip_path, timeout=timeout, opener=opener
        )
        fidus_channel.fetch_release_object(
            url + ".sha256", sidecar_path, timeout=timeout, opener=opener
        )
        report("核验")
        sha = verify_pack(zip_path, sidecar_path, expected_sha256=expected_sha256)
        report("解压")
        final_dir = addons_root / "vits-runtime" / Path(name).stem
        fresh = final_dir.with_name(final_dir.name + ".new")
        if fresh.exists():
            shutil.rmtree(fresh)
        safe_extract(zip_path, fresh)
        validate_pack_layout(fresh)
        python_exe = find_pack_python(fresh)
        if not python_exe:
            shutil.rmtree(fresh, ignore_errors=True)
            raise VitsPackError("扩展包里没有便携 Python 解释器（布局契约见模块文档）")
        # 旧包先改名再换入，失败可回滚；不做"覆盖到一半"的解压。
        old = final_dir.with_name(final_dir.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        if final_dir.exists():
            final_dir.rename(old)
        try:
            fresh.rename(final_dir)
        except OSError:
            if old.exists():
                old.rename(final_dir)
            raise
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        # 解释器路径要在换入后按最终目录重新定位——换入前找到的是 .new
        # 暂存路径，原样返回会指向一个已经不存在的目录。
        python_exe = find_pack_python(final_dir)
        report("就绪")
        return {
            "pack_dir": str(final_dir),
            "python_exe": python_exe,
            "sha256": sha,
            "route": route,
            "url": url,
        }
    except fidus_channel.FidusChannelError as exc:
        raise VitsPackError(f"扩展包取件失败：{exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
