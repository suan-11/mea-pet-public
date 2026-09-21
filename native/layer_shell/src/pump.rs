//! The pump thread (spec §4.2/§6.4) and its bounded shutdown (§4.3).
//!
//! Design invariants this file encodes:
//! - **Only this thread reads the Wayland socket** (I3). Every caller
//!   thread's critical section is `lock → send requests → flush → unlock`;
//!   the socket's read half belongs to [`run_loop`] exclusively, which is
//!   the whole reason a self-built pump replaces the C code's dependence
//!   on Qt's event loop (spec §4.7 row 1).
//! - **The exit mechanism is a self-pipe, not a version-sensitive API**
//!   (spec §0.1 residual): [`PumpHandle::quit_and_join`] writes one byte
//!   to a `pipe2(O_CLOEXEC)` end that [`run_loop`] polls alongside the
//!   Wayland fd. No `wl_display` round-trip, no `prepare_read` racing a
//!   quit — the two are just two poll fds.
//! - **Shutdown is bounded (§4.3).** std offers no `join_with_timeout`,
//!   so the wait is a 2 ms-poll on an `AtomicBool` the thread sets right
//!   before returning; the cap is 1 s. On a timeout we DETACH (drop the
//!   `JoinHandle`): the thread is already past its `poll`, will observe
//!   the socket teardown (we drop the connection reference), and exit on
//!   its own — the pump never lingers holding resources. There is
//!   deliberately no janitor thread, so a full init/cleanup cycle
//!   returns the process to its exact prior thread count (T8).
//!
//! Failure modes (§1 discipline):
//! - If `poll` is interrupted (`EINTR`) we cancel the prepared read and
//!   re-arm — dropping the `ReadEventsGuard` decrements `prepared_reads`
//!   in the backend, so a leaked guard could make the NEXT `read()` block
//!   forever waiting on a phantom second reader; dropping on every
//!   non-reading exit path is therefore load-bearing, not cleanup sugar.
//! - A dispatch/read error means the connection died or the compositor
//!   faulted our wire: we poison (§4.3) via `pump_poison`, which is
//!   epoch-guarded so a pump from a dead epoch cannot clobber a bridge a
//!   later `init` already rebuilt.

use std::os::fd::{AsRawFd, OwnedFd};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use wayland_client::backend::WaylandError;
use wayland_client::{Connection, DispatchError, EventQueue};

use crate::state::guarded;
use crate::wayland::WaylandData;

/// `poll` timeout for the pump loop. Bounded so a quit signal is noticed
/// within milliseconds even if the wake-up byte races the poll entry; the
/// value (`200 ms`) is far under the 1 s join cap, so quit latency is
/// never the reason a join times out.
const PUMP_POLL_MS: i32 = 200;
/// §4.3's cap on cleanup's wait for the pump to retire.
const JOIN_TIMEOUT: Duration = Duration::from_millis(1_000);
/// Granularity of the done-flag poll. 2 ms keeps worst-case overshoot of
/// `JOIN_TIMEOUT` negligible while costing ~500 wakeups across a full
/// timeout window — no busy-spin.
const JOIN_POLL: Duration = Duration::from_millis(2);

/// Handle the bridge keeps to retire its pump. Owned entirely by the
/// `Bridge` (via `LiveCore`); moved out by `teardown_take` under the
/// global lock and consumed OUTSIDE the lock by [`quit_and_join`]
/// (F-M3: joining while holding `BRIDGE` would deadlock against the
/// pump's own per-event `guarded` lock acquisition).
pub(crate) struct PumpHandle {
    quit_write: Option<OwnedFd>,
    done: Arc<AtomicBool>,
    join: Option<JoinHandle<()>>,
    /// Keeps the socket alive through wind-down even if the bridge has
    /// already dropped its own `Core` (spec §4.3: the pump exits on its
    /// own once every connection reference is released).
    conn: Option<Connection>,
}

impl PumpHandle {
    /// Construct from the pieces `LiveCore::spawn_pump` produced.
    pub(crate) fn new(
        quit_write: OwnedFd,
        done: Arc<AtomicBool>,
        join: JoinHandle<()>,
        conn: Connection,
    ) -> Self {
        PumpHandle {
            quit_write: Some(quit_write),
            done,
            join: Some(join),
            conn: Some(conn),
        }
    }

