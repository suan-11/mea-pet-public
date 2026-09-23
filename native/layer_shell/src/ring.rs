//! The buffer ring (spec §4.5): the crate's only pixel path.
//!
//! Two halves, deliberately split:
//! - [`RingPlan`] — the PURE decision machine (which slot is free, where the
//!   search starts, whether the ring still matches the current geometry and
//!   format). It owns no Wayland object, so the whole §4.5 selection rule is
//!   testable at L1/L2 without a compositor, which is what spec §9's T2 row
//!   ("注入 fake release：正常、永不、乱序、n=10000、尺寸变更窗口期") demands.
//! - [`Ring`] — the resources a decision needs: per slot a `memfd`, an
//!   `mmap`, a `WlShmPool` and a `WlBuffer`. Live-world only; every field is
//!   a handle whose lifetime IS the slot's, so §4.5's release order is
//!   expressed as `Drop` order rather than as a convention someone can forget.
//!
//! Why the split is not duplication: `Ring` delegates every choice to its
//! own `RingPlan` (`take_slot`/`confirm`/`needs_rebuild`), so a test that
//! drives the machine drives the same code the commit path runs.
//!
//! Failure-mode notes (agents-rules §1):
//! - `in_use` is an `AtomicBool` behind an `Arc` because its TWO writers
//!   have different locking: the submit path sets it under the bridge lock,
//!   the `wl_buffer::release` handler clears it through the buffer's own
//!   user-data clone (wayland.rs's `Dispatch<WlBuffer, Arc<AtomicBool>>`),
//!   which runs on the pump thread BEFORE it acquires that lock. A plain
//!   `bool` there would be a data race; a `Mutex` there would invert lock
//!   order against the bridge lock for no benefit.
//! - A slot can only be picked while `!in_use`, and `in_use` is set only
//!   after a successful `flush`. So between `take_slot` and `confirm` no
//!   release event can clear that slot's flag: the compositor can only
//!   release a buffer it received at an earlier commit, and this slot's
//!   buffer has not been committed yet. If that reasoning were ever broken
//!   the symptom would be a frame the compositor never shows (a lost slot
//!   lease), never memory unsafety — the buffer and its mapping outlive the
//!   commit regardless of the flag.
//! - Destroying a slot before its `release` arrives is legal per
//!   `wl_surface.attach` ("destroying before release is allowed as long as
//!   the underlying buffer storage isn't re-used"), and the condition holds
//!   by construction: teardown munmaps + closes the memfd, and a rebuilt
//!   ring mmaps a NEW memfd, so no byte of an unreleased buffer is ever
//!   handed to the compositor again. Reusing the same fd/mapping to skip one
//!   `munmap` would break the only thing that makes the early destroy safe.

use std::ffi::c_void;
use std::fs::File;
use std::os::fd::{AsFd, AsRawFd, FromRawFd};
use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use wayland_client::protocol::wl_buffer::WlBuffer;
use wayland_client::protocol::wl_shm::{Format, WlShm};
use wayland_client::protocol::wl_shm_pool::WlShmPool;
use wayland_client::QueueHandle;

use crate::format::{PixelFormat, BYTES_PER_PIXEL};
use crate::wayland::WaylandData;

/// spec §4.5: the ONE truth for ring depth, and it is READ by the
/// implementation (`SlotFlags::new`, `Ring::build`, the I5 fd bound).
/// Changing this constant changes every one of those at once — which is the
/// point: the value came out of measurement (≤240 fps needs 3; §5.4), not
/// taste, so it must not be restated as a literal anywhere else (agents-rules §7).
pub(crate) const RING_DEPTH: usize = 3;

/// The pure half: geometry/format this ring was built for + the slot flags.
#[derive(Debug)]
pub(crate) struct RingPlan {
    pub geo: (u32, u32),
    pub fmt: PixelFormat,
    flags: SlotFlags,
}

impl RingPlan {
    fn new(geo: (u32, u32), fmt: PixelFormat) -> Self {
        RingPlan {
            geo,
            fmt,
            flags: SlotFlags::new(),
        }
    }

    /// §4.5 build timing: the ring exists for exactly one logical geometry
    /// and one shm format, so a configure that moved either — or a compositor
    /// that started advertising the other format (impossible within an epoch,
    /// but the decision belongs here anyway) — means "rebuild all three".
    fn needs_rebuild(&self, geo: (u32, u32), fmt: PixelFormat) -> bool {
        self.geo != geo || self.fmt != fmt
    }

