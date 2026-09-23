#!/usr/bin/env python3
"""L3 facade probe: 10 real create/destroy cycles against the live compositor.

Subject under test (the real objects, no fake):
  meapet.desktop.wayland_layer.WaylandLayerBackend -- enable / set_position /
  set_size / update_pixels / set_click_through / disable, on top of the Rust
  bridge that build_layer_shell.sh produced.

What it decides (~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md §7.3-6 resource half, T5-6):
  after each `disable()` the process must return to its baseline count of
  `/memfd:meapet-px` descriptors, and its thread count must never end a cycle
  *above* baseline.  A cycle that leaves a descriptor behind is an I5 leak, and
  10 cycles turn it into 10.  While a ctx is live the bound is RING_DEPTH
  (native/layer_shell/src/ring.rs:62) memfds, so the peak is checked against
  `ctx * RING_DEPTH` with ctx = 1 by construction here -- measured, not assumed.
  fd and thread get different criteria on purpose: descriptors exist only while
  a ctx does (equality is the right question), while Qt retires its own startup
  threads, so demanding thread equality fails a healthy process.  spec §9's word
  is 不增长, and the 2026-09-20 run showed exactly why: 5 -> 4 on cycle 1, flat
  thereafter.

Failure mode of this probe (agents-rules §1, three parts):
  benefit  = deterministic, in-process, no UI automation, and it census its OWN
             pid, so there is no "wrong pid" failure mode like the shell probe;
  crash    = it maps a real OVERLAY layer on the user's desktop.  If the QPA is
             not wayland, or the artifact is not the Rust build, the run would
             look like a pass while measuring nothing;
  backstop = three admissibility gates before the loop, each of which can abort
             with a distinct exit code: platformName, `layer_last_error`
             presence (provenance -- the old C build at repo root lacks it), and
             a nonzero memfd peak (proof the surface really got buffers).

What this CANNOT decide: it is a single ctx driven from one process.  It does
not exercise Qt hide()/re-show geometry, the app's own timer, or two surfaces
alive at once -- those belong to the live-app run in scripts/layer_stability_probe.sh.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import c_char_p
from pathlib import Path

# This file sits directly in scripts/, so the repo root is parents[1]; sys.path[0]
# is the script's own directory and would otherwise make `import meapet` fail.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MEMFD_TAG = "/memfd:meapet-px"
# Single source of truth for the bound: agents-rules §7 forbids a second literal
# next to native/layer_shell/src/ring.rs's `RING_DEPTH`.
RING_DEPTH_SRC = "native/layer_shell/src/ring.rs"
CYCLES = 10
FRAMES_PER_CYCLE = 30
W, H = 320, 320


def read_ring_depth(repo_root: Path) -> int:
    """Read RING_DEPTH out of the Rust source instead of restating it."""
    text = (repo_root / RING_DEPTH_SRC).read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.split("//")[0]
        if "const RING_DEPTH" in line:
            return int(line.split("=")[1].strip().rstrip(";"))
    raise RuntimeError(f"const RING_DEPTH not found in {RING_DEPTH_SRC}")


def memfd_fds() -> int:
    """Own-process count of meapet memfd descriptors.

    `os.readlink` is the `-l` half of the plan's `ls -l /proc/<pid>/fd`: the
    name lives in the symlink *target*, so listing names alone would return 0
    forever and read as "no leaks" (agents-rules §8).
    """
    n = 0
    base = "/proc/self/fd"
    for entry in os.listdir(base):
        try:
            target = os.readlink(f"{base}/{entry}")
        except OSError:
            continue
        if MEMFD_TAG in target:
            n += 1
    return n


def threads() -> int:
    with open("/proc/self/status", "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("Threads:"):
                return int(line.split()[1])
    return -1


def surfaces() -> int:
    """meapet layer surfaces the compositor currently has mapped."""
    try:
        out = subprocess.run(
            ["niri", "msg", "-j", "layers"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except Exception:
        return -1
    return out.count('"namespace":"meapet"')


def drain(seconds: float) -> None:
    """Pump the Qt event loop so the bridge's pump thread can submit frames.

    Without this the shim never gets a chance to attach buffers and memfd stays
    0, which would make the whole census vacuous.
    """
    from PyQt5.QtCore import QCoreApplication

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

    from PyQt5.QtGui import QImage
    from PyQt5.QtWidgets import QApplication

    from meapet.desktop.wayland_layer import get_backend

    app = QApplication.instance() or QApplication(sys.argv[:1])

    qpa = app.platformName()
    if qpa != "wayland":
        print(f"ABORT(1): platformName={qpa!r} -- not wayland, the bridge would "
              f"open its own connection against a session Qt is not in")
        return 1

    backend = get_backend()
    if not backend.is_available():
        print("ABORT(2): layer_shell_init() failed -- no usable bridge")
        return 2

    ring_depth = read_ring_depth(Path(__file__).resolve().parents[1])
    print(f"RING_DEPTH={ring_depth} (read from {RING_DEPTH_SRC})")

    shim = backend._shim
    try:
        shim.layer_last_error.restype = c_char_p
        shim.layer_last_error.argtypes = []
    except AttributeError:
        print("ABORT(3): the loaded artifact does not export layer_last_error "
              "-- that is the OLD C build, not native/layer_shell/. "
              "Run `bash build_layer_shell.sh` first.")
        return 3

    def err() -> str:
        raw = shim.layer_last_error()
        return raw.decode("utf-8", "replace") if raw else ""

    base_memfd, base_threads = memfd_fds(), threads()
    print(f"baseline memfd={base_memfd} threads={base_threads} "
          f"qpa={qpa} cycles={CYCLES}")
    print(f"{'cyc':>3} {'pos':>11} {'memfd/thr_live':>15} {'surf_live':>9} "
          f"{'memfd_after':>11} {'threads_after':>13} {'last_err':>8}")

    leaks = 0
    thread_growth = 0
    peak_live = 0
    after_threads_series: list[int] = []
    live_threads_series: list[int] = []
    err_before = err()
    for i in range(1, CYCLES + 1):
        # A distinct non-zero position per cycle: §7.3-1 requires the surface to
        # land where it was asked, and `anchor=0` without margins centres it
        # (agents-rules §1 table row 2), so an all-zero-position run cannot tell
        # "correct" from "always the same place".
        x, y = 120 + i * 20, 140 + i * 15
        backend.enable(None, W, H, x, y)
        backend.set_position(x, y)
        backend.set_size(W, H)
        backend.set_click_through(True)

        img = QImage(W, H, QImage.Format_RGBA8888)
        img.fill(0x40FF8040)
        for _ in range(FRAMES_PER_CYCLE):
            backend.update_pixels(img)
            drain(0.012)

        live_memfd, live_surf, live_thr = memfd_fds(), surfaces(), threads()
        peak_live = max(peak_live, live_memfd)
        live_threads_series.append(live_thr)
        backend.disable()
        drain(0.2)
        after_memfd, after_threads = memfd_fds(), threads()
        after_threads_series.append(after_threads)
        # fd and thread use DIFFERENT criteria, on purpose:
        #   * fds are only ever created by a live ctx, so returning to the exact
        #     baseline is the right question;
        #   * threads are not.  ~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md §9 phrases the requirement as
        #     线程数**不增长**, and Qt itself retires startup threads, so a
        #     baseline equality check fails on a healthy process (measured
        #     2026-09-20: 5 -> 4 on the very first cycle, then flat).
        if after_memfd != base_memfd:
            leaks += 1
        if after_threads > base_threads:
            thread_growth += 1
        print(f"{i:>3} {f'{x},{y}':>11} {live_memfd:>10}/{live_thr:<3} {live_surf:>9} "
              f"{after_memfd:>11} {after_threads:>13} "
              f"{'new' if err() != err_before else 'none':>8}")

    final_err = err()
    print(f"after {CYCLES} cycles: memfd={memfd_fds()} (baseline {base_memfd}), "
          f"threads={threads()} (baseline {base_threads}), "
          f"peak_live_memfd={peak_live}, surfaces_now={surfaces()}")
    print(f"threads live  series: {live_threads_series}")
    print(f"threads after series: {after_threads_series}")
    print(f"layer_last_error: {'unchanged' if final_err == err_before else repr(final_err)}")

    verdicts = []
    if peak_live == 0:
        verdicts.append("VOID: no cycle ever held a memfd -> frames were never "
                        "attached, this run measured nothing")
    elif peak_live > ring_depth:
        # ctx count here is 1 by construction: `enable()` is called exactly once
        # per cycle and `disable()` clears `_ctx` before the next one.  A peak
        # above RING_DEPTH therefore means an earlier ctx was never torn down.
        verdicts.append(f"FAIL(I5): peak_live_memfd={peak_live} > 1 ctx x "
                        f"RING_DEPTH={ring_depth} -- a previous ctx survived")
    if leaks:
        verdicts.append(f"FAIL(I5): {leaks}/{CYCLES} cycles left memfd descriptors "
                        f"behind (baseline {base_memfd})")
    if thread_growth:
        verdicts.append(f"FAIL(I5): {thread_growth}/{CYCLES} cycles ended above the "
                        f"baseline thread count {base_threads}")
    if surfaces() != 0:
        verdicts.append(f"FAIL(T5-7): {surfaces()} meapet surfaces still mapped after disable()")
    if min(after_threads_series) < base_threads:
        print(f"NOTE: after-cycle threads dipped to {min(after_threads_series)} "
              f"(below the {base_threads} baseline) -- Qt retires startup threads; "
              f"registered as an observation, not a failure (spec §9 says 不增长).")
    if final_err != err_before:
        verdicts.append(f"NOTE: bridge sticky error advanced during the run: {final_err!r}")
    if not verdicts:
        print("VERDICT: PASS -- 10 cycles, memfd back to baseline and no thread "
              "growth after every destroy")
        return 0
    for v in verdicts:
        print("VERDICT: " + v)
    return 4


if __name__ == "__main__":
    sys.exit(main())
