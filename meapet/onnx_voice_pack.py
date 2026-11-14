"""轻量 ONNX 语音模型的取件通道（P1-4 向导收尾，纯标准库）。

为什么单独有一条通道
--------------------
ONNX 引擎（meapet/tts/engines/onnx_local.py）只认盘上的文件，而模型不进版本
控制（.gitignore）。core 包要「开箱离线发声」，最后一公里就是把那 14 MB 的
官方包取回来。本模块是向导的取件侧：下载 → 钉住 sha256 核验 → 安全解压 →
原子换入，与 meapet/vits_pack.py 同形（向导线程直接调，不碰 Qt）。

身份与路由
----------
上游（k2-fsa/sherpa-onnx 的 tts-models tag）不为这只资产发布 .sha256 边车，
所以身份直接钉档案的 sha256（fidus expected_sha256 的同款）：**身份由哈希
把关，不由路由保证**。路由按序尝试：

1. fidus 双路由（API 资产地址优先、直链兜底）——发布方声明的官方形态；
2. gh-proxy 公共镜像——本机实测（2026-11-14）直连 github.com 在 connect
   阶段失败、未认证 api.github.com 撞 403 限流时，这条路通；
3. URL 可被覆盖成任意 https 直链（用户自贴镜像），仍须 .tar.bz2 结尾，
   哈希核验不放宽。

模型布局契约（与 sherpa-onnx 官方包一致；引擎 _resolve_model_files 读同一形状）::

    <model_dir>/<名字>.onnx     恰好一个（多份并存引擎会拒绝挑）
    <model_dir>/tokens.txt
    <model_dir>/lexicon.txt / *.fst / espeak-ng-data/   可选文本前端件

本模块不 import meapet.* 之外的产品模块，也不碰 Qt。
"""
from __future__ import annotations

import contextlib
import shutil
import tarfile
from pathlib import Path
from typing import Callable

from meapet import fidus_channel
from meapet.vits_pack import sha256_of

#: 实测选型（docs/tts-engine-plugins.zh-CN.md）：中文、单说话人、int8。
ONNX_VOICE_MODEL_NAME = "vits-piper-zh_CN-chaowen-medium-int8"
DEFAULT_ONNX_VOICE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
    f"{ONNX_VOICE_MODEL_NAME}.tar.bz2"
)
#: 钉的是 2026-11-14 真机取回并逐字节核对过的那一次发布：解包后 8 个文件
#: 与 P1-4 真机验收用的模型副本全部 sha256 一致。上游重新发布就必须换钉。
ONNX_VOICE_SHA256 = (
    "f5f7c8628427fbb259ea4b7ec1a9a822a0c04e3f267071f0abfa0610371d9e0c"
)
ONNX_VOICE_ARCHIVE_BYTES = 14_011_298
#: 官方双路由都失败时的第三条路。只镜像 github.com 的 Release 直链，
#: 取回的字节仍要过上面那枚钉住的哈希——镜像只是路由，不是身份。
GITHUB_MIRROR_PREFIX = "https://gh-proxy.com/"

_CHUNK = 1024 * 1024


class OnnxVoicePackError(RuntimeError):
    """取件、核验、解压任一步没成。调用方按"这一格没成"处理，不是"装上了"。"""


def _plain_fetch(url: str, dest: Path, *, timeout: float, opener) -> None:
    """流式取回一条 https 直链（镜像 / 自定义 URL 路由）。"""
    with opener(url, timeout, {}) as response:
        with open(dest, "wb") as handle:
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                handle.write(chunk)


def _download(url: str, dest: Path, *, timeout: float, opener) -> str:
    """取回档案，返回真正走通的那条路由名。"""
    if not url.lower().startswith("https://"):
        raise OnnxVoicePackError(f"'{url}' 不是 https 直链，拒绝取件")
    if not url.rstrip("/").endswith(".tar.bz2"):
        raise OnnxVoicePackError(
            f"'{url}' 不像 tar.bz2 资产直链（文件名须以 .tar.bz2 结尾）"
        )
    if url.startswith("https://github.com/"):
        try:
            return fidus_channel.fetch_release_object(
                url, dest, timeout=timeout, opener=opener
            )
        except fidus_channel.FidusChannelError as exc:
            official_failure = str(exc)
        mirror = GITHUB_MIRROR_PREFIX + url
        try:
            _plain_fetch(mirror, dest, timeout=timeout, opener=opener)
            return "gh-proxy 镜像"
        except (OSError, ValueError) as exc:
            raise OnnxVoicePackError(
                f"官方路由：{official_failure}；镜像路由：{exc}"
            ) from exc
    try:
        _plain_fetch(url, dest, timeout=timeout, opener=opener)
    except (OSError, ValueError) as exc:
        raise OnnxVoicePackError(f"'{url}' 取件失败：{exc}") from exc
    return "自定义直链"


