//! Global bridge state: phase machine, handle registry, diagnostic
//! counters, sticky error buffer — and the crate's ONLY `catch_unwind`
//! site (spec §4.1/§4.3, I4).
//!
//! Every `#[no_mangle] extern "C"` function in `ffi.rs` routes its body
//! through [`guarded`], so implementations can `unwrap` freely: a panic
//! never crosses the FFI boundary, it flips the bridge to `Poisoned` and
//! produces the §7.1 failure shape instead (agents-rules §1: that is the
//! whole point of the wrapper — an unwrapped panic would SIGABRT the
//! Python host, §4.8-1).
//!
//! WP-D adds the thread model (spec §4.2/§6.4): the pump owns the socket's
//! read half and calls back into [`apply_configure`] / [`apply_closed`] /
//! [`apply_buffer_release`] under this same lock, each gated by the init
//! `epoch` it captured at spawn time so a dying pump of a previous
//! generation can never touch a rebuilt bridge.
//!
//! Failure mode of `guarded` itself: if `on_panic` or a `Bridge` teardown
//! panics while the lock guard drops, the unwinding crosses the boundary
//! and aborts (§4.8-1/§4.8-8). `on_panic` closures are constant returns;
//! the pump `JoinHandle` is joined OUTSIDE the lock (F-M3), never inside a
//! `Drop` that runs under it.

use std::any::Any;
use std::os::raw::c_char;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{LazyLock, Mutex, MutexGuard, PoisonError};

use wayland_client::protocol::wl_compositor::WlCompositor;
use wayland_client::QueueHandle;

use crate::handles::{Entry, HandleTable};
use crate::pump::PumpHandle;
use crate::ring::Submit;
use crate::wayland::{ConnectError, Core, LayerCtx, WaylandData};

/// §4.3 state machine. `Poisoned` is sticky: only `cleanup` (or a
/// successful `init`, which tears down first) leaves it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    Uninitialized,
    Ready,
    Poisoned,
}

/// What [`Bridge::begin_init`] tells the FFI `layer_shell_init` export it
/// has to do outside the lock before the rebuild can proceed.
pub(crate) enum StalePump {
    /// Bridge was already `Ready`: the export returns 0 with no rebuild.
    AlreadyReady,
    /// Proceed to [`Bridge::finish_init`]; the payload (a pump from a
    /// poisoned epoch, if any) must be joined BEFORE the lock is re-taken
    /// for the rebuild (F-M3). `None` for a clean `Uninitialized` start.
    Took(Option<PumpHandle>),
}

/// I6: monotonic diagnostics. They NEVER drive any decision anywhere
/// (spec §6.5/§7.2 — using `dropped_busy` to trigger rebuild/degrade
/// would read backpressure as failure). `dropped_busy` is written by the
/// ring's full path (§4.5); `releases` by the pump (§6.5).
///
/// Scope decision (WP-F, resolving the question WP-E registered): these are
/// **process-lifetime** counters, deliberately NOT reset by `init`. §6.5
/// states the property as "单调增长", and a per-epoch reset would make that
/// sentence false — the sticky error buffer (§7.1 #11) is the per-epoch
/// channel, this is not. Consequence for tests: assert a DELTA, never an
/// absolute value, because every test in this crate shares one global bridge
/// and an absolute expectation silently encodes "no earlier test moved them".
#[derive(Debug, Default)]
pub struct Counters {
    pub dropped_busy: u64,
    pub dropped_unconfigured: u64,
    pub dropped_mismatch: u64,
    /// `wl_buffer::release` events the pump observed (spec §6.5 harvest
    /// accounting; diagnostics only — the ring's real signal is the slot
    /// `in_use` flag set directly by the handler).
    pub releases: u64,
}

pub struct Bridge {
    pub phase: Phase,
    pub core: Option<Core>,
    pub handles: HandleTable,
    /// Init epoch stamped into every `Entry` (`created_by`, spec §4.4):
    /// bumped on each successful init AND on every teardown, so a handle
    /// from a dead epoch can never touch a live entry even if its
    /// (id, generation) pair were to be reused.
    pub epoch: u32,
    pub counters: Counters,
}

impl Bridge {
    pub(crate) fn new() -> Self {
        Bridge {
            phase: Phase::Uninitialized,
            core: None,
            handles: HandleTable::new(),
            epoch: 0,
            counters: Counters::default(),
        }
    }

