"""构建后置闸门：dist 产物断言验证器（P0-1）。

本文件能证明什么
----------------
* 对一个**已构建完成**的 MeaPet onedir 产物目录跑完整断言清单：
  ① exe 在位；② Windows PyQt5/Qt5/bin 旧 CRT 数量归零（2026-10-24 根因实验
  钉死的那 4 个 2018 年运行时，见 MeaPet.spec::filter_qtbin_crts）；③ 关键资产
  （内置字体、vits_infer.py）在位；④ 无开发者 config.json / mea_memory.db*
  随包泄露；⑤ Linux 侧桥接层 .so 与 numpy 在位；⑥ --doctor --doctor-format json
  冒烟 exit=0 且探针项非空、runtime 格确为 frozen=True；⑦ 包体体积读数，core 档
  Windows 包执行 ≤360 MB 硬上限。
* 全部检查跑完再汇总（不在第一个失败处停），构建日志一次看全所有缺口。

本文件不能证明什么
------------------
* 不证 GUI 真能起、Live2D 真出图、TTS 真出声（那是真机验收，doctor --skip-tts
  明确跳过合成面）。
* 不证 PyInstaller 依赖图完整（缺 hiddenimport 只在真运行某条路时暴露）。

用法::

    python scripts/verify_dist.py --dist build/stage_dist/MeaPet --platform windows --profile core

退出码：0=全部通过；1=任一断言失败。末尾打印 VERIFY_JSON= 一行机器面摘要，
供构建脚本/CI 留存（体积与 doctor 摘要进构建日志，路线图 P0-1 验收②）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# 与 MeaPet.spec::QTBIN_CRT_FILTER 同一份名单（大小写不敏感比对）。
QTBIN_CRT_NAMES = frozenset(
    ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll")
)
QTBIN_REL = ("_internal", "PyQt5", "Qt5", "bin")

# 开发者数据绝不随包（spec 只带 config.example.json）。
LEAK_NAMES = frozenset(
    ("config.json", "mea_memory.db", "mea_memory.db-wal", "mea_memory.db-shm")
)

# 关键资产：缺了就是打包缺陷，不是"警告后继续"。
REQUIRED_ASSETS = (
    ("_internal", "meapet", "assets", "fonts", "LXGWWenKai-Regular.ttf"),
    ("_internal", "meapet", "tools", "vits_infer.py"),
)

# P1-3 语音输入运行时：sherpa-onnx 与 pyaudio 都是**原生扩展**，静态分析看不见，
# 全靠 MeaPet.spec 里显式 collect_data_files 收编。2026-11-14 实测踩到过一次：
# 构建环境恰缺 pyaudio 时包照样出得来，只是录音那一路整个没了——「构建成功、
# 功能缺失」比构建失败更危险，所以这里像 Linux 的 numpy 那一格一样硬断言。
VOICE_RUNTIME_DIRS = ("sherpa_onnx", "pyaudio")

CORE_MAX_MB_DEFAULT = 360.0
DOCTOR_TIMEOUT_DEFAULT = 180


def dir_size_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


def exe_path(dist: Path, platform: str) -> Path:
    return dist / ("MeaPet.exe" if platform == "windows" else "MeaPet")


def check_qtbin_crts(dist: Path) -> tuple[list[str], int]:
    """Windows：Qt bin 旧 CRT 必须为 0。返回 (失败列表, 命中数)。"""
    qtbin = dist.joinpath(*QTBIN_REL)
    hits: list[str] = []
    if qtbin.is_dir():
        for f in qtbin.iterdir():
            if f.is_file() and f.name.lower() in QTBIN_CRT_NAMES:
                hits.append(str(f.relative_to(dist)))
    failures = [f"Qt bin 旧 CRT 未清零（{len(hits)} 个）: {hits}"] if hits else []
    return failures, len(hits)


def check_leaks(dist: Path) -> tuple[list[str], list[str]]:
    leaks: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(dist):
        for name in filenames:
            if name in LEAK_NAMES:
                leaks.append(str((Path(dirpath) / name).relative_to(dist)))
    failures = [f"开发者数据随包泄露: {leaks}"] if leaks else []
    return failures, leaks


def run_doctor(
    dist: Path,
    platform: str,
    timeout: float,
    runner=subprocess.run,
) -> tuple[list[str], dict]:
    """冻结包 doctor JSON 冒烟。返回 (失败列表, 摘要)。"""
    exe = exe_path(dist, platform)
    cmd = [str(exe), "--doctor", "--skip-tts", "--doctor-format", "json"]
    if platform == "linux":
        cmd.append("--skip-layer")
    summary: dict = {"command": cmd}
    try:
        proc = runner(
            cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return [f"doctor 冒烟超时（>{timeout:.0f}s）"], summary
    except OSError as exc:
        return [f"doctor 无法启动: {exc}"], summary
    summary["exit_code"] = proc.returncode
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-400:]
        return [f"doctor exit={proc.returncode}（stderr 尾部: {tail!r}）"], summary
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return [f"doctor stdout 不是合法 JSON: {exc}"], summary
    items = payload.get("items") or []
    summary["elapsed_s"] = payload.get("elapsed_s")
    summary["item_count"] = len(items)
    failures: list[str] = []
    if not items:
        failures.append("doctor JSON items 为空")
    for item in items:
        if not str(item.get("name") or "").strip() or not str(item.get("value") or "").strip():
            failures.append(f"doctor 探针项空值: {item!r}")
            break
    runtime = next((i for i in items if i.get("name") == "runtime"), None)
    if runtime is None or "frozen=True" not in str(runtime.get("value", "")):
        failures.append("doctor runtime 格不是 frozen=True（冒烟打到了源码态？）")
    summary["runtime"] = str(runtime.get("value", "")) if runtime else ""
    return failures, summary


def verify_dist(
    dist: Path,
    *,
    platform: str,
    profile: str = "full",
    max_core_mb: float = CORE_MAX_MB_DEFAULT,
    skip_doctor: bool = False,
    doctor_timeout: float = DOCTOR_TIMEOUT_DEFAULT,
    runner=subprocess.run,
) -> dict:
    """跑全部断言，返回报告 dict（ok/failures/warnings/size_mb/checks）。"""
    dist = Path(dist)
    failures: list[str] = []
    warnings: list[str] = []
    checks: dict = {}

    exe = exe_path(dist, platform)
    if not exe.is_file():
        failures.append(f"缺产物可执行文件: {exe}")
    elif platform == "linux" and not os.access(exe, os.X_OK):
        failures.append(f"产物无可执行位: {exe}")
    checks["exe"] = str(exe)

    if platform == "windows":
        crt_failures, crt_count = check_qtbin_crts(dist)
        failures += crt_failures
        checks["qtbin_old_crt_count"] = crt_count

    for rel in REQUIRED_ASSETS:
        if not dist.joinpath(*rel).is_file():
            failures.append(f"关键资产缺失: {'/'.join(rel)}")
    checks["required_assets"] = ["/".join(r) for r in REQUIRED_ASSETS]

    leak_failures, leaks = check_leaks(dist)
    failures += leak_failures
    checks["leaked_files"] = leaks

    if platform == "linux":
        shim = dist / "_internal" / "liblayer_shell_shim.so"
        if not shim.is_file():
            failures.append("包内缺 _internal/liblayer_shell_shim.so（穿透模式第一候选）")
        if not (dist / "_internal" / "numpy").is_dir():
            failures.append("包内缺 numpy 运行时（fidus_position.py 模块级依赖）")

    # P1-3：语音运行时（两平台都要在——它是可选功能，但**打进去了就必须完整**；
    # 故意不带语音的发行版请显式改 VOICE_RUNTIME_DIRS，别靠环境碰运气）。
    for pkg in VOICE_RUNTIME_DIRS:
        if not (dist / "_internal" / pkg).is_dir():
            failures.append(
                f"包内缺 {pkg} 运行时（P1-3 语音输入；构建环境装齐再打）"
            )
    checks["voice_runtime"] = list(VOICE_RUNTIME_DIRS)

    size_mb = dir_size_bytes(dist) / (1024 * 1024)
    checks["size_mb"] = round(size_mb, 1)
    checks["profile"] = profile
    if platform == "windows" and profile == "core":
        checks["core_max_mb"] = max_core_mb
        if size_mb > max_core_mb:
            failures.append(f"core 包体积 {size_mb:.1f} MB 超上限 {max_core_mb:.0f} MB")

    if skip_doctor:
        warnings.append("doctor 冒烟被 --skip-doctor 跳过（仅限调试，正式构建不得跳过）")
        checks["doctor"] = {"skipped": True}
    else:
        doctor_failures, doctor_summary = run_doctor(dist, platform, doctor_timeout, runner)
        failures += doctor_failures
        checks["doctor"] = doctor_summary

    return {
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "size_mb": round(size_mb, 1),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MeaPet 构建后置闸门（P0-1）")
    parser.add_argument("--dist", required=True, type=Path, help="产物目录（含 MeaPet.exe 的那层）")
    parser.add_argument("--platform", required=True, choices=("windows", "linux"))
    parser.add_argument("--profile", default="full", choices=("core", "full"))
    parser.add_argument("--max-core-mb", type=float, default=CORE_MAX_MB_DEFAULT)
    parser.add_argument("--skip-doctor", action="store_true", help="仅调试用；正式构建禁用")
    parser.add_argument("--doctor-timeout", type=float, default=DOCTOR_TIMEOUT_DEFAULT)
    args = parser.parse_args(argv)

    report = verify_dist(
        args.dist,
        platform=args.platform,
        profile=args.profile,
        max_core_mb=args.max_core_mb,
        skip_doctor=args.skip_doctor,
        doctor_timeout=args.doctor_timeout,
    )
    print(f"== verify_dist: {args.dist} ({args.platform}/{args.profile}) ==")
    print(f"体积: {report['size_mb']} MB")
    for warning in report["warnings"]:
        print(f"[WARN] {warning}")
    for failure in report["failures"]:
        print(f"[FAIL] {failure}")
    if report["ok"]:
        print("[OK] 全部后置断言通过")
    print("VERIFY_JSON=" + json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
