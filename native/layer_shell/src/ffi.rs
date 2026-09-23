//! The FFI surface: the eleven `#[no_mangle] extern "C"` exports of
//! spec §7.1 (the I2 set-equality contract).
//!
//! Every body is exactly ONE `state::guarded(...)` call (I4: the only
//! `catch_unwind` site in the crate is inside `guarded`; audited by the
//! panic-injection test below, not by counting `catch_unwind` — F-M15).
//! The signature column of §7.1 is copied verbatim; the Python facade
//! (`meapet/desktop/wayland_layer.py`) binds these with frozen
//! argtypes/restype (spec §7.4) — do not "improve" a parameter type.

use std::os::raw::{c_char, c_int, c_uchar, c_uint, c_void};
use std::ptr;

use crate::state::{guarded, last_error_ptr, StalePump};

#[cfg(test)]
pub(crate) static FORCE_PANIC: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// The global `BRIDGE` and the process-wide sticky buffer are shared
/// mutable state; EVERY test that touches them — here and in `pump` — must
/// hold this one lock, or two threads race on the very buffer they assert.
#[cfg(test)]
pub(crate) static TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// Test-only fault hook checked at the TOP of every export body, so the
/// panic-injection test can prove EVERY export is wrapped, not just that
/// some wrapper exists (§4.3 audit form).
#[cfg(test)]
fn maybe_fault() {
    use std::sync::atomic::Ordering;
    if FORCE_PANIC.load(Ordering::SeqCst) {
        panic!("injected fault");
    }
}

#[cfg(not(test))]
#[inline(always)]
fn maybe_fault() {}

/// §7.1 #1 — 0 ready / -1 no Wayland session / -2 missing global / -3
/// pump-or-internal establishment failure. Panic maps to -3: the §4.3
/// sticky `Poisoned` was just set, the same "internal state could not be
/// established" shape.
///
/// Two-phase (spec §4.3, F-M3): a poisoned bridge may still own a LIVE
/// pump thread, and joining it while holding the global lock would
/// deadlock against the pump's per-event `guarded`. So phase 1 decides and
/// retires the stale pump UNDER the lock, the join happens WITHOUT it, and
/// phase 2 rebuilds UNDER the lock again.
#[no_mangle]
pub extern "C" fn layer_shell_init() -> c_int {
    let stale = guarded(
        "layer_shell_init",
        |b| {
            maybe_fault();
            b.begin_init()
        },
        |_| StalePump::Took(None),
    );
    match stale {
        StalePump::AlreadyReady => return 0,
        StalePump::Took(pump) => {
            if let Some(pump) = pump {
                // A join timeout here is benign: the rebuild below starts a
                // brand-new epoch and every pump callback is epoch-gated, so
                // a detached old pump cannot poison the fresh bridge.
                let _ = pump.quit_and_join();
            }
        }
    }
    guarded(
        "layer_shell_init",
        |b| {
            maybe_fault();
            b.finish_init()
        },
        |_| -3,
    )
}

/// §7.1 #2 — reentrant, no failure shape: it must work even when poisoned
/// (that is its recovery role). Two-phase for the same F-M3 reason as init:
/// retire the pump under the lock, join it outside, and (only on a 1 s
/// join timeout) record why it was detached.
#[no_mangle]
pub extern "C" fn layer_shell_cleanup() {
    let pump = guarded(
        "layer_shell_cleanup",
        |b| {
            maybe_fault();
            b.teardown()
        },
        |_| None,
    );
    if let Some(pump) = pump {
        if let Some(reason) = pump.quit_and_join() {
            // State is already `Uninitialized` (teardown ran under the
            // lock); this second guarded call only surfaces the timeout.
            guarded(
                "layer_shell_cleanup",
                move |b| {
                    b.set_sticky_error(&reason);
                },
                |_| (),
            );
        }
    }
}

