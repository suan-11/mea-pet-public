"""应用内检查更新：只提示，绝不静默升级。

行为边界（P0-3，docs/update-and-release.zh-CN.md）
--------------------------------------------------
* 拉 GitHub latest release 元数据（约 10s 超时），把 tag_name 与
  meapet.__version__ 比较，返回结构化结果（有更新 / 已是最新 / 检查失败）。
* 有更新时展示：更新说明、资产名与 sha256（读 release assets 的 digest
  或 SHA256SUMS.txt 文本资产）、浏览器下载链接。
* **绝不下载安装包、绝不覆盖任何文件、绝不执行任何升级动作**。网络上只取
  两样小东西：release JSON 与 SHA256SUMS.txt 文本（校验和元数据）。
* 失败静默降级成 error 结果并记日志，不向 UI 抛异常；入口是手动菜单项，
  没有定时自动检查，因此不存在弹窗轰炸。
* 检查跑在 QThread 后台线程（UpdateCheckWorker），不阻塞 UI；结果经信号
  回 GUI 线程，由 window_chrome 弹 QMessageBox 信息框。

覆盖更新的数据保护白名单契约在 meapet/update_policy.py（给未来更新器用）。
"""
from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass

import httpx
from PyQt5.QtCore import QThread, pyqtSignal

from meapet import USER_AGENT, __version__

logger = logging.getLogger(__name__)

# 真实上游。注意：pyproject 里的 suan-11 URL 是旧地址，这里不用它。
RELEASE_API = (
    "https://api.github.com/repos/NekoHome-Studio/mea-pet-public/releases/latest"
)
RELEASE_PAGE_URL = (
    "https://github.com/NekoHome-Studio/mea-pet-public/releases/latest"
)
CHECK_TIMEOUT_SECONDS = 10.0
SUMS_ASSET_NAME = "SHA256SUMS.txt"
# 更新说明在信息框里的展示上限（字符）——全文去 Release 页看。
_NOTES_DISPLAY_LIMIT = 800

_WIN64_CORE_ZIP_RE = re.compile(r"^MeaPet-win64-core-v.*\.zip$", re.IGNORECASE)


def parse_version(text: object) -> tuple[int, ...] | None:
    """把 "v1.2.3" / "1.2.3-beta" 一类版本解析成数字段元组。

    容错：v/V 前缀、每段只取前导数字（"3rc1" → 3）、遇到非数字段即停
    （"1.2.3-beta" → (1, 2, 3)）。完全解析不出数字段时返回 None，
    调用方按「判不了」处理，而不是猜一个版本出来。
    """
    if not isinstance(text, str):
        return None
    cleaned = text.strip().lstrip("vV")
    parts: list[int] = []
    for segment in cleaned.split("."):
        match = re.match(r"\d+", segment)
        if match is None:
            break
        parts.append(int(match.group()))
    return tuple(parts) or None


def is_newer_version(candidate: object, current: object) -> bool | None:
    """candidate 是否比 current 新；任一侧解析不出数字段时返回 None（判不了）。

    数字段按位比较，短的一侧补 0：1.0 与 1.0.0 视为同版本。
    """
    left = parse_version(candidate)
    right = parse_version(current)
    if left is None or right is None:
        return None
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left > right


@dataclass(frozen=True)
class UpdateCheckResult:
    """一次检查更新的结构化结果。

    status 三态："update_available" / "up_to_date" / "error"。
    error 态只带原因（给人看的一句话），不带任何升级动作。
    """

    status: str
    current_version: str
    latest_version: str = ""
    release_url: str = ""
    release_notes: str = ""
    asset_name: str = ""
    asset_download_url: str = ""
    asset_sha256: str = ""
    error: str = ""

    @property
    def has_update(self) -> bool:
        return self.status == "update_available"


def _error_result(current: str, reason: str) -> UpdateCheckResult:
    """失败降级：记日志、返回 error 结果，不向调用方抛异常。"""
    logger.warning("检查更新失败：%s", reason)
    return UpdateCheckResult(status="error", current_version=current, error=reason)


def _pick_windows_asset(assets: list) -> dict | None:
    """优先选 core 档 win64 zip；退而求其次任何 win64 zip / 第一个 zip。"""
    zips = [a for a in assets if isinstance(a, dict) and str(a.get("name", "")).lower().endswith(".zip")]
    for asset in zips:
        if _WIN64_CORE_ZIP_RE.match(str(asset.get("name", ""))):
            return asset
    for asset in zips:
        if "win64" in str(asset.get("name", "")).lower():
            return asset
    return zips[0] if zips else None


def _sha256_from_sums_text(text: str, asset_name: str) -> str:
    """解析 SHA256SUMS.txt（sha256sum 格式：<hash>  <文件名>）。"""
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        # 兼容 sha256sum 的 "*二进制" 标记与路径前缀，只比文件名。
        name = name.lstrip("*").strip().replace("\\", "/").rsplit("/", 1)[-1]
        if name.lower() == asset_name.lower() and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return digest.lower()
    return ""


