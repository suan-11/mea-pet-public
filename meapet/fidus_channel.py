"""fidus 发行轮子的取件路径：三步钉法，纯标准库。

发布方声明的唯一官方形态是 GitHub Release 资产直链。本模块只负责把那条声明
跑通，三步缺一不可：

1. 钉**完整 URL**（tag 是身份的第一来源，文件名只是它在 PEP 440 里的归一形态）；
2. 取回同目录的 ``.sha256`` 边车，用 ``sha256sum -c`` 自校验，并要求边车声明的
   哈希逐字等于本文件钉住的那一枚；
3. 装完再读 ``fidus.__git_commit__``，与钉住的 tag 比对。

少任何一步都会退化成"看上去装上了"：只按版本号取，``>=0.1.0`` 这类约束根本
取不到预发布轮；只按文件名取，同名的两次构建无从分辨；只验边车，边车和轮子
出自同一台服务器，它一致不代表身份对。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from meapet.paths import is_frozen

FIDUS_RELEASE_TAG = "v0.1.0-beta.2"
FIDUS_WHEEL_NAME = "fidus-0.1.0b2-cp310-abi3-manylinux_2_35_x86_64.whl"
FIDUS_SIDECAR_NAME = f"{FIDUS_WHEEL_NAME}.sha256"
FIDUS_WHEEL_URL = (
    "https://github.com/FlexiAtom/fidus/releases/download/"
    f"{FIDUS_RELEASE_TAG}/{FIDUS_WHEEL_NAME}"
)
FIDUS_SIDECAR_URL = f"{FIDUS_WHEEL_URL}.sha256"
# 边车钉的是一次构建事件（maturin 往轮子里塞了带随机 UUID 的 SBOM），不是源码树，
# 所以这个数字会随重新构建而变 —— 变了就必须换 tag，不能就地改它。
FIDUS_WHEEL_SHA256 = (
    "0b55463b118829d11cd3599bd01c34a507a2cd9c3ff9a541afa3d93f64bf7abb"
)
FIDUS_GIT_COMMIT = "v0.1.0-beta.2"

_GITHUB_RELEASE_PREFIX = "https://github.com/"
_API_ROOT = "https://api.github.com/"
_CHUNK_BYTES = 65536


class FidusChannelError(RuntimeError):
    """取件、核验或装机任一步没成。调用方按"这一格没成"处理，不是"装上了"。"""


class _TransportFailure(RuntimeError):
    """单条传输路由失败，允许换另一条重试。"""


# 注入点：opener(url, timeout, headers) 返回支持上下文管理的响应；
# runner(command, **kwargs) 与 subprocess.run 同形。测试用假的，不碰网络与 pip。
Opener = Callable[..., Any]
Runner = Callable[..., Any]


def _urlopen(url: str, timeout: float, headers: Mapping[str, str]):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=dict(headers)),
        timeout=timeout,
    )


def _split_release_url(url: str) -> tuple[str, str, str]:
    """``https://github.com/<owner>/<repo>/releases/download/<tag>/<file>`` → 三段。

    tag 从第 5 段起一路拼到倒数第 2 段：git tag 本身可以含斜杠，按"第 5 段就是 tag"
    切会把 `release/1.0` 这类 tag 切成两半，反而在远端解析出一只不相干的资产。
    """
    if not url.startswith(_GITHUB_RELEASE_PREFIX):
        raise FidusChannelError(f"`{url}` 不是 GitHub Release 资产直链，取件路径拒绝猜渠道")
    segments = url[len(_GITHUB_RELEASE_PREFIX) :].split("/")
    if len(segments) < 6 or segments[2:4] != ["releases", "download"]:
        raise FidusChannelError(f"`{url}` 不像一条 release 资产直链")
    owner, repo = segments[0], segments[1]
    name = segments[-1]
    tag = "/".join(segments[4:-1])
    if not tag or not name:
        raise FidusChannelError(f"`{url}` 里缺 tag 或文件名")
    return f"{owner}/{repo}", tag, name


def _release_asset_url(url: str, *, timeout: float, opener: Opener) -> str:
    """把直链换成 ``api.github.com`` 上同一只资产的地址。"""
    project, tag, name = _split_release_url(url)
    metadata_url = f"{_API_ROOT}repos/{project}/releases/tags/{tag}"
    metadata = _transfer(
        metadata_url,
        timeout=timeout,
        opener=opener,
        headers={"Accept": "application/vnd.github+json"},
        json_decode=True,
    )
    for asset in metadata.get("assets") or ():
        if isinstance(asset, Mapping) and asset.get("name") == name:
            api_url = asset.get("url")
            if isinstance(api_url, str) and api_url.startswith(_API_ROOT):
                return api_url
    raise FidusChannelError(
        f"远端 tag `{tag}` 下面没有名为 `{name}` 的资产 ⇒ 这一轮的身份在源头就不成立"
    )


def _transfer(
    url: str,
    *,
    timeout: float,
    opener: Opener,
    headers: Mapping[str, str] | None = None,
    json_decode: bool = False,
):
    started = time.monotonic()
    deadline = started + timeout
    try:
        with opener(url, timeout, headers or {}) as response:
            if json_decode:
                return json.loads(response.read().decode("utf-8"))
            chunks: list[bytes] = []
            while True:
                chunk = response.read(_CHUNK_BYTES)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
                if time.monotonic() >= deadline:
                    raise _TransportFailure(
                        f"{url} 在 {timeout:.0f}s 内只收到 {sum(map(len, chunks))} B"
                    )
    except (OSError, ValueError, _TransportFailure) as exc:
        # OSError 覆盖 URLError/socket.timeout；ValueError 覆盖 JSON 解码失败。
        raise _TransportFailure(f"{url}：{exc}") from exc


def _fetch_object(
    direct_url: str,
    dest: Path,
    *,
    timeout: float,
    opener: Opener,
) -> str:
    """取回一个对象，返回真正走通的那条路由名。

    钉的是直链——那是发布方声明的唯一形态，API 地址是从它**解析**出来的，不是另起的
    渠道。两条路由最终落到同一只 CDN（`release-assets.githubusercontent.com`），传的
    是同一只字节，身份由 :func:`verify_wheel` 里钉住的哈希把关，不由路由保证。

    先 API 后直链，是本机实测出来的顺序，不是偏好：直链在 connect 阶段就失败过一次
    （``github.com:443`` 136 s 未连上），连上也只挤进 46,601/548,732 B；API 资产地址
    同机 85.9 s 拿全 548,732 B、sha 逐字对上（均速 6,387 B/s）。反过来留直链兜底是因为
    未认证的 ``api.github.com`` 有按 IP 的限流，它可能比直链先没。
    """
    failures: list[str] = []
    routes: list[tuple[str, str, Mapping[str, str]]] = []
    try:
        routes.append(
            (
                "api 资产路由",
                _release_asset_url(direct_url, timeout=timeout, opener=opener),
                {"Accept": "application/octet-stream"},
            )
        )
    except _TransportFailure as exc:
        failures.append(f"资产地址解析：{exc}")
    routes.append(("直链", direct_url, {}))
    for name, url, headers in routes:
        try:
            dest.write_bytes(
                _transfer(url, timeout=timeout, opener=opener, headers=headers)
            )
            return name
        except _TransportFailure as exc:
            failures.append(f"{name}：{exc}")
    raise FidusChannelError(f"`{dest.name}` 取不回来 ⇒ " + "；".join(failures))


def fetch_release_object(
    url: str,
    dest: Path,
    *,
    timeout: float = 300.0,
    opener: Opener = _urlopen,
) -> str:
    """公共复用入口（P0-2 扩展包通道）：取回一只 GitHub Release 资产。

    双路由（API 资产地址优先、直链兜底）与超时形状与 fidus 取件完全一致；
    身份核验由调用方负责（fidus 用钉住的哈希，扩展包用边车 + 可选 expected）。
    """
    return _fetch_object(url, Path(dest), timeout=timeout, opener=opener)


def default_cache_dir() -> Path:
    """取件落点。刻意用系统临时目录：把 wheel 存进仓库就是发布方明令拒绝的二次分发。"""
    return Path(tempfile.gettempdir()) / "meapet-fidus-channel"


def download(
    dest_dir: Path | str | None = None,
    *,
    timeout: float = 300.0,
    opener: Opener = _urlopen,
) -> tuple[Path, Path, str]:
    """取回轮子与边车，返回 ``(轮子路径, 边车路径, 走通的路由)``。"""
    directory = Path(dest_dir) if dest_dir is not None else default_cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / FIDUS_WHEEL_NAME
    sidecar = directory / FIDUS_SIDECAR_NAME
    route = _fetch_object(FIDUS_WHEEL_URL, wheel, timeout=timeout, opener=opener)
    _fetch_object(FIDUS_SIDECAR_URL, sidecar, timeout=timeout, opener=opener)
    return wheel, sidecar, route


def parse_sidecar(sidecar: Path) -> tuple[str, str]:
    """边车格式为 ``<hash>␣␣<裸文件名>``，同目录 ``sha256sum -c`` 直接可用。"""
    text = sidecar.read_text(encoding="utf-8")
    tokens = text.split()
    if len(tokens) != 2:
        raise FidusChannelError(f"边车 `{sidecar.name}` 不是 `<hash> <文件名>` 两段式：{text!r}")
    declared, name = tokens
    if len(declared) != 64 or any(char not in "0123456789abcdef" for char in declared.lower()):
        raise FidusChannelError(f"边车里的 `{declared}` 不是一枚 sha256")
    return declared.lower(), name


def verify_wheel(
    wheel: Path,
    sidecar: Path,
    *,
    runner: Runner = subprocess.run,
) -> str:
    """先比身份再验传输：边车声明必须等于代码里钉的哈希，然后 ``sha256sum -c``。"""
    declared, name = parse_sidecar(sidecar)
    if name != wheel.name:
        raise FidusChannelError(f"边车写的是 `{name}`，取回的却是 `{wheel.name}`")
    if declared != FIDUS_WHEEL_SHA256:
        raise FidusChannelError(
            f"边车声明 `{declared}` ≠ 本模块钉住 `{FIDUS_WHEEL_SHA256}` ⇒ "
            "这一轮不是我们要钉的那一次构建；要么 tag 变了要么有人换了资产"
        )
    if shutil.which("sha256sum") is None:
        actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if actual != declared:
            raise FidusChannelError(
                f"落盘字节 `{actual}` 与边车声明不符（现场没有 sha256sum，由本进程复算）"
            )
        return "本进程 hashlib"
    result = runner(
        ["sha256sum", "-c", sidecar.name],
        cwd=str(sidecar.parent),
        capture_output=True,
        text=True,
    )
    if getattr(result, "returncode", 1) != 0:
        detail = (getattr(result, "stdout", "") or getattr(result, "stderr", "") or "").strip()
        raise FidusChannelError(f"`sha256sum -c` 没放过这只轮子：{detail}")
    return "sha256sum -c"


def install_command(wheel: Path, *, python: str | None = None) -> Sequence[str] | None:
    """源码态返回 pip 命令；冻结态返回 None —— 打进包的可执行文件不是 python。"""
    if is_frozen():
        return None
    return [
        sys.executable if python is None else python,
        "-m",
        "pip",
        "install",
        # 轮子声明了 Requires-Dist: numpy>=1.24。放开解析会顺着索引把 numpy 抬到
        # 2.x，撞穿 linux_requirements.txt 里那条 numpy<2（本地 VITS 的前提）。
        # numpy 由 requirements 负责，这里 --no-deps；真缺了第 3 步的 import 会响亮失败。
        "--no-deps",
        "--force-reinstall",
        str(wheel),
    ]


def read_installed_identity(
    *,
    runner: Runner = subprocess.run,
    python: str | None = None,
) -> tuple[str, str]:
    """装完读回 ``(fidus.__version__, fidus.__git_commit__)``。"""
    probe = "import fidus; print(fidus.__version__); print(fidus.__git_commit__)"
    result = runner(
        [sys.executable if python is None else python, "-c", probe],
        capture_output=True,
        text=True,
    )
    if getattr(result, "returncode", 1) != 0:
        detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
        raise FidusChannelError(f"装上后 import 不进去：{detail}")
    lines = [line.strip() for line in str(getattr(result, "stdout", "")).splitlines() if line.strip()]
    if len(lines) < 2:
        raise FidusChannelError(f"读不回两个身份串，只拿到 {lines}")
    return lines[0], lines[1]


def install_fidus(
    dest_dir: Path | str | None = None,
    *,
    timeout: float = 300.0,
    opener: Opener = _urlopen,
    runner: Runner = subprocess.run,
    python: str | None = None,
) -> str:
    """走完三步钉法，返回一行人话结论。任一步不符抛 :class:`FidusChannelError`。"""
    wheel, sidecar, route = download(dest_dir, timeout=timeout, opener=opener)
    verifier = verify_wheel(wheel, sidecar, runner=runner)
    command = install_command(wheel, python=python)
    if command is None:
        raise FidusChannelError(
            "冻结态程序不装 fidus：请在构建环境里取件、核验、装上，再重新打包"
        )
    result = runner(list(command), capture_output=True, text=True)
    if getattr(result, "returncode", 1) != 0:
        detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
        raise FidusChannelError(f"pip 安装失败：{detail}")
    version, commit = read_installed_identity(runner=runner, python=python)
    if commit != FIDUS_GIT_COMMIT:
        raise FidusChannelError(
            f"装出来的 `__git_commit__` 是 `{commit}`，不是钉住的 `{FIDUS_GIT_COMMIT}` ⇒ "
            "三步的最后一环没过，这次装的来源与本模块声明的不是同一棵源码树"
        )
    return (
        f"fidus {version}（`{commit}`）已就位｜"
        f"sha256 {FIDUS_WHEEL_SHA256[:12]}… 经 {verifier} 核验｜传输走通「{route}」"
    )
