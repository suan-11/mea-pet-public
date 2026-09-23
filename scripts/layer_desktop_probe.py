#!/usr/bin/env python3
"""L3 desktop probe: frames, geometry and census for the LIVE pet process.

Subject under test: the running app (`pet.py` under QT_QPA_PLATFORM=wayland)
pushing frames through the Rust bridge onto a real niri OVERLAY surface.

What it decides (~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md §7.3 items 1 and 2, the Agent-objective half):
  * 第 1 项 -- **only the part a probe without geometry reporting can decide**: pixels
    land inside the rect the app *says* it requested, the rect is non-blank, and
    exactly one meapet OVERLAY is mapped.  Position and size themselves are the app's
    self-report (L0), NOT an L3 verdict of this probe -- niri gives namespace/layer/
    output/keyboard_interactivity and no geometry (spec I3), so a surface pinned to
    the top-left by the §4.8-2 margin bug would still produce this PASS.  A human
    reading the same screen is the only check for "位置与尺寸正确";
  * 第 2 项 -- the rect keeps changing over >= 30 s while a surface is mapped
    (no 定格), with no new `[layer]` error lines in the app log;
  * census -- memfd / VmRSS / thread / surface series for that same pid.

Failure mode of this probe (agents-rules §1, three parts):
  benefit  = it resolves the pid by "which process maps liblayer_shell_shim.so
             AND has pet.py in its cmdline", so the 2026-09-19 wrong-pid failure
             (pgrep returned a launcher pid; every counter read 0 and looked
             like a pass) cannot recur silently -- a wrong pid aborts;
  crash    = niri reports no geometry for layer surfaces (spec I3), so the
             expected rect comes from the APP'S OWN log line.  If that line is
             stale or belongs to an earlier run, we would be cropping the wrong
             place and reading "no content" as a defect;
  backstop = the rect is re-derived from the newest matching log line at start,
             printed in the header, and every sample records whether the crop
             was non-blank -- a blank crop is reported as VOID, never as FAIL.

Exit codes: 0 = no FAIL/VOID verdict (advisory NOTEs may still be printed);
4 = a blocking verdict exists, where FAIL means "measured a defect" and VOID
means "this run cannot judge the item at all".  They share rc=4 because both
bar 第 2 项 from being ticked, but the text is never interchangeable -- reading
a VOID as a defect would report a bug that was not measured, and reading a FAIL
as a gap would hide one that was (agents-rules §10).  A NOTE is an observation the
caller must see but which does not bear on the tick, which is why it does not
change the exit code: making advisory text blocking would train the reader to
ignore the exit code.

What this CANNOT decide (agents-rules §10: L3 is only this machine, this revision):
  * 第 3 项 (指针穿透手感), 第 4 项 (颜色是否红蓝互换 -- the probe reports mean
    R/B for the human to compare, it does not judge), 第 5 项 (交互模式可拖动),
    第 7 项 (退出后无残留) -- all four are the user's call;
  * `layer_last_error` for the APP process: the Python facade deliberately does
    not bind that symbol (wayland_layer.py:201, registered as gap#1), and the
    sticky buffer is per-process, so a probe in another address space cannot
    read it.  Here the observable channel is the app's own `[layer]` lines.
  * "no second ghost" is only checkable as a surface COUNT here (niri gives
    namespace but no geometry); a ghost drawn inside the same rect is not
    distinguishable by any means available without geometry reporting.
  * the crop digest is NOT a pet-only signal: the Live2D canvas clears to fully
    transparent (live2d_widget.py:387 `glClearColor(0,0,0,0,0)`), so the composited
    rect always contains the desktop behind the sprite's margins, and that backdrop
    changes on its own.  What makes "it kept changing" mean something is the
    *control* printed next to it (same rect, same sampler, no OVERLAY mapped).  A
    frozen pet predicts the mapped rate to be at most the control rate, so the
    attribution clause FAILs when it is not strictly above; the residual gap this
    probe cannot close is a backdrop that itself changes on ~every sampled pair,
    against which no whole-rect digest can see a freeze -- a pet-masked (alpha-aware)
    freeze detector does not exist here and is registered as a gap, not as a pass.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MEMFD_TAG = "/memfd:meapet-px"
LAYER_LINE = re.compile(r"\[layer\] → 穿透模式 surface=(\d+)x(\d+) @\((-?\d+),(-?\d+)\)")
LAYER_ERR = re.compile(r"\[layer\] ✗")
SHIM_MAP = "liblayer_shell_shim"
# Minimum un-mapped ("control") consecutive pairs before the attribution
# comparison in report() has any standing to FAIL a run (agents-rules §8: one
# observation is not a baseline; measured 2026-09-20 -- without this a single
# changed control pair made 133/133 read as "no more attributable than 1/1").
# At the default 3 Hz, 10 pairs ~= 4.5 s of backdrop under the same rect.
# agents-rules §7: this is the single truth for that threshold; report() reads it.
MIN_CONTROL_PAIRS = 10


def log(*a: object) -> None:
    print(*a, file=sys.stderr, flush=True)


def resolve_pid() -> int:
    """The pid that BOTH maps the shim and runs pet.py.

    pgrep-based selection produced a launcher pid on 2026-09-19 and every
    counter then read 0, which is indistinguishable from "no leaks" -- so
    provenance is the selection key here, not the process name.
    """
    hits = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == os.getpid():
            continue
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        if "pet.py" not in cmdline:
            continue
        try:
            maps = Path(f"/proc/{pid}/maps").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if SHIM_MAP in maps:
            hits.append(pid)
    if len(hits) == 1:
        return hits[0]
    raise RuntimeError(
        f"expected exactly one pet.py process mapping {SHIM_MAP}, found {hits}. "
        f"Penetration mode is probably not active -- do not proceed (gap#0: "
        f"未激活 = 本项全部不通过)."
    )


def niri_layers() -> list[dict]:
    out = subprocess.run(["niri", "msg", "-j", "layers"],
                         capture_output=True, text=True, timeout=10).stdout
    try:
        return json.loads(out or "[]")
    except json.JSONDecodeError:
        return []


def meapet_surfaces() -> int:
    return sum(1 for row in niri_layers() if row.get("namespace") == "meapet")


def _safe_readlink(path: str):
    """True if `path` is a meapet memfd; False if it vanished or is not one."""
    try:
        return MEMFD_TAG in os.readlink(path)
    except OSError:
        return False


def census(pid: int) -> dict:
    row: dict[str, int] = {}
    try:
        # Per-entry guard: our own census opens and closes fds while it lists
        # /proc/<pid>/fd, so an entry can vanish between listdir and readlink
        # (observed as FileNotFoundError on the inline draft of this probe).
        row["memfd"] = sum(
            1 for e in os.listdir(f"/proc/{pid}/fd")
            if _safe_readlink(f"/proc/{pid}/fd/{e}")
        )
    except OSError:
        row["memfd"] = -1
    try:
        row["fds"] = len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        row["fds"] = -1
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                row["rss_kB"] = int(line.split()[1])
            elif line.startswith("Threads:"):
                row["threads"] = int(line.split()[1])
    except OSError:
        pass
    row["surfaces"] = meapet_surfaces()
    return row


def grab() -> "tuple[object, int, int]":
    """Full-screen PNG via grim, returned as a PIL image plus its size."""
    from PIL import Image

    # NOTE the argument order: grim's parser takes options first and the output
    # path last, so `grim - -t png` is read as an invalid `-` OPTION and only
    # prints its usage on stdout (measured 2026-09-20: rc=1, empty stderr).
    proc = subprocess.run(["grim", "-t", "png", "-"], capture_output=True, timeout=15)
    if proc.returncode != 0 or not proc.stdout:
        # grim writes its usage to STDOUT, so a failure with empty stderr still
        # has a reason in stdout -- capture both or the probe fails mute.
        raise RuntimeError(f"grim failed rc={proc.returncode} "
                           f"stdout={proc.stdout[:120]!r} stderr={proc.stderr[:120]!r}")
    img = Image.open(io.BytesIO(proc.stdout)).convert("RGB")
    return img, img.width, img.height


def region_stats(img, rect: tuple[int, int, int, int]):
    """Return (crop, pixels, 8-bit digest of the crop, digest of everything else).

    The "everything else" digest deliberately EXCLUDES the requested rect, so a
    moving pet cannot dominate the outside signal, and the outside is compared
    as a whole-screen digest rather than a bounding box: a second copy of the
    pet anywhere on screen changes it, and so does the user's own terminal --
    the probe reports both counts separately and lets the human attribute them
    (agents-rules §12: the desktop is in use, outside changes are not automatically
    a defect).
    """
    from PIL import Image

    crop = img.crop(rect)
    outside = img.copy()
    outside.paste(Image.new("RGB", (rect[2] - rect[0], rect[3] - rect[1])),
                  (rect[0], rect[1]))
    return crop, crop.width * crop.height, digest(crop), digest(outside)


def digest(img) -> int:
    return zlib.crc32(img.tobytes()) & 0xFFFFFFFF


def color_spread(img) -> tuple[int, bool]:
    """(max per-channel min..max spread, is-flat).

    Deliberately NOT `getcolors()`: that returns None when the image has MORE
    colours than the cap, which is the opposite of flat -- an early draft of
    this probe mapped None to 1 and would have judged a fully-rendered pet as
    blank (agents-rules §8: a statistic that inverts at the boundary).
    `getextrema` is exact, cheap and has no cap to trip over.
    """
    extrema = img.getextrema()
    pairs = extrema if isinstance(extrema[0], tuple) else [extrema]
    spread = max(hi - lo for lo, hi in pairs)
    return spread, spread == 0


def mean_rgb(img) -> list[int]:
    """Box-filter reduce to 1x1 == per-channel mean, without touching every pixel.

    BOX must be named explicitly: `resize` defaults to BICUBIC, which is not a
    mean and would make "R and B swapped" a judgment on the wrong statistic.
    """
    from PIL import Image

    return list(img.resize((1, 1), Image.BOX).getpixel((0, 0)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=35.0)
    ap.add_argument("--hz", type=float, default=3.0)
    ap.add_argument("--log", default="", help="app log to mine for the geometry line")
    ap.add_argument("--out", default="/tmp/wpG/desktop_probe.jsonl")
    ap.add_argument("--rect", default="", help="override WxH@X,Y if no log line")
    ap.add_argument("--wait-surface", type=float, default=20.0, dest="wait_surface",
                    help="seconds to wait for penetration mode to be entered")
    args = ap.parse_args()

    try:
        pid = resolve_pid()
    except RuntimeError as exc:
        log(f"ABORT: {exc}")
        return 2
    log(f"subject pid={pid} (provenance: maps {SHIM_MAP} and runs pet.py)")

    rect = None
    if args.rect:
        m = re.match(r"(\d+)x(\d+)@(-?\d+),(-?\d+)", args.rect)
        if not m:
            log("ABORT: --rect must look like 512x512@400,100")
            return 2
        w, h, x, y = (int(g) for g in m.groups())
        rect = (x, y, x + w, y + h)
    elif args.log and Path(args.log).exists():
        lines = LAYER_LINE.findall(Path(args.log).read_text(encoding="utf-8", errors="replace"))
        if lines:
            w, h, x, y = (int(g) for g in lines[-1])
            rect = (x, y, x + w, y + h)
            log(f"expected rect from log: {len(lines)} penetration-mode lines, "
                f"newest -> {rect}")
    if rect is None:
        log("ABORT: no geometry available (no --rect and no `[layer] → 穿透模式` "
            "line in the log).  Cropping a guessed rect would produce a fake FAIL.")
        return 2

    surf0 = meapet_surfaces()
    if surf0 == 0:
        # "no meapet surface right now" has two completely different causes and
        # conflating them would report a human clicking 交互 as "the backend never
        # activated" -- which is the one conclusion this whole WP-G hinges on.
        # Wait briefly, then decide using the app's own log.
        deadline = time.monotonic() + args.wait_surface
        while time.monotonic() < deadline and surf0 == 0:
            time.sleep(0.5)
            surf0 = meapet_surfaces()
    if surf0 == 0:
        entered = 0
        if args.log and Path(args.log).exists():
            entered = len(LAYER_LINE.findall(Path(args.log).read_text(
                encoding="utf-8", errors="replace")))
        if entered:
            log(f"ABORT: the app entered penetration mode {entered} time(s) but is in "
                f"交互 mode now (0 meapet surfaces after waiting "
                f"{args.wait_surface:.0f}s). Sampling the rect now would measure the "
                f"Qt window, not the layer surface -- switch it to 穿透 and re-run.")
        else:
            log("ABORT: no `[layer] → 穿透模式` line in the log and no meapet surface "
                "-- the backend never activated (gap#0: 未激活 = 本项全部不通过). "
                "The usual cause is the Live2D first-frame timeout "
                "(LIVE2D_STARTUP_TIMEOUT_MS=5000, render_host.py:57).")
        return 2

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    interval = 1.0 / max(0.5, args.hz)
    prev_crop = prev_outside = None
    rows: list[dict] = []
    screen = "?"
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"header": True, "pid": pid, "rect": list(rect),
                             "surfaces_at_start": surf0}) + "\n")
        t_start = time.monotonic()
        deadline = t_start + args.seconds
        while time.monotonic() < deadline:
            t0 = time.monotonic()
            try:
                img, sw, sh = grab()
            except Exception as exc:
                log(f"grab failed: {exc}")
                break
            screen = f"{sw}x{sh}"
            crop, npix, c_dig, o_dig = region_stats(img, rect)
            spread, flat = color_spread(crop)
            row = {
                "t": round(time.monotonic() - t_start, 3),
                "inside_changed": None if prev_crop is None else (c_dig != prev_crop[0]),
                "outside_changed": None if prev_outside is None else (o_dig != prev_outside[0]),
                "crop_color_spread": spread,
                "crop_nonblank": not flat,
                "mean_RGB": mean_rgb(crop),
            }
            row.update(census(pid))
            rows.append(row)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            prev_crop, prev_outside = (c_dig, npix), (o_dig, sw * sh)
            time.sleep(max(0.0, interval - (time.monotonic() - t0)))

    log(f"samples={len(rows)} screen={screen}")
    return report(rows, args.log, pid)


def report(rows: list[dict], logpath: str, pid: int) -> int:
    if not rows:
        print("VERDICT: VOID -- no samples")
        return 4
    inside = [r["inside_changed"] for r in rows if r["inside_changed"] is not None]
    outside = [r["outside_changed"] for r in rows if r["outside_changed"] is not None]
    nonblank = [r["crop_nonblank"] for r in rows]
    spread = [r["crop_color_spread"] for r in rows]
    surfaces = [r["surfaces"] for r in rows]
    memfd = [r["memfd"] for r in rows]
    rss = [r.get("rss_kB", -1) for r in rows]
    thr = [r.get("threads", -1) for r in rows]
    span = rows[-1]["t"] - rows[0]["t"]

    # Freeze must be judged ONLY over intervals where a layer surface was mapped
    # at BOTH ends (agents-rules §8/§12; measured 2026-09-20: a run that overlapped a
    # 穿透->交互 switch reported inside changed 90/133, and all 43 non-changes sat
    # in the 交互 stretch where surfaces==0 -- there is no OVERLAY to animate then.
    # Judging the aggregate would FAIL a healthy run, and -- worse -- would let a
    # run that never entered penetration mode look like "mostly changing").
    # The inverse hazard is also closed: a pair whose two ends straddle a switch is
    # dropped rather than counted as evidence of anything.
    # agents-rules §3 (order of an in-place update, here: which row owns which pair):
    # row j's `inside_changed` is computed against row j-1 in the sampling loop
    # (:334), so the pair (j-1, j) is described by rows[j], while this comprehension
    # is *gated* by the surfaces of rows[j-1] and rows[j].  Reading rows[j-1]'s value
    # for it -- the first draft's shape -- judges pair (j-2, j-1) while claiming to
    # judge (j-1, j): one pair per stretch boundary (and every straddling pair) would
    # be attributed to the wrong end of the switch.  The numbers only agreed by luck
    # on a long steady stretch, which is exactly how an off-by-one survives review.
    mapped_pairs = [
        rows[j]["inside_changed"] for j in range(1, len(rows))
        if rows[j]["inside_changed"] is not None
        and rows[j - 1]["surfaces"] > 0 and rows[j]["surfaces"] > 0
    ]
    unmapped_pairs = len(inside) - len(mapped_pairs)
    # Attribution control (agents-rules §8): the crop is the WHOLE requested rect as
    # composited, so a change inside it is not automatically the pet -- any window
    # underneath can flip the digest too.  The pairs where no meapet surface was
    # mapped are the control for exactly that: same rectangle, same sampler, same
    # desktop, no OVERLAY.  If that rate is materially below the mapped rate, the
    # changes while mapped are attributable to the surface, not to the backdrop.
    # It only exists if the run overlapped a mode switch, which is why the 10-toggle
    # / 30-min switch cadence makes this available and a quiet 45 s run cannot.
    unmapped_changed = sum(inside) - sum(mapped_pairs)
    # The other half of the surface check, missing until 2026-09-20 (audit finding:
    # `if max(surfaces) > 1` is one-sided -- a run in which the OVERLAY vanished
    # while its buffers stayed live could never fail).  (memfd>0, surfaces==0) is
    # the state spec I5 forbids: a ctx alive with nothing mapped, i.e. a 定格 that
    # emits no error line at all.
    # Two consecutive samples are required, because enable() builds the shm pool
    # before the first commit maps the surface, so ONE sample can straddle that
    # window legitimately; two samples ~0.5 s apart (the 3 Hz default) cannot, since
    # the facade's enable() returns well inside one interval.
    # Failure mode of THIS clause: it reads memfd from /proc/<pid>/fd of the process
    # this probe resolved, so if the pid were wrong the count would be 0 and the
    # clause would never fire -- which is why resolve_pid() aborts on ambiguity
    # rather than guessing (agents-rules §12).
    orphan_pairs = sum(
        1 for j in range(1, len(rows))
        if rows[j - 1]["memfd"] > 0 and rows[j - 1]["surfaces"] == 0
        and rows[j]["memfd"] > 0 and rows[j]["surfaces"] == 0)

    err_new = 0
    if logpath and Path(logpath).exists():
        err_new = len(LAYER_ERR.findall(Path(logpath).read_text(encoding="utf-8",
                                                                errors="replace")))
    mean_rgb = [round(sum(r["mean_RGB"][i] for r in rows) / len(rows), 1) for i in range(3)]

    print(f"samples          : {len(rows)} over {span:.1f}s "
          f"(surface mapped at both ends of {len(mapped_pairs)} consecutive pairs, "
          f"{unmapped_pairs} pairs excluded as straddling an un-mapped stretch)")
    print(f"rect non-blank   : {sum(nonblank)}/{len(nonblank)} samples, "
          f"colour-spread min={min(spread)} max={max(spread)}")
    print(f"inside changed   : {sum(inside)}/{len(inside)} consecutive pairs")
    print(f"  ... while mapped: {sum(mapped_pairs)}/{len(mapped_pairs)}  "
          f"<-- the 第2项 freeze statistic")
    if unmapped_pairs:
        print(f"  ... NOT mapped   : {unmapped_changed}/{unmapped_pairs}  "
          f"<-- attribution control: same rect, no OVERLAY")
    print(f"outside changed  : {sum(outside)}/{len(outside)} consecutive pairs "
          f"(desktop in use: not a defect by itself, agents-rules §12)")
    print(f"meapet surfaces  : min={min(surfaces)} max={max(surfaces)}")
    print(f"live-but-unmapped: {orphan_pairs} consecutive pairs with memfd>0 and "
          f"surfaces==0  (I5: 应为 0)")
    print(f"memfd            : min={min(memfd)} max={max(memfd)} last={memfd[-1]}")
    print(f"VmRSS_kB         : first={rss[0]} last={rss[-1]} max={max(rss)}")
    print(f"threads          : min={min(thr)} max={max(thr)}")
    print(f"crop mean RGB    : {mean_rgb}  (红蓝是否互换由人判定 -- 本探针不判决)")
    print(f"app log `[layer] ✗` lines: {err_new}")

    verdicts = []
    nb_mapped = [r["crop_nonblank"] for r in rows if r["surfaces"] > 0]
    if not nb_mapped:
        verdicts.append("VOID(第1/第2项): no sample had a meapet surface mapped -- "
                        "this run measured the Qt window, not the OVERLAY")
    elif not all(nb_mapped):
        verdicts.append(f"FAIL(第1项): {nb_mapped.count(False)}/{len(nb_mapped)} "
                        f"surface-mapped samples had a blank requested rect")
    if not mapped_pairs:
        verdicts.append("VOID(第2项): every consecutive pair straddled an un-mapped "
                        "stretch, so no freeze judgement is possible in this window")
    elif not all(mapped_pairs):
        verdicts.append(f"FAIL(第2项): {mapped_pairs.count(False)}/{len(mapped_pairs)} "
                        f"pairs changed NOT while a surface was mapped -> 定格嫌疑 "
                        f"(longest identical run inside a mapped stretch is the number "
                        f"to read from the jsonl)")
    elif len(mapped_pairs) < 30:
        verdicts.append(f"VOID(第2项): only {len(mapped_pairs)} mapped pairs; "
                        f"第2项 needs >=30s of mapped frames, so this window cannot "
                        f"judge it either way")
    if mapped_pairs and unmapped_pairs:
        # agents-rules §8: a rate from 1–2 control pairs is not a control.  Measured
        # 2026-09-20 -- the first version of this clause turned the cleanest run
        # of the round (133/133 mapped, a single straddling pair that also changed)
        # into FAIL(第2项归因), because 133/133 == 1/1 as rates.  A quiet 45 s
        # window contains at most one or two un-mapped pairs, so "backdrop changed
        # too" there is one observation, not a baseline.
        # 10 pairs is the smallest control that can separate "the desktop under
        # this rect is basically static" from "it is not" at 3 Hz (~4.5 s of
        # backdrop), which is what the judgement actually claims.
        # Failure mode if the guard is wrong the other way: a large control would
        # be skipped and the attribution silently dropped -- so the numbers are
        # ALWAYS printed, below as a NOTE carrying both counts for a human to read.
        mapped_rate = sum(mapped_pairs) / len(mapped_pairs)
        unmapped_rate = unmapped_changed / unmapped_pairs
        if unmapped_pairs < MIN_CONTROL_PAIRS:
            verdicts.append(
                f"NOTE(第2项归因): control too small to judge -- mapped "
                f"{sum(mapped_pairs)}/{len(mapped_pairs)} vs un-mapped "
                f"{unmapped_changed}/{unmapped_pairs} (< {MIN_CONTROL_PAIRS} pairs); "
                f"read it with a run that straddles a 穿透->交互 switch")
        elif mapped_rate <= unmapped_rate:
            verdicts.append(
                f"FAIL(第2项归因): the rect changes no more while a surface is mapped "
                f"({sum(mapped_pairs)}/{len(mapped_pairs)}) than while it is not "
                f"({unmapped_changed}/{unmapped_pairs}) -- the change is not "
                "attributable to the OVERLAY (agents-rules §8)")
    elif mapped_pairs:
        verdicts.append("NOTE(第2项归因): this window has no unmapped control pairs, so "
                        "it cannot by itself separate pet motion from backdrop motion; "
                        "read it together with a run that straddles a 穿透->交互 switch")
    if err_new:
        verdicts.append(f"FAIL(第2项): {err_new} `[layer] ✗` lines in the app log")
    if orphan_pairs:
        verdicts.append(f"FAIL(I5/第2项): {orphan_pairs} consecutive pairs had memfd>0 "
                        "with no meapet surface mapped -- a live ctx showing nothing "
                        "(定格 without an error line)")
    if max(surfaces) > 1:
        # Was a NOTE until 2026-09-20: acceptable while 2 surfaces was the KNOWN
        # state of the double-enable bug and this probe was not the gate for it.
        # That bug is now fixed and this probe's rc is quoted in the WP-G record,
        # so leaving it advisory would make the rc blind to the exact regression
        # the record claims is closed (agents-rules §1: a relaxation must say what
        # breaks -- here, "PASS" while a ghost OVERLAY is mapped).
        verdicts.append(f"FAIL(第1项): {max(surfaces)} meapet surfaces observed at once "
                        f"-- more than one OVERLAY alive (残影/未销毁的旧图层)")
    if span < 30.0:
        verdicts.append(f"VOID(第2项): window {span:.1f}s < the 30s 第2项 requires")
    # Severity split.  agents-rules §1 requires all three parts for a relaxation:
    #   benefit  = a clean-but-short window (the 45 s 第2项 run contains no mode
    #              switch, so it has no unmapped control pairs) reports rc 0, so
    #              the WP-G record and any `&&` chain can key off the exit code
    #              instead of re-reading prose to find out whether anything failed.
    #   crash    = if advisory text could set rc, the exit code would stop meaning
    #              "第 2 项 is tickable" and readers would start ignoring it -- at
    #              which point a real FAIL scrolls past unnoticed.
    #   backstop = the split keys on the STRING PREFIX and every entry is built in
    #              this function, so a NOTE literal cannot reach rc; and the two
    #              severities keep distinct wording ("cannot judge" vs "measured a
    #              defect"), so a reader who does look is never told a gap is a bug.
    blocking = [v for v in verdicts if v.startswith(("FAIL", "VOID"))]
    for v in verdicts:
        if not v.startswith(("FAIL", "VOID")):
            print("NOTE: " + v)
    if not blocking:
        print(f"VERDICT: PASS for 第2项 (no 定格 over {span:.1f}s) and for 第1项's "
              f"gatable half -- {sum(mapped_pairs)}/{len(mapped_pairs)} mapped pairs "
              f"changed, exactly one OVERLAY, non-blank rect. 第1项's position/size is "
              f"NOT gated here: niri reports no geometry (spec I3), so the rect is the "
              f"app's own claim (L0 self-attestation) "
              f"(L3, pid {pid}, this machine only)")
        return 0
    for v in blocking:
        print("VERDICT: " + v)
    return 4


if __name__ == "__main__":
    sys.exit(main())