def _asset_sha256(client: httpx.Client, asset: dict, assets: list) -> str:
    """取资产 sha256：先读 API 的 digest 字段，再退到 SHA256SUMS.txt 资产。

    只下载 SHA256SUMS.txt 这一份小文本；**绝不下载 zip 本体**。
    取不到就返回空串（展示层如实说「未提供」），不让整次检查失败。
    """
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        value = digest.split(":", 1)[1].strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    sums = next(
        (
            a
            for a in assets
            if isinstance(a, dict) and str(a.get("name", "")) == SUMS_ASSET_NAME
        ),
        None,
    )
    url = str((sums or {}).get("browser_download_url") or "")
    if not url:
        return ""
    try:
        resp = client.get(url)
        if resp.status_code != 200:
            logger.warning("SHA256SUMS.txt 返回 HTTP %s，跳过校验和展示", resp.status_code)
            return ""
        return _sha256_from_sums_text(resp.text, str(asset.get("name", "")))
    except httpx.HTTPError as exc:
        logger.warning("SHA256SUMS.txt 拉取失败：%s", type(exc).__name__)
        return ""


def check_for_updates(
    current_version: str | None = None,
    *,
    timeout: float = CHECK_TIMEOUT_SECONDS,
) -> UpdateCheckResult:
    """拉 latest release 并与当前版本比较；任何失败都降级成 error 结果。

    current_version 缺省读 meapet.__version__。网络姿势与 meapet/http_async.py
    同款：User-Agent 用 meapet.USER_AGENT，TLS 走 OS 证书库
    （ssl.create_default_context），避免冻结态 certifi 缺失。
    """
    current = __version__ if current_version is None else str(current_version)
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            verify=ssl.create_default_context(),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        ) as client:
            resp = client.get(RELEASE_API)
            if resp.status_code != 200:
                return _error_result(current, f"GitHub API 返回 HTTP {resp.status_code}")
            payload = resp.json()
            if not isinstance(payload, dict):
                return _error_result(current, "Release 元数据不是 JSON 对象")
            tag = str(payload.get("tag_name") or "").strip()
            if not tag:
                return _error_result(current, "Release 元数据缺少 tag_name")
            newer = is_newer_version(tag, current)
            if newer is None:
                return _error_result(
                    current, f"版本号判不了（tag={tag!r}，当前={current!r}）"
                )
            release_url = str(payload.get("html_url") or RELEASE_PAGE_URL)
            if not newer:
                return UpdateCheckResult(
                    status="up_to_date",
                    current_version=current,
                    latest_version=tag,
                    release_url=release_url,
                )
            assets = payload.get("assets") or []
            if not isinstance(assets, list):
                assets = []
            asset = _pick_windows_asset(assets) or {}
            sha256 = _asset_sha256(client, asset, assets) if asset else ""
            return UpdateCheckResult(
                status="update_available",
                current_version=current,
                latest_version=tag,
                release_url=release_url,
                release_notes=str(payload.get("body") or "").strip(),
                asset_name=str(asset.get("name") or ""),
                asset_download_url=str(asset.get("browser_download_url") or ""),
                asset_sha256=sha256,
            )
    except httpx.TimeoutException:
        return _error_result(current, f"网络超时（{timeout:.0f}s 内没有响应）")
    except httpx.HTTPError as exc:
        return _error_result(current, f"网络错误：{type(exc).__name__}")
    except (ValueError, TypeError, KeyError) as exc:
        return _error_result(current, f"Release 元数据解析失败：{type(exc).__name__}")
    except Exception as exc:  # 检查失败必须降级成结果，绝不把异常递给 UI
        logger.exception("检查更新出现未预期错误")
        return _error_result(current, f"未预期错误：{type(exc).__name__}")


def format_result_text(result: UpdateCheckResult) -> tuple[str, str]:
    """把结果渲染成 (标题, 正文) 纯文本——不碰 Qt，便于测试与复用。"""
    if result.status == "update_available":
        notes = result.release_notes or "（Release 未填写更新说明）"
        if len(notes) > _NOTES_DISPLAY_LIMIT:
            notes = notes[:_NOTES_DISPLAY_LIMIT] + "…（全文见下载页）"
        lines = [
            f"发现新版本 {result.latest_version}（当前 {result.current_version}）。",
            "",
            "更新说明：",
            notes,
            "",
        ]
        if result.asset_name:
            lines.append(f"下载资产：{result.asset_name}")
        lines.append(
            f"SHA256：{result.asset_sha256 or '未提供（请到下载页核对）'}"
        )
        lines.append(f"下载页：{result.release_url or RELEASE_PAGE_URL}")
        lines += [
            "",
            "本次检查只提示，不会自动下载或安装。",
            "请手动下载并核对 SHA256 后，按文档演练覆盖更新。",
        ]
        return "发现新版本", "\n".join(lines)
    if result.status == "up_to_date":
        latest = f"（最新 {result.latest_version}）" if result.latest_version else ""
        return "检查更新", f"已是最新版本 {result.current_version}{latest}。"
    return (
        "检查更新",
        f"检查更新失败：{result.error or '原因未知'}\n"
        "可以稍后再试，或手动访问发布页：\n"
        f"{RELEASE_PAGE_URL}",
    )


class UpdateCheckWorker(QThread):
    """后台检查线程：只拉元数据，结果经信号回 GUI 线程。

    run() 里的 check_for_updates 自带全量降级，不会向线程外抛异常。
    """

    checked = pyqtSignal(object)  # UpdateCheckResult

    def run(self) -> None:
        self.checked.emit(check_for_updates())
