//! Probe B: standalone Rust Wayland client (own connection, no Qt) that
//! replicates the live code path of liblayer_shell_shim.so:
//!   bind globals -> bare surface -> layer OVERLAY -> empty input region
//!   -> shm pixel upload -> configure ack -> sustained frame commits.

use std::ffi::CString;
use std::os::fd::{AsFd, FromRawFd, OwnedFd};
use std::ptr;
use std::time::{Duration, Instant};

use wayland_client::globals::{registry_queue_init, GlobalList, GlobalListContents};
use wayland_client::protocol::wl_buffer::WlBuffer;
use wayland_client::protocol::wl_compositor::WlCompositor;
use wayland_client::protocol::wl_output::WlOutput;
use wayland_client::protocol::wl_region::WlRegion;
use wayland_client::protocol::wl_registry::WlRegistry;
use wayland_client::protocol::wl_shm::{self, Format, WlShm};
use wayland_client::protocol::wl_shm_pool::WlShmPool;
use wayland_client::protocol::wl_surface::WlSurface;
use wayland_client::{Connection, Dispatch, EventQueue, Proxy, QueueHandle, WEnum};
use wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_shell_v1::{Layer, ZwlrLayerShellV1};
use wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_surface_v1::{
    Anchor, Event as LayerEvent, ZwlrLayerSurfaceV1,
};

const W: i32 = 400;
const H: i32 = 400;
const DURATION: Duration = Duration::from_secs(2);

#[derive(Default)]
struct Probe {
    formats: Vec<u32>,
    configured: bool,
    width: u32,
    height: u32,
    released: u64,
}

impl Dispatch<WlRegistry, GlobalListContents> for Probe {
    fn event(_: &mut Probe, _: &WlRegistry, _: <WlRegistry as Proxy>::Event, _: &GlobalListContents, _: &Connection, _: &QueueHandle<Probe>) {}
}
impl Dispatch<WlCompositor, ()> for Probe {
    fn event(_: &mut Probe, _: &WlCompositor, _: <WlCompositor as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Probe>) {}
}
impl Dispatch<WlShm, ()> for Probe {
    fn event(data: &mut Probe, _: &WlShm, event: <WlShm as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Probe>) {
        if let wl_shm::Event::Format { format } = event {
            data.formats.push(format_raw(format));
        }
    }
}
impl Dispatch<ZwlrLayerShellV1, ()> for Probe {
    fn event(_: &mut Probe, _: &ZwlrLayerShellV1, _: <ZwlrLayerShellV1 as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Probe>) {}
}
impl Dispatch<ZwlrLayerSurfaceV1, ()> for Probe {
    fn event(data: &mut Probe, proxy: &ZwlrLayerSurfaceV1, event: LayerEvent, _: &(), _: &Connection, _: &QueueHandle<Probe>) {
        match event {
            LayerEvent::Configure { serial, width, height } => {
                proxy.ack_configure(serial);
                data.configured = true;
                if width > 0 { data.width = width; }
                if height > 0 { data.height = height; }
            }
            _ => {}
        }
    }
}
impl Dispatch<WlBuffer, ()> for Probe {
    fn event(data: &mut Probe, _: &WlBuffer, event: <WlBuffer as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Probe>) {
        if let <WlBuffer as Proxy>::Event::Release = event {
            data.released += 1;
        }
    }
}
impl Dispatch<WlSurface, ()> for Probe {
    fn event(_: &mut Probe, _: &WlSurface, _: <WlSurface as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Probe>) {}
}
impl Dispatch<WlShmPool, ()> for Probe {
    fn event(_: &mut Probe, _: &WlShmPool, _: <WlShmPool as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Probe>) {}
}
impl Dispatch<WlRegion, ()> for Probe {
    fn event(_: &mut Probe, _: &WlRegion, _: <WlRegion as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Probe>) {}
}
impl Dispatch<WlOutput, ()> for Probe {
    fn event(_: &mut Probe, _: &WlOutput, _: <WlOutput as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Probe>) {}
}

fn format_raw(f: WEnum<Format>) -> u32 {
    match f {
        WEnum::Value(Format::Argb8888) => 0,
        WEnum::Value(Format::Xrgb8888) => 1,
        WEnum::Value(Format::Abgr8888) => 0x3432_4241,
        WEnum::Value(Format::Xbgr8888) => 0x3432_4258,
        WEnum::Value(Format::Rgba8888) => 0x3432_4952,
        WEnum::Value(Format::Rgbx8888) => 0x3432_5852,
        WEnum::Value(Format::Bgra8888) => 0x4152_4742,
        WEnum::Value(Format::Bgrx8888) => 0x4231_5852,
        WEnum::Value(other) => { let _ = other; 0xFFFF_FFFF }
        WEnum::Unknown(raw) => raw,
        _ => 0xFFFF_FFFF,
    }
}

