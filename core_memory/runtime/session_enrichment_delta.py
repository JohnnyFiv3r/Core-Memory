"""Backward-compat shim. Canonical location: core_memory.runtime.session.session_enrichment_delta."""
from core_memory.runtime.session.session_enrichment_delta import (  # noqa: F401
    SCHEMA,
    NORMALIZER_VERSION,
    DELTA_QUARANTINE_PATH,
    DELTA_ROW_LIMITS,
    DELTA_ROW_TYPES,
    build_window_context_ref,
    canonical_session_projection,
    crawler_updates_to_delta,
    delta_to_crawler_updates,
    projections_equal,
    stable_hash,
    write_delta_quarantine,
)
