from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io_utils import append_jsonl, atomic_write_json

STRUCTURAL_RELS = {"caused_by", "supports", "derived_from", "supersedes", "superseded_by", "contradicts", "resolves"}


def _paths(root: Path) -> tuple[Path, Path, Path]:
    beads_dir = root / ".beads"
    events_dir = beads_dir / "events"
    return events_dir / "edges.jsonl", beads_dir / "bead_graph.json", beads_dir / "index.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _edge_identity(src_id: str, dst_id: str, rel: str, klass: str) -> str:
    raw = f"{src_id}|{dst_id}|{rel}|{klass}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _edge_id(src_id: str, dst_id: str, rel: str, klass: str) -> str:
    return f"edge-{_edge_identity(src_id, dst_id, rel, klass)}"


def add_structural_edge(root: Path, *, src_id: str, dst_id: str, rel: str, created_by: str = "system", evidence: list[dict] | None = None) -> dict:
    event = {
        "event": "edge_add",
        "edge_id": _edge_id(src_id, dst_id, rel, "structural"),
        "src_id": src_id,
        "dst_id": dst_id,
        "rel": rel,
        "class": "structural",
        "immutable": True,
        "created_at": _now(),
        "created_by": created_by,
        "evidence": evidence or [],
    }
    edges_file, _, _ = _paths(root)
    append_jsonl(edges_file, event)
    return event


def add_semantic_edge(root: Path, *, src_id: str, dst_id: str, rel: str, w: float, created_by: str = "system", evidence: list[dict] | None = None) -> dict:
    w = max(0.0, min(1.0, float(w)))
    now = _now()
    event = {
        "event": "edge_add",
        "edge_id": _edge_id(src_id, dst_id, rel, "semantic"),
        "src_id": src_id,
        "dst_id": dst_id,
        "rel": rel,
        "class": "semantic",
        "immutable": False,
        "created_at": now,
        "created_by": created_by,
        "evidence": evidence or [],
        "w": w,
        "last_reinforced_at": now,
        "reinforcement_count": 0,
    }
    edges_file, _, _ = _paths(root)
    append_jsonl(edges_file, event)
    return event


def update_semantic_edge(root: Path, *, edge_id: str, w: float, reinforcement_count: int, last_reinforced_at: str | None = None) -> dict:
    event = {
        "event": "edge_update",
        "edge_id": edge_id,
        "w": max(0.0, min(1.0, float(w))),
        "last_reinforced_at": last_reinforced_at or _now(),
        "reinforcement_count": max(0, int(reinforcement_count)),
        "updated_at": _now(),
    }
    edges_file, _, _ = _paths(root)
    append_jsonl(edges_file, event)
    return event


def deactivate_semantic_edge(root: Path, *, edge_id: str, reason: str = "decayed_below_threshold") -> dict:
    event = {
        "event": "edge_deactivate",
        "edge_id": edge_id,
        "deactivated_at": _now(),
        "reason": reason,
    }
    edges_file, _, _ = _paths(root)
    append_jsonl(edges_file, event)
    return event


