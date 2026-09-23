//! Pixel format conversion (spec §6.2).
//!
//! Input contract: the Python side already did
//! `convertToFormat(QImage.Format_RGBA8888)`, so `src` is `[R,G,B,A]` with
//! stride `w*4` (no row padding) and length `w*h*4`.
//!
//! P0 supports exactly two real layouts:
//! - `WL_SHM_FORMAT_ABGR8888` (`'AB24'` = 0x3432_4241): shm memory layout is
//!   also `[R,G,B,A]` → whole-buffer copy, zero conversion (niri advertises
//!   it; measurement §3B).
//! - `WL_SHM_FORMAT_ARGB8888` (0): shm memory layout `[B,G,R,A]` → swap R/B
//!   per pixel, G/A pass through.
//!
//! Neither path premultiplies or unpremultiplies. Basis (spec §6.2): the
//! wl_shm protocol TEXT says pre-multiplied alpha, but wlroots/niri treats
//! these formats as straight alpha, and the reference implementation only
//! copies/swaps (`convert_rgba_to_format`, layer_shell_c.c) with correct
//! output on niri long-term (L3 corroboration). Registered as a known
//! boundary for strict-premultiply compositors — out of P0 scope.

/// wl_shm fourcc for ARGB8888 (shm memory `[B,G,R,A]`). Protocol value 0.
pub const WL_SHM_FORMAT_ARGB8888: u32 = 0;
/// wl_shm fourcc for ABGR8888 (`'AB24'`, shm memory `[R,G,B,A]`).
pub const WL_SHM_FORMAT_ABGR8888: u32 = 0x3432_4241;

/// Bytes per pixel: both formats §6.2 allows are 32 bpp. The ONE stride
/// truth — the ring sizes its buffers with it (spec §4.5) and the submit path
/// sizes the caller's readable slice with it (§6.3). Restating `4` at either
/// site is agents-rules §7's "knob that isn't read" in literal form.
pub const BYTES_PER_PIXEL: usize = 4;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PixelFormat {
    Abgr8888,
    Argb8888,
}

impl PixelFormat {
    pub fn fourcc(self) -> u32 {
        match self {
            PixelFormat::Abgr8888 => WL_SHM_FORMAT_ABGR8888,
            PixelFormat::Argb8888 => WL_SHM_FORMAT_ARGB8888,
        }
    }
}

/// Auto choice per spec §6.2: prefer ABGR8888 (zero conversion) when the
/// compositor advertises it, else fall back to ARGB8888.
pub fn choose_format(abgr_supported: bool) -> PixelFormat {
    if abgr_supported {
        PixelFormat::Abgr8888
    } else {
        PixelFormat::Argb8888
    }
}

/// Resolve the `force` argument of `layer_update_pixels_with_format`.
///
/// Value domain (spec §6.2): `{0, ABGR8888, ARGB8888}`. Because the
/// ARGB8888 fourcc IS 0, an explicit "force ARGB" is indistinguishable from
/// auto — this matches the reference implementation exactly
/// (`force_fmt ? force_fmt : choose_upload_format(state)`,
/// layer_shell_c.c:329: 0 takes the auto path even when ABGR is supported).
///
/// `None` → the caller must NOT submit the frame and must record the sticky
/// error `unsupported force format 0x…` (spec §6.2, I7: never silently pick
/// a plausible-looking format — the debug channel must respond, not guess).
pub fn resolve_force(force: u32, abgr_supported: bool) -> Option<PixelFormat> {
    match force {
        0 => Some(choose_format(abgr_supported)),
        WL_SHM_FORMAT_ABGR8888 => Some(PixelFormat::Abgr8888),
        _ => None,
    }
}

