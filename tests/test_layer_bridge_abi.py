"""layer-shell 桥接层的 Python 侧 ABI 测试（spec §9 的 T4 / T7 / T8 行，计划书 WP-F）。

本文件能证明什么（L0/L1/L2，agents-rules §10）
------------------------------------------------
* **产物来源核验**（T7 行的三段式判定，本文件的准入前提）：仓库根那个 `.so` 的
  `nm -D --defined-only` 集合 == `~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md` §7.1 的 11 个符号。这一步之所以排在
  一切行为断言之前，是因为该路径**在 P0 之前长期放着旧 C 实现的同名产物**，而
  `*.so` 被 `.gitignore` 排除（不进 VCS）⇒ 行为类断言（`init==-1`、非法参数=NULL、
  double-destroy 不崩……）**对 C 产物同样会通过**。C 产物导出 19 个符号（含 §4.1
  弃用的 9 个、`layer_shell_shim.cpp` 的 mangled 名与 `*_interface` 表），集合相等
  判定天然把它拒掉。（依据：计划书 WP-F 行"被测 `.so` 来源核验"、
  `~/.Athena/projects/meapet/reference/project-process-2026-09-21.md` §2.1 记录 1J 第 8 条。）
* **无 Wayland 会话时每个入口的失败形态**：`layer_shell_init` 给 -1（§7.1 #1）、
  其余导出返回 NULL / no-op 且**绝不返回假句柄**（§7.1 #3、I7），诊断缓冲三条性质
  （#11：永不 NULL、地址永不变、256 B 上界），以及"拒绝必须报出是哪个符号"（§7.2）。
* **进程生命周期不变量**：反复 init/cleanup 不增长线程数、fd 数与 `/memfd:meapet-px`
  计数（§6.5 稳态上界的失败路径版本）。
* **Python 绑定面与 §7.1 的必需/可选分级一致**：`wayland_layer.py` 无条件绑定的符号
  不得包含 spec 标"可选"的行，反之探测集必须恰是那三个可选符号（agents-rules §5 点名的
  固化风险：把 #6 与 #7–#9 读成同一类，就把一次真实的整体失效固化成了合法行为）。
* **产物不链接 Qt**（§4.7 第 1 行：消灭 Qt5 私有头依赖）。

本文件**不能**证明什么（不静默，逐条给出判决力的实际落点）
----------------------------------------------------------
* **尺寸界的判决力（§7.1 #3）**。无 Wayland 会话时 `layer_create_context` 先被"未
  init"这道门拒掉，于是越界尺寸与合法尺寸**同样**返回 NULL —— 在这里断言"NULL"测的是
  门 1，不是门 2。真正把 `(0,…)/(8193,1)/面积顶点` 逐例判开的是 L1 的
  `native/layer_shell/src/ffi.rs::create_context_rejects_*`（fake-live 桥上）。本文件
  保留这些用例只为一件事：**在真实 ctypes ABI 边界上不崩、且不产出假句柄**。
* **注册表级拒绝（§4.4）**：伪造句柄/重复 destroy 在同样环境下先被 init 门拒掉，
  所以这里只证"不崩 + 有可读诊断 + 诊断点名符号"。
* `force` 值域（§6.2）、ring 状态机（§4.5）、输入区双模（§4.6）：都要 Live 桥，
  属 L1/L2 的 Rust 测试；真实合成器上的 fd/RSS census 属 WP-G 的 T6（L3）。
* 成功 init 的线程/fd 收支：无 compositor 时泵线程根本不 spawn（§4.2 只在 Live 建线程），
  所以本文件的 census 只覆盖失败路径。
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# 方案自 2026-09-21 起住在 Athena 状态根，不在仓库里（本文件把它当**机器输入**解析，
# 因为 I2 规定 §7.1 那张表是导出集合的唯一真值——见下方 `_spec_7_1_rows` 的 docstring）。
# 失效模式：状态根不存在（干净 clone / 未采纳 Athena 的机器）⇒ 这里 FileNotFoundError **响亮报错**，
# 故意不写 skip：一道查不出东西的门禁比没有门禁更危险（它生产"已通过"）。
# 该后果已登记为发布门槛（clean clone 跑不了本文件），不是本文件的缺陷。
SPEC_MD = Path.home() / ".Athena" / "projects" / "meapet" / "working" / "rust-layer-shell-bridge.md"
BUILD_SH = REPO_ROOT / "build_layer_shell.sh"
SHIM_PY = REPO_ROOT / "meapet" / "desktop" / "wayland_layer.py"
SHIM_SO = REPO_ROOT / "liblayer_shell_shim.so"

# §7.1 #11 的固定诊断缓冲：字符串上界是 256-1（末字节留给 NUL）。
STICKY_MAX_BYTES = 255

# 子进程里剥掉的变量 ⇒ `connect_to_env()` 找不到 compositor ⇒ §7.1 #1 的 -1 形态。
# 只剥这两个就够（实测：XDG_RUNTIME_DIR 仍在、真实会话仍在跑，但 WAYLAND_DISPLAY
# 缺失即 -1），因此**不会**碰到用户当前的 Wayland 会话——计划书 WP-F 行"所需授权=无"
# 的全部依据就在这两行。
_WAYLAND_VARS = ("WAYLAND_DISPLAY", "WAYLAND_SOCKET")


def _require_machine_inputs() -> None:
    """把"方案已移出仓库"从一屏 collection traceback 收敛成**一行可执行的 Error**。

    agents-rules §1 三问：
      * 收益 = 干净 clone / 未采纳 Athena 的机器上跑本文件，得到的是"缺哪个文件、去哪儿取"
        的明文，而不是 collection 阶段一屏 FileNotFoundError（同一后果，可读性差、易被误当成代码 bug）。
      * 会崩的条件 = 状态根里的方案 §7.1 表或构建脚本不在场 ⇒ 下面判据直接把本文件判失败。
      * 兜底 = **故意不写 skip**（skip 会把响亮失败变成静默通过，见文件顶部注释与 pending 登记）。
        这里只把"响亮"做得更准：抛一个带绝对路径与恢复动作的 Error，仍然让该文件判失败。
    """
    missing = [p for p in (SPEC_MD, BUILD_SH) if not p.exists()]
    if missing:
        lines = "\n".join(f"    - {p}" for p in missing)
        raise RuntimeError(
            "test_layer_bridge_abi 需要 Athena 状态根里的**方案**与**构建脚本**当机器输入"
            "（I2：§7.1 符号表是导出集合的唯一真值）。以下输入本机不存在 ⇒ 环境不足：\n"
            f"{lines}\n"
            f"恢复：`athena --project meapet context` 确认本项目已采纳 Athena；"
            f"方案应位于 {SPEC_MD}。"
        )


# --------------------------------------------------------------------------
# 三份"真值"的读取：spec（权威）→ 构建脚本（门禁）→ 产物（证据）
# --------------------------------------------------------------------------
def _spec_7_1_rows() -> dict[str, str]:
    """读 §7.1 导出符号表 → ``{符号名: 必需性列原文}``。

    I2 规定那张表是导出集合的**唯一真值**，所以判定基准从表里解析，而不是在本文件
    再抄一份名单（agents-rules §7：两份定义、一处约定，没有任何东西保证它们不漂移）。
    """
    _require_machine_inputs()
    text = SPEC_MD.read_text(encoding="utf-8")
    try:
        start = text.index("### 7.1")
        end = text.index("### 7.2", start)
    except ValueError as exc:  # 章节被改名/挪动 ⇒ 解析面塌了，必须报出来
        raise AssertionError(f"{SPEC_MD.name} 里找不到 §7.1 小节的边界：{exc}") from exc
    rows: dict[str, str] = {}
    for line in text[start:end].splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 6 or not re.fullmatch(r"\d+", cells[0]):
            continue
        sym = re.fullmatch(r"`([a-z0-9_]+)`", cells[1])
        if sym is None:
            continue
        rows[sym.group(1)] = cells[-1]
    assert rows, "§7.1 表解析出 0 行——表格列数或符号列写法变了，判定基准失效"
    return rows


def _script_names(array: str) -> list[str]:
    """读 `build_layer_shell.sh` 里的 ``REQUIRED=( … )`` / ``ALLOWED=( … )`` 数组。"""
    text = BUILD_SH.read_text(encoding="utf-8")
    m = re.search(rf"^{array}=\(\n(.*?)^\)", text, re.M | re.S)
    assert m, f"{BUILD_SH.name} 里找不到 {array}=( ... ) 数组"
    names: list[str] = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        assert re.fullmatch(r"[a-z0-9_]+", s), f"{array} 里出现非法行：{line!r}"
        names.append(s)
    return names


def _nm_defined_dynamic(path: Path) -> set[str]:
    """`nm -D --defined-only` 的第三列集合（与构建脚本同一条命令形态，§9 T7 行）。"""
    nm = shutil.which("nm")
    if nm is None:
        # 缺工具**不是**放行理由：本文件的准入前提就是这道核验，静默 skip 等于
        # 让门禁"测量自己的缺席"（agents-rules §8 表第 2 行的同型缺陷）。
        raise AssertionError("PATH 里没有 nm，无法核验产物来源；请用包管理器装 binutils")
    r = subprocess.run(
        [nm, "-D", "--defined-only", str(path)], capture_output=True, text=True
    )
    assert r.returncode == 0, f"nm 失败 rc={r.returncode}：{r.stderr.strip()}"
    names: set[str] = set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:  # 脚本里的 `awk '{print $3}'` 对短行产出空串，等价于丢弃
            continue
        name = parts[-1]
        if name == "_":  # 段符号的裸下划线（脚本 `grep -v '^_$'` 的同一处理）
            continue
        names.add(name)
    return names


def _export_set_verdict(actual: set[str]) -> str | None:
    """三段式判定（F-M9）。返回 ``None`` = 通过；否则返回可直接读的失败说明。

    ①必需的 11 个逐项在场（不是"至少命中一个"）；②白名单 = 脚本 `ALLOWED`；
    ③其余任何导出符号都违反 I2（双向差集为空）。

    `actual` 是参数而不是内部读盘：判定函数自己必须能被**喂进一个已知错误的集合**
    （见 `test_provenance_gate_rejects_a_non_rust_export_set`）。一道从来没有拒绝过
    任何东西的门禁，与没有门禁等价（agents-rules §8 表第 2 行）。
    """
    rows = _spec_7_1_rows()
    required = _script_names("REQUIRED")
    allowed = set(_script_names("ALLOWED"))

    missing = sorted(set(rows) - set(required))
    if missing:
        return (
            f"构建脚本的 REQUIRED 与 §7.1 表不一致：脚本缺 {missing}。"
            f"门禁在查一份自己的名单，不是查规格（agents-rules §7）。"
        )
    extra = sorted(set(required) - set(rows))
    if extra:
        return f"构建脚本的 REQUIRED 有 §7.1 表外的名字：{extra}（§7.1 是 I2 的唯一真值）。"
    if len(required) != len(set(required)):
        return f"REQUIRED 数组里有重复项：{required}"

    absent = sorted(set(required) - actual)
    beyond = sorted(actual - set(required) - allowed)
    if absent or beyond:
        try:
            stat = SHIM_SO.stat()
            where = f"{SHIM_SO} size={stat.st_size} mtime={stat.st_mtime}"
        except OSError as exc:  # 注入用例没有磁盘产物可读
            where = f"（无磁盘产物可读：{exc}）"
        return (
            "被测产物不是 Rust 构建："
            f"导出集合 != §7.1 的 {len(required)} 个符号。\n"
            f"  必需而缺席：{absent}\n"
            f"  表外导出（必需∪白名单之外，违反 I2）：{beyond}\n"
            f"  实际导出 {len(actual)} 个：{sorted(actual)}\n"
            f"  文件：{where}\n"
            "  为什么这条必须先判：仓库根这个路径在 P0 之前放着旧 C 实现的同名产物"
            "（导出 19 个符号，含 §4.1 弃用的 9 个），而行为类断言对 C 产物同样会通过。\n"
            "  修复：bash build_layer_shell.sh"
        )
    return None


def _provenance_failure() -> str | None:
    """对**当前磁盘产物**跑一遍 `_export_set_verdict`。"""
    return _export_set_verdict(_nm_defined_dynamic(SHIM_SO))


@pytest.fixture(scope="module", autouse=True)
def _provenance_gate():
    """本模块全部测试的准入前提：在场的产物必须是 §7.1 描述的那个 Rust 构建。

    放进 autouse fixture 而不是"排在文件第一个的测试"，是为了让"先于行为断言执行"
    成为**结构**而不是排序运气（agents-rules §5：规格要求的顺序不能靠当前实现的巧合）。
    产物**不在场**时这里什么都不做——来源核验无从谈起，但 spec↔脚本一致性与判定函数
    自身的判决力用例照跑；需要磁盘产物的用例各自请求 `artifact` 并在那里 skip。
    """
    if SHIM_SO.exists():
        msg = _provenance_failure()
        if msg is not None:
            pytest.fail(msg)


@pytest.fixture(scope="module")
def artifact():
    """需要真实 `.so` 的用例的前置：缺席 = 具名 skip，在场 = 已被 `_provenance_gate` 验过。

    gap#3（`~/.Athena/projects/meapet/reference/project-process-2026-09-21.md` §2.1 记录 1J）：计划书没规定无产物时 skip 还是
    fail。这里选 **skip 且写明理由**，依据是 CI 现状——`.github/workflows/` 下只有
    `python-app.yml`，其中 cargo/build_layer_shell 均 0 命中 ⇒ 为无关的构建缺口新增一道红。
    判据不因缺席而变松：不依赖产物的 5 条（spec 解析、脚本对齐、AST 绑定面、判定函数
    的反例、集合正向）照跑。
    """
    if not SHIM_SO.exists():
        pytest.skip(
            f"{SHIM_SO.name} 不在仓库根：产物不入 VCS（.gitignore 的 *.so）且 CI 不构建 Rust。"
            "运行 `bash build_layer_shell.sh` 后即生效（计划书 WP-F 记录登记了本处置）。"
        )
    return SHIM_SO


# --------------------------------------------------------------------------
# 准入判定的三个面：spec / 脚本 / 产物
# --------------------------------------------------------------------------
def test_spec_7_1_table_is_the_expected_cardinality():
    """§9 T7 行把门禁写成"必需 11 符号逐项在场"，所以 11 这个数出自 spec 原文。"""
    rows = _spec_7_1_rows()
    assert len(rows) == 11, f"§7.1 表解析到 {len(rows)} 行：{sorted(rows)}"
    assert all(name.startswith("layer_") for name in rows), sorted(rows)
    # 必需/可选两列必须能被判出——`test_python_binding_surface_matches_spec` 依赖
    # "可选"前缀这个形态；哪天写成"（可选）"或挪列，这里先红，不要让下游静默拿到空集。
    assert sum(1 for c in rows.values() if c.startswith("可选")) == 3, rows


def test_build_script_gate_agrees_with_spec():
    """门禁查的名单必须就是规格那份名单——否则门禁通过不等于 I2 成立。"""
    rows = _spec_7_1_rows()
    assert set(_script_names("REQUIRED")) == set(rows)


def test_provenance_gate_rejects_a_non_rust_export_set():
    """判定函数自己必须有判决力——否则"绿"只证明没人看过它（agents-rules §8 表第 2 行）。

    反例集合按旧 C 产物的**形态**构造（记录 1J 第 8 条实测：仓库根曾长期放着 57312 B 的
    C 版 `liblayer_shell_shim.so`，导出 19 个符号、没有 `layer_last_error`）：§7.1 的 11
    个里缺 #11，外加 C++ mangled 名与协议 `*_interface` 表。这里注入符号集合而不是加载
    那份 C 产物：判据要证的是**集合判定**，不是磁盘上那个历史文件还在不在。
    """
    rows = set(_spec_7_1_rows())
    c_shaped = (rows - {"layer_last_error"}) | {
        "_ZN16layer_shell_initEv",
        "_ZN19layer_update_pixelsEPvPhii",
        "zwlr_layer_shell_v1_interface",
        "wl_shm_interface",
    }
    verdict = _export_set_verdict(c_shaped)
    assert verdict is not None, "C 形态的导出集合被放行 ⇒ 这道准入判定形同虚设"
    assert "layer_last_error" in verdict, verdict
    assert "不是 Rust 构建" in verdict, verdict
    # 反向：一个只有 10 个符号、但没有任何表外名字的产物同样必须被拒（①逐项在场）。
    assert _export_set_verdict(rows - {"layer_clear"}) is not None
    # 正向：精确相等必须放行，且不得把白名单里的工具链符号判成违规。
    assert _export_set_verdict(set(rows)) is None
    allowed = set(_script_names("ALLOWED"))
    assert _export_set_verdict(rows | allowed) is None
    if not allowed:
        # ALLOWED 目前是空集（首个真实 Rust 产物取证后钉死）。空集时"表外即违规"必须真的
        # 生效：多一个 `_ITM_deregister` 也要红。
        assert _export_set_verdict(rows | {"_ITM_deregisterTMCloneTable"}) is not None


def test_artifact_exports_exactly_the_spec_set(artifact):
    """三段式判定本身（也由 autouse fixture 兜住，这里让它作为一条具名测试可见）。"""
    assert _export_set_verdict(_nm_defined_dynamic(artifact)) is None


def test_artifact_links_no_qt(artifact):
    """§4.7 第 1 行：Rust 版用独立连接，Qt5 私有头依赖连同 C++ shim 一起消失。"""
    if shutil.which("ldd") is None:
        raise AssertionError("PATH 里没有 ldd，无法核验动态依赖")
    r = subprocess.run(["ldd", str(artifact)], capture_output=True, text=True)
    assert r.returncode == 0, f"ldd rc={r.returncode}：{r.stderr.strip()}"
    out = r.stdout
    assert "not found" not in out, f"产物有解析不到的依赖，加载时会失败：\n{out}"
    # 只在 `=>` **左侧**（被请求的 soname）上匹配 Qt，绝不匹配整行。
    #   * 为什么 = `ldd` 右侧是解析到的文件路径，而本项目的启动配方
    #     （`AGENTS.md`／`~/114514常用命令.txt` 教的
    #     `LD_LIBRARY_PATH=.venv/.../PyQt5/Qt5/lib`）**把字符串 `PyQt5` 放进了每个路径**
    #     ⇒ 按整行匹配时，产物唯一依赖 `libgcc_s.so.1`／`libc.so.6` 都会命中 `qt5` 子串，
    #     于是一道"§4.7 无 Qt 依赖"的门禁在**按文档方式启动的测试会话里恒红**。
    #   * 什么条件下它会崩（漏判）= 存在一个 soname 里既不含 `libqt` 也不含 `qt5/qt6`
    #     的 Qt 库。Qt 的全部导出库 soname 形如 `libQt5Core.so.5`／`libQt6Quick.so.6`，
    #     且左侧匹配仍然覆盖 `X => not found` 形态（上面那条断言另外兜住它）。
    #   * 兜底 = 真要判"链接了 Qt"，`readelf -d` 的 `NEEDED` 列表是与 `LD_LIBRARY_PATH`
    #     无关的等价读法；本条用左侧匹配已经与它一致（实测该产物 NEEDED 仅
    #     libgcc_s／libc／ld-linux 三项）。
    sonames = [
        ln.split("=>", 1)[0].strip() for ln in out.splitlines() if "=>" in ln
    ]
    hits = [s for s in sonames if re.search(r"libqt|qt5|qt6", s, re.I)]
    assert not hits, f"产物仍链接 Qt（违反 §4.7 第 1 行）：{hits}"


# --------------------------------------------------------------------------
# Python 绑定面 ↔ §7.1 的必需/可选分级（AST 静态读，不导入被测模块）
# --------------------------------------------------------------------------
def _binding_surface() -> tuple[set[str], set[str]]:
    """从 `wayland_layer.py` 的 AST 里取出（无条件绑定集，可选探测集）。

    用 AST 而不是导入模块：这里要查的是**源码声明了哪些绑定**，导入会让"模块能跑"
    与"模块声明一致"混成一件事；且部分用例（子进程那批）已经单独覆盖运行期形态。
    """
    tree = ast.parse(SHIM_PY.read_text(encoding="utf-8"))
    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_load"
        ),
        None,
    )
    assert fn is not None, f"{SHIM_PY.name} 里没有 _load()，绑定面判定失效"

    unconditional: set[str] = set()
    optional: set[str] = set()
    for node in ast.walk(fn):
        # self._shim.<sym>.restype|argtypes = ...   → 无条件绑定
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Attribute)
                    and tgt.attr in ("restype", "argtypes")
                    and isinstance(tgt.value, ast.Attribute)
                    and isinstance(tgt.value.value, ast.Attribute)
                    and tgt.value.value.attr == "_shim"
                    and isinstance(tgt.value.value.value, ast.Name)
                    and tgt.value.value.value.id == "self"
                ):
                    unconditional.add(tgt.value.attr)
        # for name, argtypes in (("layer_clear", [...]), ...)   → 可选探测
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            for elt in node.iter.elts:
                if (
                    isinstance(elt, ast.Tuple)
                    and elt.elts
                    and isinstance(elt.elts[0], ast.Constant)
                    and isinstance(elt.elts[0].value, str)
                ):
                    optional.add(elt.elts[0].value)
    return unconditional, optional


def test_python_binding_surface_matches_spec():
    """Python 的绑定方式必须与 §7.1 的必需性列同构（agents-rules §5 点名的固化风险）。

    失效模式：若把 #6 `layer_update_pixels_with_format` 也塞进可选探测列表，测试照样
    绿，而真实后果是 C 侧一旦不导出它，`_load()` 直接 `AttributeError` ⇒ 整个后端被判
    不可用（§7.1 #6 那一列写的就是这个）。兜底：本条按 spec 的"可选"前缀反查探测集，
    两侧任何一侧单独漂移都会红。
    """
    rows = _spec_7_1_rows()
    unconditional, optional = _binding_surface()
    spec_optional = {n for n, c in rows.items() if c.startswith("可选")}

    assert optional == spec_optional, (
        f"可选探测集 != §7.1 标“可选”的行："
        f"Python={sorted(optional)} spec={sorted(spec_optional)}"
    )
    assert "layer_update_pixels_with_format" in unconditional, (
        "#6 是“必需且非可选”（§7.1 该行原文），必须无条件绑定"
    )
    # #11 由 Python 侧完全不读——这正是 gap#1（§7.1 #11"不参与任何判决"却无人被指定读）。
    # 把它写成断言而不是注释：将来谁接上它，这条会红并指向挂起清单，而不是留一句谎言。
    assert (unconditional | optional) == set(rows) - {"layer_last_error"}, (
        f"门面绑定集与 §7.1 差一项都读不到：{sorted(unconditional | optional)}"
    )


# --------------------------------------------------------------------------
# 无 Wayland 会话的子进程行为（§7.1 各行的失败形态 + §6.5 生命周期不变量）
# --------------------------------------------------------------------------
# 子进程侧 preamble 自己声明一遍 §7.1 的签名：**故意不复用** wayland_layer.py 的
# 绑定，否则"模块声明错了"与"产物错了"会被同一处代码互相证实（agents-rules §8：判决量
# 必须独立于被判决的对象）。
_PREAMBLE = """
import ctypes, json, os, sys
from ctypes import POINTER, c_int, c_uint32, c_ubyte, c_void_p

