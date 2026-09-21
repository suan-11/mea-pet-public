//! Wayland side of the bridge (spec §4.2, §6.1, §6.3): one process-wide
//! connection owned by `layer_shell_init`, its globals, the per-context
//! layer objects, and the `Dispatch` impls that the pump thread runs.
//!
//! Failure-mode notes (§1 discipline):
//! - `Core::connect_to_env` classifies errors exactly as spec §7.1 #1:
//!   -1 any transport failure (env unset *or* session closed), -2 a
//!   required global is absent or outside the bind version range, -3
//!   pump/internal establishment. C mapped "connect failed" to -1 too
//!   (`layer_shell_c.c` `wl_display_connect(NULL) == NULL` → -1), so the
//!   explicit `WAYLAND_DISPLAY` precheck from the WP-C stub is gone —
//!   resolving the §4.4-era open question about `WAYLAND_SOCKET`.
//! - The pump's `Dispatch` user data for layer surfaces is the PACKED
//!   HANDLE u64, never a pointer — every event lands on the §4.4 table
//!   lookup with the epoch guard; a stale pump can only produce
//!   defined rejections (§4.7-9/§4.4 implementation note).
//! - A panic inside an event handler is routed through `guarded` (I4's
//!   single catch site), so the pump thread cannot abort the Python host;
//!   it flips §4.3 poison exactly like a panic on a call thread.

use std::os::fd::{FromRawFd, OwnedFd};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use wayland_client::globals::{registry_queue_init, GlobalListContents};
use wayland_client::protocol::wl_buffer::WlBuffer;
use wayland_client::protocol::wl_compositor::WlCompositor;
use wayland_client::protocol::wl_output::WlOutput;
use wayland_client::protocol::wl_region::WlRegion;
use wayland_client::protocol::wl_registry::WlRegistry;
use wayland_client::protocol::wl_shm::{self, Format, WlShm};
use wayland_client::protocol::wl_shm_pool::WlShmPool;
use wayland_client::protocol::wl_surface::WlSurface;
use wayland_client::{Connection, Dispatch, EventQueue, Proxy, QueueHandle, WEnum};
use wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_shell_v1::{
    Layer, ZwlrLayerShellV1,
};
use wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_surface_v1::{
    Anchor, Event as LayerEvent, KeyboardInteractivity, ZwlrLayerSurfaceV1,
};

use crate::format::PixelFormat;
use crate::pump::{self, PumpHandle};
use crate::ring::{Ring, Submit};
use crate::state::guarded;

/// `layer_shell_init` failure taxonomy (§7.1 #1): payload is the reason
/// text that becomes part of the sticky error.
pub enum ConnectError {
    /// -1: no Wayland session reachable (env unset, or socket dead).
    NoDisplay(String),
    /// -2: a required global is missing or out of the bind range.
    MissingGlobal(&'static str),
    /// -3: pump thread / internal state establishment failed.
    Internal(String),
}

impl ConnectError {
    pub(crate) fn code(&self) -> i32 {
        match self {
            ConnectError::NoDisplay(_) => -1,
            ConnectError::MissingGlobal(_) => -2,
            ConnectError::Internal(_) => -3,
        }
    }
}

impl std::fmt::Display for ConnectError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConnectError::NoDisplay(m) => write!(f, "no Wayland session: {m}"),
            ConnectError::MissingGlobal(g) => write!(f, "compositor global missing: {g}"),
            ConnectError::Internal(m) => write!(f, "internal: {m}"),
        }
    }
}

/// State the pump's `EventQueue` dispatches into (spec §6.4: ONLY the
/// pump thread owns it). `epoch` gates every bridge mutation an event
/// may cause — see the §4.4 implementation note.
pub(crate) struct WaylandData {
    pub epoch: u32,
    /// Set during the connect-phase roundtrip by the `wl_shm` format
    /// advertisement (the ABGR8888 auto-select precondition, §6.2).
    pub abgr: bool,
}

