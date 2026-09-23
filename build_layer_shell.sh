#!/bin/bash
# Contract: ~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md §5.2 — build the Rust cdylib, install it to the repo
# root (I1: the artifact name IS the contract), then verify the export set
# equals spec §7.1 (I2) with the three-part judgment (F-M9, spec §9 T7 row).
# No C/C++ compiler invocation, no Qt detection, no versioned Qt include path
# may ever reappear in this script — those were the fragility this rewrite
# eliminates (spec §5.2). The WP-A acceptance gate greps for the two tool
# names and requires zero hits, so this comment must not spell them out.
set -e
cd "$(dirname "$0")"

echo ">>> cargo build --release --locked (native/layer_shell)"
( cd native/layer_shell && cargo build --release --locked )

echo ">>> 安装到仓库根：liblayer_shell_shim.so"
cp -f native/layer_shell/target/release/liblayer_shell_shim.so liblayer_shell_shim.so

# ---- export-set verification (I2) ----
# REQUIRED = the 11 spec §7.1 symbols, verbatim. Ownership: WP-C (计划书 WP-C
#   改动摘要). Empty during the WP-A scaffold — a permanently-empty REQUIRED
#   would be an impossible-to-fail gate (agents-rules §9), so the script prints a
#   WARN whenever it is empty and WP-C's acceptance must not pass with it empty.
REQUIRED=(
  layer_shell_init
  layer_shell_cleanup
  layer_create_context
  layer_set_click_through
  layer_update_pixels
  layer_update_pixels_with_format
  layer_clear
  layer_set_position
  layer_set_size
  layer_destroy_context
  layer_last_error
)
# ALLOWED = explicit whitelist of toolchain-injected symbols (F-M9②), pinned
#   by evidence from the first real Rust artifact. The empty-shell artifact
#   exports zero dynamic symbols (WP-A measurement: nm -D --defined-only → ∅).
ALLOWED=(
)

ACTUAL=$(mktemp); REQ=$(mktemp); EXPECTED=$(mktemp)
trap 'rm -f "$ACTUAL" "$REQ" "$EXPECTED"' EXIT

# `grep -v '^_$'` drops the bare underscore nm prints for section symbols;
# pipeline exit status is sort's, so an empty artifact does not trip set -e.
nm -D --defined-only liblayer_shell_shim.so | awk '{print $3}' | grep -v '^_$' | sort -u > "$ACTUAL"
printf '%s\n' "${REQUIRED[@]}" | grep -v '^$' | sort -u > "$REQ"
printf '%s\n' "${REQUIRED[@]}" "${ALLOWED[@]}" | grep -v '^$' | sort -u > "$EXPECTED"

fail=0

# ① every required symbol present — per-item equality, NOT "at least one
#    matches" (the old `grep -E "a|b|c"` form proved almost nothing;
#    agents-rules §1 table row 3, spec I2).
n_req=${#REQUIRED[@]}
n_hit=$(comm -12 "$ACTUAL" "$REQ" | wc -l)
if [ "$n_hit" -ne "$n_req" ]; then
  echo "[FAIL] 必需符号在场数 $n_hit / $n_req，缺失："
  comm -23 "$REQ" "$ACTUAL" | sed 's/^/  missing: /'
  fail=1
fi

# ②③ any exported symbol outside required∪allowed violates I2 — both set
#    differences must be empty (① covers required⊆actual, this covers
#    actual⊆required∪allowed).
extra=$(comm -13 "$EXPECTED" "$ACTUAL" || true)
if [ -n "$extra" ]; then
  echo "[FAIL] 表外导出符号（违反 I2）："
  printf '%s\n' "$extra" | sed 's/^/  extra: /'
  fail=1
fi

if [ "$n_req" -eq 0 ]; then
  echo "[WARN] REQUIRED 为空集——仅在 WP-C 写入 spec §7.1 的 11 个符号之前合法。"
fi

if [ "$fail" -ne 0 ]; then
  echo ">>> 导出集合 != spec §7.1，按 §5.2 以非零退出码结束"
  exit 1
fi

echo ">>> 导出集合比对通过（required=$n_req，actual=$(wc -l < "$ACTUAL")）"
echo ">>> 构建完成"