/// §7.1 #3 — packed handle as `*mut c_void`; NULL on every rejection,
/// never a fabricated handle (I7).
#[no_mangle]
pub extern "C" fn layer_create_context(
    state: *mut c_void,
    w: c_int,
    h: c_int,
    x: c_int,
    y: c_int,
) -> *mut c_void {
    guarded(
        "layer_create_context",
        |b| {
            maybe_fault();
            match b.create_context(state, w as i64, h as i64, x, y) {
                Some(value) => value as *mut c_void,
                None => ptr::null_mut(),
            }
        },
        |_| ptr::null_mut(),
    )
}

#[no_mangle]
pub extern "C" fn layer_set_click_through(ctx: *mut c_void, en: c_int) {
    let handle = ctx as usize as u64;
    guarded(
        "layer_set_click_through",
        |b| {
            maybe_fault();
            b.set_click_through(handle, en)
        },
        |_| (),
    )
}

#[no_mangle]
pub extern "C" fn layer_update_pixels(ctx: *mut c_void, buf: *const c_uchar, w: c_int, h: c_int) {
    let handle = ctx as usize as u64;
    guarded(
        "layer_update_pixels",
        |b| {
            maybe_fault();
            // force 0 = auto (§6.2): the plain entry is the C facade's
            // no-format path.
            b.update_pixels(handle, buf, w as i64, h as i64, 0)
        },
        |_| (),
    )
}

#[no_mangle]
pub extern "C" fn layer_update_pixels_with_format(
    ctx: *mut c_void,
    buf: *const c_uchar,
    w: c_int,
    h: c_int,
    force: c_uint,
) {
    let handle = ctx as usize as u64;
    guarded(
        "layer_update_pixels_with_format",
        |b| {
            maybe_fault();
            b.update_pixels(handle, buf, w as i64, h as i64, force)
        },
        |_| (),
    )
}

#[no_mangle]
pub extern "C" fn layer_clear(ctx: *mut c_void) {
    let handle = ctx as usize as u64;
    guarded(
        "layer_clear",
        |b| {
            maybe_fault();
            b.clear(handle)
        },
        |_| (),
    )
}

#[no_mangle]
pub extern "C" fn layer_set_position(ctx: *mut c_void, x: c_int, y: c_int) {
    let handle = ctx as usize as u64;
    guarded(
        "layer_set_position",
        |b| {
            maybe_fault();
            b.set_position(handle, x, y)
        },
        |_| (),
    )
}

#[no_mangle]
pub extern "C" fn layer_set_size(ctx: *mut c_void, w: c_int, h: c_int) {
    let handle = ctx as usize as u64;
    guarded(
        "layer_set_size",
        |b| {
            maybe_fault();
            b.set_size(handle, w as i64, h as i64)
        },
        |_| (),
    )
}

#[no_mangle]
pub extern "C" fn layer_destroy_context(ctx: *mut c_void) {
    let handle = ctx as usize as u64;
    guarded(
        "layer_destroy_context",
        |b| {
            maybe_fault();
            b.destroy_context(handle)
        },
        |_| (),
    )
}

