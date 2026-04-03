from __future__ import annotations

import json
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.graph.api import causal_traverse


def test_legacy_missing_confidence_association_traverses(tmp_path: Path):
    s = MemoryStore(root=str(tmp_path))
    a = s.add_bead(type="lesson", title="benchmark lesson", summary=["x"], session_id="s1")
    b = s.add_bead(type="decision", title="postgres decision", summary=["y"], session_id="s1")

    # Write association in legacy shape (no confidence field)
    idx_path = Path(tmp_path) / ".beads" / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    idx["associations"].append(
        {
            "id": "assoc-legacy",
            "type": "association",
            "source_bead": a,
            "target_bead": b,
            "relationship": "supports",
            "explanation": "legacy no confidence",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")

    out = causal_traverse(Path(tmp_path), anchor_ids=[a], max_depth=3, max_chains=5)
    assert out.get("ok") is True
    assert len(out.get("chains") or []) >= 1