    // ----- §4.3 lifecycle ---------------------------------------------

    /// Test-only convenience equivalent to the FFI export's two-phase
    /// sequence. The EXPORT does NOT call this (production lives in
    /// `ffi::layer_shell_init`, which joins a taken pump OUTSIDE the global
    /// lock — F-M3, which the composite cannot arrange because it runs
    /// entirely under one `guarded`): here a taken pump is simply dropped
    /// (PumpHandle's write-end close wakes the thread and detaches it) —
    /// safe because the only callers are L2 tests over `Fake` cores that
    /// own no pump.
    #[cfg(test)]
    pub(crate) fn init(&mut self) -> i32 {
        let pump = match self.begin_init() {
            StalePump::AlreadyReady => return 0,
            StalePump::Took(pump) => pump,
        };
        drop(pump);
        self.finish_init()
    }

    /// §4.3/§4.7-9: the locked half of init. `Ready` short-circuits; a
    /// `Poisoned` bridge tears its residuals down (retiring the pump out
    /// of `core` so the export can join it lock-free) before the rebuild.
    pub(crate) fn begin_init(&mut self) -> StalePump {
        match self.phase {
            Phase::Ready => StalePump::AlreadyReady,
            Phase::Poisoned => StalePump::Took(self.teardown()),
            Phase::Uninitialized => StalePump::Took(None),
        }
    }

    /// The rebuild half: connect, spawn the pump, and enter `Ready`.
    pub(crate) fn finish_init(&mut self) -> i32 {
        self.init_impl(connect_for_init())
    }

    /// Connect-result injection point: `init` in production, tests for the
    /// deterministic -1/-2/-3 branches without touching process env.
    ///
    /// §4.2: the pump spawn lives inside this function so a spawn failure
    /// is an init `-3` and never a `Ready` bridge — a bridge whose socket
    /// nobody reads is the exact silent-degradation state §6.4 exists to
    /// prevent (spec §7.1 #1, code -3 "pump/internal establishment").
    pub(crate) fn init_impl(&mut self, conn: Result<Core, ConnectError>) -> i32 {
        match conn {
            Ok(mut core) => {
                // Stamp the NEW epoch first: it is both what the pump
                // carries for its event-gate and what every `Entry` gets
                // as `created_by`.
                self.epoch = self.epoch.wrapping_add(1);
                let epoch = self.epoch;
                let spawned = match &mut core {
                    Core::Live(live) => live.spawn_pump(epoch),
                    Core::Fake => Ok(()),
                };
                match spawned {
                    Ok(()) => {
                        self.core = Some(core);
                        self.phase = Phase::Ready;
                        self.clear_sticky_error(); // a healthy epoch starts silent
                        0
                    }
                    Err(err) => {
                        // `spawn_pump` only ever fails before starting the
                        // thread (pipe2 or spawn error), so `core` dropping
                        // here leaks no thread; its OwnedFds close on drop.
                        self.phase = Phase::Uninitialized;
                        let code = err.code();
                        self.set_sticky_error(&format!("layer_shell_init: {err}"));
                        code
                    }
                }
            }
            Err(err) => {
                self.phase = Phase::Uninitialized;
                let code = err.code();
                self.set_sticky_error(&format!("layer_shell_init: {err}"));
                code
            }
        }
    }

    /// §4.3: destroy all live ctx, retire the pump, release the
    /// connection, clear poison. Returns the pump so the CALLER joins it
    /// outside the lock. Sticky error is deliberately NOT cleared — the
    /// reason a bridge died stays readable until the next epoch starts
    /// healthy.
    ///
    /// The per-context Wayland proxies are dropped WITHOUT sending
    /// `destroy`: the connection close immediately after is the compositor's
    /// signal to reap every object of this client, so a whole-teardown needs
    /// no per-surface request (and §4.7-9's "no use-after-free" holds
    /// because the server owns reaping, not the freed client memory).
    /// Single-context `destroy_context` DOES send `destroy` because there
    /// the connection stays alive.
    ///
    /// Rings retire with their entries: `handles.clear()` drops each
    /// `LayerCtx`, whose `Option<Ring>` drops its slots, and `Slot::Drop`
    /// sends `destroy buffer → destroy pool` before the fd is closed and the
    /// mapping unmapped (§4.5's order, identical on both teardown paths).
    /// Those two requests are best-effort here — the connection goes down
    /// right after, and the compositor reaps every object of a closing
    /// client — so the part that is NOT optional is the process-side half:
    /// if `Slot::Drop` were ever reordered to close before destroy, we would
    /// be handing the compositor a buffer whose storage is gone (the exact
    /// UB §4.5's order exists to prevent), and T6's fd census would stop
    /// matching `ctx × RING_DEPTH + 常量`.
    pub(crate) fn teardown(&mut self) -> Option<PumpHandle> {
        let pump = self.core.as_mut().and_then(|core| core.take_pump());
        self.handles.clear();
        self.core = None;
        self.epoch = self.epoch.wrapping_add(1);
        self.phase = Phase::Uninitialized;
        pump
    }