    fn take_slot(&self) -> Option<usize> {
        self.flags.take_slot()
    }

    /// Caller has committed AND flushed; the slot is now leased to the
    /// compositor until its release lands (§4.5 step order).
    fn confirm(&mut self, idx: usize) {
        self.flags.mark_busy(idx);
    }

    /// Test/simulation twin of the release handler's `store(false)` on the
    /// same `Arc` the `WlBuffer` carries (wayland.rs). Production never
    /// calls this — the handler writes the flag directly, before taking the
    /// bridge lock.
    /// Test-only: production's release path stores through the `Arc` clone
    /// the buffer carries (§4.5), never through here.
    #[cfg(test)]
    fn release(&self, idx: usize) {
        self.flags.release(idx);
    }

    fn flag(&self, idx: usize) -> Arc<AtomicBool> {
        self.flags.flag(idx)
    }

    #[cfg(test)]
    fn busy_count(&self) -> usize {
        self.flags.busy_count()
    }
}

/// Slot occupancy + the §4.5 search cursor, over `RING_DEPTH` shared flags.
#[derive(Debug)]
struct SlotFlags {
    /// One flag per slot, cloned into the slot's `WlBuffer` user data so the
    /// release handler can clear it without the bridge lock (see module
    /// header). Index-stable: flag `i` never changes identity.
    flags: Vec<Arc<AtomicBool>>,
    /// Last hit position; the next search starts at the slot AFTER it
    /// (§4.5 "从上次命中位置的下一个开始环形查找"). Initialised to
    /// `RING_DEPTH - 1` so the very first search starts at slot 0.
    cursor: usize,
}

impl SlotFlags {
    fn new() -> Self {
        SlotFlags {
            flags: (0..RING_DEPTH)
                .map(|_| Arc::new(AtomicBool::new(false)))
                .collect(),
            cursor: RING_DEPTH - 1,
        }
    }

    fn take_slot(&self) -> Option<usize> {
        let depth = self.flags.len();
        for step in 1..=depth {
            let idx = (self.cursor + step) % depth;
            if !self.flags[idx].load(Ordering::Acquire) {
                return Some(idx);
            }
        }
        None
    }

    fn mark_busy(&mut self, idx: usize) {
        self.flags[idx].store(true, Ordering::Release);
        self.cursor = idx;
    }

    /// Test-only twin of the handler's `store(false)`: production clears a
    /// lease through the `Arc<AtomicBool>` clone the `WlBuffer` carries, so
    /// it needs neither this method nor the bridge lock (§4.5).
    #[cfg(test)]
    fn release(&self, idx: usize) {
        self.flags[idx].store(false, Ordering::Release);
    }

    fn flag(&self, idx: usize) -> Arc<AtomicBool> {
        Arc::clone(&self.flags[idx])
    }

    #[cfg(test)]
    fn busy_count(&self) -> usize {
        self.flags
            .iter()
            .filter(|f| f.load(Ordering::Acquire))
            .count()
    }
}

/// One slot's resources. Field order is load-bearing: `Drop` runs the
/// explicit Wayland destroys first, then releases `mem` (munmap) and `file`
/// (close) in declaration order — exactly §4.5's
/// `destroy buffer → destroy pool → munmap → close`.
struct Slot {
    buffer: WlBuffer,
    pool: WlShmPool,
    mem: Mmap,
    /// The memfd backing `mem`. Never read by the pixel path — its job is to
    /// keep the fd open until the slot dies (closing it early is legal for an
    /// existing mapping but erases the §7.3-6 census the L3 evidence uses),
    /// so `Debug` reports it as the identity it is.
    file: File,
}

impl std::fmt::Debug for Slot {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Slot(fd={}, {} bytes)",
            self.file.as_raw_fd(),
            self.mem.len
        )
    }
}

impl Drop for Slot {
    fn drop(&mut self) {
        // §4.5 order, part 1–2. Sending `destroy` on a connection that is
        // already dying (whole-teardown) is harmless: the request is queued
        // and never flushed, and the close is the compositor's reap signal
        // for every object of this client (§4.3's model for the ctx proxies
        // applies to buffers and pools identically).
        self.buffer.destroy();
        self.pool.destroy();
        // parts 3–4 (munmap, close) happen as `mem` then `file` drop.
    }
}

