#!/usr/bin/env python3
"""L1 gate self-test for scripts/layer_desktop_probe.py's verdict logic.

Why this file exists (agents-rules §6, §8, §9):
  `report()` is the thing that turns 700 desktop samples into "第 2 项 通过", and a
  clause inside it that can never fire is worse than no clause -- it produces the
  word PASS.  Reading the code cannot settle that: four of the six branches below
  had **zero** hits across every artifact collected in WP-G (2 surfaces, blank rect,
  live-but-unmapped ctx, error lines), which is exactly the state of a gate whose
  discriminating power has never been demonstrated.  So each branch is driven with a
  synthetic row set here, in a machine with no display server and no compositor
  (`report()` is pure over its `rows` argument -- nothing in it touches grim, /proc
  or niri).

Failure mode of this test itself (agents-rules §1, three parts):
  benefit  = a future edit that makes a clause unreachable (renamed key, inverted
             comparison, `>=` -> `>`) fails here instead of silently widening PASS;
  crash    = it asserts on the *prefix and the item tag* of each verdict string, so
             rewording a message still passes, but re-classifying severity
             (NOTE -> FAIL, or dropping the "FAIL" prefix) does not -- the risk is
             that both this test and report() are edited to match each other;
  backstop = the severity contract is asserted twice from opposite ends: a NOTE-only
             run must return 0 (so a NOTE can never be a hidden blocker) and any
             FAIL/VOID must return 4 (so a blocker can never be a hidden 0).  An
             author who loosens one side trips the other.

What this CANNOT decide: nothing about the real desktop.  L1 over synthetic rows is
not L3 evidence (agents-rules §10) -- the row values here are invented to name a state,
and the only claim is "given that state, report() says this".
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.layer_desktop_probe import MIN_CONTROL_PAIRS, report  # noqa: E402

# Row keys read by report(): see its body (inside/outside/crop_nonblank/spread/
# surfaces/memfd/rss_kB/threads/mean_RGB/t).
BASE: dict[str, Any] = {
    "t": 0.0,
    "inside_changed": None,
    "outside_changed": None,
    "crop_color_spread": 255,
    "crop_nonblank": True,
    "mean_RGB": [30, 30, 30],
    "memfd": 3,
    "fds": 45,
    "rss_kB": 140000,
    "threads": 22,
    "surfaces": 1,
}


def rows(n: int, *, step: float = 0.34, **overrides: Any) -> list[dict]:
    """`n` samples at the default-ish cadence; every consecutive pair "changed"."""
    out = []
    for i in range(n):
        row = dict(BASE)
        row["t"] = round(i * step, 3)
        if i:
            row["inside_changed"] = True
            row["outside_changed"] = True
        row.update(overrides)
        out.append(row)
    return out


def judge(row_list: list[dict], logpath: str = "") -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = report(row_list, logpath, 783305)
    return rc, buf.getvalue()


# ---------------------------------------------------------------- happy paths


def test_clean_long_run_passes():
    rc, text = judge(rows(120))
    assert rc == 0, text
    assert "VERDICT: PASS" in text


def test_note_alone_does_not_change_rc():
    """A run with no usable attribution control is still a PASS (rc 0).

    This is the severity contract the WP-G record leans on: the 45 s 第 2 项 window
    contains no mode switch, hence no control pairs, and that is a disclosure, not a
    failure.  If it could set rc, exit codes would stop meaning "第 2 项 is tickable".
    """
    rc, text = judge(rows(120))
    assert "NOTE(第2项归因)" in text, text
    assert "VERDICT: PASS" in text
    assert rc == 0, text


def test_strictly_above_control_passes_attribution():
    """Mapped 100% vs an un-mapped control that is mostly static -> attributable."""
    row_list = rows(100)
    # Turn the tail of the run into an un-mapped stretch whose digest changes only
    # every fifth pair: the control rate must sit strictly below the mapped rate.
    for i, row in enumerate(row_list[50:], start=50):
        row["surfaces"] = 0
        row["memfd"] = 0
        row["inside_changed"] = (i % 5 == 0)
    rc, text = judge(row_list)
    assert rc == 0, text
    assert "NOT mapped" in text
    assert "FAIL(第2项归因)" not in text


# ------------------------------------------------------- blocking conditions


def test_zero_samples_is_void():
    rc, text = judge([])
    assert rc == 4
    assert "VOID" in text


def test_frozen_frame_in_a_mapped_stretch_fails():
    row_list = rows(120)
    row_list[60]["inside_changed"] = False
    rc, text = judge(row_list)
    assert rc == 4, text
    assert "FAIL(第2项)" in text


def test_unmapped_only_run_is_void_not_pass():
    """No OVERLAY mapped at all must never read as "the rect kept changing"."""
    rc, text = judge(rows(120, surfaces=0, memfd=0))
    assert rc == 4, text
    assert "VOID(第1/第2项)" in text


def test_too_few_mapped_pairs_is_void():
    """Window long enough, but the mapped stretch is not -> sample-size VOID.

    Deliberately a long window: if it were short, the assertion would also pass via
    the "window < 30s" clause and this branch's own power would go undemonstrated.
    """
    row_list = rows(120)
    for row in row_list[10:]:
        row["surfaces"] = 0
        row["memfd"] = 0
    rc, text = judge(row_list)
    assert rc == 4, text
    assert "VOID(第2项)" in text and "mapped pairs" in text


def test_short_window_is_void():
    """39 mapped pairs (>= 30) but a 3.9 s window -> only the duration clause can fire."""
    rc, text = judge(rows(40, step=0.1))
    assert rc == 4, text
    assert "VOID(第2项)" in text and "30s" in text
    assert "mapped pairs" not in text


def test_second_surface_fails_item_one():
    """The pre-fix double-enable shape: 2 surfaces, 6 memfds (see WP-G record)."""
    rc, text = judge(rows(120, surfaces=2, memfd=6))
    assert rc == 4, text
    assert "FAIL(第1项)" in text


def test_blank_rect_while_mapped_fails_item_one():
    rc, text = judge(rows(120, crop_nonblank=False, crop_color_spread=0))
    assert rc == 4, text
    assert "FAIL(第1项)" in text


def test_live_ctx_with_no_surface_fails_i5():
    row_list = rows(120)
    for row in row_list[50:56]:
        row["surfaces"] = 0
    rc, text = judge(row_list)
    assert rc == 4, text
    assert "FAIL(I5/第2项)" in text


def test_single_unmapped_sample_is_not_an_i5_failure():
    """One straddling sample is legitimate (enable() maps after allocating)."""
    row_list = rows(120)
    row_list[50]["surfaces"] = 0
    rc, text = judge(row_list)
    assert rc == 0, text
    assert "FAIL(I5" not in text


def test_layer_error_line_in_the_log_fails():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "app.log"
        p.write_text("[layer] ✗ 第 1 次拿到空帧\n", encoding="utf-8")
        rc, text = judge(rows(120), str(p))
    assert rc == 4, text
    assert "FAIL(第2项)" in text and "✗" in text


def test_control_at_least_as_high_as_mapped_fails_attribution():
    """Backdrop changing as often as the pet rect => not attributable to the OVERLAY."""
    row_list = rows(80)
    tail = 20 + MIN_CONTROL_PAIRS
    for row in row_list[-tail:]:
        row["surfaces"] = 0
        row["memfd"] = 0
        row["inside_changed"] = True
    rc, text = judge(row_list)
    assert rc == 4, text
    assert "FAIL(第2项归因)" in text


def test_tiny_control_cannot_fail_attribution():
    """Measured 2026-09-20: 133/133 vs 1/1 must be a NOTE, not a FAIL.

    One observation is not a baseline (agents-rules §8), and the first version of the
    attribution clause turned this round's cleanest run into a false FAIL.
    """
    row_list = rows(100)
    for row in row_list[-2:]:
        row["surfaces"] = 0
        row["memfd"] = 0
    rc, text = judge(row_list)
    assert "FAIL(第2项归因)" not in text, text
    assert "NOTE(第2项归因)" in text
    assert rc == 0, text


def test_control_at_least_MIN_PAIRS_still_fails_when_it_ties():
    """The guard is not a blanket exemption: at MIN_CONTROL_PAIRS the tie FAILs."""
    row_list = rows(100)
    tail = MIN_CONTROL_PAIRS + 20
    for row in row_list[-tail:]:
        row["surfaces"] = 0
        row["memfd"] = 0
        row["inside_changed"] = True
    rc, text = judge(row_list)
    assert rc == 4, text
    assert "FAIL(第2项归因)" in text


@pytest.mark.parametrize("prefix", ["FAIL", "VOID"])
def test_every_blocking_prefix_becomes_rc4(prefix: str):
    """The severity contract, asserted from the string side."""
    row_list = rows(120)
    if prefix == "FAIL":
        row_list[60]["inside_changed"] = False
    else:
        for row in row_list:
            row["surfaces"] = 0
    rc, text = judge(row_list)
    assert any(line.startswith(f"VERDICT: {prefix}") for line in text.splitlines()), text
    assert rc == 4
