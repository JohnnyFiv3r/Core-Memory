"""Backward-compat shim. Canonical location: core_memory.runtime.flush.flush_state."""
from core_memory.runtime.flush.flush_state import (  # noqa: F401
    flush_state_file,
    read_flush_state,
    write_flush_state,
    upsert_process_flush_checkpoint_bead,
)
