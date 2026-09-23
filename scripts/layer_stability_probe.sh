#!/usr/bin/env bash
# WP-G T5-6 / T6 census: sample the LIVE pet process over a time window.
#
# Why this exists (~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md §7.3-6, §7.3 T6): the fd bound is only
# meaningful as a *series* taken while mode toggles happen, and the plan's
# acceptance command is `ls -l /proc/<pid>/fd | grep -c '/memfd:meapet-px'`
# -- `-l` is load-bearing, `ls` without it prints bare descriptors and every
# grep would return 0 forever (agents-rules §8: an impossible-to-fail gate).
#
# Failure mode of this script (agents-rules §1, all three parts):
#   benefit  = one line per interval carries fd/RSS/thread/surface counts plus
#              two provenance columns, so a "0 memfd, all green" run is
#              self-detecting instead of looking like a pass;
#   crash    = a wrong pid makes every counter read 0, which is exactly what a
#              pass would look like.  Nothing here can tell "no leaks" from
#              "watching somebody else's process";
#   backstop = PROVENANCE columns (shim_map, surfaces) and the end-of-run
#              verdict block.  shim_map==0 for the whole run means the pid is
#              not the process that loaded liblayer_shell_shim.so -> the whole
#              series is void, not a pass.
#
# What this CANNOT decide: it never enters penetration mode by itself.  If the
# app never activates the bridge, memfd stays 0 and the run proves nothing.
# That is why the verdict block requires a nonzero memfd observation.
#
# Output goes to a file only (agents-rules §12: the probe's own text is screen
# content).  Nothing is printed to stdout while sampling.
set -u

PID="${1:-}"
OUT="${2:-}"
INTERVAL="${3:-10}"
DURATION="${4:-1800}"

# The plan's acceptance command is `bash scripts/layer_stability_probe.sh <pid> 1800`,
# i.e. arg 2 is the DURATION there and an outfile here.  Accept either shape
# instead of silently sampling for 10 seconds and writing a file named "1800".
case "$OUT" in
    ''|*[!0-9]*) : ;;                       # not all digits -> it is an outfile
    *) DURATION="$OUT"; OUT="/tmp/layer_stability_probe.${PID}.txt"; INTERVAL="${3:-10}" ;;
esac

