"""统计 meapet/ 下 "except Exception: pass" 裸吞异常的数量（P0-5 增量护栏）。

口径（严格，AST 级）
--------------------
* 只数异常类型恰为 Name Exception 的 ast.ExceptHandler
  （即 "except Exception:" / "except Exception as e:"）；
* 且处理体恰好只有一条 ast.Pass（注释不进 AST，不影响计数）。

不计入：裸 "except:"、"except BaseException:"、元组类型 "except (A, B):"、
属性形式 "except builtins.Exception:"、体内除 pass 外还有任何语句（含打日志）。
路线图基线写 110，本脚本以实测为准；tests/test_bare_except_baseline.py
把实测值钉成「只减不增」的基线。

用法
----
    python scripts/bare_except_audit.py           # 文本报告
    python scripts/bare_except_audit.py --json    # JSON（供程序消费）
    python scripts/bare_except_audit.py --root X  # 审计别的目录（口径测试用）

退出码：0 = 正常；2 = 有文件解析失败（判不了就说判不了，不静默跳过）。

能证明什么：扫描范围内上述口径的计数，可复跑、输出确定。
不能证明什么：运行时行为、其它形态的吞异常、wizard/ 的情况（不在口径内）。
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def is_bare_exception_pass(handler: ast.ExceptHandler) -> bool:
    """口径判定：类型恰为 Exception，且体仅一条 pass。"""
    typ = handler.type
    if not (isinstance(typ, ast.Name) and typ.id == "Exception"):
        return False
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


def audit_tree(root: Path) -> dict:
    """递归扫 root 下 *.py，返回 {"total", "files", "parse_errors"}。

    files 只记计数 > 0 的文件（相对 root 的正斜杠路径）；解析失败的文件进
    parse_errors，不静默跳过。
    """
    files: dict[str, int] = {}
    parse_errors: list[str] = []
    total = 0
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            parse_errors.append(f"{rel}: {exc}")
            continue
        count = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and is_bare_exception_pass(node)
        )
        if count:
            files[rel] = count
            total += count
    return {"total": total, "files": files, "parse_errors": parse_errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="统计 except Exception: pass 裸吞异常数量")
    parser.add_argument(
        "--root", type=Path, default=REPO_ROOT / "meapet",
        help="被审计目录（默认 meapet/）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    result = audit_tree(args.root)
    result["root"] = str(args.root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"except Exception: pass  count={result['total']}  scope={args.root}")
        for rel, count in result["files"].items():
            print(f"  {count:3d}  {rel}")
        for err in result["parse_errors"]:
            print(f"  [解析失败] {err}")
    return 2 if result["parse_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
