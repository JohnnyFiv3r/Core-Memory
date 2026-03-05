from __future__ import annotations

import json
from pathlib import Path

from core_memory.models import BeadType
from core_memory.incidents import load_incidents


def build_catalog(root: Path) -> dict:
    idx_file = root / ".beads" / "index.json"
    beads = {}
    if idx_file.exists():
        try:
            beads = (json.loads(idx_file.read_text(encoding="utf-8")) or {}).get("beads") or {}
        except Exception:
            beads = {}

    topics = set()
    relation_types = set()
    for b in beads.values():
        for t in (b.get("tags") or []):
            ts = str(t)
            if ts and " " not in ts:
                topics.add(ts)
        for l in (b.get("links") or []):
            if isinstance(l, dict):
                rt = str(l.get("type") or "")
                if rt:
                    relation_types.add(rt)

    incidents = [str(r.get("incident_id") or "") for r in load_incidents(root) if str(r.get("incident_id") or "")]
    return {
        "intents": ["remember", "causal", "what_changed", "when", "other"],
        "bead_types": sorted([e.value for e in BeadType]),
        "relation_types": sorted(relation_types),
        "incident_ids": sorted(set(incidents)),
        "topic_keys": sorted(topics),
    }
