"""
Graph semantic operations: reinforcement, decay, deactivation.

Split from graph.py per Codex Phase 5 readability refactor.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _paths(root: Path):
    beads_dir = root / ".beads"
    events_dir = beads_dir / "events"
    edges_file = events_dir / "graph-edges.jsonl"
    return beads_dir, events_dir, edges_file


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _edge_identity(src_id: str, dst_id: str, rel: str, klass: str) -> str:
    return f"{src_id}|{dst_id}|{rel}|{klass}"


def _edge_id(src_id: str, dst_id: str, rel: str, klass: str) -> str:
    return f"edge-{_edge_identity(src_id, dst_id, rel, klass)}"


def _recency_factor(ts: str, half_life_days: float = 30.0) -> float:
    """Compute recency factor (1.0 at creation, decays over time)."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        return 2 ** (-age_days / half_life_days)
    except Exception:
        return 0.5


def add_semantic_edge(
    root: Path, 
    *, 
    src_id: str, 
    dst_id: str, 
    rel: str, 
    w: float, 
    created_by: str = "system", 
    evidence: list[dict] | None = None
) -> dict:
    """Add a semantic edge with weight."""
    from .io_utils import append_jsonl
    
    beads_dir, events_dir, edges_file = _paths(root)
    edges_file.parent.mkdir(parents=True, exist_ok=True)
    
    edge_id = _edge_id(src_id, dst_id, rel, "semantic")
    now = _now()
    
    edge = {
        "id": edge_id,
        "src": src_id,
        "dst": dst_id,
        "rel": rel,
        "class": "semantic",
        "w": w,
        "created_by": created_by,
        "created_at": now,
        "reinforcement_count": 0,
        "last_reinforced_at": None,
        "evidence": evidence or [],
    }
    
    append_jsonl(edges_file, edge)
    
    return {"ok": True, "edge_id": edge_id}


def update_semantic_edge(
    root: Path, 
    *, 
    edge_id: str, 
    w: float, 
    reinforcement_count: int, 
    last_reinforced_at: str | None = None
) -> dict:
    """Update semantic edge weight and reinforcement stats."""
    from .io_utils import append_jsonl
    
    _, _, edges_file = _paths(root)
    
    if not edges_file.exists():
        return {"ok": False, "error": "no_edges_file"}
    
    # Append update as new event (append-only)
    update = {
        "op": "update",
        "edge_id": edge_id,
        "w": w,
        "reinforcement_count": reinforcement_count,
        "last_reinforced_at": last_reinforced_at or _now(),
        "updated_at": _now(),
    }
    
    append_jsonl(edges_file, update)
    
    return {"ok": True, "edge_id": edge_id}


def deactivate_semantic_edge(
    root: Path, 
    *, 
    edge_id: str, 
    reason: str = "decayed_below_threshold"
) -> dict:
    """Deactivate a semantic edge."""
    from .io_utils import append_jsonl
    
    _, _, edges_file = _paths(root)
    
    if not edges_file.exists():
        return {"ok": False, "error": "no_edges_file"}
    
    deactivation = {
        "op": "deactivate",
        "edge_id": edge_id,
        "reason": reason,
        "deactivated_at": _now(),
    }
    
    append_jsonl(edges_file, deactivation)
    
    return {"ok": True, "edge_id": edge_id}


def decay_semantic_edges(root: Path) -> dict:
    """Apply decay to all semantic edges based on recency and reinforcement."""
    from .store import MemoryStore
    
    memory = MemoryStore(root=str(root))
    _, _, edges_file = _paths(root)
    
    if not edges_file.exists():
        return {"ok": True, "decayed": 0}
    
    # Read current edge states
    edges: dict[str, dict] = {}
    for line in edges_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            eid = event.get("edge_id") or event.get("id")
            if not eid:
                continue
                
            op = event.get("op")
            
            if op == "deactivate":
                edges.pop(eid, None)
            elif op == "update":
                edges[eid] = {
                    **edges.get(eid, {}),
                    "w": event.get("w", 0.5),
                    "reinforcement_count": event.get("reinforcement_count", 0),
                    "last_reinforced_at": event.get("last_reinforced_at"),
                }
            elif event.get("class") == "semantic":
                edges[eid] = event
        except json.JSONDecodeError:
            continue
    
    decayed = 0
    decay_threshold = 0.1
    
    for edge_id, edge in list(edges.items()):
        if edge.get("class") != "semantic":
            continue
            
        # Calculate decay
        created_at = edge.get("created_at", "")
        recency = _recency_factor(created_at)
        
        reinforcement_count = edge.get("reinforce_count", 0)
        last_reinforced = edge.get("last_reinforced_at")
        
        if last_reinforced:
            reinf_recency = _recency_factor(last_reinforced)
        else:
            reinf_recency = recency
        
        # Weight decays based on age, boosted by reinforcement
        base_decay = 0.95
        reinforced_boost = min(0.1, reinforcement_count * 0.02)
        
        new_w = edge.get("w", 0.5) * base_decay * recency + reinforced_boost
        new_w = max(0.0, min(1.0, new_w))
        
        if new_w < decay_threshold:
            deactivate_semantic_edge(root, edge_id=edge_id, reason="decayed_below_threshold")
            decayed += 1
        elif new_w != edge.get("w", 0.5):
            update_semantic_edge(
                root,
                edge_id=edge_id,
                w=new_w,
                reinforcement_count=reinforcement_count,
                last_reinforced_at=last_reinforced,
            )
    
    return {
        "ok": True,
        "total_edges": len(edges),
        "decayed": decayed,
    }


def reinforce_semantic_edges(
    root: Path, 
    edge_ids: list[str], 
    alpha: float = 0.15
) -> dict:
    """Reinforce specific semantic edges (e.g., after recall).
    
    Args:
        root: Memory root path
        edge_ids: List of edge IDs to reinforce
        alpha: Reinforcement strength (0-1)
    """
    from .store import MemoryStore
    
    memory = MemoryStore(root=str(root))
    _, _, edges_file = _paths(root)
    
    if not edges_file.exists():
        return {"ok": False, "error": "no_edges_file"}
    
    # Build current edge state
    edges: dict[str, dict] = {}
    for line in edges_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            eid = event.get("edge_id") or event.get("id")
            if not eid:
                continue
                
            op = event.get("op")
            if op == "deactivate":
                edges.pop(eid, None)
            elif op in ("update", None):
                edges[eid] = event
        except json.JSONDecodeError:
            continue
    
    reinforced = []
    
    for edge_id in edge_ids:
        edge = edges.get(edge_id)
        if not edge or edge.get("class") != "semantic":
            continue
            
        current_w = edge.get("w", 0.5)
        reinforcement_count = edge.get("reinforcement_count", 0)
        
        # Reinforce: increase weight toward 1.0
        new_w = current_w + alpha * (1.0 - current_w)
        new_w = min(1.0, new_w)
        
        update_semantic_edge(
            root,
            edge_id=edge_id,
            w=new_w,
            reinforcement_count=reinforcement_count + 1,
            last_reinforced_at=_now(),
        )
        
        reinforced.append(edge_id)
    
    return {
        "ok": True,
        "requested": len(edge_ids),
        "reinforced": len(reinforced),
        "edge_ids": reinforced,
    }
