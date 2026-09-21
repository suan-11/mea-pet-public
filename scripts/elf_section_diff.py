#!/usr/bin/env python3
r"""Byte-exact ELF section comparison, with the tool proving it can SEE the input.

Why this exists (agents-rules §8/§9, and the defect it replaces): the first version of
this measurement was

    objcopy -O binary --only-section=<name> <file> /tmp/x.bin && sha256sum /tmp/x.bin

over a name list taken from `readelf -SW | awk '/\] \./{print $3}'`.  It printed
"only .note.gnu.build-id differs".  That sentence was false, and false in the worst
available way — every line it emitted said `same`:
  * the awk column index is wrong for `readelf -W`, so the "names" included type
    strings (`PROGBITS`, `SYMTAB`, `STRTAB`) and omitted `.text`, `.rodata`,
    `.eh_frame` and `.strtab`;
  * `objcopy --only-section=<junk>` does not fail, it writes a ZERO-BYTE file, so
    both sides hashed to the empty input and compared equal;
  * non-loaded sections (`.strtab`, `.symtab`, `.shstrtab` — address 0, no `A`
    flag) are never dumped by `objcopy -O binary` at all, so that method can
    *structurally* not see the one section that had in fact changed (85 bytes of
    LLVM module hashes inside local symbol names).

Both failure modes share one property: the check could not report "I did not look".
So the guard is the point of this file, not a wrapper:
  1. the parsed section list must be non-empty, must not contain a name that looks
     like a section TYPE, and its length must equal the number readelf reports;
  2. every section's `offset + size` must fit inside the file, and the read must
     return exactly `size` bytes;
  3. SHT_NOBITS sections (`.bss`, `.tbss`, `.relro_padding`) carry no file content
     — they are reported as `nobits`, never as `same`.
Any violation exits 2 (VOID): a comparison that silently skips input is worth zero,
and a green line is worse than no line.

Usage: scripts/elf_section_diff.py <a.so> <b.so>
Exit: 0 = byte-identical in every visible section, 4 = at least one differs,
      2 = this tool refuses to make a claim (see messages).
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys

# readelf -SW line: `  [ 3] .name  TYPE  Address Off Size ES Flg Lk Inf Al`
SECTION_RE = re.compile(
    r"^\s*\[\s*\d+\]\s+(?P<name>\S+)\s+(?P<type>\S+)\s+"
    r"(?P<addr>[0-9a-f]+)\s+(?P<off>[0-9a-f]+)\s+(?P<size>[0-9a-f]+)"
)
# `readelf -S` prints a leading `There are N section headers` line; trust it as a count.
COUNT_RE = re.compile(r"There are (\d+) section headers")
TYPE_WORDS = {
    "PROGBITS", "SYMTAB", "STRTAB", "NOTE", "NOBITS", "DYNAMIC", "HASH",
    "GNU_HASH", "GNU_VERSYM", "GNU_VERNEED", "RELA", "FINI_ARRAY",
    "INIT_ARRAY", "ARM_EXIDX", "MIPS_ABIFPVR",
}


class Void(Exception):
    pass


def parse(path: str) -> dict[str, tuple[int, int, int, str]]:
    out = subprocess.run(
        ["readelf", "-SW", path], capture_output=True, text=True, check=True
    ).stdout
    declared = None
    for line in out.splitlines():
        m = COUNT_RE.search(line)
        if m:
            declared = int(m.group(1))
    sections: dict[str, tuple[int, int, int, str]] = {}
    for line in out.splitlines():
        m = SECTION_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        stype = m.group("type")
        if name == "NULL":
            continue
        if name.upper() in TYPE_WORDS:
            # A name that is really a type token means the column walk slipped.
            raise Void(f"{path}: parsed section name {name!r} is a section TYPE")
        sections[name] = (
            int(m.group("addr"), 16),
            int(m.group("off"), 16),
            int(m.group("size"), 16),
            stype,
        )
    if not sections:
        raise Void(f"{path}: readelf produced no parsable sections (format changed?)")
    if declared is not None and len(sections) + 1 != declared:
        raise Void(
            f"{path}: parsed {len(sections)} named sections but readelf declares "
            f"{declared} headers — the section set is incomplete, refusing to compare"
        )
    return sections


def read_slice(blob: bytes, path: str, name: str, off: int, size: int) -> bytes:
    if off + size > len(blob):
        raise Void(f"{path}: {name} offset+size runs past EOF ({len(blob)} bytes)")
    data = blob[off : off + size]
    if len(data) != size:
        raise Void(f"{path}: {name} asked for {size} bytes, got {len(data)}")
    return data


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: elf_section_diff.py <a.so> <b.so>")
        return 2
    a_path, b_path = argv[1], argv[2]
    try:
        sa, sb = parse(a_path), parse(b_path)
        ba, bb = open(a_path, "rb").read(), open(b_path, "rb").read()
        if set(sa) != set(sb):
            only_a = sorted(set(sa) - set(sb))
            only_b = sorted(set(sb) - set(sa))
            print(f"VOID: section name sets differ (only in A: {only_a}, "
                  f"only in B: {only_b})")
            return 2
        diffs, nobits = [], []
        for name in sorted(sa):
            addr_a, off_a, size_a, type_a = sa[name]
            _addr_b, off_b, size_b, type_b = sb[name]
            if type_a == "NOBITS" or type_b == "NOBITS":
                nobits.append(name)
                continue
            if size_a != size_b:
                diffs.append((name, size_a, size_b, "size differs"))
                continue
            xa = read_slice(ba, a_path, name, off_a, size_a)
            xb = read_slice(bb, b_path, name, off_b, size_b)
            if xa != xb:
                n = sum(1 for x, y in zip(xa, xb) if x != y)
                diffs.append((name, size_a, size_b, f"{n}/{size_a} bytes differ"))
    except Void as exc:
        print(f"VOID: {exc}")
        return 2

    print(f"sections compared: {len(sa) - len(nobits)} "
          f"(not carried in the file, excluded: {', '.join(nobits) or 'none'})")
    for name, _sz_a, _sz_b, why in diffs:
        print(f"DIFFER {name}: {why}")
    if len(ba) != len(bb):
        print(f"note: file sizes differ ({len(ba)} vs {len(bb)} bytes)")
    if diffs:
        print("VERDICT: sections differ")
        return 4
    print("VERDICT: every file-backed section is byte-identical")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
