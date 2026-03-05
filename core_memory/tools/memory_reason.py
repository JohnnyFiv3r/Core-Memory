from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.graph import causal_traverse, reinforce_semantic_edges
from core_memory.semantic_index import semantic_lookup
from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.rerank import rerank_candidates
from core_memory.retrieval.quality_gate import quality_gate_decision
from core_memory.retrieval.config import RETRY_APPEND_HINT, QUALITY_THRESHOLD_LONG
from core_memory.retrieval.query_norm import classify_intent, resolve_query_anchors
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


def _chain_why_priority(chain: dict) -> float:
    edges = chain.get("edges") or []
    beads = chain.get("beads") or []
    has_struct = any(str(e.get("class") or "") == "structural" or str(e.get("rel") or "") in {"supports", "derived_from", "supersedes", "superseded_by", "contradicts", "resolves"} for e in edges)
    types = {str(b.get("type") or "") for b in beads}
    has_dec = bool(types.intersection({"decision", "precedent"}))
    has_evd = bool(types.intersection({"evidence", "lesson", "outcome"}))
    base = float(chain.get("score") or 0.0)
    return base + (0.35 if has_struct else 0.0) + (0.2 if has_dec else 0.0) + (0.2 if has_evd else 0.0)


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


def _radius1_structural_fallback(store: MemoryStore, anchor_ids: list[str], limit: int = 3) -> list[dict]:
    idx = store._read_json(store.beads_dir / "index.json")
    beads = idx.get("beads") or {}
    out = []
    allowed = {"supports", "derived_from", "supersedes", "superseded_by", "contradicts", "resolves"}

    # Prefer graph structural heads (immutable) when available.
    graph_file = store.beads_dir / "bead_graph.json"
    graph = store._read_json(graph_file) if graph_file.exists() else {}
    edge_head = graph.get("edge_head") or {}
    adj: dict[str, list[dict]] = {}
    for e in edge_head.values():
        if str(e.get("class") or "") != "structural" or not bool(e.get("immutable", False)):
            continue
        rel = str(e.get("rel") or "")
        if rel not in allowed:
            continue
        src = str(e.get("src_id") or "")
        dst = str(e.get("dst_id") or "")
        if src and dst:
            adj.setdefault(src, []).append({"dst": dst, "rel": rel})
            adj.setdefault(dst, []).append({"dst": src, "rel": rel})

    for aid in anchor_ids[:8]:
        for row in adj.get(str(aid), [])[:3]:
            dst = str(row.get("dst") or "")
            rel = str(row.get("rel") or "")
            if not dst or dst not in beads:
                continue
            out.append(
                {
                    "score": 0.3,
                    "path": [str(aid), dst],
                    "edges": [{"src": str(aid), "dst": dst, "rel": rel, "class": "structural"}],
                    "beads": [_hydrate_bead(store, str(aid)), _hydrate_bead(store, dst)],
                    "semantic_edge_ids": [],
                }
            )
            if len(out) >= max(1, int(limit)):
                return out

    # Fallback to explicit association rows in index.
    assocs = idx.get("associations") or []
    for aid in anchor_ids[:8]:
        for a in assocs:
            if not isinstance(a, dict):
                continue
            rel = str(a.get("relationship") or "")
            if rel not in allowed:
                continue
            src = str(a.get("source_bead") or "")
            dst = str(a.get("target_bead") or "")
            if src == str(aid) and dst in beads:
                out.append(
                    {
                        "score": 0.28,
                        "path": [str(aid), dst],
                        "edges": [{"src": str(aid), "dst": dst, "rel": rel, "class": "structural"}],
                        "beads": [_hydrate_bead(store, str(aid)), _hydrate_bead(store, dst)],
                        "semantic_edge_ids": [],
                    }
                )
            elif dst == str(aid) and src in beads:
                out.append(
                    {
                        "score": 0.28,
                        "path": [str(aid), src],
                        "edges": [{"src": str(aid), "dst": src, "rel": rel, "class": "structural"}],
                        "beads": [_hydrate_bead(store, str(aid)), _hydrate_bead(store, src)],
                        "semantic_edge_ids": [],
                    }
                )
            if len(out) >= max(1, int(limit)):
                return out

    # Fallback to explicit bead links.
    for aid in anchor_ids[:8]:
        b = beads.get(str(aid)) or {}
        links = b.get("links") or []
        for l in links:
            if not isinstance(l, dict):
                continue
            rel = str(l.get("type") or "")
            if rel not in allowed:
                continue
            dst = str(l.get("bead_id") or "")
            if not dst or dst not in beads:
                continue
            ch = {
                "score": 0.25,
                "path": [str(aid), dst],
                "edges": [{"src": str(aid), "dst": dst, "rel": rel, "class": "structural"}],
                "beads": [_hydrate_bead(store, str(aid)), _hydrate_bead(store, dst)],
                "semantic_edge_ids": [],
            }
            out.append(ch)
            if len(out) >= max(1, int(limit)):
                return out
    return out