shim = ctypes.CDLL(sys.argv[1])
shim.layer_shell_init.restype = c_int
shim.layer_shell_cleanup.restype = None
shim.layer_last_error.restype = c_void_p
shim.layer_create_context.restype = c_void_p
shim.layer_create_context.argtypes = [c_void_p, c_int, c_int, c_int, c_int]
shim.layer_destroy_context.argtypes = [c_void_p]
shim.layer_set_click_through.argtypes = [c_void_p, c_int]
shim.layer_update_pixels.argtypes = [c_void_p, POINTER(c_ubyte), c_int, c_int]
shim.layer_update_pixels_with_format.argtypes = [
    c_void_p, POINTER(c_ubyte), c_int, c_int, c_uint32]
shim.layer_clear.argtypes = [c_void_p]
shim.layer_set_position.argtypes = [c_void_p, c_int, c_int]
shim.layer_set_size.argtypes = [c_void_p, c_int, c_int]

def err_addr():
    return shim.layer_last_error()

def err_text():
    return ctypes.string_at(shim.layer_last_error()).decode("utf-8", "replace")

def memfd_count():
    n = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            if os.readlink("/proc/self/fd/" + fd).startswith("/memfd:meapet-px"):
                n += 1
        except OSError:
            continue
    return n

def census():
    return {"threads": len(os.listdir("/proc/self/task")),
            "fds": len(os.listdir("/proc/self/fd")),
            "memfd": memfd_count()}

