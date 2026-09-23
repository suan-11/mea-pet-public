//! Probe C: measures the *buffer ring* behaviour the Rust bridge proposal
//! depends on, on the live compositor.
//!
//! Why this exists: probe B reported `RELEASED=0` while hammering commits, but
//! `EventQueue::dispatch_pending()` in wayland-client 0.31 does **not** read the
//! socket (events only accumulate via the read APIs / `blocking_dispatch`).
//! So probe B never observed compositor events at all and `RELEASED=0` measured
//! the probe, not niri. This probe pumps events properly on a dedicated thread
//! (the architecture the draft proposes) and measures what a fixed-depth ring
//! actually does.
//!
//! Usage: ring_probe [FPS] [SECS] [RING_DEPTH]      (defaults: 30 10 3)
//! A single-shot demo mode: ring_probe demo

use std::ffi::CString;
use std::os::fd::{AsFd, FromRawFd, OwnedFd};
use std::ptr;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use wayland_client::globals::{registry_queue_init, GlobalListContents};
use wayland_client::protocol::wl_buffer::WlBuffer;
use wayland_client::protocol::wl_compositor::WlCompositor;
use wayland_client::protocol::wl_output::WlOutput;
use wayland_client::protocol::wl_region::WlRegion;
use wayland_client::protocol::wl_registry::WlRegistry;
use wayland_client::protocol::wl_shm::{self, Format, WlShm};
use wayland_client::protocol::wl_shm_pool::WlShmPool;
use wayland_client::protocol::wl_surface::WlSurface;
use wayland_client::{Connection, Dispatch, Proxy, QueueHandle};
use wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_shell_v1::{
    Layer, ZwlrLayerShellV1,
};
use wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_surface_v1::{
    Anchor, Event as LayerEvent, ZwlrLayerSurfaceV1,
};

const W: i32 = 400;
const H: i32 = 400;

#[derive(Default)]
struct Shared {
    configured: Mutex<Option<(u32, u32)>>,
    releases: AtomicU64,
    error: Mutex<Option<String>>,
}

struct Pump {
    shared: Arc<Shared>,
}

impl Dispatch<WlRegistry, GlobalListContents> for Pump {
    fn event(
        _: &mut Pump,
        _: &WlRegistry,
        _: <WlRegistry as Proxy>::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Pump>,
    ) {
    }
}
impl Dispatch<WlCompositor, ()> for Pump {
    fn event(
        _: &mut Pump,
        _: &WlCompositor,
        _: <WlCompositor as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Pump>,
    ) {
    }
}
impl Dispatch<WlShm, ()> for Pump {
    fn event(
        data: &mut Pump,
        _: &WlShm,
        event: <WlShm as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Pump>,
    ) {
        if let wl_shm::Event::Format { format } = event {
            let raw = match format {
                wayland_client::WEnum::Value(Format::Abgr8888) => 0x3432_4241u32,
                wayland_client::WEnum::Value(Format::Argb8888) => 0u32,
                wayland_client::WEnum::Value(other) => {
                    let _ = other;
                    u32::MAX
                }
                wayland_client::WEnum::Unknown(raw) => raw,
                _ => u32::MAX,
            };
            let _ = raw;
        }
    }
}
impl Dispatch<WlShmPool, ()> for Pump {
    fn event(
        _: &mut Pump,
        _: &WlShmPool,
        _: <WlShmPool as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Pump>,
    ) {
    }
}
impl Dispatch<WlSurface, ()> for Pump {
    fn event(
        _: &mut Pump,
        _: &WlSurface,
        _: <WlSurface as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Pump>,
    ) {
    }
}
impl Dispatch<WlRegion, ()> for Pump {
    fn event(
        _: &mut Pump,
        _: &WlRegion,
        _: <WlRegion as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Pump>,
    ) {
    }
}
impl Dispatch<WlOutput, ()> for Pump {
    fn event(
        _: &mut Pump,
        _: &WlOutput,
        _: <WlOutput as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Pump>,
    ) {
    }
}
impl Dispatch<ZwlrLayerShellV1, ()> for Pump {
    fn event(
        _: &mut Pump,
        _: &ZwlrLayerShellV1,
        _: <ZwlrLayerShellV1 as Proxy>::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Pump>,
    ) {
    }
}
impl Dispatch<ZwlrLayerSurfaceV1, Arc<Shared>> for Pump {
    fn event(
        data: &mut Pump,
        proxy: &ZwlrLayerSurfaceV1,
        event: LayerEvent,
        shared: &Arc<Shared>,
        _: &Connection,
        _: &QueueHandle<Pump>,
    ) {
        match event {
            LayerEvent::Configure { serial, width, height } => {
                proxy.ack_configure(serial);
                let mut c = shared.configured.lock().unwrap();
                let (pw, ph) = c.unwrap_or((W as u32, H as u32));
                *c = Some((
                    if width > 0 { width } else { pw },
                    if height > 0 { height } else { ph },
                ));
            }
            LayerEvent::Closed => {
                *shared.error.lock().unwrap() = Some("layer_surface closed".into());
            }
            _ => {}
        }
    }
}
impl Dispatch<WlBuffer, Arc<AtomicBool>> for Pump {
    fn event(
        data: &mut Pump,
        _: &WlBuffer,
        event: <WlBuffer as Proxy>::Event,
        in_use: &Arc<AtomicBool>,
        _: &Connection,
        _: &QueueHandle<Pump>,
    ) {
        match event {
            <WlBuffer as Proxy>::Event::Release => {
                in_use.store(false, Ordering::Release);
                data.shared.releases.fetch_add(1, Ordering::Relaxed);
            }
            _ => {}
        }
    }
}

