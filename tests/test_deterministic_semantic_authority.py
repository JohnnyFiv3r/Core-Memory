from __future__ import annotations

import tempfile

from core_memory.association.crawler_contract import apply_crawler_updates
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.passes.decision_pass import run_session_decision_pass


def test_promotion_shadow_does_not_mutate_but_agent_review_does() -> None:
    with tempfile.TemporaryDirectory() as root:
        store = MemoryStore(root)
        bead_id = store.add_bead(
            type="decision",
            title="Adopt typed writes",
            summary=["Typed authoring gives the runtime a stable contract."],
            detail="The agent supplied the decision rationale.",
            because=["The current write path requires explicit semantic provenance."],
            session_id="s1",
            source_turn_ids=["t1"],
        )
        before = dict(store._read_json(store.beads_dir / "index.json")["beads"][bead_id])

        shadow = run_session_decision_pass(
            root=root,
            session_id="s1",
            visible_bead_ids=[bead_id],
            turn_id="t2",
        )
        after_shadow = store._read_json(store.beads_dir / "index.json")["beads"][bead_id]
        assert shadow["advisory_only"] is True
        assert after_shadow.get("promotion_state") == before.get("promotion_state")
        assert after_shadow.get("status") == before.get("status")

        applied = run_session_decision_pass(
            root=root,
            session_id="s1",
            visible_bead_ids=[bead_id],
            turn_id="t2",
            updates={
                "reviewed_beads": [
                    {
                        "bead_id": bead_id,
                        "promotion_state": "promote",
                        "reason": "The agent judges this durable enough for long-horizon recall.",
                    }
                ]
            },
            authorship={"source": "inline_agent", "grounding_hash": "sha256:test"},
        )
        persisted = store._read_json(store.beads_dir / "index.json")["beads"][bead_id]
        assert applied["agent_reviews"]["applied"] == 1
        assert persisted["promotion_state"] == "promoted"
        assert persisted["promotion_authorship"]["source"] == "inline_agent"


def test_missing_association_relationship_is_quarantined_not_preview_filled() -> None:
    with tempfile.TemporaryDirectory() as root:
        store = MemoryStore(root)
        source = store.add_bead(type="decision", title="Source", summary=["shared context"], session_id="s1")
        target = store.add_bead(type="evidence", title="Target", summary=["shared context"], session_id="s1")

        result = apply_crawler_updates(
            root=root,
            session_id="s1",
            updates={
                "associations": [
                    {
                        "source_bead": source,
                        "target_bead": target,
                        "reason_text": "A preview would find lexical overlap.",
                    }
                ]
            },
            visible_bead_ids=[source, target],
        )

        index = store._read_json(store.beads_dir / "index.json")
        assert result["associations_appended"] == 0
        assert result["associations_quarantined"] == 1
        assert index.get("associations") == []
