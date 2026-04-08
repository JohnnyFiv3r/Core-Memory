from __future__ import annotations

import json
from pathlib import Path

from core_memory.policy.incidents import load_incidents
from core_memory.schema.normalization import (
    PUBLIC_CATALOG_BEAD_TYPES,
    normalize_relation_type,
    relation_kind,
)


def build_catalog(root: Path) -> dict:
    idx_file = root / ".beads" / "index.json"
    idx_data = {}
    if idx_file.exists():
        try:
            idx_data = json.loads(idx_file.read_text(encoding="utf-8")) or {}
        except Exception:
            idx_data = {}

    beads = idx_data.get("beads") or {}
    associations = idx_data.get("associations") or []

    topics = set()
    relation_types = set()

    for b in beads.values():
        for t in (b.get("tags") or []):
            ts = str(t)
            if ts and " " not in ts:
                topics.add(ts)

    # Canonical sourcing: relation types come from association records.
    for a in associations:
        if not isinstance(a, dict):
            continue
        rt = str(a.get("relationship") or a.get("rel") or "")
        if rt:
            rel = normalize_relation_type(rt)
            if relation_kind(rel) == "canonical":
                relation_types.add(rel)

    # Transitional fallback only if no association records available.
    if not relation_types:
        for b in beads.values():
            for l in (b.get("links") or []):
                if isinstance(l, dict):
                    rt = str(l.get("type") or "")
                    if rt:
                        rel = normalize_relation_type(rt)
                        if relation_kind(rel) == "canonical":
                            relation_types.add(rel)

    incidents = [str(r.get("incident_id") or "") for r in load_incidents(root) if str(r.get("incident_id") or "")]
    return {
        "intents": ["remember", "causal", "what_changed", "when", "other"],
        "bead_types": sorted(PUBLIC_CATALOG_BEAD_TYPES),
        "relation_types": sorted(relation_types),
        "incident_ids": sorted(set(incidents)),
        "topic_keys": sorted(topics),
    }