def _intent_class_from_query(query: str) -> str:
    return str((classify_intent(query) or {}).get("intent_class") or "remember")


def _retrieve_ranked(root_p: Path, query: str, k: int, intent_class: str = "remember") -> dict:
    first = hybrid_lookup(root_p, query=query, k=max(1, int(k)))
    if not first.get("ok"):
        return first
    rr1 = rerank_candidates(root_p, query=query, candidates=first.get("results") or [], intent_class=intent_class)
    ranked1 = rr1.get("results") or []

    # Intent-normalized selector gating: for causal/what_changed, prioritize structurally rich candidates.
    if intent_class in {"causal", "what_changed"}:
        ranked1 = sorted(
            ranked1,
            key=lambda r: (
                -float((r.get("derived") or {}).get("structural_quality") or 0.0),
                -float((r.get("derived") or {}).get("edge_support") or 0.0),
                -float(r.get("rerank_score") or 0.0),
                str(r.get("bead_id") or ""),
            ),
        )

    gate = quality_gate_decision(ranked1, query=query)

    if not gate.get("retry"):
        return {"ok": True, "query_used": query, "results": ranked1, "debug": {"first": rr1, "gate": gate, "retry": None}}

    retry_query = (query or "") + RETRY_APPEND_HINT
    second = hybrid_lookup(root_p, query=retry_query, k=max(1, int(k)))
    if not second.get("ok"):
        return {"ok": True, "query_used": query, "results": ranked1, "debug": {"first": rr1, "gate": gate, "retry": {"ok": False, "error": second.get("error")}}}

    rr2 = rerank_candidates(root_p, query=retry_query, candidates=second.get("results") or [], intent_class=intent_class)
    ranked2 = rr2.get("results") or []
    if intent_class in {"causal", "what_changed"}:
        ranked2 = sorted(
            ranked2,
            key=lambda r: (
                -float((r.get("derived") or {}).get("structural_quality") or 0.0),
                -float((r.get("derived") or {}).get("edge_support") or 0.0),
                -float(r.get("rerank_score") or 0.0),
                str(r.get("bead_id") or ""),
            ),
        )
    # deterministic keep-better: compare top rerank score
    top1 = float((ranked1[0].get("rerank_score") if ranked1 else 0.0) or 0.0)
    top2 = float((ranked2[0].get("rerank_score") if ranked2 else 0.0) or 0.0)
    if top2 >= top1:
        return {"ok": True, "query_used": retry_query, "results": ranked2, "debug": {"first": rr1, "gate": gate, "retry": rr2, "used_retry": True}}
    return {"ok": True, "query_used": query, "results": ranked1, "debug": {"first": rr1, "gate": gate, "retry": rr2, "used_retry": False}}


