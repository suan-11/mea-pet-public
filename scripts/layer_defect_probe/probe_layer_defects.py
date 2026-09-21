#!/usr/bin/env python3
"""L1 probe for the write-side defects in the Python layer-overlay path.

Subject under test (the real methods, not a replica):
  PetRenderHostMixin._init_layer_overlay_mode / _set_layer_mode / _layer_geometry
  / _push_layer_frame -- meapet/desktop/render_host.py:631-788
The backend is a faithful fake of WaylandLayerBackend (wayland_layer.py:98-199):
same single `_ctx` slot, same "update_pixels no-ops when _ctx is None" rule, same
"enable() overwrites _ctx without destroying the previous one" rule.

Failure mode of this probe (agents-rules §1, three parts):
  benefit  = decides four defects without a compositor and without touching any source;
  crash    = host.show()/LayerDebugPanel.show() map top-level windows, so under xcb or
             wayland this "zero-mutation" probe would itself mutate the desktop;
  backstop = the QPA is forced to offscreen and app.platformName() is checked BEFORE
             any widget exists. The Wayland predicate that gates the code under test is
             faked afterwards, so the faked value never widens what this process maps.

What this probe CANNOT decide (agents-rules §10: L1 is not L3):
  * whether Qt geometry survives hide() under the wayland QPA plugin -- offscreen has no
    compositor, so an invariant result here is about *testability at L1* only;
  * anything about the C bridge -- this file never loads liblayer_shell_shim.so.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# sys.path[0] is this script's own directory, so `import meapet` would fail unless
# the caller sets PYTHONPATH. Bootstrap the repo root so the recorded command works as written.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def drain(app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def main() -> int:
    import os

    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PyQt5.QtCore import QTimer
    from PyQt5.QtGui import QGuiApplication, QImage
    from PyQt5.QtWidgets import QApplication, QLabel, QWidget

    app = QApplication(sys.argv)

    # The guard runs before any widget is shown, on the REAL QPA name.
    real_qpa = app.platformName()
    if real_qpa != "offscreen":
        print(f"ABORT: qpa={real_qpa} would map real windows")
        return 2

    # render_host.py:659 gates the whole path on platformName() == "wayland".
    # Under offscreen it is False, so without this the probe measures its own
    # absence from the code path (that is what the first run of this file did).
    # QPA stays offscreen: only the string Qt reports changes.
    QGuiApplication.platformName = staticmethod(lambda: "wayland")

    import meapet.desktop.wayland_layer as wl

    class FakeBackend:
        """Mirrors WaylandLayerBackend's ctx lifecycle, see wayland_layer.py:111-199."""

        def __init__(self):
            self.enable_calls: list[tuple[int, int, int, int]] = []
            self.created = 0
            self._ctx: int | None = None      # single slot, like the real one
            self.destroyed: list[int] = []
            self.frames = 0
            self.frames_after_destroy = 0
            self.frame_sizes: list[tuple[int, int]] = []

        def enable(self, qwindow, width, height, pos_x, pos_y):
            self.enable_calls.append((width, height, pos_x, pos_y))
            self.created += 1
            # Real code: self._ctx = shim.layer_create_context(...)  -- overwrites.
            self._ctx = self.created
            return self._ctx

        def destroy_context(self):
            if self._ctx is None:             # wayland_layer.py:193-194
                return
            self.destroyed.append(self._ctx)
            self._ctx = None

        def update_pixels(self, image):
            if not self._ctx:                 # wayland_layer.py:155-156: silent no-op
                self.frames_after_destroy += 1
                return
            self.frames += 1
            self.frame_sizes.append((image.width(), image.height()))

    backend = FakeBackend()
    wl.get_backend = lambda: backend
    wl.is_available = lambda: True

    from meapet.desktop.render_host import PetRenderHostMixin

    geom_reads: list[tuple[tuple[int, int, int, int], bool, bool]] = []

    class Host(QWidget, PetRenderHostMixin):
        def _layer_geometry(self):
            value = super()._layer_geometry()
            geom_reads.append((value, self.isVisible(), self.isHidden()))
            return value

    host = Host()
    host.resize(200, 200)
    host.move(100, 100)
    # A canvas larger than its window with a negative offset: the shape the code
    # itself documents at render_host.py:634-636.
    label = QLabel(host)
    label.setGeometry(-300, -250, 800, 800)
    frame = QImage(200, 200, QImage.Format_RGBA8888)
    frame.fill(0x11223344)
    label.render_offscreen = lambda: frame
    host.sprite_label = label

    def push_timers() -> list[QTimer]:
        """QTimers this path owns: 33 ms and wired to _push_layer_frame."""
        return [
            t
            for t in host.findChildren(QTimer)
            if t.interval() == 33 and t.receivers(t.timeout) > 0
        ]

    def timer_table(title: str) -> dict[int, bool]:
        print(f"\n--- timers {title} ---")
        current = id(getattr(host, "_layer_timer", None))
        active: dict[int, bool] = {}
        for i, t in enumerate(push_timers()):
            active[id(t)] = t.isActive()
            role = "current" if id(t) == current else "ORPHAN"
            print(
                f"  [{i}] {role} active={t.isActive()} "
                f"receivers={t.receivers(t.timeout)}"
            )
        return active

    def count_emissions(seconds: float) -> tuple[int, int]:
        """(frames pushed through live ctx, frames dropped into a destroyed ctx)."""
        before_frames, before_after = backend.frames, backend.frames_after_destroy
        drain(app, seconds)
        return (
            backend.frames - before_frames,
            backend.frames_after_destroy - before_after,
        )

    def context_line(prefix: str) -> None:
        print(
            f"  {prefix} enable_calls={backend.enable_calls} "
            f"created={backend.created} destroyed={backend.destroyed} "
            f"ctx_slot={backend._ctx} leaked={backend.created - len(backend.destroyed)}"
        )

    # ---- T1/T2: the startup path --------------------------------------------
    # The runtime shape is "pet window already visible, then first Live2D frame"
    # (render_host.py:623 calls the init from the first-frame callback), so show()
    # first: that is what makes hide() at :750 a state change and not a no-op.
    host.show()
    drain(app, 0.05)
    print(f"  shown: isVisible={host.isVisible()}")
    host._init_layer_overlay_mode()
    timer_table("after _init_layer_overlay_mode()")
    context_line("T1/T2:")
    print(f"  T1 timers wired to _push_layer_frame: {len(push_timers())}")
    print(f"  T2 layer contexts created by one init: {backend.created}")
    print(f"  geometry_reads(after init)={geom_reads}")

    # ---- T3: does every connected timer really tick? ------------------------
    live, dropped = count_emissions(0.66)
    print("\n=== T3 during 660 ms of event loop (penetrate mode) ===")
    print(f"  frames delivered to a live ctx: {live}")
    print(f"  frames dropped into a dead ctx: {dropped}")
    print(f"  -> one 33 ms timer alone would give ~20; total={live + dropped}")

    # ---- T4: switching back to interactive mode -----------------------------
    host._set_layer_mode(False)
    still = [t for t in push_timers() if t.isActive()]
    timer_table("after _set_layer_mode(False)")
    context_line("T4:")
    print(f"  T4 timers still active in interactive mode: {len(still)}")
    live2, dropped2 = count_emissions(0.66)
    print(f"  T4 in interactive mode for 660 ms: live={live2} dead={dropped2}")

    # ---- T5: geometry read after hide() -------------------------------------
    print("\n=== T5 _layer_geometry() reads (value, isVisible, isHidden) ===")
    for row in geom_reads:
        print(f"  {row}")

    # ---- T6: switching to penetrate a second time ---------------------------
    host._set_layer_mode(True)
    context_line("T6:")
    timer_table("after a second _set_layer_mode(True)")
    live3, dropped3 = count_emissions(0.34)
    print(f"  T6 340 ms after re-entry: live={live3} dead={dropped3}")
    host._set_layer_mode(False)
    context_line("T6-end:")

    print("\n=== verdict inputs (read against render_host.py, not against this file) ===")
    print(f"  V1 timer_count={len(push_timers())}")
    print(f"  V2 contexts_created_cumulative={backend.created} (2 of them from the single init)")
    print(f"  V3 contexts_destroyed_total={len(backend.destroyed)}")
    print(f"  V4 active_timers_in_interactive={len([t for t in push_timers() if t.isActive()])}")
    print(f"  V5 frames_into_dead_ctx_total={backend.frames_after_destroy}")
    print(f"  V6 proxy_rect={'set' if getattr(label, '_proxy_rect', None) is not None else 'None'}")

    host.deleteLater()
    drain(app, 0.05)
    return 0


if __name__ == "__main__":
    sys.exit(main())