impl Dispatch<WlRegistry, GlobalListContents> for WaylandData {
    fn event(
        _: &mut Self,
        _: &WlRegistry,
        _: <WlRegistry as Proxy>::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<WlCompositor, ()> for WaylandData {
    fn event(
        _: &mut Self,
        _: &WlCompositor,
        _: <WlCompositor as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<WlShm, ()> for WaylandData {
    fn event(
        data: &mut Self,
        _: &WlShm,
        event: <WlShm as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_shm::Event::Format { format } = event {
            // Compare against the crate-wide fourcc constant (AGENTS §7:
            // no second literal), accepting the raw-unknown encoding too
            // in case the compositor sends a value the headers name
            // differently.
            let raw_abgr = format == WEnum::Value(Format::Abgr8888)
                || matches!(format, WEnum::Unknown(v) if v == crate::format::WL_SHM_FORMAT_ABGR8888);
            if raw_abgr {
                data.abgr = true;
            }
        }
    }
}

impl Dispatch<ZwlrLayerShellV1, ()> for WaylandData {
    fn event(
        _: &mut Self,
        _: &ZwlrLayerShellV1,
        _: <ZwlrLayerShellV1 as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<WlSurface, ()> for WaylandData {
    fn event(
        _: &mut Self,
        _: &WlSurface,
        _: <WlSurface as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<WlRegion, ()> for WaylandData {
    fn event(
        _: &mut Self,
        _: &WlRegion,
        _: <WlRegion as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<WlShmPool, ()> for WaylandData {
    fn event(
        _: &mut Self,
        _: &WlShmPool,
        _: <WlShmPool as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<ZwlrLayerSurfaceV1, u64> for WaylandData {
    fn event(
        data: &mut Self,
        proxy: &ZwlrLayerSurfaceV1,
        event: LayerEvent,
        handle: &u64,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        let epoch = data.epoch;
        let handle = *handle;
        match event {
            LayerEvent::Configure {
                serial,
                width,
                height,
            } => {
                // §6.3: ack_configure MUST be sent for every configure,
                // even when the handle turns out dead — the compositor
                // tracks acknowledgement, not our business state.
                proxy.ack_configure(serial);
                guarded(
                    "pump:configure",
                    move |b| b.apply_configure(epoch, handle, width, height),
                    |_| (),
                );
            }
            LayerEvent::Closed => {
                guarded(
                    "pump:closed",
                    move |b| b.apply_closed(epoch, handle),
                    |_| (),
                );
            }
            _ => {}
        }
    }
}

impl Dispatch<WlBuffer, Arc<AtomicBool>> for WaylandData {
    fn event(
        data: &mut Self,
        _: &WlBuffer,
        event: <WlBuffer as Proxy>::Event,
        in_use: &Arc<AtomicBool>,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if matches!(event, <WlBuffer as Proxy>::Event::Release) {
            // The slot flag is the ring's harvest channel (WP-E); the
            // counter is diagnostics-only (I6).
            in_use.store(false, Ordering::Release);
            let epoch = data.epoch;
            guarded(
                "pump:release",
                move |b| b.apply_buffer_release(epoch),
                |_| (),
            );
        }
    }
}

/// The per-context state the §4.4 table owns (spec §4.6 geometry model +
/// §4.7-12's `dead` flag). Field types are contractual: geometry is `u32`
/// (protocol `set_size`/configure args), position is `i32` (protocol
/// `set_margin` args are `int`, so negatives pass through — §6.1 step 5).
///
/// WP-D additions:
/// - `dead`: set by the `closed` event. `guard_ctx` rejects a dead entry
///   the same way it rejects an unknown handle, which is what makes
///   §4.7-12's "compositor retracts → Python can read WHY" observable
///   instead of a silent freeze.
/// - `surface`/`layer`: the compositor-side proxies, `Option` because a
///   `Fake`-world context (and any context before §6.1 object creation
///   completes) has none. Dropping them does NOT send `destroy`
///   (wayland-client proxies are handles, not RAII destructors), so
///   `destroy_context` must `.destroy()` explicitly while the connection
///   lives; a whole-teardown relies on the connection close reaping them
///   server-side (see `Bridge::teardown`).
#[derive(Debug)]
pub(crate) struct LayerCtx {
    pub req_w: u32,
    pub req_h: u32,
    pub logical_w: u32,
    pub logical_h: u32,
    pub pos_x: i32,
    pub pos_y: i32,
    pub configured: bool,
    /// §4.6: a new context starts click-through (empty input region),
    /// matching C. Python's `layer_set_click_through(ctx, 0)` opts back in.
    pub click_through: bool,
    pub dead: bool,
    surface: Option<WlSurface>,
    layer: Option<ZwlrLayerSurfaceV1>,
    /// §4.5: the ring covering the CURRENT logical size, built on the pump
    /// thread when a configure lands and dropped when the geometry moves.
    /// `None` means "no slots to paint into" — a `Fake`-world context always,
    /// and a Live context only between a configure-triggered teardown and its
    /// rebuild succeeding (see `LayerCtx::ensure_ring`).
    ring: Option<Ring>,
}

impl LayerCtx {
    pub(crate) fn new(w: u32, h: u32, x: i32, y: i32) -> Self {
        LayerCtx {
            req_w: w,
            req_h: h,
            // §6.3: `logical` starts at the REQUEST and only follows the
            // compositor's `configure` once it lands; `configured` gates
            // frame submission so a not-yet-acked context drops frames
            // rather than painting at an unconfirmed size.
            logical_w: w,
            logical_h: h,
            pos_x: x,
            pos_y: y,
            configured: false,
            click_through: true,
            dead: false,
            surface: None,
            layer: None,
            ring: None,
        }
    }

    /// Fill in the proxies `Bridge::create_context` obtained from §6.1.
    /// Called exactly once, while the bridge lock is held, so no event
    /// can observe the entry in the half-built window (the handle is the
    /// layer surface's user data from birth, but events only dispatch
    /// once this closure releases the lock).
    pub(crate) fn attach_proxies(&mut self, surface: WlSurface, layer: ZwlrLayerSurfaceV1) {
        self.surface = Some(surface);
        self.layer = Some(layer);
    }

    // ----- §4.5 buffer ring ---------------------------------------------

    /// §4.5 build timing: the ring covers exactly the current logical size at
    /// the format this compositor advertises, so it is built when the first
    /// `configure` lands and rebuilt when a later configure moved the
    /// geometry. Always called on the pump thread under the bridge lock, so
    /// no submit path can observe a half-built ring.
    ///
    /// Failure-mode note (§1): on `memfd`/`mmap` failure the old ring is
    /// already gone and the new one does not exist, so every following
    /// `layer_update_pixels` drops its frame with a reason (`Submit::NoRing`)
    /// rather than falling back to a per-frame allocation — the "quietly do
    /// something plausible instead" shape agents-rules §4 forbids. The next
    /// configure retries; nothing about the failure is latched except the
    /// sticky reason.
    pub(crate) fn ensure_ring(
        &mut self,
        shm: &WlShm,
        qh: &QueueHandle<WaylandData>,
        fmt: PixelFormat,
    ) -> Result<(), String> {
        let geo = (self.logical_w, self.logical_h);
        if let Some(ring) = &self.ring {
            if !ring.needs_rebuild(geo, fmt) {
                return Ok(());
            }
        }
        // Drop the old ring BEFORE building: I5 bounds a context's in-flight
        // buffers (and therefore its memfds) at RING_DEPTH, not 2×RING_DEPTH,
        // and §4.5's "旧 slot 立即释放" is the same instruction.
        self.ring = None;
        // §7.1 #3 again as a GATE, not a formality: the logical size here came
        // from `configure`, i.e. from the compositor, and this is the only
        // place that turns it into bytes. Refusing keeps us from attempting the
        // ~1.6 GB mapping such a geometry asks for. It must run AFTER the drop
        // above — the logical size has already moved, so keeping a ring of the
        // previous geometry would hand `submit_frame` a source slice that does
        // not match its slots (the size-tracking failure §4.8-5 is about).
        crate::state::size_in_bounds(geo.0 as i64, geo.1 as i64)?;
        self.ring = Some(Ring::build(shm, qh, geo, fmt)?);
        Ok(())
    }

    /// §4.5 commit selection + the first three request steps for one frame:
    /// pick the slot after the last hit, write the pixels, `attach` →
    /// `damage(0,0,w,h)` → `commit`. The slot stays FREE until the caller's
    /// flush succeeded and it calls [`LayerCtx::confirm_submit`] — that order
    /// is §4.5's, and it is what keeps a flush failure (→ poison) from
    /// leasing a slot nobody will ever release.
    ///
    /// Bytes are interpreted by the LOGICAL size on both sides (§6.3,
    /// §4.8-5): `src` was sliced by the caller from `logical_*`, and the slot
    /// it lands in was mapped for `logical_*` by `ensure_ring`.
    pub(crate) fn submit_frame(&mut self, src: &[u8], want_fmt: PixelFormat) -> Submit {
        let Some(surface) = &self.surface else {
            // Unreachable in the Live world (a context gets its proxies at
            // creation and loses them only at destroy, which also removes the
            // entry), but a defined rejection is cheaper than an `unwrap` on
            // the path Python calls sixty times a second.
            return Submit::NoSurface;
        };
        let Some(ring) = &mut self.ring else {
            return Submit::NoRing;
        };
        if ring.fmt() != want_fmt {
            return Submit::FormatMismatch(ring.fmt());
        }
        let Some(idx) = ring.take_slot() else {
            return Submit::Busy;
        };
        ring.paint(idx, src);
        surface.attach(Some(ring.buffer_ref(idx)), 0, 0);
        surface.damage(0, 0, self.logical_w as i32, self.logical_h as i32);
        surface.commit();
        Submit::Pending(idx)
    }

    /// §4.5's last step: the flush took the commit to the compositor, so the
    /// slot is now leased until its `release` lands.
    ///
    /// The ring cannot have been rebuilt in between — submit and confirm run
    /// inside one `guarded` critical section, which holds the bridge lock for
    /// their entire span, and `ensure_ring` only ever runs under that same
    /// lock. If that ever stopped being true the symptom would be a slot
    /// marked busy that the new ring already considers free, i.e. a dropped
    /// frame, never memory unsafety.
    pub(crate) fn confirm_submit(&mut self, idx: usize) {
        if let Some(ring) = &mut self.ring {
            ring.confirm(idx);
        }
    }

    /// §7.1 #7 / §4.5: clearing is not a frame — `attach(NULL)` + damage the
    /// logical size + commit, and no slot lease is taken or released. The
    /// buffers already in flight stay in flight; the compositor still sends
    /// their `release`s, which just mark slots we will pick next time.
    pub(crate) fn clear_surface(&mut self) -> bool {
        let Some(surface) = &self.surface else {
            return false;
        };
        surface.attach(None, 0, 0);
        surface.damage(0, 0, self.logical_w as i32, self.logical_h as i32);
        surface.commit();
        true
    }

    /// §4.5 teardown order for one context: layer surface then base
    /// surface. Consumes the proxies (takes them out) so a double-path
    /// (destroy then clear) never sends `destroy` twice.
    pub(crate) fn destroy_proxies(&mut self) {
        // The ring goes first: §4.5 releases slots `destroy buffer → destroy
        // pool → munmap → close` while the connection is still up (this is
        // the per-context path, unlike whole-teardown where the close reaps),
        // then the surfaces those buffers were attached to.
        self.ring = None;
        if let Some(layer) = self.layer.take() {
            layer.destroy();
        }
        if let Some(surface) = self.surface.take() {
            surface.destroy();
        }
    }

    /// §4.6 / §4.7-7: rebuild the input region and apply it. `full=false`
    /// → empty region (click-through); `full=true` → `add(0,0,w,h)` at the
    /// CURRENT logical size. New region each call, set, destroy — the same
    /// sequence C used, which allows repeated toggling. The `w,h` are read
    /// by the caller from `logical_*` so a resize while clickable re-sends
    /// the NEW rectangle (the step C was missing).
    pub(crate) fn apply_input_region(
        &mut self,
        compositor: &WlCompositor,
        qh: &QueueHandle<WaylandData>,
    ) {
        let Some(surface) = &self.surface else { return };
        let region = compositor.create_region(qh, ());
        if !self.click_through {
            region.add(0, 0, self.logical_w as i32, self.logical_h as i32);
        }
        surface.set_input_region(Some(&region));
        region.destroy();
        surface.commit();
    }

    /// §6.1 step 5 margin send + commit for a position change; caller has
    /// already updated `pos_x/pos_y`. Parameter order top,right,bottom,left
    /// is locked by §6.1/§4.8-2: (x,y) → margins (y, 0, 0, x).
    pub(crate) fn apply_position(&self) {
        if let Some(layer) = &self.layer {
            layer.set_margin(self.pos_y, 0, 0, self.pos_x);
            // A layer surface change needs a commit to take effect.
            if let Some(surface) = &self.surface {
                surface.commit();
            }
        }
    }

    /// §6.3: a size change is only a REQUEST; the logical size and (if
    /// clickable) the input region follow on the next `configure`.
    pub(crate) fn apply_size_request(&self) {
        if let Some(layer) = &self.layer {
            layer.set_size(self.req_w, self.req_h);
            if let Some(surface) = &self.surface {
                surface.commit();
            }
        }
    }
}

/// Connection + bound globals + the pump, once `init` succeeded.
/// `None`-able queue/data fields: consumed exactly once by `spawn_pump`.
pub(crate) struct LiveCore {
    pub conn: Connection,
    pub qh: QueueHandle<WaylandData>,
    pub compositor: WlCompositor,
    /// §4.5: `wl_shm` is the pool factory every ring slot is built from.
    pub shm: WlShm,
    pub layer_shell: ZwlrLayerShellV1,
    pub abgr_supported: bool,
    queue: Option<EventQueue<WaylandData>>,
    data: Option<WaylandData>,
    pump: Option<PumpHandle>,
}

/// What `Bridge.core` holds: either the real connection (production
/// `Ready`) or the test sentinel (L2 world: state machine only, every
/// request-sending branch skips — mirrors how the §7.4 tests never touch
/// a compositor). In production `Ready ⇔ Live` by construction.
pub(crate) enum Core {
    /// Boxed: `LiveCore` embeds the `EventQueue` + globals (≥ 240 B),
    /// and the enum lives inside `Option<Core>` on the global bridge —
    /// boxing keeps every `match`-site copy cheap (clippy
    /// `large_enum_variant`, WP-D gate).
    Live(Box<LiveCore>),
    /// Constructed only by `Core::fake()` under `cfg(test)`, but every
    /// `match` over `Core` keeps its arm in production builds — so the
    /// warning is test-gated, not the variant (an `unreachable!` in
    /// production instead would turn a type distinction into a panic
    /// path, which §4.3's no-panic-across-FFI model forbids).
    #[cfg_attr(not(test), allow(dead_code))]
    Fake,
}

impl Core {
    /// spec §4.2/§7.1 #1 — the ONLY place a Wayland connection is
    /// established; §4.1's I3 means no fd ever escapes it.
    pub(crate) fn connect_to_env() -> Result<Core, ConnectError> {
        let conn = Connection::connect_to_env()
            .map_err(|e| ConnectError::NoDisplay(format!("connect_to_env: {e}")))?;
        let (globals, mut queue) = registry_queue_init::<WaylandData>(&conn)
            .map_err(|e| ConnectError::NoDisplay(format!("registry handshake: {e}")))?;
        let qh = queue.handle();
        let mut data = WaylandData {
            epoch: 0,
            abgr: false,
        };
        // Version ranges: spec §6.1 step 1 (compositor ≤ 4) and §4.1
        // (layer-shell v1 is all we need); shm 1..=1 matches the probe
        // that took the L3 evidence.
        let compositor: WlCompositor = globals
            .bind(&qh, 1..=4, ())
            .map_err(|_| ConnectError::MissingGlobal("wl_compositor (versions 1..=4)"))?;
        let shm: WlShm = globals
            .bind(&qh, 1..=1, ())
            .map_err(|_| ConnectError::MissingGlobal("wl_shm (version 1)"))?;
        let layer_shell: ZwlrLayerShellV1 = globals
            .bind(&qh, 1..=4, ())
            .map_err(|_| ConnectError::MissingGlobal("zwlr_layer_shell_v1 (versions 1..=4)"))?;
        // One roundtrip after binding collects the `wl_shm` format
        // advertisements (decides §6.2's auto-select). Transport death
        // mid-handshake is still "no session reachable" → -1; anything
        // the compositor rejects outright is our own wire bug → -3.
        queue
            .roundtrip(&mut data)
            .map_err(|e| ConnectError::NoDisplay(format!("post-bind roundtrip: {e}")))?;
        Ok(Core::Live(Box::new(LiveCore {
            conn,
            qh,
            compositor,
            shm,
            layer_shell,
            abgr_supported: data.abgr,
            queue: Some(queue),
            data: Some(data),
            pump: None,
        })))
    }

    #[cfg(test)]
    pub(crate) fn fake() -> Core {
        Core::Fake
    }

    pub(crate) fn take_pump(&mut self) -> Option<PumpHandle> {
        match self {
            Core::Live(l) => l.pump.take(),
            Core::Fake => None,
        }
    }

    pub(crate) fn abgr_supported(&self) -> bool {
        match self {
            Core::Live(l) => l.abgr_supported,
            Core::Fake => true,
        }
    }
}

impl LiveCore {
    /// spec §4.2: one pump thread per init, §6.4 protocol, §4.3 quit-pipe
    /// exit. Spawn failure is an init -3 (§7.1 #1), never a Ready
    /// bridge: without the pump nobody reads the socket, which is the
    /// exact silent-degradation state I3/§6.4 exists to prevent.
    pub(crate) fn spawn_pump(&mut self, epoch: u32) -> Result<(), ConnectError> {
        let queue = self
            .queue
            .take()
            .ok_or_else(|| ConnectError::Internal("pump parts already consumed".into()))?;
        let mut data = self
            .data
            .take()
            .ok_or_else(|| ConnectError::Internal("pump parts already consumed".into()))?;
        data.epoch = epoch;
        let (quit_read, quit_write) = quit_pipe()?;
        let done = Arc::new(AtomicBool::new(false));
        let done_in_thread = Arc::clone(&done);
        let join = std::thread::Builder::new()
            .name("meapet-layer-pump".into())
            .spawn(move || {
                pump::run_loop(queue, data, quit_read);
                // Reached on quit-pipe wake, disconnect, or error-exit —
                // `done` gates cleanup's bounded wait (§4.3).
                done_in_thread.store(true, Ordering::Release);
            })
            .map_err(|e| ConnectError::Internal(format!("pump thread spawn failed: {e}")))?;
        // The clone keeps the socket alive while the pump winds down even
        // if the bridge drops its own `Core` first (§4.3: the pump exits
        // once every connection reference is gone) — see `PumpHandle::conn`.
        self.pump = Some(PumpHandle::new(quit_write, done, join, self.conn.clone()));
        Ok(())
    }

    /// spec §6.1 steps 1–9, order-locked ("顺序即语义"). Step 4's
    /// anchor is what makes margins apply at all on wlroots compositors;
    /// step 5's parameter order top,right,bottom,left is the §4.8-2
    /// trap: (x,y) swapped into it would still show a normal picture.
    /// Negative x/y pass through as i32 — the protocol args are `int`,
    /// so no cast happens anywhere (§6.1 step 5's explicit prohibition).
    pub(crate) fn create_layer_ctx_objects(
        &self,
        handle: u64,
        w: u32,
        h: u32,
        x: i32,
        y: i32,
    ) -> (WlSurface, ZwlrLayerSurfaceV1) {
        let surface = self.compositor.create_surface(&self.qh, ());
        let layer = self.layer_shell.get_layer_surface(
            &surface,
            Option::<&WlOutput>::None,
            Layer::Overlay,
            // §6.1 step 2: the namespace is contractual — compositor
            // rules (and WP-G's activation proof) match on "meapet".
            "meapet".to_string(),
            &self.qh,
            handle,
        );
        layer.set_size(w, h);
        layer.set_anchor(Anchor::Top | Anchor::Left);
        layer.set_margin(y, 0, 0, x);
        layer.set_keyboard_interactivity(KeyboardInteractivity::None);
        layer.set_exclusive_zone(0);
        let region = self.compositor.create_region(&self.qh, ());
        surface.set_input_region(Some(&region));
        region.destroy();
        surface.commit();
        (surface, layer)
    }

    /// §4.2: callers end their critical section with a flush; an Err
    /// here means the socket died or the compositor faulted our wire
    /// → poison upstream.
    pub(crate) fn flush_checked(&self) -> Option<String> {
        self.conn.flush().err().map(|e| format!("{e}"))
    }
}

/// `pipe2(O_CLOEXEC)` — spec §6.4's version-stable exit mechanism; the
/// CLOEXEC flag keeps fork/exec'd children from inheriting the wake end
/// (the same reason §4.5's memfd uses MFD_CLOEXEC).
fn quit_pipe() -> Result<(OwnedFd, OwnedFd), ConnectError> {
    let mut fds = [-1i32; 2];
    // SAFETY: writing two ints into our own stack array via FFI, then
    // only wrapping the fds if pipe2 reported success.
    let rc = unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC) };
    if rc != 0 {
        return Err(ConnectError::Internal(format!(
            "pipe2 failed: {}",
            std::io::Error::last_os_error()
        )));
    }
    // SAFETY: pipe2 returned 0, so both fds are freshly owned by us.
    let pair = unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };
    Ok(pair)
}
