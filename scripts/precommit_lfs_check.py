"""提交前拦截未水化的 Git LFS 指针文件（P0-5 pre-commit local hook）。

口径
----
对每个暂存文件（added/copied/modified/renamed），读其**暂存内容**（index blob）
的头 64 字节；以 "version https://git-lfs.github.com/spec/v1" 开头即判为
LFS 指针，拒绝提交。头行规则与 scripts/build_windows.ps1 的 Test-LfsPointer
完全一致；差别在 ps1 读工作区文件（为打包），本脚本读 index（为提交）——
所以「暂存了指针、之后工作区改成真内容但没重新 add」照样拦。

用法（pre-commit 调用，也可手动跑）：
    python scripts/precommit_lfs_check.py

退出码：0 = 无指针；1 = 命中指针（打印清单与水化提示）；
2 = 判不了（git 不可用 / 不在仓库 / index 读不出）。

能证明什么：当前仓库暂存内容是否命中 LFS 指针头行。
不能证明什么：LFS 对象能否从远端拉下来（网络/水化问题，不在本闸门职责内）。
"""

from __future__ import annotations

import subprocess
import sys

LFS_SPEC_HEADER = b"version https://git-lfs.github.com/spec/v1"
_HEAD_BYTES = 64  # 与 Test-LfsPointer 的读取长度一致


def looks_like_lfs_pointer(head: bytes) -> bool:
    """头 64 字节以 LFS spec 头行开头 => 指针。"""
    return head.startswith(LFS_SPEC_HEADER)


def _git(*args: str) -> bytes:
    """跑 git 返回 stdout；失败抛 RuntimeError，由调用方决定退出码。"""
    proc = subprocess.run(["git", *args], capture_output=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(detail or f"git {' '.join(args)} 失败")
    return proc.stdout


def staged_paths() -> list[str]:
    """暂存的新增/复制/修改/改名文件（-z 分隔，容忍空格与中文路径）。"""
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def staged_head(path: str) -> bytes:
    """该文件暂存内容（index blob）的头 64 字节。"""
    return _git("show", f":{path}")[:_HEAD_BYTES]


def main() -> int:
    try:
        paths = staged_paths()
        hits = [p for p in paths if looks_like_lfs_pointer(staged_head(p))]
    except (RuntimeError, OSError) as exc:
        print(f"LFS 指针检查判不了: {exc}", file=sys.stderr)
        return 2
    if hits:
        print("拒绝提交 Git LFS 指针文件（未水化）:", file=sys.stderr)
        for p in hits:
            print(f"  {p}", file=sys.stderr)
        print("请先 git lfs pull 水化，再重新 git add 这些文件后提交。", file=sys.stderr)
        return 1
    print(f"LFS 指针检查通过：{len(paths)} 个暂存文件，无指针。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