def safe_extract_tar(archive_path: Path, dest_dir: Path) -> Path:
    """解压并拒绝越界：任何条目解析后必须仍在 dest_dir 内，链接目标同理。

    不用 tarfile 的 filter 参数（requires-python >= 3.10，旧补丁版没有），
    逐件预检给出与 vits_pack.safe_extract 同款的明确报错。
    """
    dest_dir = Path(dest_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:bz2") as archive:
        for member in archive.getmembers():
            target = (dest_dir / member.name).resolve()
            if target != dest_dir and dest_dir not in target.parents:
                raise OnnxVoicePackError(
                    f"包内条目 '{member.name}' 试图越出解压目录，已拒绝"
                )
            if member.issym() or member.islnk():
                link_target = (target.parent / member.linkname).resolve()
                if link_target != dest_dir and dest_dir not in link_target.parents:
                    raise OnnxVoicePackError(
                        f"包内链接 '{member.name}' 指向解压目录之外，已拒绝"
                    )
        # 新解释器上再叠一层官方 data filter（3.10 旧补丁版没有，预检已兜底）。
        if hasattr(tarfile, "data_filter"):
            archive.extractall(dest_dir, filter="data")
        else:
            archive.extractall(dest_dir)
    return dest_dir


def _locate_model_dir(root: Path) -> Path:
    """在解压结果里认出模型目录：官方包带一层包装目录，平铺也认。

    判据与引擎 _resolve_model_files 同一套：恰好一个 .onnx + tokens.txt。
    多份 .onnx 并存时拒绝挑——挑错的症状是音色莫名变了。
    """
    candidates = [root]
    try:
        candidates.extend(sorted(p for p in root.iterdir() if p.is_dir()))
    except OSError:
        pass
    for directory in candidates:
        if not directory.is_dir():
            continue
        try:
            onnx_files = [
                p for p in directory.iterdir()
                if p.is_file() and p.name.endswith(".onnx")
            ]
        except OSError:
            continue
        if not onnx_files:
            continue
        if len(onnx_files) > 1:
            raise OnnxVoicePackError(
                f"模型目录里有 {len(onnx_files)} 个 .onnx，不知道该用哪个: {directory}"
            )
        if not (directory / "tokens.txt").is_file():
            raise OnnxVoicePackError(f"模型目录缺 tokens.txt: {directory}")
        return directory
    raise OnnxVoicePackError(f"压缩包里没有找到 .onnx 模型: {root}")


def install_onnx_voice(
    url: str,
    model_root: Path | str,
    *,
    expected_sha256: str = ONNX_VOICE_SHA256,
    timeout: float = 300.0,
    opener: fidus_channel.Opener = fidus_channel._urlopen,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """下载 → 核验 → 解压 → 换入，返回落盘事实 {model_dir, sha256, route, url}。

    model_root 就是模型目录本身（默认 models/voice_onnx）：整目录原子换入，
    不做"覆盖到一半"的解压；失败抛 OnnxVoicePackError，不留半成品。
    进度回调收阶段名（下载/核验/解压/就绪），不谎报字节百分比。
    """
    model_root = Path(model_root)
    staging = model_root.with_name(".onnx-voice-download")
    staging.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").rsplit("/", 1)[-1]
    archive_path = staging / name

    def report(stage: str) -> None:
        if progress is not None:
            # 进度回调（UI）失败不得影响安装本身——阶段读数丢一格而已。
            # 用 suppress 而不是裸 except: pass（质量门基线只减不增）。
            with contextlib.suppress(Exception):
                progress(stage)

    try:
        report("下载")
        route = _download(url, archive_path, timeout=timeout, opener=opener)
        report("核验")
        sha = sha256_of(archive_path)
        if sha != expected_sha256.lower().strip():
            raise OnnxVoicePackError(
                f"落盘字节 '{sha}' ≠ 钉住的 '{expected_sha256}' ⇒ "
                "这不是声明的那一份模型"
            )
        report("解压")
        fresh = model_root.with_name(model_root.name + ".new")
        if fresh.exists():
            shutil.rmtree(fresh)
        safe_extract_tar(archive_path, fresh)
        located = _locate_model_dir(fresh)
        # 旧目录先改名再换入，失败可回滚（vits_pack 同款）。
        old = model_root.with_name(model_root.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        if model_root.exists():
            model_root.rename(old)
        try:
            located.rename(model_root)
        except OSError:
            if old.exists():
                old.rename(model_root)
            raise
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(fresh, ignore_errors=True)
        report("就绪")
        return {
            "model_dir": str(model_root),
            "sha256": sha,
            "route": route,
            "url": url,
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)