    // ----- §4.4 lookup with both guards --------------------------------

    fn guard_phase(&self) -> Result<(), &'static str> {
        match self.phase {
            Phase::Ready => Ok(()),
            Phase::Poisoned => Err("bridge poisoned; call layer_shell_init to rebuild (§4.3)"),
            Phase::Uninitialized => Err("layer_shell_init not called or failed"),
        }
    }

    /// Phase + live-handle + not-dead guards in the contractual order:
    /// poison → handle → closed (§4.7-12) → operation-specific params.
    fn guard_ctx(&self, handle: u64) -> Result<(), &'static str> {
        self.guard_phase()?;
        let entry = self
            .live_entry(handle)
            .ok_or("unknown, stale or foreign handle (§4.4)")?;
        if entry.ctx.dead {
            Err("layer surface was closed by the compositor (§4.7-12)")
        } else {
            Ok(())
        }
    }

    pub(crate) fn live_entry(&self, value: u64) -> Option<&Entry> {
        let e = self.handles.get(value)?;
        (e.created_by == self.epoch).then_some(e)
    }

    pub(crate) fn live_entry_mut(&mut self, value: u64) -> Option<&mut Entry> {
        let epoch = self.epoch;
        let e = self.handles.get_mut(value)?;
        (e.created_by == epoch).then_some(e)
    }

    // ----- §7.1 #11 sticky error ---------------------------------------

    /// Caller must hold the bridge lock (the `&mut self` is that proof);
    /// readers use `last_error_ptr` without the lock — atomics keep that
    /// race benign (worst case is a torn diagnostic string, §7.1 #11).
    pub(crate) fn set_sticky_error(&mut self, msg: &str) {
        write_sticky(msg);
    }

    pub(crate) fn clear_sticky_error(&mut self) {
        write_sticky("");
    }

    // ----- pump-thread event seams (§4.2/§6.3/§4.7-12) -----------------

    /// Poison from the pump's own exit paths (read/disconnect/poll error).
    /// Epoch-guarded: a pump that outlived its `teardown` (already detached)
    /// must not poison the bridge a later `init` built.
    pub(crate) fn pump_poison(&mut self, event_epoch: u32, msg: &str) {
        if self.epoch != event_epoch {
            return;
        }
        self.phase = Phase::Poisoned;
        self.set_sticky_error(msg);
    }

    /// §6.3 `configure`: ack happened in the handler; here the logical size
    /// follows the compositor. A `0` dimension is protocol for "keep the
    /// current request", not a degenerate size to clamp (§4.7 row, and
    /// AGENTS §4's reverse-caution: refusing it would be as wrong as
    /// treating it as 0). If the surface is clickable, §4.7-7 requires the
    /// input region to be re-sent at the new size (the step C omitted).
    pub(crate) fn apply_configure(
        &mut self,
        event_epoch: u32,
        handle: u64,
        width: u32,
        height: u32,
    ) {
        if self.epoch != event_epoch || !matches!(self.phase, Phase::Ready) {
            return;
        }
        let clickable = {
            let Some(e) = self.live_entry_mut(handle) else {
                return;
            };
            let ctx = &mut e.ctx;
            ctx.logical_w = if width > 0 { width } else { ctx.req_w };
            ctx.logical_h = if height > 0 { height } else { ctx.req_h };
            ctx.configured = true;
            !ctx.click_through
        };
        // §4.5: the ring's geometry follows the LOGICAL size and this is the
        // only place that decides it — the pump thread, under the bridge lock,
        // so no submit path can observe the swap. A build failure is a reason,
        // not a poison: the bridge and its surface are healthy, frames simply
        // have nowhere to be painted until the next configure retries.
        let ring_err = if let Some(Core::Live(live)) = &self.core {
            let fmt = crate::format::choose_format(live.abgr_supported);
            self.handles.get_mut(handle).and_then(|e| {
                if e.ctx.dead {
                    // §4.7-12: a configure that races in after `closed`
                    // must not allocate three memfds nobody can paint
                    // into — the entry is retired by destroy or teardown.
                    return None;
                }
                e.ctx.ensure_ring(&live.shm, &live.qh, fmt).err()
            })
        } else {
            None
        };
        if let Some(why) = ring_err {
            self.set_sticky_error(&format!(
                "layer_configure: buffer ring for the new size not built: {why} (§4.5)"
            ));
        }
        if clickable {
            // Sends the new input region AND flushes (which also pushes the
            // ring's `create_pool`/`create_buffer` requests queued above).
            self.refresh_input_region(handle);
        } else {
            self.live_flush("layer_configure");
        }
    }

    /// §4.7-12: the compositor retracted this surface. Mark it dead so
    /// every subsequent op is a defined rejection, and leave a reason.
    pub(crate) fn apply_closed(&mut self, event_epoch: u32, handle: u64) {
        if self.epoch != event_epoch {
            return;
        }
        if let Some(e) = self.live_entry_mut(handle) {
            e.ctx.dead = true;
        }
        self.set_sticky_error("layer surface closed by the compositor (§4.7-12)");
    }

    /// §6.5 release accounting. The slot's `in_use` flag is cleared
    /// directly by the handler (WP-E harvest channel); this counter is the
    /// diagnostics twin and gates nothing (I6).
    pub(crate) fn apply_buffer_release(&mut self, event_epoch: u32) {
        if self.epoch != event_epoch || !matches!(self.phase, Phase::Ready) {
            return;
        }
        self.counters.releases += 1;
    }

    // ----- exports' implementations (called from ffi.rs under guarded) --

    pub(crate) fn create_context(
        &mut self,
        state: *mut std::os::raw::c_void,
        w: i64,
        h: i64,
        x: i32,
        y: i32,
    ) -> Option<u64> {
        if let Err(why) = self.guard_phase() {
            self.set_sticky_error(&format!("layer_create_context: {why}"));
            return None;
        }
        // §4.7 row 8: the C shim accepted a non-NULL `state` by lazily
        // self-fetching the global; Rust rejects it — silently ignoring a
        // caller-provided pointer is exactly the "plausible looking value"
        // class (agents-rules §8). Python's frozen call site passes None.
        if !state.is_null() {
            self.set_sticky_error("layer_create_context: state must be NULL (§4.7-8)");
            return None;
        }
        if let Err(why) = size_in_bounds(w, h) {
            self.set_sticky_error(&format!("layer_create_context: {why}"));
            return None;
        }
        let ctx = crate::wayland::LayerCtx::new(w as u32, h as u32, x, y);
        let created_by = self.epoch;
        let handle = match self.handles.alloc(ctx, created_by) {
            Some(handle) => handle,
            None => {
                self.set_sticky_error(
                    "layer_create_context: handle id space exhausted (I7 reject)",
                );
                return None;
            }
        };
        // §6.1: in the Live world create the compositor objects now, so a
        // returned handle always names a mapped-in-waiting surface. Under
        // the lock no pump event can land yet, so filling the entry after
        // `create_layer_ctx_objects` is race-free.
        let flush_err = if let Some(Core::Live(live)) = &self.core {
            let (surface, layer) = live.create_layer_ctx_objects(handle, w as u32, h as u32, x, y);
            if let Some(e) = self.handles.get_mut(handle) {
                e.ctx.attach_proxies(surface, layer);
            }
            live.flush_checked()
        } else {
            None
        };
        if let Some(err) = flush_err {
            // The wire died mid-create: roll the half-built entry back and
            // poison — never hand back a handle to a surface the
            // compositor never received.
            self.handles.remove(handle);
            self.phase = Phase::Poisoned;
            self.set_sticky_error(&format!("layer_create_context: flush failed: {err}"));
            return None;
        }
        Some(handle)
    }

    pub(crate) fn set_click_through(&mut self, handle: u64, en: i32) {
        if let Err(why) = self.guard_ctx(handle) {
            self.set_sticky_error(&format!("layer_set_click_through: {why}"));
            return;
        }
        let through = en != 0;
        self.with_entry_and_live(handle, |ctx, live| {
            ctx.click_through = through;
            if let Some((compositor, qh)) = live {
                ctx.apply_input_region(compositor, qh);
            }
        });
        self.live_flush("layer_set_click_through");
    }

    pub(crate) fn update_pixels(
        &mut self,
        handle: u64,
        buf: *const u8,
        w: i64,
        h: i64,
        force: u32,
    ) {
        if let Err(why) = self.guard_ctx(handle) {
            self.set_sticky_error(&format!("layer_update_pixels: {why}"));
            return;
        }
        let (configured, lw, lh) = {
            let e = self.live_entry(handle).unwrap();
            (
                e.ctx.configured,
                e.ctx.logical_w as i64,
                e.ctx.logical_h as i64,
            )
        };
        if !configured {
            // §6.3: counted drop, no sticky (equivalent to C's silent
            // `if (!ctx->configured) return;`).
            self.counters.dropped_unconfigured += 1;
            return;
        }
        if let Err(why) = size_in_bounds(lw, lh) {
            // §7.1 #3 applied to what the COMPOSITOR proposed in `configure`,
            // not to the caller: `lw`/`lh` are the only inputs to the byte
            // count below, so an absurd proposal must be refused by name
            // instead of being multiplied. Failure mode if this gate were
            // dropped: `lw*lh*4` wraps on the i64/usize boundary and the
            // slice constructed from it is no longer the area we meant to
            // read — the §4.8-5 class of defect, in the other direction.
            self.set_sticky_error(&format!("layer_update_pixels: {why}"));
            return;
        }
        if w != lw || h != lh {
            // §6.3 + §4.8-5: the passed (w,h) is only a claim; bytes are
            // ever interpreted by the LOGICAL size.
            self.counters.dropped_mismatch += 1;
            self.set_sticky_error(&format!(
                "layer_update_pixels: size mismatch, got {w}x{h}, logical {lw}x{lh}"
            ));
            return;
        }
        if buf.is_null() {
            self.set_sticky_error("layer_update_pixels: NULL pixel buffer");
            return;
        }
        let abgr = self.core.as_ref().is_some_and(|c| c.abgr_supported());
        let fmt = match crate::format::resolve_force(force, abgr) {
            None => {
                // §6.2/§7.1 #6: bad force → this frame is NOT submitted +
                // sticky error (I7: no "pick something that looks right").
                self.set_sticky_error(&format!(
                    "layer_update_pixels: unsupported force format 0x{force:08x}"
                ));
                return;
            }
            Some(fmt) => fmt,
        };
        // §4.5 + §6.3: the readable length is the LOGICAL area times 4 bytes
        // per pixel (both P0 formats are 32 bpp), never the caller's claim —
        // the claim was only checked for equality above (§4.8-5). The bounds
        // gate above proves `lw*lh ≤ 16_777_216`, so this arithmetic cannot
        // overflow and needs no checked form.
        let len = (lw * lh * crate::format::BYTES_PER_PIXEL as i64) as usize;
        // SAFETY: the §7.1 #5 input contract is "缓冲 ≥ w*h*4 可读" and `lw`/
        // `lh` are this handle's logical dimensions, so `buf` has `len`
        // readable bytes. The slice is read-only in every consumer below (the
        // ring's own mapping is the write target), and it does not outlive
        // this function: nothing stores it while the compositor could still
        // be looking at the caller's memory.
        let src = unsafe { std::slice::from_raw_parts(buf, len) };
        let outcome = if let Some(Core::Live(_)) = &self.core {
            match self.handles.get_mut(handle) {
                Some(e) => e.ctx.submit_frame(src, fmt),
                // The guards above proved this entry live and nothing else
                // takes the bridge lock in between, so this arm is
                // unreachable; returning quietly beats mislabelling it.
                None => return,
            }
        } else {
            // L2 world: no compositor ⇒ no memfd to paint into, so a valid
            // frame is a documented no-op. The validation chain above is what
            // `ffi`'s Fake tests pin (spec §7.4); the ring itself is the
            // `ring::` machine plus the `#[ignore]`d live smoke (L3).
            return;
        };
        match outcome {
            Submit::Pending(idx) => {
                let err = match &self.core {
                    Some(Core::Live(live)) => live.flush_checked(),
                    _ => None,
                };
                match err {
                    Some(err) => {
                        // §4.3: the socket died mid-commit. The slot is NOT
                        // marked busy (§4.5's order), which is moot anyway —
                        // the bridge is poisoned and every path now rejects
                        // until `cleanup` rebuilds it.
                        self.phase = Phase::Poisoned;
                        self.set_sticky_error(&format!("layer_update_pixels: flush failed: {err}"));
                    }
                    None => {
                        if let Some(e) = self.handles.get_mut(handle) {
                            e.ctx.confirm_submit(idx);
                        }
                    }
                }
            }
            Submit::Busy => {
                // §4.5: backpressure is correct behavior. Counted, silent,
                // no sticky (a readable error every 16 ms would bury the
                // diagnostics that mean something — I6/§7.2).
                self.counters.dropped_busy += 1;
            }
            Submit::NoRing => {
                self.set_sticky_error(&format!(
                    "layer_update_pixels: no buffer ring for {lw}x{lh}; \
                     waiting for the next configure (§4.5)"
                ));
            }
            Submit::FormatMismatch(ring_fmt) => {
                self.set_sticky_error(&format!(
                    "layer_update_pixels: forced 0x{:08x} but the only ring holds \
                     0x{:08x}; frame not submitted (§6.2: the compositor does not \
                     advertise the forced format)",
                    fmt.fourcc(),
                    ring_fmt.fourcc()
                ));
            }
            Submit::NoSurface => {
                self.set_sticky_error("layer_update_pixels: context has no wl_surface (§4.5)");
            }
        }
    }

    pub(crate) fn clear(&mut self, handle: u64) {
        if let Err(why) = self.guard_ctx(handle) {
            self.set_sticky_error(&format!("layer_clear: {why}"));
            return;
        }
        // §7.1 #7: attach NULL + damage the logical size + commit. No
        // configured gate (clearing is not a frame) and no ring interaction:
        // in-flight buffers stay the compositor's to release (§4.5).
        let no_surface = if let Some(Core::Live(_)) = &self.core {
            match self.handles.get_mut(handle) {
                Some(e) => !e.ctx.clear_surface(),
                None => false,
            }
        } else {
            // L2 world: nothing to retract from a compositor that isn't there.
            return;
        };
        if no_surface {
            self.set_sticky_error("layer_clear: context has no wl_surface (§4.5)");
            return;
        }
        self.live_flush("layer_clear");
    }

    pub(crate) fn set_position(&mut self, handle: u64, x: i32, y: i32) {
        if let Err(why) = self.guard_ctx(handle) {
            self.set_sticky_error(&format!("layer_set_position: {why}"));
            return;
        }
        self.with_entry_and_live(handle, |ctx, live| {
            ctx.pos_x = x;
            ctx.pos_y = y;
            if live.is_some() {
                ctx.apply_position();
            }
        });
        self.live_flush("layer_set_position");
    }

    pub(crate) fn set_size(&mut self, handle: u64, w: i64, h: i64) {
        if let Err(why) = self.guard_ctx(handle) {
            self.set_sticky_error(&format!("layer_set_size: {why}"));
            return;
        }
        if let Err(why) = size_in_bounds(w, h) {
            self.set_sticky_error(&format!("layer_set_size: {why}"));
            return;
        }
        // §6.3: only the REQUEST changes; logical size follows `configure`
        // (we never assume the compositor accepted).
        self.with_entry_and_live(handle, |ctx, live| {
            ctx.req_w = w as u32;
            ctx.req_h = h as u32;
            if live.is_some() {
                ctx.apply_size_request();
            }
        });
        self.live_flush("layer_set_size");
    }

    pub(crate) fn destroy_context(&mut self, handle: u64) {
        // Deliberately NOT `guard_ctx`: §4.7-12's rejection chain covers
        // operations that RENDER (update/clear/position/size/touch), while
        // destroy RELEASES the registry slot. A `closed` ctx is still
        // alive in the table (dead ≠ removed — its proxies are), and
        // destroy is the only per-ctx path that frees them. Rejecting it
        // would strand the entry until `cleanup` nukes every ctx on the
        // bridge, turning one retracted surface into collateral damage for
        // the rest — disproportionate under §2's principles. Unknown /
        // stale / double-destroy handles still take the defined-rejection
        // path below.
        if let Err(why) = self.guard_phase() {
            self.set_sticky_error(&format!("layer_destroy_context: {why}"));
            return;
        }
        if self.live_entry(handle).is_none() {
            // double-destroy and unknown handles land here: defined
            // rejection, never a free of someone else's entry (§4.4).
            self.set_sticky_error("layer_destroy_context: unknown, stale or foreign handle (§4.4)");
            return;
        }
        // §4.5: destroy the layer surface then the base surface while the
        // connection is still up (unlike whole-teardown, which lets the
        // close reap them).
        if let Some(e) = self.handles.get_mut(handle) {
            e.ctx.destroy_proxies();
        }
        self.live_flush("layer_destroy_context");
        let _ = self.handles.remove(handle);
    }

    // ----- shared plumbing for the request-sending exports -------------

    /// Apply `f` to the entry's ctx, handing it a borrow of the compositor
    /// pair ONLY when the core is live. Splitting the `core`/`handles`
    /// field borrows is the reason this can't be a plain closure over
    /// `&mut self`.
    fn with_entry_and_live<R>(
        &mut self,
        handle: u64,
        f: impl FnOnce(&mut LayerCtx, Option<(&WlCompositor, &QueueHandle<WaylandData>)>) -> R,
    ) {
        if let Some(Core::Live(live)) = &self.core {
            let pair = (&live.compositor, &live.qh);
            if let Some(e) = self.handles.get_mut(handle) {
                f(&mut e.ctx, Some(pair));
            }
        } else if let Some(e) = self.handles.get_mut(handle) {
            f(&mut e.ctx, None);
        }
    }

    /// Refresh the clickable input region of a live context (called from
    /// the `configure` handler when the surface was clickable before the
    /// resize — §4.7-7).
    fn refresh_input_region(&mut self, handle: u64) {
        let flush_err = if let Some(Core::Live(live)) = &self.core {
            if let Some(e) = self.handles.get_mut(handle) {
                e.ctx.apply_input_region(&live.compositor, &live.qh);
            }
            live.flush_checked()
        } else {
            None
        };
        if let Some(err) = flush_err {
            self.phase = Phase::Poisoned;
            self.set_sticky_error(&format!(
                "layer_configure: input-region flush failed: {err}"
            ));
        }
    }

    /// End a request-sending critical section: push the queued requests
    /// and translate a socket/protocol error into §4.3 poison. No-op on a
    /// `Fake` core (there is no socket).
    fn live_flush(&mut self, prefix: &str) {
        let err = match &self.core {
            Some(Core::Live(live)) => live.flush_checked(),
            _ => None,
        };
        if let Some(err) = err {
            self.phase = Phase::Poisoned;
            self.set_sticky_error(&format!("{prefix}: flush failed: {err}"));
        }
    }
}

