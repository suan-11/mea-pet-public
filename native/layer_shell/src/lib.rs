//! MeaPet Linux layer-shell bridge — see ~/.Athena/projects/meapet/working/rust-layer-shell-bridge.md §4–§7 for the contract.
//!
//! WP-E closes the surface: the §7.1 symbol table (exactly 11 `extern "C"`
//! exports, I2), the single panic-capture wrapper (I4, `state::guarded`),
//! the one-connection/one-pump thread model (§4.2/§6.4) and the buffer ring
//! behind `layer_update_pixels` (§4.5) all exist.

mod ffi;
mod format;
mod handles;
mod pump;
mod ring;
mod state;
mod wayland;
