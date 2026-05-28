# V2-P20 Kickoff (Quality Hardening)

Status: Active

## Objective
Address high-priority code quality issues (datetime deprecation, robust model parsing, and serialization consistency) without changing memory architecture.

## Step plan (5)
1. Critical model correctness fixes ✅
2. Graph write locking hardening ✅
3. Graph/schema consistency + API clarity

## Step 2 completion notes
- Added `store_lock(root)` protection for graph edge-log append paths in `core_memory.graph`:
  - structural edge append
  - semantic edge append/update/deactivate
  - semantic top-K eviction deactivation appends during graph build
- This ensures edge-log write ordering/concurrency safety aligns with store/event write surfaces.
4. Exception hygiene pass
5. Sweep + closeout

## Step 1 completion notes
- Replaced deprecated `datetime.utcnow()` defaults with timezone-aware UTC timestamps in `core_memory.models`.
- Hardened `from_dict` for `Bead`, `Association`, and `Event` to ignore unknown keys safely.
- Preserved `detail` in model round-trip validation and added unknown-key tolerance tests.