def _plan_why(store: MemoryStore, root_p: Path, query: str, k: int, debug: bool = False, pinned_bead_ids: list[str] | None = None) -> dict:
    sem = _retrieve_ranked(root_p, query=query, k=max(1, int(k)), intent_class="causal")
    if not sem.get("ok"):
        return {"ok": False, "error": sem.get("error")}

    sem_results = sem.get("results") or []
    anchors = [str(r.get("bead_id") or "") for r in sem_results if r.get("bead_id")]
    pinned = [str(x) for x in (pinned_bead_ids or []) if str(x)]
    if pinned:
        anchors = pinned + [a for a in anchors if a not in set(pinned)]
    anchor = _choose_anchor(sem_results)
    if anchor and anchor not in anchors:
        anchors = [anchor] + anchors

    trav = causal_traverse(root_p, anchor_ids=anchors[:8], max_depth=4, max_chains=50)
    chains = trav.get("chains") or []
    hydrated = []
    for c in chains:
        beads = [_hydrate_bead(store, str(bid)) for bid in (c.get("path") or [])]
        hydrated.append({"score": c.get("score"), "path": c.get("path"), "edges": c.get("edges"), "beads": beads, "semantic_edge_ids": c.get("semantic_edge_ids") or []})

    ranked_hydrated = sorted(hydrated, key=lambda c: _chain_why_priority(c), reverse=True)
    out_chains = _select_diverse_chains(ranked_hydrated, top_n=3)
    fallback_struct = _radius1_structural_fallback(store, anchors, limit=3)

    if not out_chains:
        out_chains = _select_diverse_chains(sorted(fallback_struct, key=lambda c: _chain_why_priority(c), reverse=True), top_n=3)
    else:
        # If selected chains are weak/non-structural, enrich with radius-1 structural fallback.
        allowed = {"supports", "derived_from", "supersedes", "superseded_by", "contradicts", "resolves"}
        has_struct = any(
            any((str(e.get("class") or "") == "structural") or (str(e.get("rel") or "") in allowed) for e in (c.get("edges") or []))
            for c in out_chains
        )
        if not has_struct and fallback_struct:
            merged = list(out_chains) + list(fallback_struct)
            merged = sorted(merged, key=lambda c: _chain_why_priority(c), reverse=True)
            out_chains = _select_diverse_chains(merged, top_n=3)

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
    sem = _retrieve_ranked(root_p, query=query, k=max(1, int(k)), intent_class="remember")
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


def _causal_intent(query: str) -> bool:
    q = (query or "").lower()
    return any(x in q for x in ["why", "decide", "because", "rationale", "what happened"])


def _has_structural_chain(result: dict) -> bool:
    allowed = {"supports", "derived_from", "supersedes", "superseded_by", "contradicts", "resolves", "caused_by"}
    for c in (result.get("chains") or []):
        for e in (c.get("edges") or []):
            if str(e.get("class") or "") == "structural":
                return True
            if str(e.get("rel") or "") in allowed:
                return True
    return False


def _grounding_signal(result: dict) -> float:
    chains = result.get("chains") or []
    cits = result.get("citations") or []
    has_structural = 1.0 if _has_structural_chain(result) else 0.0
    has_decision = 1.0 if any(str(c.get("type") or "") in {"decision", "precedent"} for c in cits) else 0.0
    has_evidence = 1.0 if any(str(c.get("type") or "") in {"evidence", "lesson", "outcome"} for c in cits) else 0.0
    return (0.5 * has_structural) + (0.25 * has_decision) + (0.25 * has_evidence)