impl Slot {
    fn new(
        shm: &WlShm,
        qh: &QueueHandle<WaylandData>,
        geo: (u32, u32),
        fmt: PixelFormat,
        flag: Arc<AtomicBool>,
    ) -> Result<Slot, String> {
        let (w, h) = geo;
        let (stride, size) = layout(geo)?;
        let file = memfd().map_err(|e| format!("memfd: {e}"))?;
        file.set_len(size as u64)
            .map_err(|e| format!("ftruncate({size}): {e}"))?;
        let mem = Mmap::shared(&file, size).map_err(|e| format!("mmap({size}): {e}"))?;
        let pool = shm.create_pool(file.as_fd(), size as i32, qh, ());
        let buffer = pool.create_buffer(0, w as i32, h as i32, stride, shm_format(fmt), qh, flag);
        Ok(Slot {
            buffer,
            pool,
            mem,
            file,
        })
    }
}

/// Byte layout for one slot: `(stride, size)` with `size` already proven to
/// fit the protocol's `int32` pool length and `stride` to fit `int32`.
///
/// Failure-mode note (§1): the checks are not ceremony. `Ring::build` is
/// reached with geometry the COMPOSITOR proposed (§6.3 `configure`), so a
/// `u32 as i32` here could silently become a negative buffer dimension —
/// which the compositor would accept as a protocol error or, worse, map as a
/// row-slipped image. `LayerCtx::ensure_ring` applies the tighter §7.1 #3
/// policy first, so these conversions are unreachable in the Live world; they
/// stay checked because the ring must not depend on its caller having
/// validated (agents-rules §4: refuse, never truncate into a plausible number).
fn layout(geo: (u32, u32)) -> Result<(i32, usize), String> {
    let (w, h) = geo;
    let stride = (w as usize)
        .checked_mul(BYTES_PER_PIXEL)
        .ok_or_else(|| format!("stride {w}*{BYTES_PER_PIXEL} overflows"))?;
    let size = stride
        .checked_mul(h as usize)
        .ok_or_else(|| format!("size {stride}*{h} overflows"))?;
    let stride32 = i32::try_from(stride)
        .map_err(|_| format!("stride {stride} B exceeds the protocol's i32 range"))?;
    i32::try_from(size)
        .map_err(|_| format!("buffer size {size} B exceeds the protocol's i32 range"))?;
    Ok((stride32, size))
}

/// The Live-world resource holder: `RING_DEPTH` slots covering one geometry.
#[derive(Debug)]
pub(crate) struct Ring {
    plan: RingPlan,
    slots: Vec<Slot>,
}

/// What a submit attempt decided (§7.1 #5's three drop classes plus the two
/// "cannot submit at all" states).
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Submit {
    /// Pixels written, `attach`/`damage`/`commit` sent, slot still FREE until
    /// the caller's flush succeeds and it calls [`Ring::confirm`].
    Pending(usize),
    /// All slots leased → backpressure. Correct behavior, counted only (I6).
    Busy,
    /// No ring for the current logical size (§4.5 build failed or is pending
    /// the next configure) — the frame is dropped with a reason.
    NoRing,
    /// The caller forced a format this ring cannot hold (only reachable when
    /// the compositor does not advertise ABGR8888 and `force` demands it).
    /// Carries the ring's format so the diagnostic can name BOTH fourccs —
    /// "what you asked for" vs "what the only ring you have holds".
    FormatMismatch(PixelFormat),
    /// The context has no `wl_surface` at all — defined rejection rather than
    /// an `unwrap`, for the reason given on `LayerCtx::submit_frame`.
    NoSurface,
}

impl Ring {
    /// §4.5 "一次性建 3 套". On partial failure the already-built slots drop
    /// here, in §4.5's order, so a failed build leaks no fd and no mapping.
    pub(crate) fn build(
        shm: &WlShm,
        qh: &QueueHandle<WaylandData>,
        geo: (u32, u32),
        fmt: PixelFormat,
    ) -> Result<Ring, String> {
        let plan = RingPlan::new(geo, fmt);
        let mut slots = Vec::with_capacity(RING_DEPTH);
        for idx in 0..RING_DEPTH {
            match Slot::new(shm, qh, geo, fmt, plan.flag(idx)) {
                Ok(slot) => slots.push(slot),
                Err(why) => return Err(format!("slot {idx}: {why}")),
            }
        }
        Ok(Ring { plan, slots })
    }