/// Convert `src` (RGBA8888, `[R,G,B,A]`) into `dst` (shm layout for `fmt`).
/// Pure function; the T1 golden samples pin it byte-for-byte (spec §9 T1).
///
/// # Panics
/// If `src.len() != dst.len()` or the length is not a multiple of 4. The
/// commit path sizes both slices by the LOGICAL size — never by the
/// caller-claimed `(w,h)` (spec §6.3, §4.8-5: trusting declared sizes is how
/// you get out-of-bounds reads or row-slipped images). At the FFI boundary a
/// panic becomes poison + failure form (I4), never UB.
pub fn convert_rgba(src: &[u8], dst: &mut [u8], fmt: PixelFormat) {
    assert_eq!(src.len(), dst.len(), "convert_rgba: length mismatch");
    assert_eq!(src.len() % 4, 0, "convert_rgba: length not a multiple of 4");
    match fmt {
        PixelFormat::Abgr8888 => dst.copy_from_slice(src),
        PixelFormat::Argb8888 => {
            for (s, d) in src.chunks_exact(4).zip(dst.chunks_exact_mut(4)) {
                d[0] = s[2]; // B
                d[1] = s[1]; // G
                d[2] = s[0]; // R
                d[3] = s[3]; // A
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- T1 golden samples (spec §6.2 / §9 T1 row) ----
    // Every src pixel below has R != B so a missed or doubled swap cannot
    // pass (spec §4.8-6: R/B exchange is a "plausible-looking image" defect).

    #[test]
    fn one_by_one_abgr_is_identity() {
        let src = [0x11u8, 0x22, 0x33, 0xFF]; // R=0x11 G=0x22 B=0x33 A=0xFF
        let mut dst = [0u8; 4];
        convert_rgba(&src, &mut dst, PixelFormat::Abgr8888);
        assert_eq!(dst, [0x11, 0x22, 0x33, 0xFF]);
    }

    #[test]
    fn one_by_one_argb_swaps_r_b_only() {
        let src = [0x11u8, 0x22, 0x33, 0xFF];
        let mut dst = [0u8; 4];
        convert_rgba(&src, &mut dst, PixelFormat::Argb8888);
        assert_eq!(dst, [0x33, 0x22, 0x11, 0xFF]); // shm [B,G,R,A]
    }

    #[test]
    fn odd_width_three_by_one() {
        // 3x1: odd width, stride w*4 with no padding (spec §6.2).
        let src = [
            0xDE, 0x01, 0x02, 0x80, // px0: R=0xDE B=0x02 A=0x80
            0x03, 0xF0, 0xF1, 0x0F, // px1: R=0x03 B=0xF1
            0x7F, 0x7F, 0x00, 0xFF, // px2: R=0x7F B=0x00
        ];
        let mut dst = [0u8; 12];
        convert_rgba(&src, &mut dst, PixelFormat::Argb8888);
        assert_eq!(
            dst,
            [
                0x02, 0x01, 0xDE, 0x80, //
                0xF1, 0xF0, 0x03, 0x0F, //
                0x00, 0x7F, 0x7F, 0xFF, //
            ]
        );
        convert_rgba(&src, &mut dst, PixelFormat::Abgr8888);
        assert_eq!(dst, src);
    }

    #[test]
    fn alpha_extremes_pass_through_unchanged() {
        // A=0x00 and A=0xFF on both paths: alpha is never touched (no
        // premultiply — see module header).
        let src = [0xAA, 0xBB, 0xCC, 0x00, 0x11, 0x22, 0x33, 0xFF];
        let mut dst = [0u8; 8];
        convert_rgba(&src, &mut dst, PixelFormat::Argb8888);
        assert_eq!(dst[3], 0x00);
        assert_eq!(dst[7], 0xFF);
        assert_eq!(dst, [0xCC, 0xBB, 0xAA, 0x00, 0x33, 0x22, 0x11, 0xFF]);
        convert_rgba(&src, &mut dst, PixelFormat::Abgr8888);
        assert_eq!(dst[3], 0x00);
        assert_eq!(dst[7], 0xFF);
    }

    #[test]
    fn abgr_path_is_whole_buffer_copy() {
        // 4x2 buffer, zero conversion: byte-for-byte equality.
        let src: Vec<u8> = (0..32u8).collect();
        let mut dst = vec![0u8; 32];
        convert_rgba(&src, &mut dst, PixelFormat::Abgr8888);
        assert_eq!(dst, src);
    }

    #[test]
    fn argb_swap_applied_twice_is_identity() {
        // Catches "swap applied twice" / wrong direction: converting the
        // OUTPUT again as if it were input must return the original.
        let src: Vec<u8> = (0..16u8).collect();
        let mut mid = vec![0u8; 16];
        let mut back = vec![0u8; 16];
        convert_rgba(&src, &mut mid, PixelFormat::Argb8888);
        assert_ne!(mid, src); // R != B pixels must actually move
        convert_rgba(&mid, &mut back, PixelFormat::Argb8888);
        assert_eq!(back, src);
    }

    #[test]
    fn resolve_force_value_domain() {
        // 0 = auto: prefers ABGR when supported, falls back to ARGB.
        assert_eq!(resolve_force(0, true), Some(PixelFormat::Abgr8888));
        assert_eq!(resolve_force(0, false), Some(PixelFormat::Argb8888));
        // Explicit ABGR wins even when not advertised (force is a debug
        // channel; the compositor rejects unknown formats, we do not second
        // guess it here).
        assert_eq!(
            resolve_force(WL_SHM_FORMAT_ABGR8888, false),
            Some(PixelFormat::Abgr8888)
        );
        // ARGB8888 fourcc == 0 == auto (reference-equivalent, layer_shell_c.c:329).
        assert_eq!(
            resolve_force(WL_SHM_FORMAT_ARGB8888, true),
            Some(PixelFormat::Abgr8888)
        );
        // Everything else: frame is dropped + sticky error (spec §6.2).
        assert_eq!(resolve_force(1, true), None);
        assert_eq!(resolve_force(0xDEAD_BEEF, true), None);
        assert_eq!(resolve_force(u32::MAX, false), None);
    }

    #[test]
    fn fourcc_round_trip() {
        assert_eq!(PixelFormat::Abgr8888.fourcc(), 0x3432_4241);
        assert_eq!(PixelFormat::Argb8888.fourcc(), 0);
    }

    #[test]
    #[should_panic]
    fn length_mismatch_panics_instead_of_truncating() {
        // Silent truncation would be "plausible-looking output" (agents-rules
        // §8); the contract is panic → poison at the FFI boundary (I4).
        let src = [0u8; 8];
        let mut dst = [0u8; 4];
        convert_rgba(&src, &mut dst, PixelFormat::Argb8888);
    }
}
