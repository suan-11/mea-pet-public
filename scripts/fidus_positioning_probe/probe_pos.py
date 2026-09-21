#!/usr/bin/env python3
"""Fidus-direction self-window positioning — feasibility probe (agents-rules §11).

Answers only "can the principle run on this machine":
  F1 project a marker via layer-shell OVERLAY   F2 capture the output via screencopy
  F3 infer own absolute position from the capture (no geometry protocol used)
  F4 follow a margin-only move                  F5 no residue after teardown

Not accuracy, not latency, not long-run stability (those belong to later stages).
Captures are analysed in memory and the PNG is unlinked immediately.

One process, one surface, Qt owns the wl_connection the shim borrows, so
processEvents() is what makes the C layer's configure/release dispatch happen.
"""
import ctypes
import json
import os
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
X, Y = (int(v) for v in os.environ.get("PP_POS", "600,180").split(","))
X2, Y2 = (int(v) for v in os.environ.get("PP_MOVE", "300,420").split(","))
SZ = 64
# Position anchors use R==B colors on purpose: a channel-order swap inside the
# shim cannot turn them into a false negative, so F3 measures position only.
MARKERS = [
    ("magenta", (255, 0, 255), (20, 20)),
    ("lime", (0, 255, 0), (178, 128)),
]
RED = ((255, 0, 0), (330, 240))  # diagnostic only, never pass/fail input
TOL = 40

out = {"env": {}, "steps": [], "errors": []}


def pump(app, ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def cap(tag):
    path = os.path.join(TMP, tag + ".png")
    t0 = time.time()
    r = subprocess.run(["grim", "-t", "png", path], capture_output=True)
    dt = time.time() - t0
    if r.returncode != 0:
        raise RuntimeError("grim failed rc=%d %s" % (r.returncode, r.stderr.decode()[:200]))
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    os.unlink(path)
    return a, dt


def find(a, color):
    d = np.abs(a - np.array(color, dtype=np.int16)).sum(axis=2)
    mask = d <= TOL
    n = int(mask.sum())
    if n == 0:
        return None
    ys, xs = np.nonzero(mask)
    return {
        "px": n,
        "x0": int(xs.min()), "y0": int(ys.min()),
        "x1": int(xs.max()), "y1": int(ys.max()),
        "w": int(xs.max() - xs.min() + 1), "h": int(ys.max() - ys.min() + 1),
    }


def report(name, **kw):
    out["steps"].append(dict([("step", name)] + list(kw.items())))
    print("[%s] %s" % (name, json.dumps(kw, ensure_ascii=False)), flush=True)


def anchor_table(cap_img, ox, oy):
    """detected abs top-left vs requested abs top-left, per anchor."""
    det = {}
    for name, color, (lx, ly) in MARKERS:
        g = find(cap_img, color)
        det[name] = None if not g else {
            "detected_abs": [g["x0"], g["y0"]],
            "requested_abs": [ox + lx, oy + ly],
            "err_px": [g["x0"] - (ox + lx), g["y0"] - (oy + ly)],
            "bbox_wh": [g["w"], g["h"]],
            "px": g["px"],
        }
    # derived surface origin: anchor1 + its known local offset (the "no geometry
    # protocol" inference a positioning layer would actually use)
    derived = None
    if det.get("magenta"):
        dx, dy = det["magenta"]["detected_abs"]
        derived = [dx - MARKERS[0][2][0], dy - MARKERS[0][2][1]]
    red_rgb = None
    cx, cy = ox + RED[1][0] + SZ // 2, oy + RED[1][1] + SZ // 2
    if 0 <= cy < cap_img.shape[0] and 0 <= cx < cap_img.shape[1]:
        red_rgb = [int(v) for v in cap_img[cy, cx]]
    return det, derived, red_rgb


def main():
    out["env"]["niri"] = subprocess.run(["niri", "-V"], capture_output=True).stdout.decode().strip()
    out["env"]["grim"] = subprocess.run(["grim", "--version"], capture_output=True).stderr.decode().strip()
    out["env"]["WAYLAND_DISPLAY"] = os.environ.get("WAYLAND_DISPLAY", "")

    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv[:1])
    from meapet.desktop import wayland_layer as wl

    backend = wl._backend
    ok = bool(backend.is_available())
    report("init", layer_shell_init_zero=ok)
    if not ok:
        out["errors"].append("layer_shell_init 失败：F1 之前即终止")
        return

    shim = backend._load()
    out["env"]["shim_path"] = wl._SHIM_PATH

    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    for _name, color, (lx, ly) in MARKERS:
        rgba[ly:ly + SZ, lx:lx + SZ, :3] = color
        rgba[ly:ly + SZ, lx:lx + SZ, 3] = 255
    rcolor, (rlx, rly) = RED
    rgba[rly:rly + SZ, rlx:rlx + SZ, :3] = rcolor
    rgba[rly:rly + SZ, rlx:rlx + SZ, 3] = 255
    buf = np.ascontiguousarray(rgba).reshape(-1)
    ptr = buf.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte))

    base, dt0 = cap("baseline")
    report("F2_capture_only", grim_s=round(dt0, 3), capture_hw=list(base.shape[:2]),
           marker_px_before={n: (find(base, c) or {"px": 0})["px"] for n, c, _ in MARKERS})

    ctx = shim.layer_create_context(None, W, H, X, Y)
    if not ctx:
        out["errors"].append("layer_create_context 返回 NULL")
        return
    report("F1_surface_created", ctx_nonzero=True, requested_xywh=[X, Y, W, H])
    shim.layer_set_click_through(ctx, 1)

    waited = 0.0
    rounds = 0
    probe = None
    for _ in range(60):
        rounds += 1
        t0 = time.time()
        shim.layer_update_pixels_with_format(ctx, ptr, W, H, np.uint32(0))
        pump(app, 50)
        waited += time.time() - t0
        probe, _ = cap("probe")
        if find(probe, MARKERS[0][1]):
            break

    cap_img, dt = cap("with_marker")
    det, derived, red_rgb = anchor_table(cap_img, X, Y)
    report("F1_F3_project_and_infer", grim_s=round(dt, 3), upload_rounds=rounds,
           upload_pump_s=round(waited, 3), markers=det,
           derived_origin_from_capture=derived, requested_origin=[X, Y],
           derived_origin_err=[None if not derived else [derived[0] - X, derived[1] - Y]],
           size_expected=[SZ, SZ], red_center_rgb=red_rgb)

    shim.layer_set_position(ctx, X2, Y2)
    shim.layer_update_pixels_with_format(ctx, ptr, W, H, np.uint32(0))
    pump(app, 400)
    moved, dt = cap("moved")
    det2, derived2, red2 = anchor_table(moved, X2, Y2)
    report("F4_follow_margin_move", grim_s=round(dt, 3), requested_move_to=[X2, Y2],
           markers=det2, derived_origin_from_capture=derived2,
           derived_origin_err=[None if not derived2 else [derived2[0] - X2, derived2[1] - Y2]])

    shim.layer_destroy_context(ctx)
    shim.layer_shell_cleanup()
    pump(app, 400)
    after, dt = cap("after")
    residue = {n: (find(after, c) or {"px": 0})["px"] for n, c, _ in MARKERS}
    report("F5_no_residue", grim_s=round(dt, 3), marker_px_after=residue)
    if any(residue.values()):
        out["errors"].append("F5 计数非 0：teardown 后仍有残影")


try:
    main()
finally:
    for f in os.listdir(TMP):
        if f.endswith(".png"):
            os.unlink(os.path.join(TMP, f))
    print("=== JSON ===")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    with open(os.path.join(TMP, "probe_result.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
