#!/usr/bin/env python3
"""F7 — what the compositor itself tells us about our own surface.

The positioning principle is only needed because no protocol hands the client
its geometry. This probe makes that a measured statement instead of an
assumption: with our layer surface alive, dump `niri msg -j layers` and list
every key the compositor reports for it.

Transient: creates one OVERLAY surface and destroys it in the same process.
"""
import ctypes
import json
import os
import subprocess
import sys
import time

REPO = "/home/flexiatom/work/met-pet-public-fix-linux-compat"
sys.path.insert(0, REPO)
os.chdir(REPO)

W, H, X, Y = 420, 320, 500, 200


def main():
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv[:1])
    from meapet.desktop import wayland_layer as wl
    if not wl._backend.is_available():
        print(json.dumps({"fatal": "layer_shell_init failed"}))
        return
    shim = wl._backend._load()
    ctx = shim.layer_create_context(None, W, H, X, Y)
    if not ctx:
        print(json.dumps({"fatal": "layer_create_context NULL"}))
        return
    px = (ctypes.c_ubyte * (W * H * 4))()
    for _ in range(8):  # first upload is dropped until layer_surface.configure arrives
        shim.layer_update_pixels_with_format(ctx, px, W, H, 0)
        end = time.time() + 0.05
        while time.time() < end:
            app.processEvents()
            time.sleep(0.005)

    r = subprocess.run(["niri", "msg", "-j", "layers"], capture_output=True)
    raw = r.stdout.decode()
    shim.layer_destroy_context(ctx)
    shim.layer_shell_cleanup()
    for _ in range(20):
        app.processEvents()

    parsed = None
    try:
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        print("parse error:", exc)

    mine = None
    rows = parsed if isinstance(parsed, list) else []
    for row in rows:
        if isinstance(row, dict) and "meapet" in json.dumps(row, ensure_ascii=False):
            mine = row
            break

    print(json.dumps({
        "niri_msg_layers_rc": r.returncode,
        "rows": None if parsed is None else len(parsed),
        "our_row": mine,
        "our_row_keys": None if mine is None else sorted(mine.keys()),
        "geometry_keys_present": None if mine is None else sorted(
            k for k in mine if any(t in k.lower() for t in ("x", "y", "w", "h", "geo", "pos", "size", "loc"))),
        "raw_head": raw[:400],
        "stderr_head": r.stderr.decode()[:200],
    }, ensure_ascii=False, indent=1))


main()
