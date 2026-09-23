#!/usr/bin/env python3
"""F-LOCALIZE feasibility probe (proposal stage, zero desktop mutation).

Question answered: does fidus's `localizability()` gate reject the *real pet
frames* of MeaPet? If it does, the whole self-window-positioning line ends as
"do not do", for content reasons that no machine or compositor change can fix.

This is a faithful numpy port of the gate as read on 2026-09-19 from
    fidus crates/fidus-estimate/src/template.rs  (localizability / self_ncc)
    fidus crates/fidus-core/src/target.rs        (RgbaImage::luma_at, Rec.709 [0,255])
with the constants as they appear there:
    MIN_VARIANCE_PER_SAMPLE = 0.25   SELF_SIMILARITY_RADII = [2,4,8,16]
    MAX_SELF_SIMILARITY     = 0.98   MIN_SELF_SIMILARITY_DECAY = 0.1
It is OUR reimplementation, not fidus's code path: it can indicate that a
verdict is reachable, never that fidus itself returned it.

No window is created, no screen is captured, nothing is written outside stdout.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
from PIL import Image

MIN_VARIANCE_PER_SAMPLE = 0.25
SELF_SIMILARITY_RADII = (2, 4, 8, 16)
MAX_SELF_SIMILARITY = 0.98
MIN_SELF_SIMILARITY_DECAY = 0.1


def luma(rgba: np.ndarray) -> np.ndarray:
    """Rec.709 luma in [0,255], alpha ignored -- exactly fidus's luma_at."""
    r = rgba[..., 0].astype(np.float32)
    g = rgba[..., 1].astype(np.float32)
    b = rgba[..., 2].astype(np.float32)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b).astype(np.float32)


def self_ncc(plane: np.ndarray, dx: int, dy: int) -> float | None:
    h, w = plane.shape
    x0, x1 = max(-dx, 0), min(w, w - dx)
    y0, y1 = max(-dy, 0), min(h, h - dy)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    a_win = plane[y0:y1, x0:x1].astype(np.float64)
    b_win = plane[y0 + dy:y1 + dy, x0 + dx:x1 + dx].astype(np.float64)
    n = float(a_win.size)
    a = a_win - a_win.mean()
    b = b_win - b_win.mean()
    num = float((a * b).sum())
    da = float((a * a).sum())
    db = float((b * b).sum())
    if da < MIN_VARIANCE_PER_SAMPLE * n or db < MIN_VARIANCE_PER_SAMPLE * n:
        return None
    return num / (math.sqrt(da) * math.sqrt(db))


def localizability(plane: np.ndarray) -> tuple[str, float, dict]:
    """Returns (verdict, score, per-radius detail). Verdict: OK/TooSmall/Featureless/SelfSimilar."""
    h, w = plane.shape
    min_radius = SELF_SIMILARITY_RADII[0]
    if w <= min_radius * 2 or h <= min_radius * 2:
        return "TooSmall", -1.0, {}
    n = float(plane.size)
    var = float(plane.astype(np.float64).var())
    if n < 4.0 or var < MIN_VARIANCE_PER_SAMPLE:
        return "Featureless", var, {"var": var}
    by_radius: list[float] = []
    detail: dict[int, float] = {}
    for r in SELF_SIMILARITY_RADII:
        worst = -math.inf
        for dx, dy in ((r, 0), (0, r), (r, r), (r, -r)):
            s = self_ncc(plane, dx, dy)
            if s is not None:
                worst = max(worst, s)
        if math.isfinite(worst):
            by_radius.append(worst)
            detail[r] = worst
    if not by_radius:
        return "TooSmall", -1.0, detail
    worst = max(by_radius)
    if worst >= MAX_SELF_SIMILARITY:
        return "SelfSimilar", worst, detail
    near, far = by_radius[0], by_radius[-1]
    if near > 0.9 and near - far < MIN_SELF_SIMILARITY_DECAY:
        return "SelfSimilar", near, detail
    return "OK", min(max(worst, 0.0), 1.0), detail