fn shm_alloc(size: usize) -> (OwnedFd, *mut u8) {
    unsafe {
        let name = CString::new("ring-probe-px").unwrap();
        let fd = libc::syscall(libc::SYS_memfd_create, name.as_ptr(), libc::MFD_CLOEXEC) as i32;
        assert!(fd >= 0, "memfd_create failed");
        assert_eq!(libc::ftruncate(fd, size as libc::off_t), 0, "ftruncate failed");
        let addr = libc::mmap(
            ptr::null_mut(),
            size,
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED,
            fd,
            0,
        );
        assert_ne!(addr, libc::MAP_FAILED, "mmap failed");
        (OwnedFd::from_raw_fd(fd), addr as *mut u8)
    }
}

struct Slot {
    _fd: OwnedFd,
    _pool: WlShmPool,
    buffer: WlBuffer,
    mem: *mut u8,
    len: usize,
    in_use: Arc<AtomicBool>,
    commit_at: Mutex<Option<Instant>>,
}
unsafe impl Send for Slot {}

fn fd_count() -> usize {
    std::fs::read_dir("/proc/self/fd").map_or(0, |d| d.count())
}

fn rss_kib() -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| {
            s.lines()
                .find(|l| l.starts_with("VmRSS:"))
                .and_then(|l| l.split_whitespace().nth(1))
                .and_then(|v| v.parse().ok())
        })
        .unwrap_or(0)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let demo = args.get(1).map(|s| s == "demo").unwrap_or(false);
    let fps: u64 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(30);
    let secs: u64 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(10);
    let depth: usize = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(3);

    let conn = match Connection::connect_to_env() {
        Ok(c) => c,
        Err(e) => {
            println!("CONNECT=fail {e:?}");
            std::process::exit(1);
        }
    };
    let (globals, mut queue) = match registry_queue_init::<Pump>(&conn) {
        Ok(v) => v,
        Err(e) => {
            println!("REGISTRY=fail {e:?}");
            std::process::exit(1);
        }
    };
    let qh = queue.handle();
    let shared = Arc::new(Shared::default());

    let compositor: WlCompositor = globals.bind(&qh, 1..=4, ()).expect("compositor");
    let shm: WlShm = globals.bind(&qh, 1..=1, ()).expect("shm");
    let layer_shell: ZwlrLayerShellV1 = globals.bind(&qh, 1..=4, ()).expect("layer_shell");

    let surface = compositor.create_surface(&qh, ());
    let ls = layer_shell.get_layer_surface(
        &surface,
        Option::<&WlOutput>::None,
        Layer::Overlay,
        "meapet-ring-probe".to_string(),
        &qh,
        Arc::clone(&shared),
    );
    ls.set_size(W as u32, H as u32);
    ls.set_anchor(Anchor::Top | Anchor::Left);
    ls.set_margin(60, 0, 0, 60);
    ls.set_keyboard_interactivity(
        wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_surface_v1::KeyboardInteractivity::None,
    );
    ls.set_exclusive_zone(0);
    let region = compositor.create_region(&qh, ());
    surface.set_input_region(Some(&region));
    region.destroy();
    surface.commit();
    conn.flush().ok();

    // Pump thread: the architecture the draft proposes (own connection, own pump).
    let pump_shared = Arc::clone(&shared);
    let pump = std::thread::spawn(move || {
        let mut p = Pump { shared: pump_shared };
        loop {
            match queue.blocking_dispatch(&mut p) {
                Ok(0) => continue,
                Ok(_) => continue,
                Err(e) => {
                    *p.shared.error.lock().unwrap() = Some(format!("pump ended: {e:?}"));
                    return;
                }
            }
        }
    });

    // wait for configure
    let deadline = Instant::now() + Duration::from_secs(3);
    while shared.configured.lock().unwrap().is_none() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(2));
    }
    let configured = shared.configured.lock().unwrap().is_some();
    println!("CONFIGURED={configured}");
    if !configured {
        println!("EXIT=not-configured");
        std::process::exit(3);
    }

    // Build the ring: `depth` buffers that are reused, never reallocated.
    let stride = (W * 4) as usize;
    let size = stride * H as usize;
    let mut slots: Vec<Slot> = Vec::new();
    for _ in 0..depth {
        let (fd, mem) = shm_alloc(size);
        let pool = shm.create_pool(fd.as_fd(), size as i32, &qh, ());
        let in_use = Arc::new(AtomicBool::new(false));
        let buffer =
            pool.create_buffer(0, W, H, stride as i32, Format::Abgr8888, &qh, Arc::clone(&in_use));
        slots.push(Slot {
            _fd: fd,
            _pool: pool,
            buffer,
            mem,
            len: size,
            in_use,
            commit_at: Mutex::new(None),
        });
    }
    println!("RING depth={depth} fd_after_ring={}", fd_count());
    let fd_baseline = fd_count();
    let rss_baseline = rss_kib();

    if demo {
        // single visible frame, held for 3 s so a human can confirm it on screen
        let s = &slots[0];
        unsafe {
            for i in 0..(size / 4) {
                let px = s.mem.add(i * 4) as *mut u32;
                *px = 0xFF00_00FF; // ABGR8888 -> opaque red
            }
        }
        surface.attach(Some(&s.buffer), 0, 0);
        surface.damage(0, 0, W, H);
        surface.commit();
        conn.flush().ok();
        println!("DEMO=red 400x400 at (60,60) held 3s (confirm visible + click-through)");
        std::thread::sleep(Duration::from_secs(3));
        let r = shared.releases.load(Ordering::Relaxed);
        println!("DEMO_RELEASES={r}");
        return;
    }

    let period = Duration::from_nanos(1_000_000_000 / fps);
    let mut submitted: u64 = 0;
    let mut dropped_busy: u64 = 0;
    let mut hold_sum_ms = 0.0f64;
    let mut hold_max_ms = 0.0f64;
    let mut hold_n: u64 = 0;
    let mut max_inflight: u64 = 0;
    let start = Instant::now();
    let mut next = start;

    while start.elapsed() < Duration::from_secs(secs) {
        if demo {
            break;
        }
        let now = Instant::now();
        if now < next {
            std::thread::sleep(next - now);
        }
        next += period;

        // harvest slots freed by the pump (release event) and measure hold time
        for s in slots.iter() {
            if !s.in_use.load(Ordering::Acquire) {
                if let Some(t) = s.commit_at.lock().unwrap().take() {
                    let ms = t.elapsed().as_secs_f64() * 1e3;
                    hold_sum_ms += ms;
                    hold_max_ms = hold_max_ms.max(ms);
                    hold_n += 1;
                }
            }
        }

        let free = slots.iter().find(|s| {
            s.in_use
                .compare_exchange(false, true, Ordering::AcqRel, Ordering::Relaxed)
                .is_ok()
        });
        match free {
            None => {
                dropped_busy += 1;
            }
            Some(s) => {
                let f = submitted as u32;
                unsafe {
                    for y in 0..H as usize {
                        let row = s.mem.add(y * stride) as *mut u32;
                        for x in 0..W as usize {
                            *row.add(x) = 0x8000_0000
                                | ((f as u32 * 7 + x as u32) & 0xFF)
                                | (((f as u32 * 3 + y as u32) & 0xFF) << 8)
                                | ((((x * y) as u32 >> 1) & 0xFF) << 16);
                        }
                    }
                }
                surface.attach(Some(&s.buffer), 0, 0);
                surface.damage(0, 0, W, H);
                surface.commit();
                let _ = conn.flush();
                *s.commit_at.lock().unwrap() = Some(Instant::now());
                submitted += 1;
            }
        }
        let inflight = submitted.saturating_sub(shared.releases.load(Ordering::Relaxed));
        max_inflight = max_inflight.max(inflight);
    }

    let elapsed = start.elapsed().as_secs_f64();
    // let stragglers release so we can see whether they ever do
    let drain = Instant::now();
    while drain.elapsed() < Duration::from_millis(500)
        && shared.releases.load(Ordering::Relaxed) < submitted
    {
        std::thread::sleep(Duration::from_millis(10));
    }
    println!(
        "SUBMITTED={submitted} DROPPED_BUSY={dropped_busy} RELEASED={} SECS={elapsed:.3} ACHIEVED_FPS={:.1}",
        shared.releases.load(Ordering::Relaxed),
        submitted as f64 / elapsed
    );
    println!(
        "HOLD_MS mean={:.1} max={:.1} n={hold_n}  MAX_INFLIGHT={max_inflight}",
        if hold_n > 0 { hold_sum_ms / hold_n as f64 } else { 0.0 },
        hold_max_ms
    );
    println!(
        "RESOURCES fd {}->{}, RSS_kib {}->{}, final_release_in_drain={}ms",
        fd_baseline,
        fd_count(),
        rss_baseline,
        rss_kib(),
        drain.elapsed().as_millis()
    );
    if let Some(e) = shared.error.lock().unwrap().as_ref() {
        println!("ERROR={e}");
    }
    let _ = &slots; // slots (and their fds) stay alive until here
    println!("MEM_LEN_TOTAL={} bytes ({} KiB)", depth * size, depth * size / 1024);
}