/// §7.1 #3 size bounds — the ONE definition, used by every place that turns a
/// size into bytes: `create_context` and `set_size` (caller-supplied, so
/// values arrive as i64 from the FFI), `update_pixels` (the logical size the
/// compositor proposed, which gates the read length), and
/// `LayerCtx::ensure_ring` (the same proposal, which gates the mapping).
/// All math in i64: `w*h` cannot overflow for `w,h ≤ 8192`, but the
/// bound must hold before that clamp too (values arrive as caller i32,
/// product computed here).
pub(crate) fn size_in_bounds(w: i64, h: i64) -> Result<(), String> {
    if w <= 0 || h <= 0 {
        return Err(format!("size {w}*{h}: width and height must be > 0"));
    }
    if w > 8192 || h > 8192 {
        return Err(format!("size {w}*{h}: edge exceeds 8192 (§7.1 #3)"));
    }
    if w * h > 16_777_216 {
        return Err(format!(
            "size {w}*{h}: area {} exceeds 16777216 px (§7.1 #3)",
            w * h
        ));
    }
    Ok(())
}

/// Fixed 256-byte diagnostic buffer behind `layer_last_error` (§7.1 #11):
/// static → the returned address never changes and never is NULL;
/// `AtomicU8` → lock-free readers cannot be UB, only torn (I6: this
/// buffer feeds no decision).
static LAST_ERROR_BUF: [AtomicU8; 256] = [const { AtomicU8::new(0) }; 256];