def emit(obj):
    print("@@", json.dumps(obj, ensure_ascii=False), sep="")

VP = ctypes.c_void_p
BUF = (ctypes.c_ubyte * 4)()
"""


def _run_probe(body: str) -> dict:
    """在**没有 Wayland 会话**的子进程里跑 `body`，取回 `emit({...})` 的字典。"""
    env = {k: v for k, v in os.environ.items() if k not in _WAYLAND_VARS}
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(REPO_ROOT)
    r = subprocess.run(
        [sys.executable, "-c", _PREAMBLE + body, str(SHIM_SO)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    # rc != 0 本身就是失败：段错误(-11)/中止(-6) 会以这里的形式暴露，
    # 这正是"不崩"类断言（§7.1 #10、§4.4）的实现方式。
    assert r.returncode == 0, (
        f"子进程 rc={r.returncode}（负数=被信号杀死=崩溃）\n"
        f"--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
    )
    lines = [ln[2:] for ln in r.stdout.splitlines() if ln.startswith("@@")]
    assert lines, f"探针没有 emit 结果\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
    return json.loads(lines[-1])


def test_init_without_wayland_session_is_minus_one(artifact):
    """§7.1 #1 的 -1 形态 + #11 诊断缓冲的三条性质（永不 NULL / 地址不变 / 256 B 界）。"""
    r = _run_probe(
        """
addr_idle = err_addr()
rc = shim.layer_shell_init()
msg = err_text()
addr_after = err_addr()
rc_again = shim.layer_shell_init()      # 幂等失败：重跑整段序列，同因同码
addr_last = err_addr()
emit({"rc": rc, "rc_again": rc_again, "msg": msg,
      "addrs": [addr_idle, addr_after, addr_last]})
"""
    )
    assert r["rc"] == -1, f"无 Wayland 会话应为 -1，实际 {r['rc']}"
    assert r["rc_again"] == -1
    assert "wayland" in r["msg"].lower(), f"诊断必须说清是没连上会话：{r['msg']!r}"
    assert len(r["msg"].encode()) <= STICKY_MAX_BYTES
    assert all(a for a in r["addrs"]), f"#11 永不 NULL：{r['addrs']}"
    assert len(set(r["addrs"])) == 1, f"#11 地址永不变：{r['addrs']}"


