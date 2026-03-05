from __future__ import annotations


def build_explain(snapped: dict, snap_decisions: dict, warnings: list[str], retrieval_debug: dict) -> dict:
    return {
        "snapped_query": snapped,
        "snap_decisions": snap_decisions,
        "warnings": warnings,
        "retrieval": retrieval_debug,
    }
