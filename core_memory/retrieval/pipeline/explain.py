from __future__ import annotations


def build_explain(snapped: dict, snap_decisions: dict, warnings: list[str], retrieval_debug: dict) -> dict:
    labels = []
    for v in (snap_decisions or {}).values():
        if isinstance(v, dict):
            labels.append(str(v.get("confidence_label") or "low"))
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    labels.append(str(x.get("confidence_label") or "low"))
    lvl = "low"
    if labels and all(x == "high" for x in labels):
        lvl = "high"
    elif labels and any(x in {"high", "medium"} for x in labels):
        lvl = "medium"

    return {
        "snapped_query": snapped,
        "snap_decisions": snap_decisions,
        "snap_confidence": lvl,
        "warnings": warnings,
        "retrieval": retrieval_debug,
    }
