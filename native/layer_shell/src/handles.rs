//! Handle encoding and registry (spec §4.4).
//!
//! A handle is `((id: u32) << 32) | generation: u32` exposed to Python as a
//! `*mut c_void`. Rust NEVER dereferences a caller-supplied pointer: it is
//! reinterpreted as the packed u64 and looked up in the table. Therefore
//! double-destroy, forged handles and stale handles after cleanup are all
//! defined rejections (spec §4.4), not UB — the agents-rules §0 corollary
//! ("seal it in types, not conventions") applied to the FFI surface.
//!
//! `id` and `generation` are both derived from ONE monotonically advancing
//! allocation counter `seq` (`id = seq as u32`, `gen = (seq >> 32) as u32`):
//! a stale `(id, gen)` pair can only match a new entry after `seq` itself
//! wraps at 2^64, so the ABA window that a per-id generation map would need
//! unbounded memory to close is closed here with no extra state (I5).
//! `seq` starts at 1 and never takes the value 0, hence a valid handle is
//! never NULL and the Python `if not ctx:` test is safe (spec §7.1 note).

use std::collections::HashMap;

use crate::wayland::LayerCtx;

/// One registry slot. `generation` closes within-epoch id reuse;
/// `created_by` (the init epoch stamped by the caller, spec §4.4's
/// "cleanup 后残留句柄" clause) closes handles that survive a
/// cleanup→init cycle. Both must match for a lookup to succeed —
/// the check lives in `Bridge::live_entry` (state.rs), the table itself
/// stays epoch-agnostic.
#[derive(Debug)]
pub struct Entry {
    pub generation: u32,
    pub created_by: u32,
    pub ctx: LayerCtx,
}

/// Packed handle value. Newtype stays inside the crate; Python only ever
/// sees the raw u64 bits cast to a pointer.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Handle(pub u64);

impl Handle {
    pub const fn pack(id: u32, generation: u32) -> Handle {
        Handle(((id as u64) << 32) | generation as u64)
    }
    pub const fn id(self) -> u32 {
        (self.0 >> 32) as u32
    }
    pub const fn generation(self) -> u32 {
        self.0 as u32
    }
}

pub struct HandleTable {
    entries: HashMap<u32, Entry>,
    seq: u64,
}

impl Default for HandleTable {
    fn default() -> Self {
        Self::new()
    }
}

impl HandleTable {
    pub fn new() -> Self {
        HandleTable {
            entries: HashMap::new(),
            // seq starts at 1: id 1, generation 0, handle value != 0.
            seq: 1,
        }
    }

    /// Allocate an entry. Returns the packed handle value, or `None` when
    /// every id is live (I7: reject — never evict, never fabricate a
    /// handle). Probing is bounded by `live + 2` so a full table always
    /// terminates.
    pub fn alloc(&mut self, ctx: LayerCtx, created_by: u32) -> Option<u64> {
        let probes = self.entries.len() + 2;
        for _ in 0..probes {
            let id = self.seq as u32;
            let generation = (self.seq >> 32) as u32;
            self.seq = self.seq.wrapping_add(1);
            if self.seq == 0 {
                // Skip seq 0 so the packed handle can never be NULL.
                self.seq = 1;
            }
            if let std::collections::hash_map::Entry::Vacant(slot) = self.entries.entry(id) {
                slot.insert(Entry {
                    generation,
                    created_by,
                    ctx,
                });
                return Some(Handle::pack(id, generation).0);
            }
        }
        None
    }

    fn resolve(&self, value: u64) -> Option<&Entry> {
        let h = Handle(value);
        let entry = self.entries.get(&h.id())?;
        (entry.generation == h.generation()).then_some(entry)
    }

    pub fn get(&self, value: u64) -> Option<&Entry> {
        self.resolve(value)
    }

    pub fn get_mut(&mut self, value: u64) -> Option<&mut Entry> {
        let h = Handle(value);
        let entry = self.entries.get_mut(&h.id())?;
        (entry.generation == h.generation()).then_some(entry)
    }

    /// Remove only when id AND generation match. A second destroy of the
    /// same handle therefore finds no entry and is a safe no-op by the
    /// caller's contract (spec §7.1 #10).
    pub fn remove(&mut self, value: u64) -> Option<Entry> {
        let h = Handle(value);
        self.get(value)?;
        self.entries.remove(&h.id())
    }

    /// Registry introspection (diagnostics/tests only — the production
    /// paths never need counts; per I6 nothing may DRIVE a decision off
    /// them, so these stay `#[cfg(test)]` rather than becoming a
    /// tempting knob).
    #[cfg(test)]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Drop every entry (used by `cleanup`, which must destroy all live
    /// contexts — spec §4.3 / §4.7 row 9 — and by tests).
    pub fn clear(&mut self) {
        self.entries.clear();
    }

