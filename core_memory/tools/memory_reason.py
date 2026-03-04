from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.graph import causal_traverse, reinforce_semantic_edges
from core_memory.semantic_index import semantic_lookup
from core_memory.retrieval.hybrid import hybrid_lookup
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
        "created_at": bead.get("created_at"),
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


def _detect_intent(query: str) -> dict:
    q = (query or "").lower()
    why_cues = ["why did we", "why was", "reason", "because", "rationale"]
    when_cues = ["when did", "what date", "what time", "timeline"]
    changed_cues = ["what changed", "changed about", "supersed", "updated from", "replaced"]
    remember_cues = ["remember", "recall", "what do you remember"]

    scores = {
        "why": sum(1 for c in why_cues if c in q),
        "when": sum(1 for c in when_cues if c in q),
        "what_changed": sum(1 for c in changed_cues if c in q),
        "remember": sum(1 for c in remember_cues if c in q),
    }
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] <= 0:
        return {"intent": "remember", "confidence": 0.35, "scores": scores}
    total = sum(scores.values())
    conf = best[1] / max(1, total)
    return {"intent": best[0], "confidence": round(conf, 3), "scores": scores}


def _chain_signature(chain: dict) -> str:
    p = [str(x) for x in (chain.get("path") or [])]
    e = [f"{str(x.get('src'))}>{str(x.get('rel'))}>{str(x.get('dst'))}" for x in (chain.get("edges") or [])]
    return "|".join(p + e)


def _chain_confidence(chain: dict) -> float:
    score = float(chain.get("score") or 0.0)
    beads = chain.get("beads") or []
    if not beads:
        return 0.0
    types = [str(b.get("type") or "") for b in beads]
    grounded = ("decision" in types or "precedent" in types) and any(t in {"evidence", "lesson", "outcome"} for t in types)
    depth = max(1, len(chain.get("path") or []))
    base = min(1.0, score / max(1.0, float(depth)))
    if grounded:
        base = min(1.0, base + 0.2)
    return round(base, 4)


