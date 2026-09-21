#!/usr/bin/env python3
"""L3 probe: 10 real mode toggles driven through the app's own switch panel.

Subject under test: `LayerDebugPanel`'s button (title "穿透开关") toggling
`PetRenderHostMixin._set_layer_mode`, i.e. exactly what the user clicks.

What it decides (~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md §7.3-6, 第 6 项):
  across 10 穿透↔交互 round trips, `memfd:meapet-px` must return to the
  interactive-mode baseline every time the ctx is destroyed and never exceed
  `ctx x RING_DEPTH` while live; the compositor-side meapet surface count must
  never exceed the number of penetration modes currently entered.

Failure mode of this probe (agents-rules §1, three parts):
  benefit  = it is the only way to exercise the APP's toggle path (the facade
             probe can only exercise enable/disable pairs it drives itself);
  crash    = keystrokes go to whatever window niri has focused.  If focus did
             not land on the panel, `wtype ' '` would type into the user's own
             terminal or editor -- a real mutation of their session;
  backstop = focus is re-read from `niri msg -j windows` and must report
             `is_focused: true` for that exact window id before any keystroke;
             otherwise the probe stops with rc=3 and hands the remaining
             toggles to the human.  It never types blindly.
  second guard: a toggle that produced no new `[layer]` log line is reported as
             "keystroke did not toggle" instead of being counted as a cycle --
             otherwise 10 no-op keypresses would read as 10 clean cycles.

What this CANNOT decide: whether the click *feels* right (第 3/5 项 are the
human's), and it cannot prove anything about machines other than this niri.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

MEMFD_TAG = "/memfd:meapet-px"
PANEL_TITLE = "穿透开关"
TOGGLE_LINE = re.compile(
    r"\[layer\] → (穿透|交互)模式(?: surface=(\d+)x(\d+) @\((-?\d+),(-?\d+)\))?")
RING_DEPTH_SRC = "native/layer_shell/src/ring.rs"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def read_ring_depth(root: Path) -> int:
    for line in (root / RING_DEPTH_SRC).read_text(encoding="utf-8").splitlines():
        code = line.split("//")[0]
        if "const RING_DEPTH" in code:
            return int(code.split("=")[1].strip().rstrip(";"))
    raise RuntimeError(f"const RING_DEPTH not found in {RING_DEPTH_SRC}")


def niri_windows() -> list[dict]:
    out = subprocess.run(["niri", "msg", "-j", "windows"],
                         capture_output=True, text=True, timeout=10).stdout
    try:
        return json.loads(out or "[]")
    except json.JSONDecodeError:
        return []


def find_panel() -> dict | None:
    for win in niri_windows():
        if win.get("title") == PANEL_TITLE or win.get("app_id") == PANEL_TITLE:
            return win
    return None


def focus_and_verify(window_id: int, attempts: int = 4) -> tuple[bool, str]:
    """Focus the panel and CONFIRM it before any keystroke.

    Retries matter: leaving penetration mode runs `self.show(); self.raise_()`
    on the pet window (render_host.py `_set_layer_mode(False)`), which takes
    focus back asynchronously, so the first `focus-window` after a toggle
    frequently does not stick (measured 2026-09-20: cycle 1 focused, cycle 2
    not).
    """
    detail = ""
    for attempt in range(1, attempts + 1):
        # niri 26.04 spells this `--id`; `--window-id` is rejected outright, and
        # the first draft of this probe only "worked" for cycle 1 because the
        # panel happened to already have focus (agents-rules §8: a gate that passes
        # for an unrelated reason is worse than one that fails).
        proc = subprocess.run(
            ["niri", "msg", "action", "focus-window", "--id", str(window_id)],
            capture_output=True, text=True, timeout=10)
        detail = (proc.stdout + proc.stderr).strip()[:160]
        time.sleep(0.3)
        for win in niri_windows():
            if win.get("id") == window_id:
                if win.get("is_focused"):
                    return True, detail
                break
        else:
            return False, f"window {window_id} no longer exists ({detail})"
    return False, f"not focused after {attempts} attempts ({detail})"


def resolve_pid() -> int | None:
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
            if "pet.py" not in cmdline:
                continue
            if "liblayer_shell_shim" in Path(f"/proc/{pid}/maps").read_text(
                    encoding="utf-8", errors="replace"):
                return pid
        except OSError:
            continue
    return None


def census(pid: int) -> dict:
    row: dict[str, int] = {}
    base = f"/proc/{pid}/fd"
    try:
        entries = os.listdir(base)
    except OSError:
        entries = []
    n = 0
    for e in entries:
        try:
            if MEMFD_TAG in os.readlink(f"{base}/{e}"):
                n += 1
        except OSError:
            continue
    row["memfd"] = n
    row["fds"] = len(entries)
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError:
        status = ""
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            row["rss_kB"] = int(line.split()[1])
        elif line.startswith("Threads:"):
            row["threads"] = int(line.split()[1])
    layers = subprocess.run(["niri", "msg", "-j", "layers"],
                            capture_output=True, text=True, timeout=10).stdout
    row["surfaces"] = layers.count('"namespace":"meapet"')
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=10)
    ap.add_argument("--log", required=True, help="app log, for the [layer] lines")
    ap.add_argument("--out", default="/tmp/wpG/toggle_probe.jsonl")
    args = ap.parse_args()

    pid = resolve_pid()
    if pid is None:
        log("ABORT: no pet.py process maps liblayer_shell_shim -- penetration mode "
            "is not active (gap#0), nothing to toggle.")
        return 2
    ring_depth = read_ring_depth(Path(__file__).resolve().parents[1])
    log(f"subject pid={pid} RING_DEPTH={ring_depth} cycles={args.cycles}")

    panel = find_panel()
    if panel is None:
        log(f"ABORT: no window titled {PANEL_TITLE!r} -- the switch panel is not "
            f"mapped, so there is nothing to focus and we refuse to type anywhere else.")
        return 2
    log(f"panel window id={panel['id']} focused={panel.get('is_focused')}")

    logpath = Path(args.log)
    toggles_before = len(TOGGLE_LINE.findall(logpath.read_text(encoding="utf-8",
                                                               errors="replace")))
    rows = []
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"pid": pid, "ring_depth": ring_depth,
                             "panel_id": panel["id"]}) + "\n")
        for i in range(1, args.cycles + 1):
            # Re-resolve every cycle: the panel could be recreated by the app,
            # and pressing a stale window id would target whatever took its id.
            panel_now = find_panel()
            if panel_now is None:
                log(f"ABORT at toggle {i}: the {PANEL_TITLE!r} panel is gone")
                return 3
            ok, detail = focus_and_verify(panel_now["id"])
            if not ok:
                log(f"ABORT at toggle {i}: niri did not report is_focused for the "
                    f"panel window -- refusing to type into an unknown window. "
                    f"detail: {detail}. Hand the remaining toggles to the human.")
                return 3
            before = len(TOGGLE_LINE.findall(logpath.read_text(encoding="utf-8",
                                                               errors="replace")))
            subprocess.run(["wtype", " "], capture_output=True, timeout=10)
            time.sleep(0.7)
            text = logpath.read_text(encoding="utf-8", errors="replace")
            after_lines = TOGGLE_LINE.findall(text)
            after = len(after_lines)
            last = after_lines[-1] if after_lines else ("?", "", "", "", "")
            mode = last[0]
            geom = "x".join(last[1:3]) + "@" + ",".join(last[3:5]) if last[1] else "-"
            if after == before:
                log(f"toggle {i}: keystroke produced NO new [layer] line "
                    f"(button did not receive it) -- not counted as a cycle")
                rows.append({"cycle": i, "toggled": False, "mode": mode})
            else:
                row = {"cycle": i, "toggled": True, "mode": mode, "geom": geom}
                row.update(census(pid))
                rows.append(row)
            fh.write(json.dumps(rows[-1]) + "\n")
            fh.flush()
            print(f"{i:>3}  toggled={str(rows[-1]['toggled']):>5}  mode={mode}  geom={geom}  "
                  + " ".join(f"{k}={v}" for k, v in rows[-1].items()
                             if k not in ("cycle", "toggled", "mode", "geom")))

    done = [r for r in rows if r.get("toggled")]
    if len(done) < args.cycles:
        print(f"VERDICT: INCOMPLETE -- only {len(done)}/{args.cycles} keystrokes "
              f"actually toggled; 第 6 项 needs the rest from the human")
        return 4
    live = [r for r in done if r["mode"] == "穿透"]
    back = [r for r in done if r["mode"] == "交互"]
    bad_live = [r for r in live if r["memfd"] > ring_depth]
    bad_back = [r for r in back if r["memfd"] != 0 or r["surfaces"] != 0]
    print(f"census: 穿透 samples={len(live)} 交互 samples={len(back)}")
    print(f"memfd while live   : {sorted({r['memfd'] for r in live})} "
          f"(bound 1 ctx x RING_DEPTH={ring_depth})")
    print(f"memfd after 交互   : {sorted({r['memfd'] for r in back})} (bound 0)")
    print(f"surfaces live/back : {sorted({r['surfaces'] for r in live})} / "
          f"{sorted({r['surfaces'] for r in back})}")
    print(f"rss over run       : {min(r['rss_kB'] for r in done)} -> "
          f"{max(r['rss_kB'] for r in done)} kB, threads "
          f"{sorted({r['threads'] for r in done})}")
    verdicts = []
    if bad_live:
        verdicts.append(f"FAIL(第6项): {len(bad_live)} penetration samples above "
                        f"1 ctx x RING_DEPTH")
    if bad_back:
        verdicts.append(f"FAIL(第6项/I5): {len(bad_back)} interactive samples did not "
                        f"return to memfd=0 and surfaces=0")
    geoms = {r.get("geom") for r in done if r.get("mode") == "穿透" and r.get("geom")}
    print(f"surface geometry per 穿透 entry: {sorted(geoms)}")
    if len(geoms) > 1:
        verdicts.append(f"FAIL(第6项): position/size differs across toggles: {sorted(geoms)}")
    if len({r["surfaces"] for r in live} - {1}) > 0:
        verdicts.append(f"NOTE: penetration surface count not uniformly 1: "
                        f"{sorted({r['surfaces'] for r in live})}")
    if not verdicts:
        print(f"VERDICT: PASS 第6项 -- {args.cycles} toggles, each round trip back to "
              f"memfd=0/surfaces=0 and never above {ring_depth} while live (L3, "
              f"this machine only)")
        return 0
    for v in verdicts:
        print("VERDICT: " + v)
    return 4


if __name__ == "__main__":
    sys.exit(main())
