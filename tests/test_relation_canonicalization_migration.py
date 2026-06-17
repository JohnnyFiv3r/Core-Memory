import tempfile
from pathlib import Path

from core_memory.persistence import events
from core_memory.persistence.relation_migration import canonicalize_associations_for_store
from core_memory.persistence.store import MemoryStore


def _assoc_by_id(index: dict, assoc_id: str) -> dict:
    for assoc in index.get("associations") or []:
        if assoc.get("id") == assoc_id:
            return assoc
    return {}


def test_inverse_relation_migration_dry_run_apply_and_rebuild():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = MemoryStore(td)
        blocked = store.add_bead(type="context", title="Blocked", summary=["blocked"])
        blocker = store.add_bead(type="context", title="Blocker", summary=["blocker"])
        old = store.add_bead(type="context", title="Old", summary=["old"])
        new = store.add_bead(type="context", title="New", summary=["new"])
        older = store.add_bead(type="context", title="Older", summary=["older"])
        newer = store.add_bead(type="context", title="Newer", summary=["newer"])
        effect = store.add_bead(type="context", title="Effect", summary=["effect"])
        cause = store.add_bead(type="context", title="Cause", summary=["cause"])

        legacy = [
            {"id": "assoc-cause", "source_bead": effect, "target_bead": cause, "relationship": "caused_by"},
            {"id": "assoc-block", "source_bead": blocked, "target_bead": blocker, "relationship": "blocked_by"},
            {"id": "assoc-super", "source_bead": old, "target_bead": new, "relationship": "superseded_by"},
            {"id": "assoc-follow", "source_bead": newer, "target_bead": older, "relationship": "follows"},
        ]
        index_path = root / ".beads" / "index.json"
        index = store._read_json(index_path)
        index["associations"] = legacy
        index.setdefault("stats", {})["total_associations"] = len(legacy)
        store._write_json(index_path, index)
        for assoc in legacy:
            events.event_association_created(root, assoc)

        dry = canonicalize_associations_for_store(td, apply=False)
        assert dry["ok"] is True
        assert dry["matched_count"] == 4
        assert _assoc_by_id(store._read_json(index_path), "assoc-block")["relationship"] == "blocked_by"

        applied = canonicalize_associations_for_store(td, apply=True)
        assert applied["ok"] is True
        assert applied["applied_count"] == 4

        current = store._read_json(index_path)
        causal = _assoc_by_id(current, "assoc-cause")
        assert (causal["source_bead"], causal["target_bead"], causal["relationship"]) == (cause, effect, "causes")

        block = _assoc_by_id(current, "assoc-block")
        assert (block["source_bead"], block["target_bead"], block["relationship"]) == (blocker, blocked, "blocks")
        assert block["endpoints_swapped"] is True

        super_edge = _assoc_by_id(current, "assoc-super")
        assert (super_edge["source_bead"], super_edge["target_bead"], super_edge["relationship"]) == (new, old, "supersedes")

        temporal = _assoc_by_id(current, "assoc-follow")
        assert (temporal["source_bead"], temporal["target_bead"], temporal["relationship"]) == (older, newer, "precedes")

        canonicalized_events = [
            ev for ev in events.iter_events(root)
            if ev.get("event_type") == events.EVENT_ASSOCIATION_CANONICALIZED
        ]
        assert len(canonicalized_events) == 4

        rebuilt = events.rebuild_index(root)
        rebuilt_causal = _assoc_by_id(rebuilt, "assoc-cause") or {}
        assert (rebuilt_causal.get("source_bead"), rebuilt_causal.get("target_bead"), rebuilt_causal.get("relationship")) == (
            cause,
            effect,
            "causes",
        )
        assert (_assoc_by_id(rebuilt, "assoc-block") or {}).get("relationship") == "blocks"
        assert (_assoc_by_id(rebuilt, "assoc-super") or {}).get("relationship") == "supersedes"
        assert (_assoc_by_id(rebuilt, "assoc-follow") or {}).get("relationship") == "precedes"