def memory_reason(
    query: str,
    k: int = 8,
    root: str = "./memory",
    debug: bool = False,
    explain: bool = False,
    pinned_incident_ids: list[str] | None = None,
    pinned_topic_keys: list[str] | None = None,
    pinned_bead_ids: list[str] | None = None,
) -> dict:
    root_p = Path(root)
    store = MemoryStore(root)

    intent = _detect_intent(query)
    intent_meta = classify_intent(query)
    anchor_meta = resolve_query_anchors(query, root_p)

    pin_inc = [str(x) for x in (pinned_incident_ids or []) if str(x)]
    pin_top = [str(x) for x in (pinned_topic_keys or []) if str(x)]
    pin_beads = [str(x) for x in (pinned_bead_ids or []) if str(x)]

    if pin_inc:
        existing = {str(x.get("incident_id") or "") for x in (anchor_meta.get("matched_incidents") or [])}
        merged = list(anchor_meta.get("matched_incidents") or [])
        for iid in pin_inc:
            if iid not in existing:
                merged.append({"incident_id": iid, "strength": 1.0, "source": "pinned"})
        anchor_meta["matched_incidents"] = merged

    if pin_top:
        existing = {str(x.get("topic_key") or "") for x in (anchor_meta.get("matched_topics") or [])}
        merged = list(anchor_meta.get("matched_topics") or [])
        for tk in pin_top:
            if tk not in existing:
                merged.append({"topic_key": tk, "strength": 1.0, "source": "pinned"})
        anchor_meta["matched_topics"] = merged

    retrieval_query = str(anchor_meta.get("expanded_query") or query)
    if pin_inc or pin_top:
        retrieval_query = " ".join(
            [retrieval_query]
            + [x.replace("_", " ") for x in pin_inc]
            + [x.replace("_", " ") for x in pin_top]
        ).strip()

    intent_class = str(intent_meta.get("intent_class") or "remember")
    hint_route = intent_class if intent_class in {"why", "when", "what_changed", "remember"} else str(intent.get("intent") or "remember")
    if intent_class == "causal":
        hint_route = "why"

    planners = {
        "why": lambda s, r, q, kk: _plan_why(s, r, q, kk, debug=debug, pinned_bead_ids=pin_beads),
        "when": _plan_when,
        "what_changed": _plan_changed,
        "remember": lambda s, r, q, kk: _plan_remember(s, r, q, kk, debug=debug),
    }

    route_by_intent = {
        "causal": "why",
        "when": "when",
        "what_changed": "what_changed",
        "remember": "remember",
    }
    primary_route = route_by_intent.get(intent_class, "remember")
    primary = planners.get(primary_route, _plan_why)(store, root_p, retrieval_query, k)
    if not primary.get("ok"):
        return primary

    primary_q = _quality_score(primary)
    no_hits = len(primary.get("citations") or []) == 0 or len(primary.get("chains") or []) == 0
    low_quality = primary_q < float(QUALITY_THRESHOLD_LONG)

    used_retry = False
    chosen_route = primary_route
    causal_query = bool(intent_meta.get("causal_intent"))
    retry = {"ok": False}
    retry_route = ""

    if no_hits or low_quality:
        retry_route = hint_route if hint_route in planners else "remember"
        if retry_route == primary_route:
            retry_route = "remember"
        retry = planners.get(retry_route, _plan_remember)(store, root_p, retrieval_query, k) if retry_route else {"ok": False}
        if retry_route and retry.get("ok"):
            retry_q = _quality_score(retry)
            p_ground = _grounding_signal(primary)
            r_ground = _grounding_signal(retry)
            should_take = retry_q >= primary_q
            if causal_query and r_ground < p_ground:
                should_take = False
            if should_take:
                primary = retry
                primary_q = retry_q
                chosen_route = retry_route
            used_retry = True

    # Structural grounding constraint for causal queries.
    primary_struct = _has_structural_chain(primary)
    retry_struct = bool(retry.get("ok")) and _has_structural_chain(retry)
    if causal_query:
        structural_candidates_found = int(primary_struct) + int(retry_struct)
        if structural_candidates_found > 0 and not primary_struct and retry_struct:
            primary = retry
            primary_q = _quality_score(retry)
            chosen_route = retry_route or chosen_route
            primary_struct = True
        primary["grounding"] = {
            "causal_intent": True,
            "structural_candidates_found": structural_candidates_found,
            "selected_has_structural": bool(primary_struct),
            "reason": "no_structural_candidates" if structural_candidates_found == 0 else "structural_constraint_applied",
        }
    else:
        primary["grounding"] = {
            "causal_intent": False,
            "structural_candidates_found": int(primary_struct) + int(retry_struct),
            "selected_has_structural": bool(primary_struct),
            "reason": "non_causal_query",
        }

    chain_confs = [float(c.get("confidence") or 0.0) for c in (primary.get("chains") or [])]
    overall = max(chain_confs) if chain_confs else 0.0
    primary["intent"] = {
        "selected": chosen_route,
        "hint": hint_route,
        "intent_class": intent_class,
        "causal_intent": bool(intent_meta.get("causal_intent")),
        "query_type_bucket": intent_meta.get("query_type_bucket"),
        "hint_confidence": intent.get("confidence"),
        "scores": intent.get("scores"),
        "used_hint_retry": used_retry,
        "matched_incidents": anchor_meta.get("matched_incidents") or [],
        "matched_topics": anchor_meta.get("matched_topics") or [],
        "pinned_incident_ids": pin_inc,
        "pinned_topic_keys": pin_top,
        "pinned_bead_ids": pin_beads,
    }
    primary["confidence"] = {
        "overall": round(overall, 4),
        "grounded": bool(overall >= 0.5),
        "chain_confidences": chain_confs,
        "quality_score": primary_q,
    }

    if explain:
        import hashlib
        import json
        from datetime import datetime, timezone

        rdbg = primary.get("retrieval_debug") or {}
        rinner = rdbg.get("debug") or {}
        ranked = rdbg.get("results") or []
        idx_file = Path(root) / ".beads" / "index.json"
        beads = {}
        if idx_file.exists():
            try:
                beads = (json.loads(idx_file.read_text(encoding="utf-8")) or {}).get("beads") or {}
            except Exception:
                beads = {}

        def _row(bid: str) -> dict:
            b = beads.get(str(bid)) or {}
            return {
                "bead_id": str(bid),
                "title": str(b.get("title") or b.get("snapshot_title") or ""),
                "tags": [str(t) for t in (b.get("tags") or [])],
                "incident_id": str(b.get("incident_id") or ""),
            }

        top_sem = sorted(ranked, key=lambda r: (-float(r.get("sem_score") or 0.0), str(r.get("bead_id") or "")))[:5]
        top_lex = sorted(ranked, key=lambda r: (-float(r.get("lex_score") or 0.0), str(r.get("bead_id") or "")))[:5]
        incident_targets = {str(x.get("incident_id") or "") for x in (anchor_meta.get("matched_incidents") or []) if x.get("incident_id")}
        topic_targets = {str(x.get("topic_key") or "") for x in (anchor_meta.get("matched_topics") or []) if x.get("topic_key")}
        top5_ids = [str(r.get("bead_id") or "") for r in ranked[:5] if r.get("bead_id")]
        anchor_hit_top5 = False
        for bid in top5_ids:
            b = beads.get(str(bid)) or {}
            if incident_targets and str(b.get("incident_id") or "") in incident_targets:
                anchor_hit_top5 = True
                break
            btags = set([str(t) for t in (b.get("tags") or [])])
            if topic_targets and btags.intersection(topic_targets):
                anchor_hit_top5 = True
                break

        penalties_applied = []
        for r in ranked:
            d = r.get("derived") or {}
            p = float(d.get("penalties") or 0.0)
            if p <= 0:
                continue
            penalties_applied.append({
                "bead_id": str(r.get("bead_id") or ""),
                "penalties": p,
                "low_info_score": float((r.get("features") or {}).get("low_info_score") or 0.0),
                "superseded_penalty": float(d.get("superseded_penalty") or 0.0),
            })

        payload = {
            "schema_version": "reason_explain.v1",
            "query": query,
            "normalized_query": str((intent_meta.get("normalized") or {}).get("raw_normalized") or ""),
            "query_tokens": (intent_meta.get("normalized") or {}).get("tokens") or [],
            "query_phrases": (intent_meta.get("normalized") or {}).get("phrases") or [],
            "expanded_query": retrieval_query,
            "matched_incidents": anchor_meta.get("matched_incidents") or [],
            "matched_topics": anchor_meta.get("matched_topics") or [],
            "k": int(k),
            "intent": primary.get("intent"),
            "confidence": primary.get("confidence"),
            "retrievers": {
                "first_pass": (rinner.get("first") or {}).get("results") if isinstance(rinner.get("first"), dict) else None,
                "retry_pass": (rinner.get("retry") or {}).get("results") if isinstance(rinner.get("retry"), dict) else None,
                "gate": rinner.get("gate"),
            },
            "rank_decisions": [
                {
                    "bead_id": r.get("bead_id"),
                    "rank": r.get("rerank_rank") or r.get("rank"),
                    "fused_score": r.get("fused_score"),
                    "sem_score": r.get("sem_score"),
                    "lex_score": r.get("lex_score"),
                    "rerank_score": r.get("rerank_score"),
                    "features": r.get("features"),
                    "tie_break_policy": r.get("rerank_tie_break_policy") or r.get("tie_break_policy"),
                }
                for r in ranked
            ],
            "anchor_diagnostics": {
                "matched_incidents": sorted(incident_targets),
                "matched_topics": sorted(topic_targets),
                "anchor_hit_top5": bool(anchor_hit_top5),
                "why_no_anchor_hit": "none" if anchor_hit_top5 else ("no_anchor_matches" if (not incident_targets and not topic_targets) else "top5_missing_anchor"),
                "top_semantic_hits": [dict(_row(str(r.get("bead_id") or "")), sem_score=float(r.get("sem_score") or 0.0)) for r in top_sem],
                "top_lexical_hits": [dict(_row(str(r.get("bead_id") or "")), lex_score=float(r.get("lex_score") or 0.0)) for r in top_lex],
                "expanded_neighbors": any(len((c.get("path") or [])) > 1 for c in (primary.get("chains") or [])),
                "penalties_applied": penalties_applied,
            },
            "retrieval_debug": rdbg,
            "final_bead_ids": [str(c.get("bead_id") or "") for c in ranked],
        }
        stable = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        replay_hash = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = Path(root) / "runs" / "reason" / f"{ts}_{replay_hash}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        primary["explain"] = {"replay_hash": replay_hash, "report": str(run_dir / "report.json")}

    return primary