    pub(crate) fn fmt(&self) -> PixelFormat {
        self.plan.fmt
    }

    pub(crate) fn needs_rebuild(&self, geo: (u32, u32), fmt: PixelFormat) -> bool {
        self.plan.needs_rebuild(geo, fmt)
    }

    /// Write `src` (RGBA8888, caller-sized by the LOGICAL dimensions) into
    /// slot `idx`'s shm memory, converting per §6.2.
    ///
    /// # Panics
    /// If `src.len()` is not the slot's byte length. Both sides are derived
    /// from the same logical size — a mismatch means the crate's own size
    /// bookkeeping is wrong, and §4.3's poison (panic at the FFI boundary,
    /// I4) is the correct shape for "we lost track of our own buffer", as
    /// opposed to a partial frame painted with a stale prefix.
    pub(crate) fn paint(&mut self, idx: usize, src: &[u8]) {
        let dst = self.slots[idx].mem.bytes_mut();
        crate::format::convert_rgba(src, dst, self.plan.fmt);
    }

    pub(crate) fn take_slot(&mut self) -> Option<usize> {
        self.plan.take_slot()
    }

    pub(crate) fn confirm(&mut self, idx: usize) {
        self.plan.confirm(idx);
    }

    pub(crate) fn buffer_ref(&self, idx: usize) -> &WlBuffer {
        &self.slots[idx].buffer
    }
}

/// `memfd_create("meapet-px", MFD_CLOEXEC)` — the name and flag are
/// contractual: spec §4.5 takes the flag from the reference implementation
/// (`layer_shell_c.c:335`) AND from ABI hygiene (this is a cdylib; whether
/// the host execs is not ours to decide, so the fd must not leak into a
/// child), and §7.3-6 counts fds by that name on live compositors.
fn memfd() -> Result<File, std::io::Error> {
    let name = c"meapet-px";
    // SAFETY: `name` is a static NUL-terminated string; a negative return
    // is never wrapped.
    let fd = unsafe { libc::memfd_create(name.as_ptr(), libc::MFD_CLOEXEC) };
    if fd < 0 {
        return Err(std::io::Error::last_os_error());
    }
    // SAFETY: fd >= 0 and is freshly owned by this process.
    Ok(unsafe { File::from_raw_fd(fd) })
}

fn shm_format(fmt: PixelFormat) -> Format {
    match fmt {
        PixelFormat::Abgr8888 => Format::Abgr8888,
        PixelFormat::Argb8888 => Format::Argb8888,
    }
}

/// A `MAP_SHARED` mapping over a memfd. Its only job is to make the
/// munmap/close ordering expressible as field order (§4.5) and to hand out
/// length-checked slices, never raw pointers.
struct Mmap {
    ptr: NonNull<u8>,
    len: usize,
}

// SAFETY: the mapping is private memory owned by exactly one `Slot`, reachable
// only through the bridge lock (submit and rebuild both hold it). `Send` is
// therefore the same claim the Wayland proxies in the same struct already make
// — the lock, not the type, serialises access. `Sync` matters for nothing
// beyond that lock either, but `Bridge` must be `Send` to live behind
// `Mutex<Bridge>` in a `static`, and the field-visit order the compiler checks
// is the whole struct's.
unsafe impl Send for Mmap {}
unsafe impl Sync for Mmap {}

impl Mmap {
    fn shared(file: &File, len: usize) -> Result<Mmap, std::io::Error> {
        // SAFETY: `file` is a memfd sized to `len` by the caller; MAP_SHARED
        // over a private fd. `len` is never 0 (sizes come from validated
        // dimensions ≥ 1×1), so the mapping is non-degenerate.
        let addr = unsafe {
            libc::mmap(
                std::ptr::null_mut(),
                len,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                file.as_fd().as_raw_fd(),
                0,
            )
        };
        if addr == libc::MAP_FAILED {
            return Err(std::io::Error::last_os_error());
        }
        // `mmap` returned neither MAP_FAILED nor null, so this cannot be None;
        // `expect` is the §4.3 shape (panic → poison), not a guess.
        let ptr = NonNull::new(addr.cast()).expect("mmap returned NULL");
        Ok(Mmap { ptr, len })
    }