# replay mode: re-judge an EXISTING series without sampling and without
# appending to the file.  The census runs for 30 minutes against the live desktop
# and can only be taken once per window, so the verdict logic must be reviewable
# after the fact (and the parser fix below was found by reading a 90-sample series
# whose own verdict line was wrong).  Replay does NOT rewrite the artifact: the
# previous verdict lines start with `#`, and the extraction skips them by matching
# data rows only -- an appended second verdict would otherwise be read as evidence.
verdict_block() {
    # Every extraction reads ONLY data rows (`^[0-9]`), for exactly that reason.
    # Extraction is `grep -o 'memfd=[0-9]*' | cut`, i.e. the SAME shape that
    # already works for meapet_surfaces below.  The previous form
    # `awk -F'memfd=|[^0-9]' '{print $2}'` was broken: the field separator was an
    # alternation that also matched every single non-digit, so $2 was never the
    # value, max_memfd came out empty, `${max_memfd:-0}` read it as 0 and every
    # real run self-declared VOID (measured 2026-09-20: a 90-sample series whose
    # memfd column held 3 in 45 rows was reported as "memfd stayed 0").
    # Failure mode of the fix (agents-rules §1): a value-less row (`memfd=?`, which
    # the sampler writes when /proc disappears) is silently skipped by grep -o,
    # so a truncated series could look short-but-clean.  Backstop: the parsed
    # count below is compared with the number of data rows and a mismatch voids
    # the run instead of being reported as an admissible series.
    memfd_vals=$(grep '^[0-9]' "$OUT" | grep -o 'memfd=[0-9]*' | cut -d= -f2)
    data_rows=$(grep -c '^[0-9]' "$OUT")
    parsed=$(printf '%s\n' "$memfd_vals" | grep -c '[0-9]')
    max_memfd=$(printf '%s\n' "$memfd_vals" | sort -n | tail -1)
    min_memfd=$(printf '%s\n' "$memfd_vals" | sort -n | head -1)
    max_surfaces=$(grep '^[0-9]' "$OUT" | grep -o 'meapet_surfaces=[0-9]*' | cut -d= -f2 | sort -n | tail -1)
    max_rss=$(grep '^[0-9]' "$OUT" | grep -o 'VmRSS_kB=[0-9]*' | cut -d= -f2 | sort -n | tail -1)
    min_rss=$(grep '^[0-9]' "$OUT" | grep -o 'VmRSS_kB=[0-9]*' | cut -d= -f2 | sort -n | head -1)
    shim_hits=$(grep '^[0-9]' "$OUT" | grep -c 'shim_map=[1-9]' || true)
    # ---- the bound itself (~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md §7.3-6, F-M7 "先数 ctx 再判 fd") ----
    # ctx count has a witness in this very file: `meapet_surfaces` is the number
    # of niri layers carrying our namespace, and one ctx maps exactly one layer
    # surface, so the bound `memfd <= ctx x RING_DEPTH` is evaluable per row
    # without trusting any call-count narrative.  RING_DEPTH is READ from the
    # crate, never restated here (agents-rules §7).
    #   benefit = §7.3-6's fd clause stops being a description ("max_memfd=3")
    #             that a reader turns into a decision;
    #   crash   = if RING_DEPTH cannot be read, every bound comparison would be
    #             `memfd <= 0`-ish nonsense or silently vacuous;
    #   backstop= an unreadable RING_DEPTH voids the run (rc=3) instead of
    #             printing "admissible", and rows whose counters do not parse
    #             are counted and void rather than skipped.
    ring_depth=$(sed -n 's/^\(pub([a-z]*) \)\?const RING_DEPTH: *usize *= *\([0-9][0-9]*\);.*/\2/p' \
        "$(dirname "$0")/../native/layer_shell/src/ring.rs" 2>/dev/null | head -1)
    counts=$(grep '^[0-9]' "$OUT" | awk -v rd="${ring_depth:-0}" '
        { m = -1; s = -1
          for (i = 1; i <= NF; i++) {
              if ($i ~ /^memfd=/)            { sub(/^memfd=/, "", $i);            m = $i + 0 }
              if ($i ~ /^meapet_surfaces=/)  { sub(/^meapet_surfaces=/, "", $i);  s = $i + 0 }
          }
          if (m < 0 || s < 0) { unparse++; next }
          if (s == 0 && m > 0) orphan++          # I5: memfd held with no surface
          if (rd > 0 && s > 0 && m > s * rd) viol++
          if (s > maxs) maxs = s
          if (m > maxm) maxm = m
          rows++
        }
        END { printf "%d %d %d %d %d", viol + 0, orphan + 0, unparse + 0, maxs + 0, maxm + 0 }
    ')
    read -r bound_viol orphan_viol unparse_viol max_ctx max_mfd <<<"$counts"
    VERDICT_RC=0
    echo "#"
    echo "# samples=$n last_alive=$alive"
    echo "# memfd column: parsed=$parsed data_rows=$data_rows min=${min_memfd:-none} max=${max_memfd:-none}"
    echo "# max_meapet_surfaces=${max_surfaces:-none} VmRSS_kB min=${min_rss:-none} max=${max_rss:-none}"
    echo "# samples_with_shim=${shim_hits:-0}"
    if [ -z "$ring_depth" ]; then
        echo "# bound: RING_DEPTH NOT READABLE from native/layer_shell/src/ring.rs --"
        echo "#        the §7.3-6 comparison cannot be evaluated; this run is void."
        VERDICT_RC=3
    else
        echo "# bound: memfd <= ctx x RING_DEPTH, ctx = meapet_surfaces,"
        echo "#        RING_DEPTH=$ring_depth (read from ring.rs), worst row observed"
        echo "#        memfd=${max_mfd} with ctx=${max_ctx} (unparseable rows: ${unparse_viol})"
    fi
    if [ "${shim_hits:-0}" -eq 0 ]; then
        echo "# VERDICT: VOID -- pid $PID never mapped liblayer_shell_shim.so."
        echo "#          This is the wrong pid (or the bridge never loaded)."
        VERDICT_RC=2
    elif [ "$parsed" -ne "$data_rows" ]; then
        echo "# VERDICT: VOID -- $data_rows data rows but only $parsed carry a numeric"
        echo "#          memfd, so the series is truncated and cannot be judged."
        VERDICT_RC=2
    elif [ "${max_memfd:-0}" -eq 0 ]; then
        echo "# VERDICT: VOID -- shim mapped but memfd stayed 0: penetration mode"
        echo "#          was never entered, so there is nothing to count (agents-rules §8)."
        VERDICT_RC=2
    elif [ -z "$ring_depth" ] || [ "${unparse_viol:-0}" -gt 0 ]; then
        echo "# VERDICT: VOID -- ${unparse_viol:-0} row(s) lack a parseable memfd/ctx pair"
        echo "#          or RING_DEPTH is unreadable, so the bound was never applied."
        VERDICT_RC=3
    elif [ "${orphan_viol:-0}" -gt 0 ]; then
        echo "# VERDICT: FAIL(I5) -- ${orphan_viol} row(s) hold memfd>0 with ctx=0"
        echo "#          (descriptors with no layer surface: the orphan-ctx signature)."
        VERDICT_RC=4
    elif [ "${bound_viol:-0}" -gt 0 ]; then
        echo "# VERDICT: FAIL(§7.3-6) -- ${bound_viol} row(s) exceed ctx x RING_DEPTH"
        echo "#          (per-ctx leak). Note what this bound CANNOT catch: an extra"
        echo "#          ctx with its own full ring still satisfies it (pre-fix"
        echo "#          pid 779175 read 6 memfd / 2 surfaces = 2 x RING_DEPTH exactly);"
        echo "#          ctx count is judged by §7.3-1's surface count, not here."
        VERDICT_RC=4
    else
        echo "# VERDICT: series admissible AND bound held -- every row satisfies"
        echo "#          memfd <= ctx x RING_DEPTH (L3, this machine/revision only)."
    fi
}

if [ "$PID" = "--replay" ]; then
    OUT="${2:-}"
    if [ -z "$OUT" ] || [ ! -f "$OUT" ]; then
        echo "usage: $0 --replay <census-file>" >&2
        exit 2
    fi
    PID="(replay)"
    n=$(grep -c '^[0-9]' "$OUT")
    alive="n/a (replayed; liveness was recorded by the original run)"
    verdict_block
    exit "${VERDICT_RC:-0}"
fi


if [ -z "$PID" ] || [ -z "$OUT" ]; then
    echo "usage: $0 <pid> <outfile|duration_s> [interval_s] [duration_s]" >&2
    exit 2
fi
if ! kill -0 "$PID" 2>/dev/null; then
    echo "abort: pid $PID is not alive (resolve it from \`niri msg -j windows\`," >&2
    echo "       never from pgrep: a launcher/wrapper pid is alive but empty)." >&2
    exit 2
fi

: > "$OUT"

sample() {
    local ts memfd fds rss threads surfaces shim_map dead
    if ! kill -0 "$PID" 2>/dev/null; then
        printf '%s\tDEAD\n' "$(date +%s)" >> "$OUT"
        return 1
    fi
    # `-l` then grep the *target* of the symlink: the memfd name is
    # native/layer_shell/src/ring.rs:360 (`memfd_create("meapet-px", MFD_CLOEXEC)`),
    # so a leak shows up as `/memfd:meapet-px (deleted)`.
    memfd=$(ls -l "/proc/$PID/fd" 2>/dev/null | grep -c '/memfd:meapet-px' || true)
    fds=$(ls -1 "/proc/$PID/fd" 2>/dev/null | wc -l || true)
    rss=$(awk '/^VmRSS:/{print $2}' "/proc/$PID/status" 2>/dev/null)
    threads=$(awk '/^Threads:/{print $2}' "/proc/$PID/status" 2>/dev/null)
    # niri reports no geometry (I3); the only externally visible liveness
    # signal is how many surfaces carry our namespace.  jq is absent here,
    # hence grep.
    surfaces=$(niri msg -j layers 2>/dev/null | grep -o '"namespace":"meapet"' | wc -l || true)
    shim_map=$(grep -c liblayer_shell_shim "/proc/$PID/maps" 2>/dev/null || true)
    printf '%s\tmemfd=%s\tfds=%s\tVmRSS_kB=%s\tthreads=%s\tmeapet_surfaces=%s\tshim_map=%s\n' \
        "$(date +%s)" "${memfd:-?}" "${fds:-?}" "${rss:-?}" "${threads:-?}" \
        "${surfaces:-?}" "${shim_map:-?}" >> "$OUT"
    return 0
}

printf '# ts\tmemfd\tfds\tVmRSS_kB\tthreads\tmeapet_surfaces\tshim_map\n' > "$OUT"
printf '# subject pid=%s interval=%ss duration=%ss started=%s\n' \
    "$PID" "$INTERVAL" "$DURATION" "$(date -Is)" >> "$OUT"

end=$(( $(date +%s) + DURATION ))
n=0
alive=1
while [ "$(date +%s)" -lt "$end" ]; do
    sample || { alive=0; break; }
    n=$(( n + 1 ))
    sleep "$INTERVAL"
done

# Verdict block -- written after the series so a reader cannot mistake the
# header for evidence.  Every clause is a *falsification* check: passing them
# is what makes the series admissible, failing any voids the run.
# It is a function printing to stdout, because `--replay` must be able to
# re-judge an existing series WITHOUT appending to it (appending would let a
# second verdict line enter the file, after which the extraction below reads
# `max_memfd=3` out of the previous verdict's own text -- the register writing
# into the quantity it registers, agents-rules §16).
verdict_block >> "$OUT"
exit "${VERDICT_RC:-0}"
