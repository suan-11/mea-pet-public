#!/usr/bin/env python3
"""L3 size-boundary probe: does the Rust bridge map the two ENDPOINT sizes?

Subject under test: `WaylandLayer.enable/update_pixels/disable` at the extremes of
the geometry the bridge itself promises to accept — spec §4.6's `w*h <= 16_777_216`
cap and the `layout()` path that turns (w, h) into a memfd length.  The sizes come
from ~/.Athena/projects/meapet/reference/plan-rust-layer-shell-bridge.md §0.1, which assigned "1x1 与 2048x2048
两端各测一次" to WP-G; 512x512 rides along as the positive control, because it is
the only size any artifact of this project has ever mapped.

What it decides (rc 0 only if every size passes every clause):
  * the compositor has exactly ONE meapet layer mapped while the ctx is live
    (`niri msg -j layers`; a cap that silently refuses to map is the failure this
    probe exists to catch),
  * at least one real-size frame is accepted with no sticky error
    (`layer_last_error`, bound locally — the facade does not bind that symbol),
  * teardown returns the process to its own baseline: 0 layers, 0 meapet memfds.

Failure mode of this probe (agents-rules §1, three parts):
  benefit  = the two quantities it asserts are read from the compositor and from
             `/proc/self/fd`, not from the bridge's own log lines, so an app-side
             echo cannot make it green;
  crash    = it maps a real OVERLAY on the user's desktop.  If some other process
             (the pet itself) already owns a meapet layer, every count below is
             attributed to the wrong owner and the run would read as a pass while
             measuring someone else's surface;
  backstop = four admissibility gates abort with distinct exit codes before the
             loop: QPA is wayland, the bridge initialises, the loaded artifact
             exports `layer_last_error` (provenance — the old C build does not),
             and the *baseline* meapet layer count is 0.

What this CANNOT decide:
  * it does not establish spec §7.3-6's fd bound for a real multi-ctx process.
    Here ctx == 1 is a fact about this process's own call count, not a measurement
    (the criticism registered against `layer_toggle_cycle_probe.py` applies here
    too), so `memfd <= RING_DEPTH` is reported as an observation, never as the
    verdict;
  * whether the pixels land where the app asked, or are visible at all, is not
    measurable here: niri reports no geometry for layer surfaces (I3) and the
    surfaces mapped by this probe are fully transparent on purpose.
"""
from __future__ import annotations

import os
import sys
import time
from ctypes import c_char_p
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layer_toggle_cycle_probe import (  # noqa: E402  (single home for the census helpers)
    RING_DEPTH_SRC,
    memfd_fds,
    read_ring_depth,
    surfaces,
)

DEFAULT_SIZES = "1x1,512x512,2048x2048"
HOLD_SECONDS = 2.0
FRAMES_PER_SIZE = 20


def parse_sizes(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        w_txt, _, h_txt = part.partition("x")
        out.append((int(w_txt), int(h_txt)))
    if not out:
        raise ValueError(f"no sizes parsed from {spec!r}")
    return out


def main(argv: list[str]) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
    sizes = parse_sizes(argv[1] if len(argv) > 1 else DEFAULT_SIZES)

    from PyQt5.QtCore import QCoreApplication
    from PyQt5.QtGui import QImage
    from PyQt5.QtWidgets import QApplication

    from meapet.desktop.wayland_layer import get_backend

    app = QApplication.instance() or QApplication(sys.argv[:1])

    qpa = app.platformName()
    if qpa != "wayland":
        print(f"VOID(1): platformName={qpa!r} -- not wayland, the bridge would "
              f"open its own connection against a session Qt is not in")
        return 2
    if surfaces() != 0:
        print(f"VOID(2): {surfaces()} meapet layer(s) already mapped by someone "
              f"else -- counts would be attributed to the wrong owner.  Stop the "
              f"pet first (this probe maps a real OVERLAY itself).")
        return 2

    backend = get_backend()
    if not backend.is_available():
        print("VOID(3): layer_shell_init() failed -- no usable bridge")
        return 2

    shim = backend._shim
    try:
        shim.layer_last_error.restype = c_char_p
        shim.layer_last_error.argtypes = []
    except AttributeError:
        print("VOID(4): the loaded artifact does not export layer_last_error "
              "-- that is the OLD C build, not native/layer_shell/. "
              "Run `bash build_layer_shell.sh` first.")
        return 2

    ring_depth = read_ring_depth(Path(__file__).resolve().parents[1])
    print(f"RING_DEPTH={ring_depth} (read from {RING_DEPTH_SRC})")

    def last_err() -> str:
        raw = shim.layer_last_error()
        return raw.decode("utf-8", "replace") if raw else ""

    def settle(seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.005)

    failures = []
    for w, h in sizes:
        base_fds = memfd_fds()
        note = ""
        try:
            backend.enable(None, w, h, 0, 0)
            backend.set_click_through(True)
            mapped = surfaces()
            img = QImage(w, h, QImage.Format_RGBA8888)
            img.fill(0)  # fully transparent: nothing hides the user's desktop
            submitted = 0
            err = ""
            for _ in range(FRAMES_PER_SIZE):
                backend.update_pixels(img)
                submitted += 1
                settle(HOLD_SECONDS / FRAMES_PER_SIZE)
                err = last_err()
                if err:
                    break
            peak_fds = memfd_fds()
            backend.disable()
            settle(0.2)
            after = surfaces()
            residue = memfd_fds() - base_fds
        except Exception as exc:  # a refused size is a result, not a crash
            failures.append(f"{w}x{h}: raised {type(exc).__name__}: {exc}")
            print(f"FAIL {w}x{h}: raised {type(exc).__name__}: {exc}")
            continue

        ok = (
            mapped == 1
            and submitted == FRAMES_PER_SIZE
            and not err
            and after == 0
            and residue == 0
        )
        verdict = "PASS" if ok else "FAIL"
        if not ok:
            failures.append(f"{w}x{h}: {verdict}")
        print(
            f"{verdict} {w}x{h}: mapped={mapped} frames={submitted}/{FRAMES_PER_SIZE} "
            f"memfd_peak={peak_fds} residue={residue} layers_after={after} "
            f"last_error={err!r}{note}"
        )
        print(
            f"  (observation, NOT the §7.3-6 verdict: ctx==1 here is this process's "
            f"own call count, so memfd_peak<={ring_depth} proves nothing about a "
            f"multi-ctx process)"
        )

    for v in failures:
        print(f"VERDICT: FAIL {v}")
    if failures:
        return 4
    print(f"VERDICT: all {len(sizes)} sizes mapped, submitted and torn down clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