    fn bytes_mut(&mut self) -> &mut [u8] {
        // SAFETY: `ptr`/`len` come from one successful mmap of this slot's
        // memfd and are never mutated afterwards; `&mut self` proves no other
        // live borrow of the same mapping exists.
        unsafe { std::slice::from_raw_parts_mut(self.ptr.as_ptr(), self.len) }
    }
}

impl Drop for Mmap {
    fn drop(&mut self) {
        // SAFETY: the pair came from the single mmap above.
        unsafe { libc::munmap(self.ptr.as_ptr() as *mut c_void, self.len) };
    }
}

impl std::fmt::Debug for Mmap {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Mmap({} bytes)", self.len)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::MetadataExt;

    fn plan(geo: (u32, u32)) -> RingPlan {
        RingPlan::new(geo, PixelFormat::Abgr8888)
    }

    /// §4.5 selection rule, normal path: the first frame takes slot 0 and
    /// each subsequent search starts one past the previous hit.
    #[test]
    fn first_hit_is_slot_zero_then_walks_forward() {
        let p = plan((4, 4));
        assert_eq!(p.take_slot(), Some(0));
        let mut p = p;
        p.confirm(0);
        assert_eq!(p.take_slot(), Some(1));
        p.confirm(1);
        assert_eq!(p.take_slot(), Some(2));
        p.confirm(2);
        // All three leased → the walk is over, backpressure begins.
        assert_eq!(p.take_slot(), None);
        assert_eq!(p.busy_count(), RING_DEPTH);
    }

    /// §4.5 + §6.5's terminal case for "release never arrives": the ring
    /// saturates at RING_DEPTH in-flight and then drops frames silently —
    /// it never queues a 4th buffer, never grows, never fakes a commit (I5).
    /// The slot picked after each release walks the whole ring, which is what
    /// makes "the same slot twice in a row" impossible below.
    #[test]
    fn never_release_saturates_then_cycles_without_pinning_one_slot() {
        let mut p = plan((8, 8));
        for _ in 0..RING_DEPTH {
            let idx = p.take_slot().expect("depth slots are free");
            p.confirm(idx);
        }
        for _ in 0..1000 {
            assert_eq!(p.take_slot(), None, "leased ring must stay busy");
            assert_eq!(p.busy_count(), RING_DEPTH);
        }
        // One slot comes back → used → comes back, repeatedly: the free slot
        // is found regardless of where the cursor sits, and the cursor then
        // advances past it so the next free slot wins the next round.
        for expected in [1usize, 2, 0, 1] {
            p.release(expected);
            assert_eq!(p.take_slot(), Some(expected));
            p.confirm(expected);
        }
    }

    /// Out-of-order release: whichever slot the compositor hands back first
    /// is the one that becomes pickable, and the cursor still governs WHERE
    /// the search starts (so two simultaneous frees pick the one after the
    /// last hit, not the lowest index).
    #[test]
    fn out_of_order_release_reopens_exactly_that_slot() {
        let mut p = plan((4, 4));
        for idx in 0..RING_DEPTH {
            let got = p.take_slot().unwrap();
            assert_eq!(got, idx);
            p.confirm(idx);
        }
        // Compositor releases slot 1 only.
        p.release(1);
        // Cursor is at 2, so the scan order is 2,0,1 → slot 2 is still leased,
        // 0 is leased, 1 is the hit.
        assert_eq!(p.take_slot(), Some(1));
        p.confirm(1);
        assert_eq!(p.take_slot(), None);
        // Now release 0 and 2: scan starts after 1, i.e. 2 first.
        p.release(0);
        p.release(2);
        assert_eq!(p.take_slot(), Some(2));
    }

    /// n=10000 steady state with a realistic compositor: release lands one
    /// configure-beat behind the commit, i.e. at most RING_DEPTH leases are
    /// open at once. Zero drops here is the machine-level statement of
    /// measurement §5.4's "深度 3 在 ≤240 fps 全零丢帧".
    #[test]
    fn ten_thousand_frames_with_depth_lag_release_never_drops() {
        let mut p = plan((64, 64));
        let mut inflight: std::collections::VecDeque<usize> = std::collections::VecDeque::new();
        let mut drops = 0usize;
        for _ in 0..10_000 {
            match p.take_slot() {
                Some(idx) => {
                    p.confirm(idx);
                    inflight.push_back(idx);
                }
                None => drops += 1,
            }
            // The compositor finishes the frame it took RING_DEPTH commits ago.
            if inflight.len() == RING_DEPTH {
                if let Some(old) = inflight.pop_front() {
                    p.release(old);
                }
            }
        }
        assert_eq!(
            drops, 0,
            "steady-state lag ≤ depth must never hit backpressure"
        );
        assert!(p.busy_count() <= RING_DEPTH);
    }