def test_every_export_rejects_uninitialized_without_a_fake_handle(artifact):
    """I7 + §7.1 #3/#5/#7–#10：退化输入不伪装成合法输出。

    注意本条**测不到**尺寸界（§7.1 #3）的判决力——未 init 的门先落下，合法与越界
    尺寸同样是 NULL。见模块 docstring"不能证明什么"第 1 条。
    """
    r = _run_probe(
        """
created = []
for label, st, (w, h, x, y) in [
    ("0x0",              VP(None), (0, 0, 0, 0)),
    ("neg",              VP(None), (-1, -1, 0, 0)),
    ("edge 8193x1",      VP(None), (8193, 1, 0, 0)),
    ("edge 8192x2049",   VP(None), (8192, 2049, 0, 0)),
    ("legal 400x400",    VP(None), (400, 400, 0, 0)),
    ("state non-NULL",   VP(0x10), (400, 400, 0, 0)),
]:
    created.append({"label": "layer_create_context " + label,
                    "sym": "layer_create_context",
                    "handle": shim.layer_create_context(st, w, h, x, y),
                    "err": err_text()})
calls = [
    ("layer_set_click_through", lambda: shim.layer_set_click_through(VP(0x1234), 1)),
    ("layer_clear", lambda: shim.layer_clear(VP(0x1234))),
    ("layer_set_position", lambda: shim.layer_set_position(VP(0x1234), -1, -1)),
    ("layer_set_size", lambda: shim.layer_set_size(VP(0x1234), 400, 400)),
    ("layer_update_pixels", lambda: shim.layer_update_pixels(VP(0x1234), BUF, 400, 400)),
    ("layer_update_pixels", lambda: shim.layer_update_pixels(VP(0x1234), None, 400, 400)),
    ("layer_update_pixels_with_format",
     lambda: shim.layer_update_pixels_with_format(VP(0x1234), BUF, 400, 400, 0xdeadbeef)),
    ("layer_destroy_context", lambda: shim.layer_destroy_context(VP(0x1234))),
]
observed = []
for sym, fn in calls:
    fn()
    observed.append({"label": sym, "sym": sym, "err": err_text()})
emit({"created": created, "calls": observed})
"""
    )
    for c in r["created"]:
        assert c["handle"] is None, f"{c['label']}：I7 禁止把退化输入变成合法句柄"
    # "拒绝"必须可观察（§7.2 的"拒绝：no-op + 粘性错误"）。判据是"这条诊断点名了**本次
    # 调用所能归因的**导出符号"，取诊断里最先出现的符号名比较（`layer_update_pixels` 是
    # `layer_update_pixels_with_format` 的前缀，故长名排在前面）。
    # 若某次拒绝**没有**覆写诊断，读到的会是上一次的符号 ⇒ 落进 accept 之外 ⇒ 本条变红。
    #
    # 为什么 accept 里允许 `layer_update_pixels` 顶替 `..._with_format`：#5 与 #6 共用
    # `Bridge::update_pixels`，粘性前缀写的是共用实现名。§7.1 #6 只要求"本帧不提交 +
    # 粘性错误"、§6.2 只规定消息里的 `unsupported force format 0x…` 片段，**没有**规定
    # 前缀必须等于入口符号 ⇒ 断言相等就是拿测试固化一个规格没写的期望
    # （agents-rules §5）。该观察登记在计划书 WP-F 记录，不在本轮改实现。
    order = [
        "layer_update_pixels_with_format",
        "layer_create_context",
        "layer_update_pixels",
        "layer_set_click_through",
        "layer_set_position",
        "layer_set_size",
        "layer_clear",
        "layer_destroy_context",
    ]
    accept: dict[str, set[str]] = {
        "layer_update_pixels_with_format": {"layer_update_pixels_with_format", "layer_update_pixels"}
    }
    for c in r["created"] + r["calls"]:
        assert c["err"], f"{c['label']} 被拒绝却没留下任何诊断（§7.2）"
        named = next((s for s in order if s in c["err"]), None)
        assert named is not None, f"{c['label']} 的诊断没点名任何导出符号：{c['err']!r}"
        assert named in accept.get(c["sym"], {c["sym"]}), (
            f"{c['label']} 的诊断点名了 {named}，疑似读到上一次调用的残留：{c['err']!r}"
        )