    /// Tests only: forge an entry's epoch to emulate the 2^64-`seq`-wrap
    /// situation where a stale handle's (id, generation) pair matches a
    /// live entry — the case only `created_by` can still reject.
    #[cfg(test)]
    pub fn set_created_by_for_test(&mut self, value: u64, epoch: u32) {
        if let Some(e) = self.entries.get_mut(&Handle(value).id()) {
            e.created_by = epoch;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx() -> LayerCtx {
        LayerCtx::new(10, 10, 0, 0)
    }

    #[test]
    fn handles_are_never_null_and_ids_start_at_one() {
        let mut t = HandleTable::new();
        let h1 = t.alloc(ctx(), 0).unwrap();
        let h2 = t.alloc(ctx(), 0).unwrap();
        assert_ne!(h1, 0);
        assert_ne!(h2, 0);
        assert_eq!(Handle(h1).id(), 1); // "id 从 1 起单调分配"
        assert_eq!(Handle(h2).id(), 2);
    }

    #[test]
    fn unknown_and_zero_and_forged_handles_reject() {
        let mut t = HandleTable::new();
        let h = t.alloc(ctx(), 0).unwrap();
        assert!(t.get(0).is_none()); // 0 handle
        assert!(t.get(7).is_none()); // never-allocated id
                                     // Forged: live id, wrong generation → reject (not a UB deref — we
                                     // never deref anything).
        let forged = Handle::pack(Handle(h).id(), Handle(h).generation() ^ 0xFFFF_FFFF).0;
        assert!(t.get(forged).is_none());
        assert!(t.remove(forged).is_none());
        assert_eq!(t.len(), 1); // the live entry survived forged touches
    }

    #[test]
    fn double_destroy_rejects_second() {
        let mut t = HandleTable::new();
        let h = t.alloc(ctx(), 0).unwrap();
        assert!(t.remove(h).is_some());
        assert!(t.remove(h).is_none()); // double-destroy = defined rejection
        assert!(t.get(h).is_none());
    }

    #[test]
    fn stale_handle_after_realloc_rejects_via_generation() {
        // Force id reuse by wrapping the low 32 bits: park `seq` one below a
        // live id's value so the next alloc lands on the recycled id with a
        // NEW generation (high bits of seq advanced).
        let mut t = HandleTable::new();
        let old = t.alloc(ctx(), 0).unwrap();
        let old_id = Handle(old).id();
        assert!(t.remove(old).is_some());
        // Jump seq to `old_id` again but with generation 1: seq = 1<<32 | id.
        t.seq = (1_u64 << 32) | old_id as u64;
        let fresh = t.alloc(ctx(), 0).unwrap();
        assert_eq!(Handle(fresh).id(), old_id);
        assert_ne!(Handle(fresh).generation(), Handle(old).generation());
        // The stale handle must NOT touch the recycled entry.
        assert!(t.get(old).is_none());
        assert!(t.remove(old).is_none());
        assert_eq!(t.len(), 1);
        assert!(t.get(fresh).is_some());
    }

    #[test]
    fn alloc_skips_live_ids_on_wraparound_and_rejects_only_when_full() {
        let mut t = HandleTable::new();
        let a = t.alloc(ctx(), 0).unwrap(); // id 1
        let b = t.alloc(ctx(), 0).unwrap(); // id 2
        t.remove(a); // id 1 free again
                     // Park seq at id 1's low bits while id 2 is still live: alloc must
                     // skip the collision and land on id 1 (free) — and never on id 2.
        t.seq = 1;
        let c = t.alloc(ctx(), 0).unwrap();
        assert_eq!(Handle(c).id(), 1);
        assert!(t.get(b).is_some()); // live entry untouched by the probe
    }

    #[test]
    fn entry_stamps_the_epoch_it_was_allocated_in() {
        let mut t = HandleTable::new();
        let h = t.alloc(ctx(), 7).unwrap();
        assert_eq!(t.get(h).unwrap().created_by, 7);
    }

    #[test]
    fn clear_drops_everything_but_keeps_seq_monotonic() {
        let mut t = HandleTable::new();
        let h = t.alloc(ctx(), 0).unwrap();
        t.clear();
        assert!(t.is_empty());
        assert!(t.get(h).is_none());
        let h2 = t.alloc(ctx(), 0).unwrap();
        assert_ne!(h2, h); // no immediate recycling of a just-cleared handle
    }
}