fn write_sticky(msg: &str) {
    let bytes = msg.as_bytes();
    let mut end = bytes.len().min(LAST_ERROR_BUF.len() - 1);
    // Truncate on a char boundary so a lock-free reader never sees the
    // tail of a severed multi-byte character.
    while end > 0 && !msg.is_char_boundary(end) {
        end -= 1;
    }
    for (slot, b) in LAST_ERROR_BUF[..end].iter().zip(bytes[..end].iter()) {
        slot.store(*b, Ordering::Relaxed);
    }
    LAST_ERROR_BUF[end].store(0, Ordering::Relaxed);
}

pub(crate) fn last_error_ptr() -> *const c_char {
    LAST_ERROR_BUF.as_ptr().cast::<c_char>()
}

/// Reads the sticky buffer as a Rust string (tests + future diagnostics;
/// Python reads it through `layer_last_error`).
#[cfg(test)]
pub(crate) fn sticky_text() -> String {
    let bytes: Vec<u8> = LAST_ERROR_BUF
        .iter()
        .map(|a| a.load(Ordering::Relaxed))
        .take_while(|&b| b != 0)
        .collect();
    String::from_utf8_lossy(&bytes).into_owned()
}

static BRIDGE: LazyLock<Mutex<Bridge>> = LazyLock::new(|| Mutex::new(Bridge::new()));