def _iter_events(edges_file: Path):
    if not edges_file.exists():
        return
    with open(edges_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _normalize_links(v) -> list[dict]:
    out: list[dict] = []
    if isinstance(v, list):
        for row in v:
            if not isinstance(row, dict):
                continue
            rel = str(row.get("type") or "").strip()
            dst = str(row.get("bead_id") or row.get("id") or "").strip()
            if rel and dst:
                out.append({"rel": rel, "dst_id": dst})
    elif isinstance(v, dict):
        for rel, val in v.items():
            if isinstance(val, list):
                for dst in val:
                    d = str(dst or "").strip()
                    if d:
                        out.append({"rel": str(rel), "dst_id": d})
            else:
                d = str(val or "").strip()
                if d:
                    out.append({"rel": str(rel), "dst_id": d})
    return out


def _relation_map_path() -> Path:
    return Path(__file__).parent / "data" / "structural_relation_map.json"


def _load_structural_relation_map() -> dict[str, str]:
    p = _relation_map_path()
    if not p.exists():
        return {r: r for r in sorted(STRUCTURAL_RELS)}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            out = {}
            for k, v in data.items():
                sk = str(k or "").strip()
                sv = str(v or "").strip()
                if sk and sv:
                    out[sk] = sv
            return out or {r: r for r in sorted(STRUCTURAL_RELS)}
    except Exception:
        pass
    return {r: r for r in sorted(STRUCTURAL_RELS)}


def _sync_associations_to_links(index: dict, rel_map: dict[str, str]) -> tuple[int, int]:
    beads = index.get("beads") or {}
    assocs = index.get("associations") or []
    scanned = 0
    added = 0
    for a in assocs:
        if not isinstance(a, dict):
            continue
        rel0 = str(a.get("relationship") or "").strip()
        rel = rel_map.get(rel0, rel0)
        if rel not in STRUCTURAL_RELS:
            continue
        edge_class = str(a.get("edge_class") or "").strip().lower()
        if edge_class in {"derived", "weak", "auto"}:
            continue
        src = str(a.get("source_bead") or "").strip()
        dst = str(a.get("target_bead") or "").strip()
        if not src or not dst or src not in beads or dst not in beads:
            continue
        scanned += 1
        links = beads[src].setdefault("links", [])
        if not any(isinstance(l, dict) and str(l.get("type") or "") == rel and str(l.get("bead_id") or "") == dst for l in links):
            links.append({"type": rel, "bead_id": dst, "source": "association_sync"})
            added += 1
    return scanned, added


def sync_structural_pipeline(root: Path, *, apply: bool = False, strict: bool = False) -> dict:
    """Deterministic pipeline: associations -> links -> immutable structural edges -> graph snapshot."""
    edges_file, _, index_file = _paths(root)
    if not index_file.exists():
        return {"ok": False, "error": "index_missing"}

    rel_map = _load_structural_relation_map()
    index = json.loads(index_file.read_text(encoding="utf-8"))

    scanned_assoc, links_added = _sync_associations_to_links(index, rel_map)

    if apply and links_added > 0:
        atomic_write_json(index_file, index)

    existing_struct = set()
    for e in _iter_events(edges_file) or []:
        if str(e.get("event") or "") != "edge_add":
            continue
        if str(e.get("class") or "") != "structural":
            continue
        existing_struct.add((str(e.get("src_id") or ""), str(e.get("dst_id") or ""), str(e.get("rel") or "")))

    beads = index.get("beads") or {}
    missing_edges = []
    for src, b in sorted(beads.items()):
        for l in _normalize_links((b or {}).get("links")):
            rel = rel_map.get(str(l.get("rel") or ""), str(l.get("rel") or ""))
            dst = str(l.get("dst_id") or "")
            if rel not in STRUCTURAL_RELS or not dst:
                continue
            key = (str(src), dst, rel)
            if key not in existing_struct:
                missing_edges.append({"src_id": str(src), "dst_id": dst, "rel": rel})

    applied_edges = 0
    if apply:
        for m in missing_edges:
            add_structural_edge(root, src_id=m["src_id"], dst_id=m["dst_id"], rel=m["rel"], created_by="system", evidence=[{"reason": "sync_structural_pipeline"}])
            applied_edges += 1

    g = build_graph(root, write_snapshot=apply)

    # invariants report (post-index state)
    idx2 = json.loads(index_file.read_text(encoding="utf-8"))
    beads2 = idx2.get("beads") or {}
    assocs2 = idx2.get("associations") or []
    missing_link_from_association = 0
    for a in assocs2:
        if not isinstance(a, dict):
            continue
        rel0 = str(a.get("relationship") or "").strip()
        rel = rel_map.get(rel0, rel0)
        if rel not in STRUCTURAL_RELS:
            continue
        src = str(a.get("source_bead") or "").strip()
        dst = str(a.get("target_bead") or "").strip()
        if src not in beads2 or dst not in beads2:
            continue
        links = beads2[src].get("links") or []
        if not any(isinstance(l, dict) and str(l.get("type") or "") == rel and str(l.get("bead_id") or "") == dst for l in links):
            missing_link_from_association += 1

    edge_head = g.get("edge_head") or {}
    head_struct = set()
    for e in edge_head.values():
        if str(e.get("class") or "") == "structural":
            head_struct.add((str(e.get("src_id") or ""), str(e.get("dst_id") or ""), str(e.get("rel") or "")))

    missing_graph_head_from_edge = 0
    if apply:
        for m in missing_edges:
            if (m["src_id"], m["dst_id"], m["rel"]) not in head_struct:
                missing_graph_head_from_edge += 1

    report = {
        "ok": True,
        "apply": bool(apply),
        "strict": bool(strict),
        "associations_scanned": scanned_assoc,
        "links_added": links_added,
        "missing_edge_from_link": len(missing_edges),
        "edges_applied": applied_edges,
        "invariants": {
            "missing_link_from_association": missing_link_from_association,
            "missing_graph_head_from_edge": missing_graph_head_from_edge,
        },
    }

    if strict and (missing_link_from_association > 0 or (apply and missing_graph_head_from_edge > 0)):
        report["ok"] = False
        report["error"] = "structural_invariant_violation"
    return report


def backfill_structural_edges(root: Path) -> dict:
    """Backfill structural edge_add events from current index explicit links/associations.

    Safety hardening:
    - never convert derived/weak association rows into structural edges
    - for association-based backfill, require non-derived edge_class and at least one endpoint
      in candidate/promoted status.
    """
    edges_file, _, index_file = _paths(root)
    edges_file.parent.mkdir(parents=True, exist_ok=True)
    if not index_file.exists():
        return {"ok": True, "added": 0}

    index = json.loads(index_file.read_text(encoding="utf-8"))
    existing_keys: set[str] = set()
    for e in _iter_events(edges_file) or []:
        if str(e.get("event")) != "edge_add":
            continue
        k = _edge_identity(str(e.get("src_id") or ""), str(e.get("dst_id") or ""), str(e.get("rel") or ""), str(e.get("class") or ""))
        existing_keys.add(k)

    added = 0

    for bead_id, bead in sorted((index.get("beads") or {}).items()):
        for link in _normalize_links(bead.get("links")):
            rel = str(link.get("rel") or "").strip()
            if rel not in STRUCTURAL_RELS:
                continue
            dst = str(link.get("dst_id") or "").strip()
            key = _edge_identity(bead_id, dst, rel, "structural")
            if key in existing_keys:
                continue
            append_jsonl(
                edges_file,
                {
                    "event": "edge_add",
                    "edge_id": f"edge-{key}",
                    "src_id": bead_id,
                    "dst_id": dst,
                    "rel": rel,
                    "class": "structural",
                    "immutable": True,
                    "created_at": _now(),
                    "created_by": "system",
                    "evidence": [],
                },
            )
            existing_keys.add(key)
            added += 1

    bead_status = {str(bid): str((b or {}).get("status") or "") for bid, b in (index.get("beads") or {}).items()}
    for assoc in (index.get("associations") or []):
        rel = str(assoc.get("relationship") or "").strip()
        if rel not in STRUCTURAL_RELS:
            continue
        # hardening: do not auto-upgrade weak/derived associations to structural
        edge_class = str(assoc.get("edge_class") or "").strip().lower()
        if edge_class in {"derived", "weak", "auto"}:
            continue

        src = str(assoc.get("source_bead") or "").strip()
        dst = str(assoc.get("target_bead") or "").strip()
        if not src or not dst:
            continue

        src_st = bead_status.get(src, "")
        dst_st = bead_status.get(dst, "")
        if src_st not in {"candidate", "promoted"} and dst_st not in {"candidate", "promoted"}:
            continue

        key = _edge_identity(src, dst, rel, "structural")
        if key in existing_keys:
            continue
        append_jsonl(
            edges_file,
            {
                "event": "edge_add",
                "edge_id": f"edge-{key}",
                "src_id": src,
                "dst_id": dst,
                "rel": rel,
                "class": "structural",
                "immutable": True,
                "created_at": _now(),
                "created_by": "system",
                "evidence": [{"reason": "association_backfill", "association_id": assoc.get("id")}],
            },
        )
        existing_keys.add(key)
        added += 1

    return {"ok": True, "added": added}


def infer_structural_edges(root: Path, *, min_confidence: float = 0.9, apply: bool = False) -> dict:
    """Deterministic structural inference with strict safety gates.

    Rules:
    - only rel in {supports, derived_from}
    - at least one endpoint status in {candidate, promoted}
    - require deterministic provenance (shared source_turn_ids)
    - confidence must meet threshold
    """
    _, _, index_file = _paths(root)
    if not index_file.exists():
        return {"ok": True, "candidates": 0, "applied": 0}

    index = json.loads(index_file.read_text(encoding="utf-8"))
    beads = index.get("beads") or {}
    by_turn: dict[str, list[dict]] = {}
    for b in beads.values():
        for tid in (b.get("source_turn_ids") or []):
            by_turn.setdefault(str(tid), []).append(b)

    candidates: list[dict] = []
    for tid, rows in by_turn.items():
        if len(rows) < 2:
            continue
        # pairwise within turn, deterministic ordering
        rows = sorted(rows, key=lambda x: str(x.get("id") or ""))
        for i, a in enumerate(rows):
            for b in rows[i + 1 :]:
                a_id = str(a.get("id") or "")
                b_id = str(b.get("id") or "")
                if not a_id or not b_id:
                    continue
                a_type = str(a.get("type") or "")
                b_type = str(b.get("type") or "")
                a_st = str(a.get("status") or "")
                b_st = str(b.get("status") or "")
                if a_st not in {"candidate", "promoted"} and b_st not in {"candidate", "promoted"}:
                    continue

                rel = None
                conf = 0.0
                if a_type == "evidence" and b_type in {"decision", "lesson", "outcome"}:
                    rel = "supports"; conf = 0.95; src, dst = a_id, b_id
                elif b_type == "evidence" and a_type in {"decision", "lesson", "outcome"}:
                    rel = "supports"; conf = 0.95; src, dst = b_id, a_id
                elif a_type == "lesson" and b_type == "decision":
                    rel = "derived_from"; conf = 0.9; src, dst = b_id, a_id
                elif b_type == "lesson" and a_type == "decision":
                    rel = "derived_from"; conf = 0.9; src, dst = a_id, b_id
                else:
                    continue

                if conf < float(min_confidence):
                    continue

                candidates.append({
                    "src_id": src,
                    "dst_id": dst,
                    "rel": rel,
                    "confidence": conf,
                    "turn_id": tid,
                })

    # dedupe
    seen = set()
    uniq = []
    for c in candidates:
        k = (c["src_id"], c["dst_id"], c["rel"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)

    applied = 0
    if apply:
        for c in uniq:
            add_structural_edge(
                root,
                src_id=c["src_id"],
                dst_id=c["dst_id"],
                rel=c["rel"],
                created_by="system",
                evidence=[{"turn_id": c.get("turn_id"), "confidence": c.get("confidence"), "reason": "deterministic_inference"}],
            )
            applied += 1

    return {"ok": True, "candidates": len(uniq), "applied": applied, "sample": uniq[:50]}


def build_graph(root: Path, *, write_snapshot: bool = True, semantic_active_k: int = 50) -> dict:
    edges_file, graph_file, index_file = _paths(root)
    node_meta: dict[str, dict[str, Any]] = {}
    if index_file.exists():
        index = json.loads(index_file.read_text(encoding="utf-8"))
        for bid, b in (index.get("beads") or {}).items():
            node_meta[bid] = {
                "type": b.get("type"),
                "created_at": b.get("created_at"),
                "session_id": b.get("session_id"),
                "status": b.get("status"),
            }

    edge_head: dict[str, dict] = {}
    warnings: list[str] = []

    for e in _iter_events(edges_file) or []:
        ev = str(e.get("event") or "")
        eid = str(e.get("edge_id") or "")
        if not eid:
            continue
        if ev == "edge_add":
            edge_head[eid] = dict(e)
            edge_head[eid]["active"] = True
            continue
        if eid not in edge_head:
            continue
        current = edge_head[eid]
        klass = str(current.get("class") or "")
        if ev == "edge_update":
            if klass == "structural" or bool(current.get("immutable")):
                warnings.append(f"ignored_update_on_immutable:{eid}")
                continue
            current["w"] = e.get("w", current.get("w", 0.0))
            current["last_reinforced_at"] = e.get("last_reinforced_at", current.get("last_reinforced_at"))
            current["reinforcement_count"] = e.get("reinforcement_count", current.get("reinforcement_count", 0))
            current["updated_at"] = e.get("updated_at")
        elif ev == "edge_deactivate":
            if klass == "structural" or bool(current.get("immutable")):
                warnings.append(f"ignored_deactivate_on_immutable:{eid}")
                continue
            current["active"] = False
            current["deactivated_at"] = e.get("deactivated_at")
            current["deactivate_reason"] = e.get("reason")

    adj_structural_out: dict[str, list[str]] = {}
    sem_all_out: dict[str, list[str]] = {}

    for eid, edge in sorted(edge_head.items(), key=lambda kv: kv[0]):
        src = str(edge.get("src_id") or "")
        if not src:
            continue
        klass = str(edge.get("class") or "")
        if klass == "structural":
            adj_structural_out.setdefault(src, []).append(eid)
        elif klass == "semantic" and bool(edge.get("active", True)):
            sem_all_out.setdefault(src, []).append(eid)

    # Active semantic cache: top-K per source by weight, tie-break by edge_id
    adj_semantic_out: dict[str, list[str]] = {}
    evicted_semantic = 0
    for src, eids in sem_all_out.items():
        ranked = sorted(
            eids,
            key=lambda eid: (float((edge_head.get(eid) or {}).get("w") or 0.0), eid),
            reverse=True,
        )
        kept = ranked[: max(0, int(semantic_active_k))]
        adj_semantic_out[src] = kept
        for eid in ranked[max(0, int(semantic_active_k)) :]:
            # emit deactivation only if still active
            if bool((edge_head.get(eid) or {}).get("active", True)):
                deactivate_semantic_edge(root, edge_id=eid, reason="evicted_from_active_cache")
                evicted_semantic += 1

    # Simple centrality (degree) from active graph heads.
    centrality: dict[str, int] = {}
    for e in edge_head.values():
        src = str(e.get("src_id") or "")
        dst = str(e.get("dst_id") or "")
        if src:
            centrality[src] = centrality.get(src, 0) + 1
        if dst:
            centrality[dst] = centrality.get(dst, 0) + 1

    out = {
        "ok": True,
        "nodes": len(node_meta),
        "edges_total": len(edge_head),
        "structural_edges": sum(1 for e in edge_head.values() if str(e.get("class") or "") == "structural"),
        "semantic_edges_active": sum(1 for e in edge_head.values() if str(e.get("class") or "") == "semantic" and bool(e.get("active", True))),
        "semantic_edges_inactive": sum(1 for e in edge_head.values() if str(e.get("class") or "") == "semantic" and not bool(e.get("active", True))),
        "semantic_active_k": int(semantic_active_k),
        "semantic_evicted": int(evicted_semantic),
        "warnings": warnings,
        "node_meta": node_meta,
        "adj_structural_out": adj_structural_out,
        "adj_semantic_out": adj_semantic_out,
        "edge_head": edge_head,
        "node_centrality": centrality,
    }

    if write_snapshot:
        atomic_write_json(graph_file, out)
        out["snapshot"] = str(graph_file)

    return out


EDGE_WEIGHT = {
    "supports": 1.0,
    "causes": 0.9,
    "derived_from": 0.8,
    "precedent": 0.7,
    "contradicts": 0.6,
    "supersedes": 0.6,
}
NODE_IMPORTANCE = {
    "decision": 1.0,
    "outcome": 0.95,
    "precedent": 0.9,
    "evidence": 0.9,
    "lesson": 0.85,
    "design_principle": 0.85,
    "failed_hypothesis": 0.75,
    "goal": 0.7,
    "checkpoint": 0.55,
    "context": 0.4,
    "tool_call": 0.4,
}


def _recency_factor(ts: str, half_life_days: float = 30.0) -> float:
    if not ts:
        return 1.0
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
        return 0.5 ** (age_days / max(1e-6, half_life_days))
    except ValueError:
        return 1.0


def causal_traverse(
    root: Path,
    anchor_ids: list[str],
    max_depth: int = 4,
    max_chains: int = 50,
    semantic_expansion_hops: int = 1,
    semantic_w_min: float = 0.35,
) -> dict:
    g = build_graph(root, write_snapshot=False)
    edge_head = g.get("edge_head") or {}
    node_meta = g.get("node_meta") or {}
    node_centrality = g.get("node_centrality") or {}
    max_cent = max([int(v) for v in node_centrality.values()] or [1])
    s_adj = g.get("adj_structural_out") or {}
    sem_adj = g.get("adj_semantic_out") or {}

    chains = []

    def expand_from(start: str, semantic_used: list[str] | None = None):
        semantic_used = semantic_used or []
        stack = [(start, [], 0, 0.0)]  # node, path_edges, depth, score
        while stack:
            node, path, depth, score = stack.pop()
            if depth >= max_depth:
                continue
            for eid in s_adj.get(node, []):
                e = edge_head.get(eid) or {}
                dst = str(e.get("dst_id") or "")
                rel = str(e.get("rel") or "")
                if not dst or dst in [p.get("src") for p in path] or dst in [p.get("dst") for p in path]:
                    continue
                ni = NODE_IMPORTANCE.get(str((node_meta.get(dst) or {}).get("type") or "").lower(), 0.3)
                ew = EDGE_WEIGHT.get(rel, 0.4)
                rf = _recency_factor(str((node_meta.get(dst) or {}).get("created_at") or ""))
                cent = float(node_centrality.get(dst, 0)) / float(max_cent or 1)
                cent_factor = 0.85 + 0.15 * max(0.0, min(1.0, cent))
                step = ew * ni * rf * cent_factor
                p2 = path + [{"edge_id": eid, "src": node, "dst": dst, "rel": rel, "class": "structural", "step_score": round(step, 6)}]
                s2 = score + step
                chains.append({
                    "score": round(s2, 6),
                    "path": [start] + [x["dst"] for x in p2],
                    "edges": p2,
                    "semantic_edge_ids": list(semantic_used),
                })
                stack.append((dst, p2, depth + 1, s2))

    for a in anchor_ids[:8]:
        expand_from(str(a), [])

    def grounded(c):
        types = [str((node_meta.get(bid) or {}).get("type") or "") for bid in c.get("path") or []]
        return ("decision" in types or "precedent" in types) and any(t in {"evidence", "lesson", "outcome"} for t in types)

    grounded_chains = [c for c in chains if grounded(c)]

    # semantic expansion only if insufficient grounding
    if not grounded_chains and semantic_expansion_hops > 0:
        sem_anchors = []
        for a in anchor_ids[:8]:
            for eid in sem_adj.get(str(a), []):
                e = edge_head.get(eid) or {}
                if float(e.get("w") or 0.0) < semantic_w_min:
                    continue
                dst = str(e.get("dst_id") or "")
                if not dst:
                    continue
                sem_anchors.append((dst, str(eid)))
        for a, sem_eid in sem_anchors[:16]:
            expand_from(a, [sem_eid])
        grounded_chains = [c for c in chains if grounded(c)]

    ranked = sorted(grounded_chains if grounded_chains else chains, key=lambda c: (c.get("score", 0.0), len(c.get("path") or [])), reverse=True)
    ranked = ranked[: max(1, int(max_chains))]

    return {
        "ok": True,
        "anchors": anchor_ids,
        "grounded": bool(grounded_chains),
        "chains": ranked,
    }


def decay_semantic_edges(
    root: Path,
    *,
    w_drop: float = 0.08,
    half_life_days: float = 14.0,
) -> dict:
    g = build_graph(root, write_snapshot=False)
    edge_head = g.get("edge_head") or {}
    updated = 0
    deactivated = 0
    now = datetime.now(timezone.utc)

    for eid, e in edge_head.items():
        if str(e.get("class") or "") != "semantic" or not bool(e.get("active", True)):
            continue
        w = float(e.get("w") or 0.0)
        ts = str(e.get("last_reinforced_at") or e.get("created_at") or "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
        except ValueError:
            age_days = 0.0
        decayed = w * (0.5 ** (age_days / max(1e-6, half_life_days)))
        if decayed < w_drop:
            deactivate_semantic_edge(root, edge_id=eid, reason="decayed_below_threshold")
            deactivated += 1
        else:
            update_semantic_edge(
                root,
                edge_id=eid,
                w=decayed,
                reinforcement_count=int(e.get("reinforcement_count") or 0),
                last_reinforced_at=str(e.get("last_reinforced_at") or e.get("created_at") or _now()),
            )
            updated += 1

    return {"ok": True, "updated": updated, "deactivated": deactivated}


def reinforce_semantic_edges(root: Path, edge_ids: list[str], alpha: float = 0.15) -> dict:
    g = build_graph(root, write_snapshot=False)
    edge_head = g.get("edge_head") or {}
    reinforced = 0
    for eid in edge_ids:
        e = edge_head.get(str(eid)) or {}
        if str(e.get("class") or "") != "semantic" or not bool(e.get("active", True)):
            continue
        w = float(e.get("w") or 0.0)
        w2 = w + float(alpha) * (1.0 - w)
        update_semantic_edge(
            root,
            edge_id=str(eid),
            w=w2,
            reinforcement_count=int(e.get("reinforcement_count") or 0) + 1,
            last_reinforced_at=_now(),
        )
        reinforced += 1
    return {"ok": True, "reinforced": reinforced}


def graph_stats(root: Path) -> dict:
    k = int(os.environ.get("CORE_MEMORY_SEMANTIC_ACTIVE_K", "50"))
    g = build_graph(root, write_snapshot=False, semantic_active_k=k)
    cent = g.get("node_centrality") or {}
    top_cent = sorted(cent.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return {
        "ok": True,
        "nodes": g.get("nodes", 0),
        "edges_total": g.get("edges_total", 0),
        "structural_edges": g.get("structural_edges", 0),
        "semantic_edges_active": g.get("semantic_edges_active", 0),
        "semantic_edges_inactive": g.get("semantic_edges_inactive", 0),
        "warnings": len(g.get("warnings") or []),
        "top_central_nodes": top_cent,
    }
