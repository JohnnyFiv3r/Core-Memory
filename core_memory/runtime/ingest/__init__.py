from importlib import import_module
from typing import Any


_EXPORT_MODULES = {
    "SourceEventMapping": ".source_events",
    "SourceEventRule": ".source_events",
    "ingest_document_reference": ".external_evidence",
    "ingest_external_evidence": ".external_evidence",
    "ingest_operational_event": ".external_evidence",
    "ingest_source_event": ".source_events",
    "ingest_state_assertion": ".external_evidence",
    "ingest_structured_observation": ".external_evidence",
    "resolve_external_bead_type": ".external_evidence",
}


__all__ = [
    "SourceEventMapping",
    "SourceEventRule",
    "ingest_document_reference",
    "ingest_external_evidence",
    "ingest_operational_event",
    "ingest_source_event",
    "ingest_state_assertion",
    "ingest_structured_observation",
    "resolve_external_bead_type",
]


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