    /// The same loop, one slot shallower: the 4th in-flight frame has nowhere
    /// to go, so backpressure appears at exactly the depth bound — proving the
    /// counter above is the ring doing its job and not a test that cannot fail.
    #[test]
    fn one_frame_too_many_in_flight_is_backpressure_not_a_queue() {
        let mut p = plan((16, 16));
        let mut inflight: std::collections::VecDeque<usize> = std::collections::VecDeque::new();
        let mut drops = 0usize;
        for _ in 0..100 {
            match p.take_slot() {
                Some(idx) => {
                    p.confirm(idx);
                    inflight.push_back(idx);
                }
                None => drops += 1,
            }
            // Release only every (depth+1)-th commit → one lease too many.
            if inflight.len() == RING_DEPTH {
                let _held = inflight.pop_front(); // take it, but do NOT release
            }
        }
        assert_eq!(
            drops,
            100 - RING_DEPTH,
            "after the depth leases, every frame drops"
        );
        assert_eq!(p.busy_count(), RING_DEPTH);
    }

    /// §4.5 build timing as a decision: geometry and format both pin the ring;
    /// the identical pair never rebuilds (that would be per-frame churn the
    /// "整轮复用" clause forbids).
    #[test]
    fn rebuild_is_decided_by_geometry_and_format_only() {
        let p = plan((40, 30));
        assert!(!p.needs_rebuild((40, 30), PixelFormat::Abgr8888));
        assert!(p.needs_rebuild((40, 31), PixelFormat::Abgr8888));
        assert!(p.needs_rebuild((39, 30), PixelFormat::Abgr8888));
        assert!(p.needs_rebuild((40, 30), PixelFormat::Argb8888));
    }

    /// T3's rebuild window: when the ring is replaced, leases from the OLD
    /// geometry cannot corrupt the new machine — the old `Arc<AtomicBool>` is
    /// a different object, so a late `release` for a destroyed buffer writes a
    /// flag nobody consults. This is the §4.4 epoch guard in structural form.
    ///
    /// It is the DISPATCH SIDE's identity that makes this true, not index
    /// arithmetic: `Dispatch<WlBuffer, Arc<AtomicBool>>` clears the flag the
    /// buffer itself carries, so a release can never be applied "by position"
    /// to whichever ring is current. A plan-level `release(idx)` (test-only)
    /// does address by position, which is exactly why the old lease here has to
    /// be poked through its Arc rather than through `fresh`.
    #[test]
    fn stale_release_after_rebuild_cannot_touch_the_new_machine() {
        let mut old = plan((40, 40));
        let stale_idx = old.take_slot().unwrap();
        old.confirm(stale_idx);
        let stale_flag = old.flag(stale_idx);
        assert!(stale_flag.load(Ordering::Acquire));

        // §4.5 rebuild: fresh slots for the new geometry, old ones destroyed
        // without waiting for release.
        let mut fresh = plan((80, 60));
        let live_idx = fresh.take_slot().expect("a fresh ring is free");
        fresh.confirm(live_idx);
        assert_eq!(fresh.busy_count(), 1);
        // The two machines' flags are distinct objects even at the same index,
        // which is the whole guarantee — assert it, don't imply it.
        assert!(
            !Arc::ptr_eq(&stale_flag, &fresh.flag(live_idx)),
            "a rebuild must not share flag storage"
        );
        // The late release for the destroyed buffer lands — on the OLD flag.
        stale_flag.store(false, Ordering::Release);
        assert_eq!(fresh.busy_count(), 1, "new ring's own lease is unaffected");
        assert_eq!(fresh.take_slot(), Some(1));
        assert!(!fresh.needs_rebuild((80, 60), PixelFormat::Abgr8888));
    }

