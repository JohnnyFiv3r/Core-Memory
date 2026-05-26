"""Backward-compat shim. Canonical location: core_memory.runtime.session.session_start_flow."""
from core_memory.runtime.session.session_start_flow import (  # noqa: F401
    find_existing_session_start_bead,
    build_session_start_snapshot,
    process_session_start_impl,
)