/// §7.1 #11 — never NULL, address never changes, never fails. Even its
/// panic path must honour that, hence `on_panic` returns the same pointer.
#[no_mangle]
pub extern "C" fn layer_last_error() -> *const c_char {
    guarded(
        "layer_last_error",
        |_| {
            maybe_fault();
            last_error_ptr()
        },
        |_| last_error_ptr(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::handles::Handle;
    use crate::state::{lock_bridge, sticky_text, Bridge, Phase, CONNECT_REJECT};
    use crate::wayland::ConnectError;
    use std::sync::atomic::Ordering;

    fn lock() -> std::sync::MutexGuard<'static, ()> {
        TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn fault(on: bool) {
        FORCE_PANIC.store(on, Ordering::SeqCst);
    }

    /// Bring the process-global bridge to a clean, fake-Ready state and
    /// hand back a live 400x400 handle. Assumes `TEST_LOCK` held.
    fn fresh_ready_handle() -> *mut c_void {
        layer_shell_cleanup();
        let mut b = lock_bridge();
        assert_eq!(b.init_fake(), 0);
        let h = b
            .create_context(ptr::null_mut(), 400, 400, 0, 0)
            .expect("create on fake-ready");
        h as *mut c_void
    }

    /// The `CONNECT_REJECT` injection (state.rs) pins the export's §7.1 #1
    /// failure codes on ANY machine — including this dev box, which has a
    /// live niri session and would otherwise really connect and really
    /// spawn a pump thread from inside an L2 test.
    #[test]
    fn init_export_maps_each_connect_failure_shape_deterministically() {
        let _g = lock();
        for (inject, want) in [(-1i32, -1i32), (-2, -2), (-3, -3)] {
            CONNECT_REJECT.store(inject, Ordering::SeqCst);
            layer_shell_cleanup();
            assert_eq!(layer_shell_init(), want, "CONNECT_REJECT {inject}");
            assert!(matches!(lock_bridge().phase, Phase::Uninitialized));
            assert!(
                !sticky_text().is_empty(),
                "init failure must leave a reason"
            );
        }
        CONNECT_REJECT.store(-1, Ordering::SeqCst);
    }

    #[test]
    fn bridge_level_init_codes_are_deterministic() {
        let _g = lock();
        let mut b = Bridge::new();
        assert_eq!(b.init_impl(Err(ConnectError::NoDisplay("x".into()))), -1);
        assert_eq!(b.init_impl(Err(ConnectError::MissingGlobal("wl_shm"))), -2);
        assert_eq!(b.init_impl(Err(ConnectError::Internal("x".into()))), -3);
        assert_eq!(b.phase, Phase::Uninitialized);
        assert_eq!(b.init_fake(), 0);
        assert_eq!(b.phase, Phase::Ready);
        assert!(sticky_text().is_empty(), "a healthy epoch starts silent");
        let epoch = b.epoch;
        assert_eq!(b.init(), 0, "idempotent init on Ready must return 0");
        assert_eq!(b.epoch, epoch, "idempotent init must not start a new epoch");
    }

    #[test]
    fn poisoned_init_tears_down_residuals() {
        let _g = lock();
        let mut b = Bridge::new();
        assert_eq!(b.init_fake(), 0);
        let h = b.create_context(ptr::null_mut(), 64, 64, 0, 0).unwrap();
        b.set_click_through(h, 1); // touch the entry so teardown has something to drop
        b.phase = Phase::Poisoned;
        let epoch_before_cleanup = b.epoch;
        // init() from Poisoned: teardown then rebuild; WP-C rebuild
        // cannot succeed, but the teardown half must have run.
        let rc = b.init();
        assert!((-3..=-1).contains(&rc));
        assert_eq!(
            b.epoch,
            epoch_before_cleanup + 1,
            "teardown must bump epoch"
        );
        assert!(b.handles.is_empty(), "residual contexts must be destroyed");
        assert_eq!(b.phase, Phase::Uninitialized);
        // The stale handle now fails everywhere, with no crash.
        let buf = [0u8; 4];
        b.update_pixels(h, buf.as_ptr(), 64, 64, 0);
        b.destroy_context(h);
    }

    #[test]
    fn panic_injection_poisons_and_every_export_returns_its_failure_shape() {
        let _g = lock();
        let ctx = fresh_ready_handle();
        let buf = [0u8; 4];
        type FaultCase = (&'static str, Box<dyn Fn()>);
        let exports: Vec<FaultCase> = vec![
            (
                "layer_shell_init",
                Box::new(|| {
                    let _ = layer_shell_init();
                }),
            ),
            (
                "layer_set_click_through",
                Box::new(move || layer_set_click_through(ctx, 1)),
            ),
            (
                "layer_update_pixels",
                Box::new(move || layer_update_pixels(ctx, buf.as_ptr(), 400, 400)),
            ),
            (
                "layer_update_pixels_with_format",
                Box::new(move || layer_update_pixels_with_format(ctx, buf.as_ptr(), 400, 400, 0)),
            ),
            ("layer_clear", Box::new(move || layer_clear(ctx))),
            (
                "layer_set_position",
                Box::new(move || layer_set_position(ctx, 1, -1)),
            ),
            (
                "layer_set_size",
                Box::new(move || layer_set_size(ctx, 100, 100)),
            ),
            (
                "layer_last_error",
                Box::new(|| {
                    assert!(!layer_last_error().is_null());
                }),
            ),
            (
                "layer_destroy_context",
                Box::new(move || layer_destroy_context(ctx)),
            ),
            (
                "layer_create_context",
                Box::new(|| {
                    assert!(layer_create_context(ptr::null_mut(), 10, 10, 0, 0).is_null());
                }),
            ),
            ("layer_shell_cleanup", Box::new(|| layer_shell_cleanup())),
        ];
        for (name, call) in exports {
            fault(true);
            call(); // must not abort the test process
            fault(false);
            let s = sticky_text();
            assert!(
                s.starts_with(&format!("panic in {name}")),
                "sticky for {name}: {s:?}"
            );
            assert!(
                matches!(lock_bridge().phase, Phase::Poisoned),
                "panic in {name} must flip the bridge to Poisoned"
            );
            // Recovery per §4.3: cleanup clears poison; the table lost all
            // ctx, so a fresh handle is needed for the next iteration.
            layer_shell_cleanup();
            assert!(matches!(lock_bridge().phase, Phase::Uninitialized));
            // Re-arming with a fresh Ready bridge also proves cleanup
            // left the state machine usable.
            let _ = lock_bridge().init_fake();
        }
    }

    #[test]
    fn poisoned_bridge_rejects_ctx_ops_until_cleanup() {
        let _g = lock();
        let ctx = fresh_ready_handle();
        fault(true);
        layer_set_position(ctx, 5, 5); // panics inside f → poison
        fault(false);
        // Now poisoned and fault-free: every ctx op takes the rejection
        // chain (poison-first) rather than touching the entry.
        let before = {
            let b = lock_bridge();
            (b.counters.dropped_unconfigured, b.counters.dropped_mismatch)
        };
        layer_update_pixels(ctx, [0u8; 4].as_ptr(), 1, 1);
        layer_clear(ctx);
        layer_set_size(ctx, 1, 1);
        layer_destroy_context(ctx);
        assert!(layer_create_context(ptr::null_mut(), 10, 10, 0, 0).is_null());
        let s = sticky_text();
        assert!(
            s.contains("poisoned"),
            "expected poison rejection, got {s:?}"
        );
        let after = {
            let b = lock_bridge();
            (b.counters.dropped_unconfigured, b.counters.dropped_mismatch)
        };
        assert_eq!(before, after, "poison rejections must not move counters");
        layer_shell_cleanup();
        assert_eq!(lock_bridge().init_fake(), 0);
        assert!(!layer_create_context(ptr::null_mut(), 10, 10, 0, 0).is_null());
    }

    #[test]
    fn forged_zero_and_stale_handles_are_defined_rejections() {
        let _g = lock();
        let ctx = fresh_ready_handle();
        let live = ctx as usize as u64;
        let forged_gen = Handle::pack(Handle(live).id(), Handle(live).generation() ^ 0xFFFF_FFFF).0;
        let buf = [0u8; 4];
        for bad in [
            ptr::null_mut(),
            0xDEAD_BEEF_usize as *mut c_void,
            forged_gen as *mut c_void,
        ] {
            layer_set_click_through(bad, 1);
            layer_update_pixels(bad, buf.as_ptr(), 400, 400);
            layer_clear(bad);
            layer_set_position(bad, 0, 0);
            layer_set_size(bad, 400, 400);
            layer_destroy_context(bad);
            let s = sticky_text();
            assert!(s.contains("handle"), "last sticky: {s:?}");
        }
        // The live entry survived every forged touch:
        assert_eq!(lock_bridge().handles.len(), 1);
        assert!(lock_bridge().live_entry(live).is_some());
        // destroy → same handle again = double-destroy rejection + sticky
        layer_destroy_context(ctx);
        assert!(lock_bridge().handles.is_empty());
        layer_destroy_context(ctx);
        assert!(sticky_text().contains("stale"));
    }

    #[test]
    fn cleanup_epoch_bump_kills_residual_handles_with_reused_id() {
        let _g = lock();
        let ctx = fresh_ready_handle();
        let value = ctx as usize as u64;
        layer_shell_cleanup();
        // Re-init and re-create: with the real seq the id will not repeat,
        // but the epoch guard covers the wrap case. Forge the situation:
        let mut b = lock_bridge();
        assert_eq!(b.init_fake(), 0);
        let fresh = b.create_context(ptr::null_mut(), 400, 400, 0, 0).unwrap();
        // Hand-forge an entry that pretends to carry the OLD epoch —
        // only reachable via a 2^64 seq wrap; emulate it directly:
        let stale_epoch = b.epoch - 1;
        b.handles.set_created_by_for_test(fresh, stale_epoch);
        assert!(
            b.live_entry(value).is_none(),
            "cleanup killed the residual handle"
        );
        assert!(
            b.live_entry(fresh).is_none(),
            "old-epoch entry must not resolve"
        );
    }

    #[test]
    fn create_context_rejects_nonnull_state_and_out_of_bounds_sizes() {
        let _g = lock();
        let _ctx = fresh_ready_handle();
        let sentinel = std::ptr::dangling_mut::<c_void>();
        assert!(layer_create_context(sentinel, 400, 400, 0, 0).is_null());
        assert!(sticky_text().contains("state must be NULL"));
        // (w,h) edges: 0 / negative / edge > 8192 / area > 2^24 → NULL.
        for (w, h) in [
            (0, 10),
            (-1, -1),
            (8193, 1),
            (1, 8193),
            (8192, 2049),
            (i32::MIN, 1),
        ] {
            assert!(
                layer_create_context(ptr::null_mut(), w, h, 0, 0).is_null(),
                "{w}x{h} accepted"
            );
        }
        // Boundary that must PASS: area == 16_777_216 exactly (§7.1 #3).
        assert!(
            !layer_create_context(ptr::null_mut(), 8192, 2048, 0, 0).is_null(),
            "area == 16_777_216 must be accepted"
        );
    }

    /// The three counted-drop counters (§6.3), read as the baseline of a
    /// **delta**. `Counters` are process-lifetime (state.rs's scope note,
    /// spec §6.5 "单调增长"), and every test here shares one global bridge,
    /// so an absolute expectation would just be an unwritten statement about
    /// test order (agents-rules §5).
    fn drop_counters() -> (u64, u64, u64) {
        let b = lock_bridge();
        (
            b.counters.dropped_busy,
            b.counters.dropped_unconfigured,
            b.counters.dropped_mismatch,
        )
    }

    #[test]
    fn update_pixels_validation_chain() {
        let _g = lock();
        let ctx = fresh_ready_handle();
        let value = ctx as usize as u64;
        let base = drop_counters();
        // Unconfigured gate: counted drop, sticky must NOT be written,
        // counter moves by exactly 1 per call (§6.3).
        layer_update_pixels(ctx, [0u8; 4].as_ptr(), 400, 400);
        layer_update_pixels(ctx, [0u8; 4].as_ptr(), 400, 400);
        assert_eq!(
            drop_counters(),
            (base.0, base.1 + 2, base.2),
            "two unconfigured frames = exactly +2 on that counter alone"
        );
        assert_eq!(
            sticky_text(),
            "",
            "a counted drop is not an error (§7.2: C's silent `!configured` return)"
        );
        // Fake a configure landing (WP-D does this from the event):
        {
            let mut b = lock_bridge();
            let e = b.live_entry_mut(value).unwrap();
            e.ctx.configured = true;
        }
        // Size mismatch: counted + sticky names BOTH sizes (§6.3).
        layer_update_pixels(ctx, [0u8; 4].as_ptr(), 401, 400);
        assert_eq!(
            drop_counters(),
            (base.0, base.1 + 2, base.2 + 1),
            "the mismatch frame moves only `dropped_mismatch`"
        );
        let s = sticky_text();
        assert!(
            s.contains("got 401x400") && s.contains("logical 400x400"),
            "{s:?}"
        );
        // NULL buffer with matching size: rejection (no deref happens).
        layer_update_pixels(ctx, ptr::null(), 400, 400);
        assert!(sticky_text().contains("NULL pixel buffer"));
        // Valid frame + bad force: frame dropped, sticky names the value
        // (§6.2 closed domain {0, ABGR8888}; ARGB8888 == 0 == auto).
        layer_update_pixels_with_format(ctx, [0u8; 4].as_ptr(), 400, 400, 1);
        assert!(sticky_text().contains("force format 0x00000001"));
        layer_update_pixels_with_format(ctx, [0u8; 4].as_ptr(), 400, 400, 0x4242_4142);
        assert!(sticky_text().contains("force format 0x42424142"));
        // Valid: 0 (auto) and ABGR8888 pass validation silently (submit is
        // the WP-E seam; no counter may move on any of these calls).
        layer_update_pixels_with_format(
            ctx,
            [0u8; 4].as_ptr(),
            400,
            400,
            crate::format::WL_SHM_FORMAT_ABGR8888,
        );
        layer_update_pixels(ctx, [0u8; 4].as_ptr(), 400, 400);
        assert_eq!(
            drop_counters(),
            (base.0, base.1 + 2, base.2 + 1),
            "rejections that are not counted drops must leave every counter still"
        );
    }

    #[test]
    fn set_size_updates_request_only_until_configure() {
        let _g = lock();
        let ctx = fresh_ready_handle();
        let value = ctx as usize as u64;
        layer_set_size(ctx, 512, 256);
        {
            let b = lock_bridge();
            let e = b.live_entry(value).unwrap();
            assert_eq!((e.ctx.req_w, e.ctx.req_h), (512, 256));
            assert_eq!((e.ctx.logical_w, e.ctx.logical_h), (400, 400));
        }
        layer_set_size(ctx, 0, 400); // out of bounds → rejection, request unchanged
        assert!(sticky_text().contains("layer_set_size"));
        {
            let b = lock_bridge();
            let e = b.live_entry(value).unwrap();
            assert_eq!((e.ctx.req_w, e.ctx.req_h), (512, 256));
        }
    }

    #[test]
    fn contexts_start_click_through_per_spec_4_6() {
        let _g = lock();
        let ctx = fresh_ready_handle();
        let value = ctx as usize as u64;
        assert!(lock_bridge().live_entry(value).unwrap().ctx.click_through);
        layer_set_click_through(ctx, 0);
        assert!(!lock_bridge().live_entry(value).unwrap().ctx.click_through);
        layer_set_click_through(ctx, -5); // any non-zero = through
        assert!(lock_bridge().live_entry(value).unwrap().ctx.click_through);
    }

    #[test]
    fn last_error_is_never_null_and_its_address_never_moves() {
        let _g = lock();
        let p1 = layer_last_error();
        assert!(!p1.is_null());
        // Trigger an error, then re-read: same address (fixed buffer).
        layer_destroy_context(ptr::null_mut());
        let p2 = layer_last_error();
        assert_eq!(p1, p2);
        let len = unsafe { libc_strlen(p2) };
        assert!(len < 256);
    }

    #[allow(non_snake_case)]
    unsafe fn libc_strlen(s: *const c_char) -> usize {
        let mut n = 0usize;
        while *s.add(n) != 0 {
            n += 1;
        }
        n
    }
}