def _select_diverse_chains(chains: list[dict], top_n: int = 3) -> list[dict]:
    selected = []
    seen_sig = set()
    seen_nodes = set()
    for c in sorted(chains, key=lambda x: float(x.get("score") or 0.0), reverse=True):
        sig = _chain_signature(c)
        if sig in seen_sig:
            continue
        nodes = set([str(x) for x in (c.get("path") or [])])
        overlap = len(nodes.intersection(seen_nodes))
        if selected and overlap >= max(2, len(nodes) // 2):
            continue
        c2 = dict(c)
        c2["confidence"] = _chain_confidence(c2)
        selected.append(c2)
        seen_sig.add(sig)
        seen_nodes.update(nodes)
        if len(selected) >= max(1, int(top_n)):
            break
    return selected


def _collect_citations_from_chains(chains: list[dict]) -> tuple[list[dict], list[str]]:
    citations = []
    used_semantic: list[str] = []
    for c in chains:
        chain_conf = float(c.get("confidence") or 0.0)
        for b in c.get("beads") or []:
            b_type = str(b.get("type") or "")
            grounded_role = b_type in {"decision", "precedent", "evidence", "lesson", "outcome"}
            citations.append(
                {
                    "bead_id": b.get("id"),
                    "type": b_type,
                    "title": b.get("title") or b.get("snapshot_title"),
                    "session_id": b.get("session_id") or b.get("snapshot_session_id"),
                    "turn_ids": b.get("source_turn_ids") or b.get("snapshot_turn_ids") or [],
                    "archive_ptr": b.get("archive_ptr"),
                    "grounded_role": grounded_role,
                    "confidence": round(min(1.0, chain_conf + (0.1 if grounded_role else 0.0)), 4),
                }
            )
        used_semantic.extend([str(x) for x in (c.get("semantic_edge_ids") or []) if x])
    dedup = {}
    for c in citations:
        key = str(c.get("bead_id") or "")
        if key and key not in dedup:
            dedup[key] = c
    return list(dedup.values()), sorted(set(used_semantic))


def _plan_why(store: MemoryStore, root_p: Path, query: str, k: int, debug: bool = False) -> dict:
    sem = hybrid_lookup(root_p, query=query, k=max(1, int(k)))
    if not sem.get("ok"):
        return {"ok": False, "error": sem.get("error")}

    sem_results = sem.get("results") or []
    anchors = [str(r.get("bead_id") or "") for r in sem_results if r.get("bead_id")]
    anchor = _choose_anchor(sem_results)
    if anchor and anchor not in anchors:
        anchors = [anchor] + anchors

    trav = causal_traverse(root_p, anchor_ids=anchors[:8], max_depth=4, max_chains=50)
    chains = trav.get("chains") or []
    hydrated = []
    for c in chains:
        beads = [_hydrate_bead(store, str(bid)) for bid in (c.get("path") or [])]
        hydrated.append({"score": c.get("score"), "path": c.get("path"), "edges": c.get("edges"), "beads": beads, "semantic_edge_ids": c.get("semantic_edge_ids") or []})

    out_chains = _select_diverse_chains(hydrated, top_n=3)
    citations, used_semantic = _collect_citations_from_chains(out_chains)
    reinforce_semantic_edges(root_p, used_semantic, alpha=0.15)

    grounded = bool(trav.get("grounded"))
    if grounded and out_chains:
        first = out_chains[0]
        labels = []
        for b in first.get("beads") or []:
            t = str(b.get("type") or "")
            title = str(b.get("title") or b.get("snapshot_title") or "")
            if t in {"decision", "precedent", "evidence", "lesson", "outcome"} and title:
                labels.append(f"{t}: {title}")
        answer = "I remember this and can ground it causally: " + (" | ".join(labels[:4]) if labels else "grounded chain found.")
    else:
        answer = "I remember related context, but I don’t have a grounded decision chain for that yet."

    out = {
        "ok": True,
        "answer": answer,
        "anchor_bead_id": anchor,
        "chains": out_chains,
        "citations": citations,
        "reinforced_semantic_edges": used_semantic,
    }
    if debug:
        out["retrieval_debug"] = sem
    return out


def _plan_when(store: MemoryStore, root_p: Path, query: str, k: int) -> dict:
    sem = semantic_lookup(root_p, query=query, k=max(1, int(k)))
    if not sem.get("ok"):
        return {"ok": False, "error": sem.get("error")}
    ids = [str(r.get("bead_id") or "") for r in (sem.get("results") or []) if r.get("bead_id")]
    beads = [_hydrate_bead(store, bid) for bid in ids]
    beads = sorted(beads, key=lambda b: str(b.get("created_at") or ""))
    top = beads[:3]
    if top:
        answer = "Timeline context: " + " | ".join([f"{b.get('created_at')}: {b.get('title')}" for b in top])
    else:
        answer = "I remember related context, but I couldn’t establish a clear timeline yet."
    citations, _ = _collect_citations_from_chains([{"beads": top, "semantic_edge_ids": []}])
    c = {"score": 0.0, "path": [b.get("id") for b in top], "edges": [], "beads": top}
    c["confidence"] = _chain_confidence(c)
    return {"ok": True, "answer": answer, "anchor_bead_id": (top[0].get("id") if top else None), "chains": [c], "citations": citations, "reinforced_semantic_edges": []}


def _plan_changed(store: MemoryStore, root_p: Path, query: str, k: int) -> dict:
    sem = semantic_lookup(root_p, query=query, k=max(1, int(k)))
    if not sem.get("ok"):
        return {"ok": False, "error": sem.get("error")}
    idx = store._read_json(store.beads_dir / "index.json")
    beads_map = idx.get("beads") or {}
    ids = [str(r.get("bead_id") or "") for r in (sem.get("results") or []) if r.get("bead_id")]
    chains = []
    for bid in ids[:5]:
        b = beads_map.get(bid) or {}
        links = b.get("links") or []
        supers = []
        for l in links:
            if isinstance(l, dict) and str(l.get("type") or "") in {"supersedes", "superseded_by"}:
                supers.append(str(l.get("bead_id") or ""))
        path = [bid] + [x for x in supers if x]
        beads = [_hydrate_bead(store, x) for x in path]
        chains.append({"score": 0.0, "path": path, "edges": [], "beads": beads, "semantic_edge_ids": []})
    chains = _select_diverse_chains(chains, top_n=3)
    citations, _ = _collect_citations_from_chains(chains)
    answer = "I found change/supersession context in related beads." if chains else "I remember related context, but I don’t have a clear change chain yet."
    return {"ok": True, "answer": answer, "anchor_bead_id": (ids[0] if ids else None), "chains": chains, "citations": citations, "reinforced_semantic_edges": []}


def _plan_remember(store: MemoryStore, root_p: Path, query: str, k: int, debug: bool = False) -> dict:
    sem = hybrid_lookup(root_p, query=query, k=max(1, int(k)))
    if not sem.get("ok"):
        return {"ok": False, "error": sem.get("error")}
    ids = [str(r.get("bead_id") or "") for r in (sem.get("results") or []) if r.get("bead_id")]
    beads = [_hydrate_bead(store, bid) for bid in ids[:3]]
    c = {"score": 0.0, "path": [b.get("id") for b in beads], "edges": [], "beads": beads, "semantic_edge_ids": []}
    c["confidence"] = _chain_confidence(c)
    citations, _ = _collect_citations_from_chains([c])
    answer = "I remember related context from memory beads." if beads else "I don’t have a strong memory match yet."
    out = {"ok": True, "answer": answer, "anchor_bead_id": (beads[0].get("id") if beads else None), "chains": [c], "citations": citations, "reinforced_semantic_edges": []}
    if debug:
        out["retrieval_debug"] = sem
    return out


def _is_low_info_citation(c: dict) -> bool:
    t = str(c.get("title") or "").strip().lower()
    if not t:
        return True
    if "[[reply_to_current]]" in t or "auto-compaction complete" in t:
        return True
    return False


def _quality_score(result: dict) -> float:
    chains = result.get("chains") or []
    cits = result.get("citations") or []
    if not chains or not cits:
        return 0.0
    chain_confs = [float(c.get("confidence") or 0.0) for c in chains]
    avg_chain = sum(chain_confs) / max(1, len(chain_confs))
    grounded = sum(1 for c in cits if bool(c.get("grounded_role"))) / max(1, len(cits))
    low_info = sum(1 for c in cits if _is_low_info_citation(c)) / max(1, len(cits))
    return round(max(0.0, min(1.0, (0.55 * avg_chain) + (0.45 * grounded) - (0.35 * low_info))), 4)


def memory_reason(query: str, k: int = 8, root: str = "./memory", debug: bool = False) -> dict:
    root_p = Path(root)
    store = MemoryStore(root)

    intent = _detect_intent(query)
    hint_route = str(intent.get("intent") or "remember")

    planners = {
        "why": lambda s, r, q, kk: _plan_why(s, r, q, kk, debug=debug),
        "when": _plan_when,
        "what_changed": _plan_changed,
        "remember": lambda s, r, q, kk: _plan_remember(s, r, q, kk, debug=debug),
    }

    # Primary route is robust default; intent router is fallback hint only.
    primary_route = "why"
    primary = planners.get(primary_route, _plan_why)(store, root_p, query, k)
    if not primary.get("ok"):
        return primary

    primary_q = _quality_score(primary)
    no_hits = len(primary.get("citations") or []) == 0 or len(primary.get("chains") or []) == 0
    low_quality = primary_q < 0.45

    used_retry = False
    chosen_route = primary_route

    if no_hits or low_quality:
        retry_route = hint_route if hint_route in planners else "remember"
        if retry_route == primary_route:
            retry_route = "remember"
        retry = planners.get(retry_route, _plan_remember)(store, root_p, query, k)
        if retry.get("ok"):
            retry_q = _quality_score(retry)
            if retry_q >= primary_q:
                primary = retry
                primary_q = retry_q
                chosen_route = retry_route
            used_retry = True

    chain_confs = [float(c.get("confidence") or 0.0) for c in (primary.get("chains") or [])]
    overall = max(chain_confs) if chain_confs else 0.0
    primary["intent"] = {
        "selected": chosen_route,
        "hint": hint_route,
        "hint_confidence": intent.get("confidence"),
        "scores": intent.get("scores"),
        "used_hint_retry": used_retry,
    }
    primary["confidence"] = {
        "overall": round(overall, 4),
        "grounded": bool(overall >= 0.5),
        "chain_confidences": chain_confs,
        "quality_score": primary_q,
    }
    return primary