    /// Signal quit, release our connection reference, and wait a bounded
    /// time for the thread to retire. Returns `Some(reason)` only on the
    /// 1 s timeout (which the caller turns into a sticky diagnostic; the
    /// detached thread still self-terminates). `None` = clean join.
    pub(crate) fn quit_and_join(mut self) -> Option<String> {
        // 1. Wake `run_loop`'s poll. Best-effort: EPIPE (reader gone) or
        //    a full pipe both mean the pump will exit without this byte.
        if let Some(fd) = &self.quit_write {
            // SAFETY: writing one byte from a valid stack buffer to our
            // own pipe's write end; the fd is live until `self` drops.
            let byte = [1u8];
            unsafe {
                libc::write(fd.as_raw_fd(), byte.as_ptr() as *const libc::c_void, 1);
            }
        }
        // The write end no longer needs to outlive the wake signal; close
        // it so a poll on the read end also sees EOF (POLLHUP) as a
        // second, independent wake path.
        self.quit_write = None;
        // 2. Drop our connection reference. If the quit byte somehow
        //    raced the poll entry, socket teardown (POLLHUP on the Wayland
        //    fd) still ends the loop.
        self.conn = None;
        // 3. Bounded wait on the done flag (std has no join_with_timeout).
        let start = Instant::now();
        while !self.done.load(Ordering::Acquire) && start.elapsed() < JOIN_TIMEOUT {
            std::thread::sleep(JOIN_POLL);
        }
        if self.done.load(Ordering::Acquire) {
            if let Some(join) = self.join.take() {
                // The thread already returned, so this cannot block. A
                // panicking pump would already have poisoned via the
                // handler's `guarded`; we surface nothing new here.
                let _ = join.join();
            }
            None
        } else {
            // Timeout: DETACH. `join.take()` leaves the JoinHandle to
            // drop, which detaches; the thread finishes its current
            // iteration and exits. It cannot poison a live bridge because
            // `pump_poison` is epoch-guarded and teardown already bumped
            // the epoch before this call.
            let _ = self.join.take();
            Some("cleanup: pump-join-timeout, pump detached (§4.3)".to_string())
        }
    }
}

/// The poll outcome for one loop iteration. Factored out so the fd
/// bookkeeping (quit vs Wayland vs neither) is unit-testable without a
/// live connection — the `Dispatch` state machine and this classifier
/// are the two halves the acceptance `cargo test … pump::` covers
/// offline; the full loop runs only under the `#[ignore]` live smoke.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Wake {
    /// Quit pipe became readable (or its write end closed) — exit.
    Quit,
    /// Wayland socket ready — read + dispatch.
    Wayland,
    /// Timeout with no readiness — cancel the guard and re-arm.
    Idle,
    /// `poll` failed in a way that must tear the bridge down.
    Failed,
}

/// Decide the loop action from the two `revents`. A non-zero revents on
/// EITHER fd is treated as actionable: quit wins over Wayland (a
/// simultaneous teardown + event must still exit), and `POLLERR`/`POLLHUP`
/// on the Wayland fd count as readable so the following `read()` surfaces
/// the disconnect error rather than the loop spinning on a dead fd.
fn classify(poll_rc: i32, quit_revents: i16, wl_revents: i16) -> Wake {
    if poll_rc < 0 {
        // EINTR is handled by the caller BEFORE this (retry, not failure);
        // any other negative is a real poll error.
        Wake::Failed
    } else if quit_revents != 0 {
        Wake::Quit
    } else if wl_revents != 0 {
        Wake::Wayland
    } else {
        Wake::Idle
    }
}

