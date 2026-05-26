"""Backward-compat shim. Canonical location: core_memory.integrations.openclaw.runtime."""
from core_memory.integrations.openclaw.runtime import (  # noqa: F401
    resolve_core_session_id,
    coordinator_finalize_hook,
    finalize_and_process_turn,
)