#[cfg(test)]
impl Bridge {
    /// Deterministic path to `Ready` for the §7.1 chain tests: injects the
    /// fake core instead of reading the process environment. Because the
    /// fake never spawns a pump, L2 tests never start a real thread
    /// (T8's thread-count invariant holds across the whole suite).
    pub(crate) fn init_fake(&mut self) -> i32 {
        self.init_impl(Ok(Core::fake()))
    }
}

/// Test override for the connect step, so the FFI `layer_shell_init`
/// export is deterministic on ANY machine (a dev box WITH a live session
/// would otherwise really connect and really spawn a pump thread). Default
/// `-1` forces a transport-failure shape; a test that wants the real path
/// sets `0` (only the `#[ignore]`d live smoke does).
#[cfg(test)]
pub(crate) static CONNECT_REJECT: std::sync::atomic::AtomicI32 =
    std::sync::atomic::AtomicI32::new(-1);

/// §4.2's single connect call site.
fn connect_for_init() -> Result<Core, ConnectError> {
    #[cfg(test)]
    {
        match CONNECT_REJECT.load(Ordering::SeqCst) {
            0 => Core::connect_to_env(),
            -1 => Err(ConnectError::NoDisplay("injected CONNECT_REJECT".into())),
            -2 => Err(ConnectError::MissingGlobal("injected CONNECT_REJECT")),
            _ => Err(ConnectError::Internal("injected CONNECT_REJECT".into())),
        }
    }
    #[cfg(not(test))]
    {
        Core::connect_to_env()
    }
}

