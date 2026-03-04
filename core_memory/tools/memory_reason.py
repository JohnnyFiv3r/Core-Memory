from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.graph import causal_traverse, reinforce_semantic_edges
from core_memory.semantic_index import semantic_lookup
from core_memory.archive_index import read_snapshot
from core_memory.store import MemoryStore


def _hydrate_bead(store: MemoryStore, bead_id: str) -> dict[str, Any]:
    idx = store._read_json(store.beads_dir / "index.json")
    bead = (idx.get("beads") or {}).get(bead_id)
    if not bead:
        return {"id": bead_id, "missing": True}

    out = {
        "id": bead.get("id"),
        "type": bead.get("type"),
        "title": bead.get("title"),
        "summary": (bead.get("summary") or [])[:2],
        "session_id": bead.get("session_id"),
        "source_turn_ids": bead.get("source_turn_ids") or [],
        "status": bead.get("status"),
        "archive_ptr": bead.get("archive_ptr"),
    }

    rev = ((bead.get("archive_ptr") or {}).get("revision_id") if isinstance(bead.get("archive_ptr"), dict) else None)
    if rev:
        snap = read_snapshot(store.root, str(rev or ""))
        if snap and isinstance(snap.get("snapshot"), dict):
            ss = snap.get("snapshot") or {}
            out["snapshot_title"] = ss.get("title")
            out["snapshot_summary"] = (ss.get("summary") or [])[:2]
            out["snapshot_session_id"] = ss.get("session_id")
            out["snapshot_turn_ids"] = ss.get("source_turn_ids") or []
    return out


def _choose_anchor(results: list[dict]) -> str | None:
    preferred = []
    for r in results:
        t = str(r.get("type") or "")
        if t in {"decision", "precedent"}:
            preferred.append(r)
    top = preferred[0] if preferred else (results[0] if results else None)
    return str((top or {}).get("bead_id") or "") or None


def memory_reason(query: str, k: int = 8, root: str = "./memory") -> dict:
    root_p = Path(root)
    store = MemoryStore(root)

    sem = semantic_lookup(root_p, query=query, k=max(1, int(k)))
    if not sem.get("ok"):
        return {"ok": False, "error": sem.get("error")}

    sem_results = sem.get("results") or []
    anchors = [str(r.get("bead_id") or "") for r in sem_results if r.get("bead_id")]
    anchor = _choose_anchor(sem_results)
    if anchor and anchor not in anchors:
        anchors = [anchor] + anchors

    trav = causal_traverse(root_p, anchor_ids=anchors[:8], max_depth=4, max_chains=50)
    chains = trav.get("chains") or []
    top = chains[:3]

    used_semantic: list[str] = []
    citations = []
    out_chains = []

    for c in top:
        beads = []
        for bid in c.get("path") or []:
            b = _hydrate_bead(store, str(bid))
            beads.append(b)
            citations.append(
                {
                    "bead_id": b.get("id"),
                    "session_id": b.get("session_id") or b.get("snapshot_session_id"),
                    "turn_ids": b.get("source_turn_ids") or b.get("snapshot_turn_ids") or [],
                    "archive_ptr": b.get("archive_ptr"),
                }
            )
        used_semantic.extend([str(x) for x in (c.get("semantic_edge_ids") or []) if x])
        out_chains.append(
            {
                "score": c.get("score"),
                "path": c.get("path"),
                "edges": c.get("edges"),
                "beads": beads,
            }
        )

    # reinforce only semantic edges that contributed to final selected chains
    used_semantic = sorted(set(used_semantic))
    reinforce_semantic_edges(root_p, used_semantic, alpha=0.15)

    grounded = bool(trav.get("grounded"))
    if grounded and out_chains:
        answer = "I remember this and can ground it causally: "
        first = out_chains[0]
        labels = []
        for b in first.get("beads") or []:
            t = str(b.get("type") or "")
            title = str(b.get("title") or b.get("snapshot_title") or "")
            if t in {"decision", "precedent", "evidence", "lesson", "outcome"} and title:
                labels.append(f"{t}: {title}")
        answer += " | ".join(labels[:4]) if labels else "grounded chain found."
    else:
        answer = "I remember related context, but I don’t have a grounded decision chain for that yet."

    # dedupe citations
    dedup = {}
    for c in citations:
        key = str(c.get("bead_id") or "")
        if key and key not in dedup:
            dedup[key] = c

    return {
        "ok": True,
        "answer": answer,
        "anchor_bead_id": anchor,
        "chains": out_chains,
        "citations": list(dedup.values()),
        "reinforced_semantic_edges": used_semantic,
    }
