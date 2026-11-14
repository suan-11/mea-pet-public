"""更新覆盖的数据保护白名单：用户数据绝不允许被更新覆盖。

背景（docs/development-suggestions.zh-CN.md §1.4 / §7 风险登记册）
------------------------------------------------------------------
便携契约由 tests/test_frozen_paths.py 锁定：冻结态的运行数据根是
`sys._MEIPASS`（即 dist/MeaPet/_internal），config.json、mea_memory.db、
日志与各类缓存都随发行文件夹走。这意味着「解压新包直接覆盖」会连用户
数据一起碾掉——本模块就是挡在那条路上的契约。

契约内容
--------
* `UPDATE_PROTECTED`：受保护路径的唯一真值表（相对数据根）。
* `is_protected_relpath(rel)`：单条路径是否受保护。
* `assert_update_safe(relpaths)`：未来更新器在落盘前必须整表过一遍的
  覆盖清单闸门——任何受保护路径出现即抛 `UpdateProtectedError`，
  宁可更新失败，不可覆盖用户数据（fail-closed）。

路径口径：rel 是相对**便携数据根**（get_data_dir()，冻结态即 _internal）
的路径；也容忍以 "_internal/" 开头的写法（更新器若从 dist/MeaPet 根
枚举路径，剥掉这一层前缀后按同一张表判）。Windows 路径大小写不敏感，
比较前统一小写；反斜杠统一成正斜杠。

迁移预留（长远）
----------------
路线图要求后续把 userdata/ 与 addons/ 抽离 _internal、放到 MeaPet.exe
同级目录。届时本契约的语义不变，只需把判定根扩成两处（数据根 + exe
同级目录），并参照 meapet/paths.py:migrate_legacy_home_data 的先例做
「只补缺、不覆盖」的一次性迁移。在那之前，白名单就是更新器的硬边界。

本模块不证明什么：它只约束「覆盖清单」这一入口；真正执行复制/解压的
更新器（尚未实现）必须自觉调用 assert_update_safe，绕过它直接写盘不在
本契约的防护范围内。
"""
from __future__ import annotations

import re
from typing import Iterable

# 数据根下受保护的文件（精确匹配，大小写不敏感）。
PROTECTED_FILES = ("config.json",)

# 数据根下受保护的文件名前缀：SQLite 主库与它的 -wal/-shm/-journal 边车。
PROTECTED_FILE_PREFIXES = ("mea_memory.db",)

# 受保护的目录：目录本身与其下所有内容都不可覆盖。
PROTECTED_DIRS = (
    "logs",
    "voice_cache",
    "voice_asr",
    "addons",
    "audio_cache",
)

#: 人读的唯一真值表（测试钉死这张表；改表必须有意识地同步测试与文档）。
UPDATE_PROTECTED: tuple[str, ...] = (
    *PROTECTED_FILES,
    *(prefix + "*" for prefix in PROTECTED_FILE_PREFIXES),
    *(name + "/" for name in PROTECTED_DIRS),
)

# 绝对路径（POSIX 风格或 Windows 盘符）——覆盖清单只收相对路径。
_ABSOLUTE_RE = re.compile(r"^(?:/|[A-Za-z]:)")


class UpdateProtectedError(RuntimeError):
    """覆盖清单触碰了受保护的用户数据；更新必须中止。"""

    def __init__(self, violations: tuple[str, ...]):
        self.violations = violations
        super().__init__(
            "更新覆盖清单触碰受保护的用户数据："
            + "、".join(violations)
            + "（契约见 meapet/update_policy.py 与 docs/update-and-release.zh-CN.md）"
        )


def _normalize(rel: str) -> str:
    """反斜杠转正斜杠、去掉开头的 ./ 层；不改大小写之外的语义。"""
    text = str(rel).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def is_protected_relpath(rel: str) -> bool:
    """判断一条相对数据根的路径是否落在保护白名单内。

    命中规则（全部大小写不敏感）：
    * 根下 config.json；
    * 根下以 mea_memory.db 开头的文件（含 -wal/-shm/-journal 等边车）；
    * logs/ voice_cache/ voice_asr/ addons/ audio_cache/ 目录本身及其下任意深度；
    * 以 _internal/ 开头时剥掉该前缀再按上述规则判。

    注意：只认数据根这一层。程序资产里的同名文件（如 meapet/assets/config.json）
    不在保护范围——那是可更新的程序文件，不是用户数据。
    """
    text = _normalize(rel).lower()
    if not text:
        return False
    segments = [seg for seg in text.split("/") if seg]
    if not segments:
        return False
    if segments[0] == "_internal":
        segments = segments[1:]
        if not segments:
            return False
    head = segments[0]
    if head in PROTECTED_DIRS:
        return True
    if len(segments) == 1:
        if head in PROTECTED_FILES:
            return True
        if any(head.startswith(prefix) for prefix in PROTECTED_FILE_PREFIXES):
            return True
    return False


def assert_update_safe(relpaths: Iterable[str]) -> None:
    """更新器落盘前的强制闸门：整张覆盖清单过白名单。

    任何受保护路径、绝对路径、.. 逃逸或空路径出现即抛 UpdateProtectedError
    （fail-closed：清单口径不明时同样拒绝）。全部合法时静默返回 None。
    """
    violations: list[str] = []
    for rel in relpaths:
        text = _normalize(rel)
        if not text:
            violations.append("<空路径>")
            continue
        if _ABSOLUTE_RE.match(text):
            violations.append(f"{rel}（绝对路径，清单只收相对路径）")
            continue
        if any(seg == ".." for seg in text.split("/")):
            violations.append(f"{rel}（含 .. 逃逸）")
            continue
        if is_protected_relpath(text):
            violations.append(str(rel))
    if violations:
        raise UpdateProtectedError(tuple(violations))