    /// `layout` is the pure half of `Slot::new`, and it runs BEFORE the memfd:
    /// an absurd geometry must be refused by name rather than reach the
    /// protocol as a truncated `int32` (a negative buffer dimension the
    /// compositor would either fault or honour as a row-slipped image).
    #[test]
    fn layout_reports_byte_geometry_and_refuses_unmappable_sizes() {
        // No fd census here, on purpose: `layout` is pure arithmetic (no
        // syscall between its `checked_mul` and its `Err`), and the name-
        // scoped census this assertion used to carry counted OTHER tests'
        // slots — the default harness runs this module in parallel inside one
        // process, so it could only ever flake (agents-rules §8: a check whose
        // failure depends on the schedule is not a check). fd lifetime is
        // asserted by `memfd_and_mapping_lifetime_is_exactly_one_fd`, scoped
        // to our own inode.
        assert_eq!(layout((1, 1)), Ok((4, 4)));
        assert_eq!(layout((40, 30)), Ok((160, 4800)));
        // The largest area §7.1 #3 allows, and the largest edge it allows:
        assert_eq!(layout((4096, 4096)), Ok((16_384, 67_108_864)));
        assert_eq!(layout((8192, 2048)), Ok((32_768, 67_108_864)));
        for bad in [
            (1 << 29, 4),
            (1 << 30, 1 << 30),
            (u32::MAX, 1),
            (1, u32::MAX),
        ] {
            assert!(layout(bad).is_err(), "{bad:?} accepted");
        }
    }

    /// Zero area is a protocol-legal-looking input that cannot hold a pixel;
    /// the gate that refuses it is §7.1 #3, applied where the logical size
    /// becomes bytes (`ensure_ring`, `update_pixels`) rather than here.
    #[test]
    fn zero_area_is_refused_by_the_policy_gate_not_by_layout() {
        assert_eq!(layout((0, 40)), Ok((0, 0)));
        assert!(crate::state::size_in_bounds(0, 40).is_err());
        assert!(crate::state::size_in_bounds(40, 0).is_err());
    }

    /// The real machine is never constructed in L2 (it needs `wl_shm`), so
    /// this asserts what IS constructible: depth flags, one per slot, stable
    /// identity across clones of the Arc.
    #[test]
    fn flags_are_shared_by_identity_not_copy() {
        let p = plan((4, 4));
        let a = p.flag(0);
        let b = p.flag(0);
        a.store(true, Ordering::Release);
        assert!(b.load(Ordering::Acquire));
        assert_eq!(p.take_slot(), Some(1), "slot 0 is leased through the clone");
    }

    /// Documents (as an executable claim) that `Ring` cannot be built without
    /// a compositor, hence every assertion above is about the machine the
    /// commit path runs: the L1/L2 coverage of §4.5 IS `RingPlan`, and the
    /// resource holder adds only fd/mmap lifetime, which the `#[ignore]`d
    /// live smoke plus WP-G's fd census (spec §7.3-6) cover at L3.
    #[test]
    fn ring_depth_constant_is_what_the_machine_reads() {
        assert_eq!(RING_DEPTH, 3, "spec §4.5 fixes this from measurement §5.4");
        assert_eq!(SlotFlags::new().flags.len(), RING_DEPTH);
    }

    /// The pairing between the two representations of "which fourcc is this
    /// buffer" (spec §6.2). `PixelFormat::fourcc()` is pinned in `format.rs`;
    /// `shm_format()` is what actually reaches
    /// `wl_shm_pool::create_buffer` (`Slot::new`), and until now nothing
    /// tested it — two truths for one fact, aligned only by the reader's
    /// attention (agents-rules §7).
    ///
    /// Failure mode if the two arms are swapped: everything else still passes.
    /// `format.rs` is untouched, the ring keeps its depth, the fd census is
    /// unchanged, and the image is committed and mapped normally — the only
    /// symptom is red/blue inversion on screen, i.e. exactly the silent
    /// wrong-output class spec §4.8-6 names. The recovery path before this
    /// test was T5-4, a human eye on a live compositor.
    ///
    /// Non-obvious half of the assertion: this does not pin which *layout*
    /// each name means (that is `format.rs`'s job, and `fourcc()`'s test); it
    /// pins that the variant whose memory order `convert_rgba` writes is the
    /// one advertised to the compositor.
    #[test]
    fn shm_format_pairs_each_layout_with_its_own_fourcc() {
        assert_eq!(shm_format(PixelFormat::Abgr8888), Format::Abgr8888);
        assert_eq!(shm_format(PixelFormat::Argb8888), Format::Argb8888);
        assert_ne!(
            shm_format(PixelFormat::Abgr8888),
            Format::Argb8888,
            "the two arms are not interchangeable"
        );
    }

