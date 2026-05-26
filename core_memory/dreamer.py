"""Backward-compat shim. Canonical location: core_memory.runtime.dreamer.analysis."""
from core_memory.runtime.dreamer.analysis import (  # noqa: F401
    RELATIONSHIP_TYPES,
    load_index,
    get_promoted_beads,
    extract_mechanism,
    compute_distance,
    score_association,
    run_analysis,
    record_association,
    prompt_template,
    _load_seen_state,
    _append_seen,
    _pair_key,
)