def test_unknown_and_repeated_destroy_do_not_crash(artifact):
    """§7.1 #10 + §4.4：double-destroy、伪造句柄、cleanup 后残留句柄 = 定义良好的拒绝。

    崩溃与否的判据是子进程退出码（`_run_probe` 里的 rc 断言）；本条只追加"拒绝要可读"。
    """
    r = _run_probe(
        """
shim.layer_destroy_context(VP(0x1234))
first = err_text()
shim.layer_destroy_context(VP(0x1234))
second = err_text()
for v in (0x0, 0x1, 1 << 63, 0xFFFFFFFFFFFFFFFF, 0x7FFFFFFFFFFE):
    shim.layer_destroy_context(VP(v))
shim.layer_shell_cleanup()          # 未 init 也 cleanup：§7.1 #2"可重入、无失败形态"
shim.layer_shell_cleanup()
third = err_text()
emit({"first": first, "second": second, "third": third})
"""
    )
    assert r["first"], "拒绝必须留下诊断"
    assert r["first"] == r["second"], "同一非法输入的两次拒绝应给出同一条诊断"
    assert "layer_destroy_context" in r["third"]


def test_repeated_init_cleanup_does_not_grow_threads_or_fds(artifact):
    """§6.5 稳态上界的失败路径版本：失败的 init 不得留下线程、fd 或 ring memfd。

    成功路径的收支（1 socket + 2 pipe + ctx×RING_DEPTH 个 memfd）需要真实 compositor，
    那是 WP-G 的 T6（本机 L3）；无 compositor 时泵线程不 spawn（§4.2），所以这里
    `memfd == 0` 是**绝对**断言而不是 delta：任何一枚都意味着 ring 在还没有 surface
    的时候就被建了，或失败路径没走 §4.5 的释放顺序。
    """
    r = _run_probe(
        """
base = census()
for _ in range(3):
    shim.layer_shell_init()
    shim.layer_create_context(None, 400, 400, 0, 0)
    shim.layer_update_pixels(VP(0x1234), BUF, 400, 400)
    shim.layer_shell_cleanup()
after = census()
emit({"base": base, "after": after})
"""
    )
    assert r["after"]["threads"] == r["base"]["threads"], r
    assert r["after"]["fds"] == r["base"]["fds"], r
    assert r["after"]["memfd"] == 0 == r["base"]["memfd"], r


