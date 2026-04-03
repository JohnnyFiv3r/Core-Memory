import json
import tempfile
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import apply_crawler_turn_updates


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def test_noncanonical_precedes_quarantined_and_deduped():
    with tempfile.TemporaryDirectory() as td:
        s = MemoryStore(td)
        a = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
        b = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])

        payload = {
            "associations": [
                {
                    "source_bead_id": a,
                    "target_bead_id": b,
                    "relationship": "precedes",
                    "reason_text": "temporal ordering",
                    "confidence": 0.77,
                    "provenance": "model_inferred",
                }
            ]
        }

        out1 = apply_crawler_turn_updates(root=td, session_id="s1", visible_bead_ids=[a, b], updates=payload)
        assert out1.get("ok") is True
        assert out1.get("associations_appended") == 0
        assert out1.get("associations_quarantined") == 1

        qpath = Path(out1.get("quarantine_path") or "")
        rows1 = _read_jsonl(qpath)
        assert len(rows1) == 1
        assert rows1[0].get("seen_count") == 1

        out2 = apply_crawler_turn_updates(root=td, session_id="s1", visible_bead_ids=[a, b], updates=payload)
        assert out2.get("ok") is True
        assert out2.get("associations_appended") == 0
        assert out2.get("associations_quarantined") == 1

        rows2 = _read_jsonl(qpath)
        assert len(rows2) == 1
        assert rows2[0].get("seen_count") == 2


def test_missing_reason_or_confidence_quarantined_in_strict_default():
    with tempfile.TemporaryDirectory() as td:
        s = MemoryStore(td)
        a = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
        b = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])

        payload = {
            "associations": [
                {
                    "source_bead_id": a,
                    "target_bead_id": b,
                    "relationship": "supports",
                    "provenance": "model_inferred",
                }
            ]
        }
        out = apply_crawler_turn_updates(root=td, session_id="s1", visible_bead_ids=[a, b], updates=payload)
        assert out.get("ok") is True
        assert out.get("associations_appended") == 0
        assert out.get("associations_quarantined") == 1