def composited(rgba: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """What the screencopy readback actually contains: sprite over a background."""
    a = (rgba[..., 3:4].astype(np.float64) / 255.0)
    f = rgba[..., :3].astype(np.float64) * a + bg.astype(np.float64) * (1.0 - a)
    return np.dstack([f, rgba[..., 3:4]]).round().clip(0, 255).astype(np.uint8)


def tight_crop(rgba: np.ndarray) -> np.ndarray:
    alpha = rgba[..., 3]
    rows = np.flatnonzero(alpha.max(axis=1) > 0)
    cols = np.flatnonzero(alpha.max(axis=0) > 0)
    if rows.size == 0 or cols.size == 0:
        return rgba
    return rgba[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


BACKGROUNDS = {
    "bg-gray128": np.array([128, 128, 128], dtype=np.uint8),
    "bg-dark": np.array([24, 22, 30], dtype=np.uint8),
    "bg-white": np.array([250, 250, 250], dtype=np.uint8),
}


def zero_invisible(rgba: np.ndarray) -> np.ndarray:
    """Force RGB=0 wherever alpha==0: same visible content, different invisible data."""
    out = rgba.copy()
    out[rgba[..., 3] == 0, :3] = 0
    return out


def report(name: str, rgba: np.ndarray) -> None:
    alpha = rgba[..., 3]
    transp = float((alpha == 0).mean())
    plane = luma(rgba)
    verdict, score, detail = localizability(plane)
    # variance contributed by pixels that are fully transparent in the template
    opaque = alpha > 0
    var_all = float(plane.astype(np.float64).var())
    var_transp = float(plane[~opaque].astype(np.float64).var()) if (~opaque).any() else float("nan")
    det = " ".join(f"r{k}={v:.3f}" if isinstance(k, int) else f"{k}={v:.3f}"
                   for k, v in detail.items())
    print(f"{name:<34} {rgba.shape[1]}x{rgba.shape[0]:<4} "
          f"transp={transp*100:5.1f}% var={var_all:8.2f}/varT={var_transp:7.2f} "
          f"-> {verdict:<12} score={score:.4f}  {det}")


def _gray(fn, w=60, h=40) -> np.ndarray:
    """fidus's tpl_from(): grey RGBA, full alpha."""
    y, x = np.mgrid[0:h, 0:w]
    v = np.array([[fn(int(cx), int(cy)) for cx in range(w)] for cy in range(h)], dtype=np.uint8)
    return np.dstack([np.stack([v, v, v], axis=-1), np.full((h, w), 255, np.uint8)])


def _hash_luma(x: int, y: int) -> int:
    m = 0xFFFFFFFF
    h = ((x * 0x9E3779B1) & m) ^ ((y * 0x85EBCA6B) & m)
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & m
    h ^= h >> 16
    return h & 0xFF


def selftest() -> int:
    """Reproduce the verdicts and numbers that fidus's own #[cfg(test)] cases assert.

    Source of truth: template.rs tests `gradients_are_self_similar`,
    `flat_and_tiny_templates_are_refused`, `genuinely_locatable_templates_are_accepted`.
    If this function does not pass, the port above is wrong and every number it
    prints about real pet frames is meaningless.
    """
    cases = []

    def check(label, plane, want_verdict, want=None, tol=0.05):
        v, s, detail = localizability(plane)
        ok = v == want_verdict if isinstance(want_verdict, str) else v in want_verdict
        num_ok = True if want is None else abs(s - want) <= tol
        cases.append((label, v, s, detail, ok and num_ok))

    check("flat128", luma(_gray(lambda x, y: 128)), "Featureless")
    check("tiny3x3", np.full((3, 3), 200.0, np.float32), "TooSmall")
    check("h-gradient", luma(_gray(lambda x, y: x * 255 // 60)), "SelfSimilar")
    check("v-gradient", luma(_gray(lambda x, y: y * 255 // 40)), "SelfSimilar")
    check("noise-dither", luma(_gray(lambda x, y: max(0, min(255, x * 255 // 60 + ((x * 3) ^ (y * 5)) % 3 - 1)))),
          ("SelfSimilar", "Featureless"))
    check("lsb-dither", luma(_gray(lambda x, y: 128 + ((x * 7) ^ (y * 13)) % 2)),
          ("SelfSimilar", "Featureless"))
    check("hash(<0.1)", luma(_gray(_hash_luma)), "OK", want=0.025)
    check("grating", luma(_gray(lambda x, y: int(min(255.0, max(0.0, 127.0 + 120.0 * math.sin(2 * math.pi * x / 60.0) * math.sin(2 * 2 * math.pi * y / 40.0)))))), "OK")
    check("bordered", luma(_gray(lambda x, y: 250 if (2 <= x < 57 and 2 <= y < 37) else 20)), "OK", want=0.65, tol=0.15)

    print("# port self-test against fidus's own #[cfg(test)] expectations")
    bad = 0
    for label, v, s, detail, ok in cases:
        det = " ".join(f"r{k}={x:.3f}" for k, x in detail.items())
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<14} -> {v:<12} score={s:.4f}  {det}")
        bad += 0 if ok else 1
    print(f"# {len(cases) - bad}/{len(cases)} cases reproduce fidus's documented verdicts")
    return 1 if bad else 0


def decay_margin(plane: np.ndarray) -> tuple[str, float, float]:
    """(verdict, worst, margin) where margin = by_radius[0] - by_radius[-1] vs the 0.1 gate."""
    v, s, detail = localizability(plane)
    vals = [detail[r] for r in SELF_SIMILARITY_RADII if r in detail]
    margin = (vals[0] - vals[-1]) if len(vals) >= 2 else float("nan")
    return v, s, margin


def size_sweep(rgba: np.ndarray, heights) -> list[tuple[int, str, float, float]]:
    """Downscale like the renderer does (Qt target = pixmap * 0.5 * size_factor)."""
    img = Image.fromarray(rgba)
    rows = []
    for h in heights:
        w = max(5, round(img.width * h / img.height))
        rows.append((h,) + decay_margin(luma(np.asarray(img.resize((w, h), Image.BOX)))))
    return rows


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    sprites = sorted(
        os.path.join(dp, f)
        for dp, _, fs in os.walk(os.path.join(root, "sprites"))
        for f in fs if f.lower().endswith(".webp")
    )
    print(f"# sprites found: {len(sprites)}")
    sample = sprites[:: max(1, len(sprites) // 12)][:12] if sprites else []

    print("\n## A. raw template as the caller would hand it over (alpha ignored by luma_at)")
    for p in sample:
        im = Image.open(p).convert("RGBA")
        report(os.path.basename(p), np.asarray(im))

    print("\n## A2. identical visible content, invisible RGB forced to 0 (control for A)")
    for p in sample:
        im = Image.open(p).convert("RGBA")
        report(os.path.basename(p), zero_invisible(np.asarray(im)))

    print("\n## B. tight alpha bbox (what a sane caller would crop to)")
    for p in sample[:6]:
        im = Image.open(p).convert("RGBA")
        report(os.path.basename(p), tight_crop(np.asarray(im)))

    print("\n## C. what the screen really shows: sprite alpha-composited over a background")
    for p in sample[:4]:
        im = np.asarray(Image.open(p).convert("RGBA"))
        base = tight_crop(im)
        for bgname, bg in BACKGROUNDS.items():
            tile = np.broadcast_to(bg.reshape(1, 1, 3), base[..., :3].shape).copy()
            out = np.dstack([tile, base[..., 3]])
            report(f"{os.path.basename(p)[:-5]}/{bgname}", composited(base, out[..., :3]))

    print("\n## D. displayed size sweep (size factor 30%-300%, box-downsampled)")
    print("##    CAVEAT: Pillow's RGBA resize premultiplies alpha, so this path silently")
    print("##    zeroes invisible RGB -- D therefore measures 'A2 at various sizes', not Qt's")
    print("##    QPixmap.scaled semantics. Qt's own alpha handling is untested here (L3 needed).")
    if sample:
        im = np.asarray(Image.open(sample[0]).convert("RGBA"))
        base_img = Image.fromarray(tight_crop(im))
        for h in (91, 137, 182, 273, 364, 455, 546, 637, 728, 910):
            w = max(1, round(base_img.width * h / base_img.height))
            for res, tag in ((Image.BOX, "box"), (Image.LANCZOS, "lanczos")):
                arr = np.asarray(base_img.resize((w, h), res))
                report(f"h={h:<4} {tag}", arr)

    print("\n## E. live2d texture atlas (only real live2d pixels on disk)")
    for rel in ("live2d/model/mea_live2d/textures/texture_00.png",
                "live2d/model/mea_live2d/textures/texture_01.png"):
        p = os.path.join(root, rel)
        if os.path.exists(p):
            arr = np.asarray(Image.open(p).convert("RGBA"))
            report(os.path.basename(p), arr)
            # atlas is mostly unused space -> also probe its opaque bbox
            report(os.path.basename(p) + "/tight", tight_crop(arr))
    print("\n## F. decay margin vs displayed size (the binding gate is near-far > 0.1, not 0.98)")
    print("##    renderer default: pixmap * display.scale(0.5) * size_factor(1.0) -> h~455")
    print("##    gate: SelfSimilar when near > 0.9 and (near - far) < 0.1")
    heights = (137, 228, 318, 455, 592, 728, 910)
    for p in sample:
        im = np.asarray(Image.open(p).convert("RGBA"))
        base = zero_invisible(tight_crop(im))
        cells = []
        for h, v, s, m in size_sweep(base, heights):
            flag = "R" if v == "SelfSimilar" else ("o" if v == "OK" else "?")
            cells.append(f"h{h}:{m:.3f}{flag}")
        print(f"  {os.path.basename(p):<20} " + "  ".join(cells))

    print("\n## G. design-faithful case: background-filled raster, then resized to displayed size")
    print("##    (no alpha survives compositing -> no PIL premultiply caveat, and this is the")
    print("##     only variant matching what wlr-screencopy would hand back)")
    for p in sample:
        im = np.asarray(Image.open(p).convert("RGBA"))
        base = tight_crop(im)
        for bgname, bg in BACKGROUNDS.items():
            tile = np.broadcast_to(bg.reshape(1, 1, 3), base[..., :3].shape).copy()
            flat = composited(base, tile)[..., :3]
            img = Image.fromarray(flat)
            cells = []
            for h in heights:
                w = max(5, round(img.width * h / img.height))
                v, s, m = decay_margin(luma(np.asarray(img.resize((w, h), Image.BOX))))
                flag = "R" if v == "SelfSimilar" else ("o" if v == "OK" else "?")
                cells.append(f"h{h}:{m:.3f}{flag}")
            print(f"  {os.path.basename(p)[:12]:<14}{bgname:<12} " + "  ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
