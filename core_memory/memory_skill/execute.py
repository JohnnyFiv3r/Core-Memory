"""Legacy shim for moved retrieval pipeline execute module."""

from core_memory.retrieval.pipeline import execute as _impl

# Re-export collaborator symbols so legacy tests/callers can patch this module.
build_catalog = _impl.build_catalog
snap_form = _impl.snap_form
search_typed = _impl.search_typed
build_explain = _impl.build_explain
_load_beads = _impl._load_beads
evaluate_confidence_next = _impl.evaluate_confidence_next


def execute_request(request: dict, root: str = "./memory", explain: bool = True) -> dict:
    # Sync collaborators into moved implementation so patches applied to this
    # legacy module still affect execution behavior, without leaking globals.
    old = {
        "build_catalog": _impl.build_catalog,
        "snap_form": _impl.snap_form,
        "search_typed": _impl.search_typed,
        "build_explain": _impl.build_explain,
        "_load_beads": _impl._load_beads,
    }
    _impl.build_catalog = build_catalog
    _impl.snap_form = snap_form
    _impl.search_typed = search_typed
    _impl.build_explain = build_explain
    _impl._load_beads = _load_beads
    try:
        return _impl.execute_request(request=request, root=root, explain=explain)
    finally:
        _impl.build_catalog = old["build_catalog"]
        _impl.snap_form = old["snap_form"]
        _impl.search_typed = old["search_typed"]
        _impl.build_explain = old["build_explain"]
        _impl._load_beads = old["_load_beads"]


__all__ = [
    "execute_request",
    "evaluate_confidence_next",
    "_load_beads",
    "build_catalog",
    "snap_form",
    "search_typed",
    "build_explain",
]