// ---- memfd + mmap, mirroring layer_shell_c.c Phase 2 ----
fn shm_alloc(size: usize) -> (OwnedFd, *mut u8) {
    unsafe {
        let name = CString::new("probe-px").unwrap();
        let fd = libc::syscall(libc::SYS_memfd_create, name.as_ptr(), libc::MFD_CLOEXEC) as i32;
        assert!(fd >= 0, "memfd_create failed");
        assert_eq!(libc::ftruncate(fd, size as libc::off_t), 0, "ftruncate failed");
        let addr = libc::mmap(
            ptr::null_mut(), size,
            libc::PROT_READ | libc::PROT_WRITE, libc::MAP_SHARED, fd, 0,
        );
        assert_ne!(addr, libc::MAP_FAILED, "mmap failed");
        (OwnedFd::from_raw_fd(fd), addr as *mut u8)
    }
}

fn main() {
    let t0 = Instant::now();
    let conn = match Connection::connect_to_env() {
        Ok(c) => c,
        Err(e) => { println!("CONNECT=fail {e:?}"); std::process::exit(1); }
    };
    let (globals, mut queue): (GlobalList, EventQueue<Probe>) =
        match registry_queue_init::<Probe>(&conn) {
            Ok(v) => v,
            Err(e) => { println!("REGISTRY=fail {e:?}"); std::process::exit(1); }
        };
    let qh = queue.handle();

    let has_layer_shell = globals.contents().clone_list().iter().any(|g| g.interface == "zwlr_layer_shell_v1");
    println!("GLOBAL zwlr_layer_shell_v1={has_layer_shell}");
    if !has_layer_shell { std::process::exit(2); }

    let compositor: WlCompositor = globals.bind(&qh, 1..=4, ()).expect("compositor");
    let shm: WlShm = globals.bind(&qh, 1..=1, ()).expect("shm");
    let layer_shell: ZwlrLayerShellV1 = globals.bind(&qh, 1..=4, ()).expect("layer_shell");

    let mut state = Probe::default();
    queue.roundtrip(&mut state).expect("roundtrip"); // collect shm formats
    println!("SHM_FORMATS={:?}", state.formats);

    let surface = compositor.create_surface(&qh, ());
    let ls = layer_shell.get_layer_surface(&surface, Option::<&WlOutput>::None, Layer::Overlay, "probe".to_string(), &qh, ());
    ls.set_size(W as u32, H as u32);
    ls.set_anchor(Anchor::Top | Anchor::Left);
    ls.set_margin(40, 0, 0, 40);
    ls.set_exclusive_zone(0);

    // empty input region -> pass-through (same trick as layer_shell_c.c)
    let region = compositor.create_region(&qh, ());
    surface.set_input_region(Some(&region));
    region.destroy();
    surface.commit();

    // wait for configure
    let deadline = Instant::now() + Duration::from_secs(3);
    while !state.configured && Instant::now() < deadline {
        let _ = queue.blocking_dispatch(&mut state);
    }
    println!("CONFIGURED={} size={}x{} setup_ms={:.1}",
        state.configured, state.width, state.height, t0.elapsed().as_secs_f64() * 1e3);
    if !state.configured { std::process::exit(3); }

    // sustained frame loop, one fresh memfd buffer per frame (as C does today)
    let stride = (W * 4) as usize;
    let size = stride * H as usize;
    let mut frames: u64 = 0;
    let mut _keep: Vec<WlShmPool> = Vec::new();
    let mut _keepmap: Vec<(*mut u8, usize)> = Vec::new();
    let start = Instant::now();
    while start.elapsed() < DURATION {
        let f = frames as u32;
        let (fd, data) = shm_alloc(size);
        unsafe {
            for y in 0..H as usize {
                let row = data.add(y * stride) as *mut u32;
                for x in 0..W as usize {
                    *row.add(x) = 0x8000_0000
                        | ((f as u32 * 3 + x as u32) & 0xFF)
                        | ((((y as u32) << 3) & 0xFF) << 8)
                        | (((x * y) as u32 & 0xFF) << 16);
                }
            }
        }
        let pool = shm.create_pool(fd.as_fd(), size as i32, &qh, ());
        let buffer = pool.create_buffer(0, W, H, stride as i32, Format::Abgr8888, &qh, ());
        surface.attach(Some(&buffer), 0, 0);
        surface.damage(0, 0, W, H);
        surface.commit();
        conn.flush().ok();
        let _ = queue.dispatch_pending(&mut state);
        _keep.push(pool);          // probe leaks buffers until exit
        _keepmap.push((data, size));
        frames += 1;
    }
    let secs = start.elapsed().as_secs_f64();
    println!("FRAMES={frames} SECS={secs:.3} FPS={:.1} RELEASED={}",
        frames as f64 / secs, state.released);
}