/// The pump thread body. Consumes the queue, data and quit-pipe read end
/// moved in by `spawn_pump`; returns only on quit, disconnect, or error.
pub(crate) fn run_loop(
    mut queue: EventQueue<WaylandData>,
    mut data: WaylandData,
    quit_read: OwnedFd,
) {
    let quit_fd = quit_read.as_raw_fd();
    loop {
        // §6.4: the guard MUST be created before polling. It reserves the
        // read; `drop`-ing it without `read()` cancels that reservation.
        let guard = match queue.prepare_read() {
            Some(g) => g,
            // None means a read is already prepared elsewhere. There is no
            // elsewhere — the pump is the sole reader — so this arm only
            // fires if the backend ever gains a second consumer; drain what
            // is already queued and re-prepare (the documented contract).
            None => {
                if drain_pending(&mut queue, &mut data) {
                    return;
                }
                continue;
            }
        };
        let wl_fd = guard.connection_fd().as_raw_fd();
        let mut pfds = [
            libc::pollfd {
                fd: quit_fd,
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: wl_fd,
                events: libc::POLLIN,
                revents: 0,
            },
        ];
        // SAFETY: `pfds` is a live, correctly-sized array for the call.
        let rc = unsafe { libc::poll(pfds.as_mut_ptr(), pfds.len() as libc::nfds_t, PUMP_POLL_MS) };
        if rc < 0 {
            let err = std::io::Error::last_os_error();
            if err.raw_os_error() == Some(libc::EINTR) {
                // Cancel the prepared read (drops the guard) and retry —
                // NOT a failure, so no poison.
                drop(guard);
                continue;
            }
            drop(guard);
            pump_poison(&data, "pump: poll failed");
            return;
        }
        match classify(rc, pfds[0].revents, pfds[1].revents) {
            Wake::Quit => return,
            Wake::Idle => drop(guard), // timeout: cancel + re-arm, no events
            Wake::Failed => {
                drop(guard);
                pump_poison(&data, "pump: poll error");
                return;
            }
            Wake::Wayland => {
                // `read(self)` consumes the guard and performs the socket
                // read + callback dispatch (rs backend: see
                // `dispatch_events`). A WouldBlock is a spurious readiness
                // (POLLHUP raced with an empty queue); treat as idle.
                match guard.read() {
                    Ok(_) => {}
                    Err(WaylandError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                    Err(_) => {
                        pump_poison(&data, "pump: read/dispatch error (connection lost?)");
                        return;
                    }
                }
                // Drain any callbacks the read queued.
                if drain_pending(&mut queue, &mut data) {
                    return;
                }
            }
        }
    }
}

/// Dispatch everything already sitting in the queue. Returns `true` when
/// the pump must exit (a dispatch error). `Ok(0)` = queue empty, keep
/// running.
fn drain_pending(queue: &mut EventQueue<WaylandData>, data: &mut WaylandData) -> bool {
    loop {
        match queue.dispatch_pending(data) {
            Ok(0) => return false,
            Ok(_) => continue,
            Err(DispatchError::BadMessage { .. }) => {
                pump_poison(data, "pump: malformed event from compositor");
                return true;
            }
            Err(DispatchError::Backend(WaylandError::Io(_))) => {
                // Normal-unclean: the compositor closed the socket. Poison
                // so callers get a defined rejection instead of a hang.
                pump_poison(data, "pump: Wayland connection closed by compositor");
                return true;
            }
            Err(DispatchError::Backend(WaylandError::Protocol(_))) => {
                pump_poison(data, "pump: protocol error from compositor");
                return true;
            }
        }
    }
}

/// Flip the bridge to `Poisoned` from the pump thread, guarded against a
/// stale pump clobbering a rebuilt bridge (the epoch stamp is captured at
/// spawn time and carried in `WaylandData`). Routes through `guarded` so
/// this stays I4's single `catch_unwind` site even on the pump's own exit
/// paths.
fn pump_poison(data: &WaylandData, msg: &str) {
    let epoch = data.epoch;
    let owned = msg.to_string();
    guarded("pump:poison", move |b| b.pump_poison(epoch, &owned), |_| ());
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ffi::TEST_LOCK;
    use crate::state::{lock_bridge, sticky_text, Bridge, Phase};
    use std::ptr;

    fn lock() -> std::sync::MutexGuard<'static, ()> {
        TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Bring the global bridge to a clean fake-Ready state and hand back
    /// `(epoch, handle)` for an unconfigured 400x400 context. Assumes
    /// `TEST_LOCK`.
    fn fresh_ready() -> (u32, u64) {
        crate::ffi::layer_shell_cleanup();
        let mut b = lock_bridge();
        assert_eq!(b.init_fake(), 0);
        let h = b
            .create_context(ptr::null_mut(), 400, 400, 0, 0)
            .expect("create on fake-ready");
        (b.epoch, h)
    }

    #[test]
    fn wake_classification_covers_the_four_poll_shapes() {
        assert_eq!(classify(1, 1, 0), Wake::Quit);
        assert_eq!(classify(1, 0, 1), Wake::Wayland);
        assert_eq!(classify(0, 0, 0), Wake::Idle);
        assert_eq!(classify(-1, 0, 0), Wake::Failed);
        // Quit wins when both fds light up in the same iteration: a
        // teardown concurrent with an event must still exit.
        assert_eq!(classify(2, 1, 1), Wake::Quit);
    }

    #[test]
    fn configure_zero_keeps_the_requested_size() {
        let _g = lock();
        let (epoch, h) = fresh_ready();
        // §6.3: a configure dimension of 0 means "the compositor accepts
        // our request", so the logical size follows the REQUEST, not 0.
        {
            let mut b = lock_bridge();
            b.apply_configure(epoch, h, 0, 0);
        }
        let b = lock_bridge();
        let e = b.live_entry(h).unwrap();
        assert!(e.ctx.configured);
        assert_eq!((e.ctx.logical_w, e.ctx.logical_h), (400, 400));
    }

    #[test]
    fn configure_nonzero_sets_logical_size() {
        let _g = lock();
        let (epoch, h) = fresh_ready();
        {
            let mut b = lock_bridge();
            b.apply_configure(epoch, h, 512, 256);
        }
        let b = lock_bridge();
        let e = b.live_entry(h).unwrap();
        assert_eq!((e.ctx.logical_w, e.ctx.logical_h), (512, 256));
        assert!(e.ctx.configured);
    }

    /// §7.1 #3 gates the COMPOSITOR's proposal, not just the caller's, because
    /// `layer_update_pixels` derives its read length from the logical size.
    /// Failure mode without that gate: `lw*lh*4` on an `i32::MAX`² proposal
    /// wraps (release) or panics (debug), and a wrapped length fed to
    /// `from_raw_parts` is an out-of-bounds read of the caller's memory — the
    /// §4.8-5 defect class approached from the other side. Here every OTHER
    /// check passes: the frame is configured, the claimed (w,h) equals the
    /// logical size, the pointer is non-NULL; only the bounds gate stands
    /// between that and the slice, which is why the 4-byte buffer below is
    /// deliberately too small to be legal.
    #[test]
    fn configure_beyond_the_size_bounds_cannot_become_a_byte_count() {
        let _g = lock();
        let (epoch, h) = fresh_ready();
        {
            let mut b = lock_bridge();
            b.apply_configure(epoch, h, i32::MAX as u32, i32::MAX as u32);
        }
        assert_eq!(
            lock_bridge().live_entry(h).unwrap().ctx.logical_w,
            i32::MAX as u32,
            "configure itself must not clamp — §6.3 keeps what was proposed"
        );
        // Delta, not absolute: `Counters` is per-`Bridge` and survives
        // cleanup→init, so other tests' totals are still in the table.
        let before = {
            let b = lock_bridge();
            (
                b.counters.dropped_busy,
                b.counters.dropped_mismatch,
                b.counters.dropped_unconfigured,
            )
        };
        crate::ffi::layer_update_pixels(h as *mut _, [0u8; 4].as_ptr(), i32::MAX, i32::MAX);
        let s = sticky_text();
        assert!(
            s.contains("8192") && s.contains("layer_update_pixels"),
            "expected the §7.1 #3 refusal, got {s:?}"
        );
        let after = {
            let b = lock_bridge();
            (
                b.counters.dropped_busy,
                b.counters.dropped_mismatch,
                b.counters.dropped_unconfigured,
            )
        };
        assert_eq!(
            before, after,
            "an out-of-bounds logical size is its own refusal, not a mislabelled drop"
        );
    }

    #[test]
    fn stale_epoch_configure_event_is_dropped() {
        let _g = lock();
        let (epoch, h) = fresh_ready();
        {
            let mut b = lock_bridge();
            // An event carrying a future/foreign epoch (a pump from
            // another bridge generation) must not touch live state.
            b.apply_configure(epoch.wrapping_add(1), h, 999, 999);
        }
        let b = lock_bridge();
        let e = b.live_entry(h).unwrap();
        assert!(!e.ctx.configured, "stale configure must be ignored");
        // `new()` stamps logical = the create size; a dropped configure
        // leaves BOTH the gate and that size exactly as they were.
        assert_eq!((e.ctx.logical_w, e.ctx.logical_h), (400, 400));
    }

    #[test]
    fn closed_surface_rejects_further_ops() {
        let _g = lock();
        let (epoch, h) = fresh_ready();
        {
            let mut b = lock_bridge();
            // Configure so the ctx is otherwise usable, then the
            // compositor retracts it.
            b.apply_configure(epoch, h, 400, 400);
            b.apply_closed(epoch, h);
        }
        // §4.7-12: after `closed` the entry is dead, so an update takes
        // the rejection chain with a readable reason, never a silent
        // no-op that Python can't diagnose.
        let buf = [0u8; 4];
        crate::ffi::layer_update_pixels(h as *mut _, buf.as_ptr(), 400, 400);
        let s = sticky_text();
        assert!(s.contains("closed"), "expected closed rejection, got {s:?}");
        // The handle is still in the table (dead ≠ removed): destroy is
        // still legal and must succeed cleanly.
        crate::ffi::layer_destroy_context(h as *mut _);
        assert!(lock_bridge().handles.is_empty());
    }

    #[test]
    fn buffer_release_counts_and_ignores_stale_epoch() {
        let _g = lock();
        let (epoch, _h) = fresh_ready();
        {
            let mut b = lock_bridge();
            b.apply_buffer_release(epoch);
            assert_eq!(b.counters.releases, 1);
            // A release stamped with the wrong epoch is a dead pump's
            // echo; diagnostics must not count it (I6: monotonic truth).
            b.apply_buffer_release(epoch.wrapping_add(7));
            assert_eq!(b.counters.releases, 1);
        }
    }

    #[test]
    fn pump_poison_respects_epoch_boundary() {
        let _g = lock();
        let (epoch, _h) = fresh_ready();
        // Stale pump: wrong epoch → no effect.
        {
            let mut b = lock_bridge();
            b.pump_poison(epoch.wrapping_add(1), "stale");
            assert!(
                matches!(b.phase, Phase::Ready),
                "stale pump must not poison"
            );
        }
        // Matching epoch → poisoned + sticky reason.
        {
            let mut b = lock_bridge();
            b.pump_poison(epoch, "pump: died");
            assert!(matches!(b.phase, Phase::Poisoned));
        }
        assert!(sticky_text().contains("pump: died"));
        // Recovery: cleanup clears the poison, exactly as a call-thread
        // panic does (§4.3 — pump poison is not a special state).
        crate::ffi::layer_shell_cleanup();
        assert!(matches!(lock_bridge().phase, Phase::Uninitialized));
        let mut b = Bridge::new();
        assert_eq!(b.init_fake(), 0);
    }

    /// §0.1's first residual: does the poll-path pump actually reach
    /// `configured` on this compositor, and does cleanup retire it inside
    /// 1 s? This is the L3 smoke that resolves the "exact API form"
    /// uncertainty; it is `#[ignore]`d because CI/offline runs have no
    /// Wayland session (it would fail at connect). Run by hand on niri:
    /// `cargo test --manifest-path native/layer_shell/Cargo.toml --release \
    ///  live_init_configure_cleanup -- --ignored --nocapture`
    /// The filter is the bare name, NOT `pump::live_init_configure_cleanup`:
    /// cargo's filter is a substring of the full path `pump::tests::…`, so the
    /// qualified form matches zero tests and the harness then prints `test
    /// result: ok … 1 ignored` for a run that executed nothing — a command that
    /// cannot fail (agents-rules §8). Check the `running 1 test` line, not the `ok`.
    #[test]
    #[ignore = "requires a live Wayland compositor (L3; run manually)"]
    fn live_init_configure_cleanup() {
        let _g = lock();
        // Opt out of the test-default CONNECT_REJECT (state.rs) for the
        // real connect, and RESTORE it even if an assert below fails: a
        // leaked 0 would make every later L2 test in this process open a
        // real connection — the exact surprise the injection exists to
        // prevent.
        crate::state::CONNECT_REJECT.store(0, std::sync::atomic::Ordering::SeqCst);
        struct Restore;
        impl Drop for Restore {
            fn drop(&mut self) {
                crate::state::CONNECT_REJECT.store(-1, std::sync::atomic::Ordering::SeqCst);
            }
        }
        let _restore = Restore;
        crate::ffi::layer_shell_cleanup();
        let rc = crate::ffi::layer_shell_init();
        assert_eq!(
            rc,
            0,
            "init on a live compositor, sticky: {:?}",
            sticky_text()
        );
        let ctx = crate::ffi::layer_create_context(ptr::null_mut(), 400, 400, 0, 0);
        assert!(
            !ctx.is_null(),
            "create_context failed, sticky: {:?}",
            sticky_text()
        );
        let value = ctx as usize as u64;
        // The pump must deliver a configure within 3 s.
        let deadline = Instant::now() + Duration::from_secs(3);
        let configured = loop {
            {
                let b = lock_bridge();
                if b.live_entry(value).is_some_and(|e| e.ctx.configured) {
                    break true;
                }
            }
            if Instant::now() > deadline {
                break false;
            }
            std::thread::sleep(Duration::from_millis(5));
        };
        assert!(configured, "no configure from the compositor within 3 s");
        crate::ffi::layer_destroy_context(ctx);
        crate::ffi::layer_shell_cleanup();
        assert!(
            !sticky_text().contains("pump-join-timeout"),
            "cleanup exceeded the 1 s join cap on a healthy session"
        );
    }
}
