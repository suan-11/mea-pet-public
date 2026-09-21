//! Probe A: minimal ABI surface matching how meapet/desktop/wayland_layer.py
//! calls liblayer_shell_shim.so through ctypes.

use std::os::raw::c_void;

#[no_mangle]
pub extern "C" fn probe_add(a: i32, b: i32) -> i32 {
    a.wrapping_add(b)
}

/// Mimics `layer_update_pixels(ctx, buf, w, h)`: ctypes passes `(c_ubyte*N)`
/// as POINTER(c_ubyte); we must be able to read `w*h*4` bytes safely.
#[no_mangle]
pub extern "C" fn probe_sum_bytes(buf: *const u8, n: i32) -> u64 {
    if buf.is_null() || n <= 0 {
        return 0;
    }
    let slice = unsafe { std::slice::from_raw_parts(buf, n as usize) };
    slice.iter().fold(0u64, |acc, b| acc.wrapping_add(*b as u64))
}

/// Mimics opaque handle passing: `layer_create_context` -> c_void_p -> back in.
#[no_mangle]
pub extern "C" fn probe_make_ctx() -> *mut c_void {
    let b: Box<u64> = Box::new(0xC0FFEE);
    Box::into_raw(b) as *mut c_void
}

#[no_mangle]
pub extern "C" fn probe_use_ctx(ctx: *mut c_void) -> u64 {
    if ctx.is_null() {
        return 0;
    }
    unsafe { *(ctx as *mut u64) }
}

#[no_mangle]
pub extern "C" fn probe_destroy_ctx(ctx: *mut c_void) {
    if !ctx.is_null() {
        unsafe {
            drop(Box::from_raw(ctx as *mut u64));
        }
    }
}
