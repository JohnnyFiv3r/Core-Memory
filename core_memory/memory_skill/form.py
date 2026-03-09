from __future__ import annotations

# Compatibility shim (P7C deprecation marker).
# Primary module: core_memory.retrieval.search_form
# Status: deprecated shim; retained for migration safety.

LEGACY_SHIM = True
SHIM_REPLACEMENT = "core_memory.retrieval.search_form"

from core_memory.retrieval.search_form import get_search_form

__all__ = ["get_search_form", "LEGACY_SHIM", "SHIM_REPLACEMENT"]