def test_facade_loads_artifact_and_reports_unavailable_without_a_session(artifact):
    """`wayland_layer.py` 面对真实产物的启动路径（§7.1 #1/#6 + §4.7 第 10 行）。

    * `_load()` 不抛 `AttributeError` —— #6 缺席时整个后端会在这里死掉，是真实故障形态。
    * `is_available()` 在无会话时是 False 而不是异常。
    * `enable()` 抛 RuntimeError 且**不留句柄** —— §4.7 第 10 行"穿透模式从未进入"
      的 Python 侧对应物：桌宠退回普通 Qt 窗口，而不是持有一个假句柄每帧静默丢帧。
    """
    r = _run_probe(
        """
from meapet.desktop import wayland_layer as wl
b = wl.WaylandLayerBackend()
shim = b._load()
optional = sorted(getattr(b, "_optional", []))
available = b.is_available()
err = None
try:
    b.enable(None, 400, 400, 0, 0)
except Exception as exc:
    err = type(exc).__name__ + ": " + str(exc)
emit({"loaded": shim is not None, "optional": optional, "available": available,
      "enable_error": err, "ctx_after_enable": bool(b._ctx),
      "pixel_format_attr": b._pixel_format})
"""
    )
    assert r["loaded"] is True
    assert r["optional"] == [
        "layer_clear",
        "layer_set_position",
        "layer_set_size",
    ], "§7.1 标“可选”的三个符号必须被探测到；缺席即后端整体判死"
    assert r["available"] is False
    assert r["enable_error"] is not None and r["enable_error"].startswith("RuntimeError")
    assert r["ctx_after_enable"] is False, "失败的 enable() 不得留下 ctx 句柄（I7）"
    assert r["pixel_format_attr"] == 0, "默认值 0 = 自动选格式（§6.2 的 force 值域）"