pub(crate) fn lock_bridge() -> MutexGuard<'static, Bridge> {
    // guarded() catches every panic INSIDE the closure, so the std poison
    // flag should never be set; into_inner keeps a panic that slips
    // through elsewhere (e.g. a Drop impl at guard drop, or a test helper
    // that panics while holding the lock) from poisoning the bridge for
    // the whole process — the §4.3 poison state machine, not std's, owns
    // availability (that is why it is sticky-and-recoverable by design).
    BRIDGE.lock().unwrap_or_else(PoisonError::into_inner)
}

/// The single `catch_unwind` site (I4). `f` runs with the bridge lock
/// held; on panic the bridge flips to `Poisoned`, the payload becomes the
/// sticky error, and `on_panic` supplies the §7.1 failure value for this
/// export. Auditable form per §4.3: panic-injection test (ffi.rs), not a
/// grep of catch-site counts (F-M15).
pub(crate) fn guarded<R>(
    name: &'static str,
    f: impl FnOnce(&mut Bridge) -> R,
    on_panic: impl FnOnce(&mut Bridge) -> R,
) -> R {
    let mut bridge = lock_bridge();
    let outcome = catch_unwind(AssertUnwindSafe(|| f(&mut bridge)));
    match outcome {
        Ok(value) => value,
        Err(payload) => {
            bridge.phase = Phase::Poisoned;
            bridge.set_sticky_error(&format!("panic in {name}: {}", panic_text(&payload)));
            on_panic(&mut bridge)
        }
    }
}

fn panic_text(payload: &Box<dyn Any + Send>) -> String {
    if let Some(s) = payload.downcast_ref::<&str>() {
        (*s).to_string()
    } else if let Some(s) = payload.downcast_ref::<String>() {
        s.clone()
    } else {
        "unknown payload".to_string()
    }
}
