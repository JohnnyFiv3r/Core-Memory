"""
Graph structural operations: sync, backfill, inference.

Split from graph.py per Codex Phase 5 readability refactor.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _relation_map_path() -> Path:
    """Path to the relation mapping file."""
    from core_memory import DEFAULT_ROOT
    return Path(DEFAULT_ROOT) / ".beads" / "structural-relation-map.json"


def _load_structural_relation_map() -> dict[str, str]:
    """Load the structural relation mapping for causal link inference."""
    p = _relation_map_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _sync_associations_to_links(index: dict, rel_map: dict[str, str]) -> tuple[int, int]:
    """Sync active associations to bead.links and prune stale association_sync links.

    Returns (updated_count, new_links_count).
    """
    beads = index.get("beads") or {}
    assocs = index.get("associations") or []

    desired_by_src: dict[str, list[tuple[str, str]]] = {}
    for assoc in assocs:
        if not isinstance(assoc, dict):
            continue
        status = str(assoc.get("status") or "active").strip().lower() or "active"
        if status in {"retracted", "superseded", "inactive"}:
            continue
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
        dst = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
        rel = str(assoc.get("relationship") or "").strip()
        if not src or not dst or src not in beads or dst not in beads:
            continue
        desired_by_src.setdefault(src, []).append((rel_map.get(rel, "related"), dst))

    updated = 0
    new_links = 0

    for bead_id, bead in beads.items():
        links = bead.get("links")

        # Normalize legacy dict-form links into list-form for source-tagged sync entries.
        normalized: list[dict[str, Any]] = []
        if isinstance(links, dict):
            for rel, targets in links.items():
                if isinstance(targets, list):
                    for tid in targets:
                        tid_s = str(tid or "").strip()
                        if tid_s:
                            normalized.append({"type": str(rel), "bead_id": tid_s, "source": "legacy_links"})
                else:
                    tid_s = str(targets or "").strip()
                    if tid_s:
                        normalized.append({"type": str(rel), "bead_id": tid_s, "source": "legacy_links"})
        elif isinstance(links, list):
            for row in links:
                if isinstance(row, dict):
                    normalized.append(dict(row))

        preserved = [
            l
            for l in normalized
            if str((l or {}).get("source") or "").strip().lower() != "association_sync"
        ]

        existing_sync = {
            (str((l or {}).get("type") or ""), str((l or {}).get("bead_id") or ""))
            for l in normalized
            if str((l or {}).get("source") or "").strip().lower() == "association_sync"
        }

        desired_pairs = {(rel, dst) for rel, dst in desired_by_src.get(str(bead_id), [])}
        out = list(preserved)
        for rel, dst in sorted(desired_pairs):
            out.append({"type": rel, "bead_id": dst, "source": "association_sync"})
            if (rel, dst) not in existing_sync:
                new_links += 1

        if out != normalized:
            bead["links"] = out
            updated += 1

    return updated, new_links


def _text_tokens(bead: dict) -> set[str]:
    """Extract searchable tokens from bead for causal link inference."""
    title = (bead.get("title") or "").lower()
    summary = " ".join(bead.get("summary") or []).lower()
    because = " ".join(bead.get("because") or []).lower()
    
    combined = f"{title} {summary} {because}"
    tokens = set()
    for word in combined.split():
        word = word.strip(".,!?;:\"'()[]")
        if len(word) >= 3:
            tokens.add(word)
    return tokens


def backfill_causal_links(
    root: Path, 
    *, 
    apply: bool = False, 
    max_per_target: int = 3, 
    min_overlap: int = 2,
    require_shared_turn: bool = True,
    include_bead_ids: list[str] | None = None
) -> dict:
    """Backfill causal links for beads that don't have them.
    
    Args:
        root: Memory root path
        apply: If False, dry-run only
        max_per_target: Max links to create per bead
        min_overlap: Min token overlap for causal inference
        require_shared_turn: Only link beads sharing a source turn
        include_bead_ids: If provided, only process these bead IDs
    """
    from ..persistence.store import MemoryStore
    
    memory = MemoryStore(root=str(root))
    index = memory._read_json(memory.beads_dir / "index.json")
    rel_map = _load_structural_relation_map()
    
    beads = list((index.get("beads") or {}).values())
    
    if include_bead_ids:
        include_set = set(include_bead_ids)
        beads = [b for b in beads if b.get("id") in include_set]
    
    candidates = []
    for bead in beads:
        if bead.get("links"):
            continue
        if bead.get("type") in {"session_start", "session_end"}:
            continue
        candidates.append(bead)
    
    # Group beads by session for shared-turn detection
    session_beads: dict[str, list[dict]] = {}
    for bead in beads:
        sid = str(bead.get("session_id") or "")
        if sid:
            session_beads.setdefault(sid, []).append(bead)
    
    actions = []
    for target in candidates:
        target_tokens = _text_tokens(target)
        target_turns = set(target.get("source_turn_ids") or [])
        
        best_matches = []
        for other in beads:
            if other.get("id") == target.get("id"):
                continue
            if other.get("type") in {"session_start", "session_end"}:
                continue
                
            if require_shared_turn:
                other_turns = set(other.get("source_turn_ids") or [])
                if not target_turns.intersection(other_turns):
                    continue
                    
            other_tokens = _text_tokens(other)
            overlap = len(target_tokens.intersection(other_tokens))
            
            if overlap >= min_overlap:
                best_matches.append({
                    "bead_id": other.get("id"),
                    "overlap": overlap,
                    "title": other.get("title"),
                })
        
        best_matches.sort(key=lambda x: -x["overlap"])
        
        for match in best_matches[:max_per_target]:
            action = {
                "target_id": target.get("id"),
                "link_to": match["bead_id"],
                "relationship": "caused_by",
                "overlap": match["overlap"],
            }
            actions.append(action)
            
            if apply:
                target.setdefault("links", {})["caused_by"] = [match["bead_id"]]
    
    if apply:
        memory._write_json(memory.beads_dir / "index.json", index)
    
    return {
        "ok": True,
        "apply": apply,
        "candidates": len(candidates),
        "actions": len(actions),
        "sample": actions[:20],
    }


def sync_structural_pipeline(root: Path, *, apply: bool = False, strict: bool = False) -> dict:
    """Run the full structural sync pipeline.
    
    Args:
        root: Memory root path
        apply: If False, dry-run only
        strict: If True, require all beads to have links
    """
    from ..persistence.store import MemoryStore
    
    memory = MemoryStore(root=str(root))
    index = memory._read_json(memory.beads_dir / "index.json")
    rel_map = _load_structural_relation_map()
    
    # Step 1: Sync associations to links
    updated, new_links = _sync_associations_to_links(index, rel_map)
    
    # Step 2: Backfill causal links
    backfill_result = backfill_causal_links(root, apply=apply)
    
    if apply:
        memory._write_json(memory.beads_dir / "index.json", index)
    
    # Check for beads still missing links
    missing_links = []
    for bead in (index.get("beads") or {}).values():
        if not bead.get("links") and bead.get("type") not in {"session_start", "session_end"}:
            missing_links.append(bead.get("id"))
    
    return {
        "ok": True,
        "associations_synced": updated,
        "new_links": new_links,
        "backfill_attempted": backfill_result.get("actions", 0),
        "missing_links": len(missing_links) if strict else None,
        "strict_violations": missing_links if strict else [],
    }


def backfill_structural_edges(root: Path) -> dict:
    """Backfill structural edges from events (legacy compatibility)."""
    from ..persistence.store import MemoryStore
    
    memory = MemoryStore(root=str(root))
    
    # Re-run association sync as edge backfill
    index = memory._read_json(memory.beads_dir / "index.json")
    rel_map = _load_structural_relation_map()
    
    updated, new_links = _sync_associations_to_links(index, rel_map)
    
    if updated > 0:
        memory._write_json(memory.beads_dir / "index.json", index)
    
    return {
        "ok": True,
        "edges_backfilled": new_links,
    }


def infer_structural_edges(
    root: Path, 
    *, 
    min_confidence: float = 0.9, 
    apply: bool = False
) -> dict:
    """Infer new structural edges based on content similarity.
    
    Args:
        root: Memory root path
        min_confidence: Minimum confidence for inference
        apply: If False, dry-run only
    """
    from ..persistence.store import MemoryStore
    
    memory = MemoryStore(root=str(root))
    index = memory._read_json(memory.beads_dir / "index.json")
    
    beads = list((index.get("beads") or {}).values())
    candidates = []
    
    for i, bead in enumerate(beads):
        if bead.get("type") in {"session_start", "session_end"}:
            continue
        if bead.get("links"):
            continue
            
        tokens = _text_tokens(bead)
        
        for other in beads[i+1:]:
            if other.get("type") in {"session_start", "session_end"}:
                continue
            if other.get("links"):
                continue
                
            other_tokens = _text_tokens(other)
            overlap = len(tokens.intersection(other_tokens))
            
            # Simple confidence based on overlap ratio
            max_tokens = max(len(tokens), len(other_tokens))
            confidence = overlap / max_tokens if max_tokens > 0 else 0
            
            if confidence >= min_confidence:
                candidates.append({
                    "from": bead.get("id"),
                    "to": other.get("id"),
                    "confidence": confidence,
                    "overlap": overlap,
                })
    
    if apply:
        for c in candidates:
            from_id = c["from"]
            to_id = c["to"]
            
            from_bead = (index.get("beads") or {}).get(from_id)
            to_bead = (index.get("beads") or {}).get(to_id)
            
            if from_bead:
                from_bead.setdefault("links", {})["related"] = [to_id]
            if to_bead:
                to_bead.setdefault("links", {})["related"] = [from_id]
        
        memory._write_json(memory.beads_dir / "index.json", index)
    
    return {
        "ok": True,
        "apply": apply,
        "inferences": len(candidates),
        "sample": candidates[:20],
    }
