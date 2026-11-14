"""裸吞异常增量护栏：meapet/ 的 "except Exception: pass" 实测数钉成只减不增基线。

本文件能证明什么（L0）
----------------------------------------
* 严格口径（异常类型恰为 Exception、体仅一条 pass，见
  scripts/bare_except_audit.py）下，meapet/ 全量计数不超过实测基线——
  谁新增一处无注释裸吞异常，这里先红。
* 口径本身正确：合成目录上恰好数中目标形态，裸 except / 其它异常类型 /
  体内带日志的都不误计。
* 审计脚本可经 CLI（--json）复跑，输出可解析、与函数口径一致。

本文件**不能**证明什么
----------------------
* 不证运行时行为（吞异常是否合理是评审的事，不是计数的事）。
* 不覆盖其它吞异常形态（裸 except: / except BaseException / 打日志后 pass），
  也不覆盖 wizard/（不在口径内）。
* 基线是快照不是目标：路线图 P0-5 写 110，2026-11-14 实测 113，以实测为准。
  将来清理使计数下降时，应同步调低 BASELINE；禁止调高。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bare_except_audit import audit_tree, main  # noqa: E402

# 实测基线：2026-11-14，python scripts/bare_except_audit.py。
# 首测 112（36 文件、0 解析失败）；约 10 分钟后复测 113——并行代理新增
# meapet/vits_pack.py 带入 1 处。路线图写 110，一律以实测为准。
# 只减不增：清理裸吞异常时同步调低此值；禁止调高。
BASELINE = 113


def test_baseline_only_decreases():
    """当前计数 <= 实测基线；新增裸吞异常即红。"""
    result = audit_tree(ROOT / "meapet")
    assert result["parse_errors"] == [], "存在解析失败的文件，计数判不了"
    assert result["total"] <= BASELINE, (
        f"except Exception: pass 增加了: {result['total']} > 基线 {BASELINE}。"
        "新代码不应裸吞异常；确属存量清理请同步调低基线。"
    )


def test_criteria_on_synthetic_tree(tmp_path):
    """口径自检：只数「类型恰为 Exception 且体仅 pass」，其余形态不误计。"""
    (tmp_path / "hit.py").write_text(
        "try:\n    f()\nexcept Exception:\n    pass\n"
        "try:\n    f()\nexcept Exception as e:  # 注释不影响口径\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "miss.py").write_text(
        "try:\n    f()\nexcept Exception:\n    log()\n"
        "try:\n    f()\nexcept ValueError:\n    pass\n"
        "try:\n    f()\nexcept:\n    pass\n"
        "try:\n    f()\nexcept (Exception,):\n    pass\n"
        "try:\n    f()\nexcept Exception:\n    pass\n    print()\n",
        encoding="utf-8",
    )
    result = audit_tree(tmp_path)
    assert result["total"] == 2
    assert result["files"] == {"hit.py": 2}
    assert result["parse_errors"] == []


def test_cli_json_rerunnable(tmp_path, capsys):
    """CLI --json 可复跑：输出可解析，计数与函数口径一致。"""
    (tmp_path / "a.py").write_text(
        "try:\n    f()\nexcept Exception:\n    pass\n", encoding="utf-8"
    )
    code = main(["--json", "--root", str(tmp_path)])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 1
    assert data["files"] == {"a.py": 1}