    /// The resource half of §4.5 needs no compositor to prove its lifetime
    /// claims: a memfd + mapping behaves exactly as the slot's field order
    /// promises — one fd while alive, none after the mapping drops and the
    /// file closes, and `MFD_CLOEXEC` set (the contractual flag, §4.5 F-M8).
    /// This is the PER-SLOT half of that claim; spec §7.3-6's L3 census counts
    /// by name across the whole process (ctx × RING_DEPTH), which is a
    /// different quantity — neither one substitutes for the other.
    #[test]
    fn memfd_and_mapping_lifetime_is_exactly_one_fd() {
        let file = memfd().expect("memfd_create");
        file.set_len(4096).expect("ftruncate");
        let meta = file.metadata().expect("fstat on our own memfd");
        let (dev, ino) = (meta.dev(), meta.ino());
        let mut mem = Mmap::shared(&file, 4096).expect("mmap");
        assert_eq!(
            open_fds_pointing_at(dev, ino),
            1,
            "the slot holds exactly one fd while live"
        );
        assert!(
            fd_is_cloexec(file.as_raw_fd()),
            "MFD_CLOEXEC must be on the shm fd (§4.5)"
        );
        mem.bytes_mut()[..4].copy_from_slice(&[0xDE, 0xAD, 0xBE, 0xEF]);
        assert_eq!(&mem.bytes_mut()[..4], &[0xDE, 0xAD, 0xBE, 0xEF]);
        drop(mem);
        drop(file);
        assert_eq!(open_fds_pointing_at(dev, ino), 0, "munmap then close");
    }

    /// A mapping's slice must be exactly the requested length: `paint` and
    /// `convert_rgba` agree on the byte count only if this holds, and a short
    /// slice would be a silent truncated frame instead of the panic (§4.8-5).
    #[test]
    fn mapping_slice_is_the_whole_region() {
        let file = memfd().expect("memfd_create");
        file.set_len(1024).expect("ftruncate");
        let mut mem = Mmap::shared(&file, 1024).expect("mmap");
        assert_eq!(mem.bytes_mut().len(), 1024);
    }

    /// How many descriptors of THIS process still point at the inode
    /// `(dev, ino)`.
    ///
    /// Identity-scoped, not name-scoped, and that is the whole point
    /// (agents-rules §8): `cargo test` runs this module in parallel inside one
    /// process, so a census of the name `meapet-px` also counts the slots
    /// other tests hold open at that moment. The previous name-scoped form of
    /// `memfd_and_mapping_lifetime_is_exactly_one_fd` therefore failed under
    /// the default harness and passed only with `--test-threads=1`
    /// (measured 2026-09-21) — it was measuring the schedule, not the slot.
    /// `metadata()` follows each `/proc/self/fd/N` symlink to the open file,
    /// so `dev`/`ino` are the memfd's own. A missing `/proc` yields 0, which
    /// makes the "alive" assertion fail loudly instead of pretending to have
    /// measured. Residual limit, stated rather than hidden: after our own close
    /// the kernel could in principle hand the same inode number to another
    /// `memfd_create`, which would read as a leak; tmpfs numbers inodes from a
    /// per-superblock counter, so within one test run that is not a race this
    /// assertion depends on.
    fn open_fds_pointing_at(dev: u64, ino: u64) -> usize {
        let mut hits = 0;
        if let Ok(entries) = std::fs::read_dir("/proc/self/fd") {
            for entry in entries.flatten() {
                let Ok(meta) = std::fs::metadata(entry.path()) else {
                    continue;
                };
                if meta.dev() == dev && meta.ino() == ino {
                    hits += 1;
                }
            }
        }
        hits
    }

    fn fd_is_cloexec(fd: i32) -> bool {
        let path = format!("/proc/self/fdinfo/{fd}");
        let Ok(text) = std::fs::read_to_string(path) else {
            return false;
        };
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("flags:") {
                let trimmed = rest.trim();
                let digits = trimmed.strip_prefix("0o").unwrap_or(trimmed);
                if let Ok(flags) = usize::from_str_radix(digits, 8) {
                    return flags & (libc::O_CLOEXEC as usize) != 0;
                }
            }
        }
        false
    }
}
