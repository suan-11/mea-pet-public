#!/usr/bin/env python3
"""F6 — continuous readback feasibility (agents-rules §11 feasibility scope).

One-shot capture already passed (probe_pos.py F2). The principle of *continuous*
self-positioning needs a stronger condition: while our own OVERLAY surface is
being re-uploaded, can we repeatedly read the marker back and get a position
each time? This probe answers "does it run and in what shape it fails" — it
does not judge whether the resulting rate is good enough (that is a later stage).

Captures stay in memory; the PNG is unlinked immediately.
"""
import ctypes
import json
import os
import statistics
import subprocess
import sys
import time

REPO = "/home/flexiatom/work/met-pet-public-fix-linux-compat"
TMP = os.environ.get("PP_TMP", "/tmp/meapet-pos")
sys.path.insert(0, REPO)
os.makedirs(TMP, exist_ok=True)
os.chdir(REPO)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

W, H = 420, 320
SZ = 64
TOL = 40
MAGENTA = (255, 0, 255)
FRAMES = 40
TARGET_PATH = [(600, 180)] * 10 + [(700, 260)] * 10 + [(600, 180)] * 20


def cap():
    path = os.path.join(TMP, "f6.png")
    t0 = time.perf_counter()
    r = subprocess.run(["grim", "-t", "png", path], capture_output=True)
    dt = time.perf_counter() - t0
    if r.returncode != 0:
        raise RuntimeError("grim rc=%d %s" % (r.returncode, r.stderr.decode()[:200]))
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    os.unlink(path)
    return a, dt


def find(a, color):
    d = np.abs(a - np.array(color, dtype=np.int16)).sum(axis=2)
    ys, xs = np.nonzero(d <= TOL)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min())


def main():
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv[:1])
    from meapet.desktop import wayland_layer as wl
    if not wl._backend.is_available():
        print(json.dumps({"fatal": "layer_shell_init failed"}))
        return
    shim = wl._backend._load()

    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    rgba[20:20 + SZ, 20:20 + SZ, :3] = MAGENTA
    rgba[20:20 + SZ, 20:20 + SZ, 3] = 255
    buf = np.ascontiguousarray(rgba).reshape(-1)
    ptr = buf.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte))

    ctx = shim.layer_create_context(None, W, H, *TARGET_PATH[0])
    res = {"frames_requested": FRAMES, "miss": 0, "cap_s": [], "err": [], "detected": []}
    t_start = time.perf_counter()
    for i in range(FRAMES):
        x, y = TARGET_PATH[i]
        shim.layer_set_position(ctx, x, y)
        shim.layer_update_pixels_with_format(ctx, ptr, W, H, np.uint32(0))
        end = time.perf_counter() + 0.016
        while time.perf_counter() < end:
            app.processEvents()
        a, dt = cap()
        res["cap_s"].append(round(dt, 4))
        g = find(a, MAGENTA)
        if g is None:
            res["miss"] += 1
            res["detected"].append(None)
        else:
            res["detected"].append(list(g))
            res["err"].append([g[0] - (x + 20), g[1] - (y + 20)])
    total = time.perf_counter() - t_start

    shim.layer_destroy_context(ctx)
    shim.layer_shell_cleanup()
    for _ in range(20):
        app.processEvents()

    caps = sorted(res.pop("cap_s"))
    errs = [abs(e) for pair in res["err"] for e in pair]
    res = dict(res,
               total_s=round(total, 3),
               fps=round(FRAMES / total, 2),
               cap_ms_median=round(caps[len(caps) // 2] * 1000, 1),
               cap_ms_p95=round(caps[int(len(caps) * 0.95)] * 1000, 1),
               err_abs_max_px=max(errs) if errs else None,
               unique_detected_origins=sorted({tuple(v) for v in res["detected"] if v}))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    with open(os.path.join(TMP, "probe_f6.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)


try:
    main()
finally:
    for f in os.listdir(TMP):
        if f.endswith(".png"):
            os.unlink(os.path.join(TMP, f))
