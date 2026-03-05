from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core_memory.memory_skill.catalog import build_catalog
from core_memory.memory_skill.snap import snap_form
from core_memory.memory_skill.search import search_typed
from core_memory.memory_skill.explain import build_explain
from core_memory.tools.memory_reason import memory_reason


def _mk_request_id(req: dict) -> str:
    s = json.dumps(req or {}, sort_keys=True, ensure_ascii=False)
    return "mrq_" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _load_beads(root: Path) -> dict:
    p = root / ".beads" / "index.json"
    if not p.exists():
        return {}
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("beads") or {}
    except Exception:
        return {}


def _anchor_rank(results: list[dict], incident_id: str, topic_keys: set[str], beads: dict) -> int:
    if not results:
        return 999
    for i, r in enumerate(results[:10], start=1):
        b = beads.get(str(r.get("bead_id") or "")) or {}
        if incident_id and str(b.get("incident_id") or "") == incident_id:
            return i
        tags = set([str(t) for t in (b.get("tags") or [])])
        if topic_keys and tags.intersection(topic_keys):
            return i
    return 999


def _cluster_coherence(results: list[dict], beads: dict) -> tuple[float, int]:
    top = results[:5]
    if not top:
        return 0.0, 0
    labels = []
    for r in top:
        b = beads.get(str(r.get("bead_id") or "")) or {}
        iid = str(b.get("incident_id") or "")
        if iid:
            labels.append("i:" + iid)
            continue
        tags = sorted([str(t) for t in (b.get("tags") or []) if str(t)])
        labels.append("t:" + (tags[0] if tags else "none"))
    counts = {}
    for x in labels:
        counts[x] = counts.get(x, 0) + 1
    dominant = max(counts.values()) if counts else 0
    clusters = sum(1 for v in counts.values() if v >= 2)
    return round(dominant / max(1, len(top)), 4), clusters


def _chain_quality(chains: list[dict]) -> float:
    if not chains:
        return 0.0
    scores = [float(c.get("score") or 0.0) for c in chains[:3]]
    if not scores:
        return 0.0
    return round(sum(scores) / max(1, len(scores)), 4)


def _confidence_and_next_base(intent: str, results: list[dict], chains: list[dict], snapped: dict, beads: dict) -> tuple[str, str, dict]:
    if not results:
        return "low", "ask_clarifying", {"reason": "no_results"}

    top1 = float((results[0] or {}).get("score") or 0.0)
    top2 = float((results[1] or {}).get("score") or 0.0) if len(results) > 1 else 0.0
    margin = round(top1 - top2, 4)

    incident_id = str(snapped.get("incident_id") or "")
    topic_keys = set([str(x) for x in (snapped.get("topic_keys") or []) if str(x)])
    arank = _anchor_rank(results, incident_id, topic_keys, beads)
    coh, cluster_count = _cluster_coherence(results, beads)

    if intent in {"remember", "what_changed", "when", "other"}:
        high = (arank == 1) or (margin >= 0.12) or (coh >= 0.6)
        medium = (arank <= 3) or (margin >= 0.06) or (coh >= 0.4)
        if high:
            return "high", "answer", {"margin": margin, "anchor_rank": arank, "coherence": coh, "clusters": cluster_count}
        if medium:
            return "medium", "answer", {"margin": margin, "anchor_rank": arank, "coherence": coh, "clusters": cluster_count}
        if cluster_count >= 2:
            return "low", "ask_clarifying", {"margin": margin, "anchor_rank": arank, "coherence": coh, "clusters": cluster_count}
        return "low", "broaden", {"margin": margin, "anchor_rank": arank, "coherence": coh, "clusters": cluster_count}

    # causal
    grounded = bool(chains)
    if grounded:
        if (margin >= 0.06) or (arank <= 3):
            return "high", "answer", {"margin": margin, "anchor_rank": arank, "coherence": coh, "clusters": cluster_count}
        return "medium", "answer", {"margin": margin, "anchor_rank": arank, "coherence": coh, "clusters": cluster_count}
    if cluster_count >= 2:
        return "low", "ask_clarifying", {"margin": margin, "anchor_rank": arank, "coherence": coh, "clusters": cluster_count}
    return "low", "broaden", {"margin": margin, "anchor_rank": arank, "coherence": coh, "clusters": cluster_count}


def evaluate_confidence_next(intent: str, results: list[dict], chains: list[dict], snapped: dict, beads: dict, warnings: list[str] | None = None) -> tuple[str, str, dict]:
    confidence, next_action, diag = _confidence_and_next_base(intent, results, chains, snapped, beads)
    warnings = list(warnings or [])
    benign_warnings = {"require_structural_requested_but_no_chains"}
    only_benign = all(str(w) in benign_warnings for w in warnings)
    anchor_present = bool((snapped or {}).get("incident_id")) or bool((snapped or {}).get("topic_keys") or [])
    chq = _chain_quality(chains)

    if confidence == "high":
        warning_ok = (not warnings) or only_benign
        causal_ok = bool(chains) and (chq >= 0.2)
        non_causal_ok = anchor_present
        if not warning_ok:
            confidence = "medium"
        elif intent == "causal" and not causal_ok:
            confidence = "medium"
        elif intent != "causal" and not non_causal_ok:
            confidence = "medium"

    diag = dict(diag or {})
    diag.update({
        "warnings": warnings,
        "only_benign_warnings": only_benign,
        "anchor_present": anchor_present,
        "chain_quality": chq,
    })
    return confidence, next_action, diag


def execute_request(request: dict, root: str = "./memory", explain: bool = True) -> dict:
    req = dict(request or {})
    raw_query = str(req.get("raw_query") or req.get("query_text") or "").strip()
    intent = str(req.get("intent") or "other")
    constraints = dict(req.get("constraints") or {})
    facets = dict(req.get("facets") or {})

    mem_req = {
        "request_id": str(req.get("request_id") or _mk_request_id(req)),
        "raw_query": raw_query,
        "intent": intent,
        "constraints": {
            "require_structural": bool(constraints.get("require_structural", False)),
        },
        "facets": {
            "incident_ids": [str(x) for x in (facets.get("incident_ids") or [])][:3],
            "topic_keys": [str(x) for x in (facets.get("topic_keys") or [])][:3],
            "bead_types": [str(x) for x in (facets.get("bead_types") or [])][:3],
            "relation_types": [str(x) for x in (facets.get("relation_types") or [])][:3],
            "pinned_bead_ids": [str(x) for x in (facets.get("pinned_bead_ids") or [])][:5],
            "must_terms": [str(x) for x in (facets.get("must_terms") or [])][:5],
            "avoid_terms": [str(x) for x in (facets.get("avoid_terms") or [])][:5],
            "time_range": dict(facets.get("time_range") or {}),
        },
        "k": max(1, min(30, int(req.get("k") or 10))),
    }

    typed_form = {
        "intent": intent,
        "query_text": raw_query,
        "incident_id": (mem_req["facets"]["incident_ids"][0] if mem_req["facets"]["incident_ids"] else None),
        "topic_keys": mem_req["facets"]["topic_keys"],
        "bead_types": mem_req["facets"]["bead_types"],
        "relation_types": mem_req["facets"]["relation_types"],
        "must_terms": mem_req["facets"]["must_terms"],
        "avoid_terms": mem_req["facets"]["avoid_terms"],
        "time_range": mem_req["facets"]["time_range"],
        "k": mem_req["k"],
        # agent/user controlled: not auto-forced by intent
        "require_structural": bool(mem_req["constraints"].get("require_structural")),
    }

    rp = Path(root)
    catalog = build_catalog(rp)
    snapped = snap_form(typed_form, catalog)
    sres = search_typed(rp, snapped.get("snapped") or {}, include_explain=bool(explain))
    sres["snapped_query"] = snapped.get("snapped") or typed_form
    if explain:
        sres["explain"] = build_explain(sres.get("snapped_query") or {}, snapped.get("decisions") or {}, sres.get("warnings") or [], sres.get("retrieval_debug") or {})
    results = sres.get("results") or []
    chains = sres.get("chains") or []
    beads = _load_beads(rp)

    grounding_required = bool(mem_req["constraints"].get("require_structural")) or intent == "causal"
    grounding_achieved = bool(chains)
    grounding_reason = "grounded" if grounding_achieved else ("not_required" if not grounding_required else "no_structural_edges_found")

    # If grounding requested but not achieved, run reasoner for structural proof attempt,
    # while preserving never-empty results contract from typed search.
    reason_payload = None
    if grounding_required and not grounding_achieved:
        reason_payload = memory_reason(
            raw_query,
            root=root,
            k=max(6, mem_req["k"]),
            debug=bool(explain),
            explain=False,
            pinned_incident_ids=mem_req["facets"]["incident_ids"],
            pinned_topic_keys=mem_req["facets"]["topic_keys"],
            pinned_bead_ids=mem_req["facets"]["pinned_bead_ids"],
        )
        rchains = reason_payload.get("chains") or []
        if rchains:
            chains = rchains[:3]
            grounding_achieved = True
            grounding_reason = "grounded_via_reasoner"

    # never-empty contract (if corpus has beads): keep typed results, fallback to reason citations
    if not results:
        if reason_payload is None:
            reason_payload = memory_reason(
                raw_query,
                root=root,
                k=max(6, mem_req["k"]),
                debug=bool(explain),
                explain=False,
                pinned_incident_ids=mem_req["facets"]["incident_ids"],
                pinned_topic_keys=mem_req["facets"]["topic_keys"],
                pinned_bead_ids=mem_req["facets"]["pinned_bead_ids"],
            )
        cits = reason_payload.get("citations") or []
        for c in cits[: mem_req["k"]]:
            results.append(
                {
                    "bead_id": str(c.get("bead_id") or ""),
                    "title": str(c.get("title") or ""),
                    "type": str(c.get("type") or ""),
                    "snippet": "",
                    "score": float(c.get("confidence") or 0.0),
                    "source": "reason_fallback",
                }
            )

    confidence, next_action, conf_diag = evaluate_confidence_next(
        intent=intent,
        results=results,
        chains=chains,
        snapped=(sres.get("snapped_query") or typed_form),
        beads=beads,
        warnings=(sres.get("warnings") or []),
    )

    # For non-causal low-confidence with no clear ambiguity, do one deterministic broaden pass then answer.
    if intent in {"remember", "what_changed", "when", "other"} and next_action == "broaden":
        broader_form = dict(sres.get("snapped_query") or typed_form)
        broader_form["incident_id"] = None
        broader_form["topic_keys"] = []
        broader_form["bead_types"] = []
        broader_form["relation_types"] = []
        broader_form["k"] = max(mem_req["k"], 12)
        broad = search_typed(rp, broader_form, include_explain=False)
        bres = broad.get("results") or []
        if bres:
            results = bres[: mem_req["k"]]
            # preserve chains from prior pass unless causal reasoner added stronger evidence
            confidence, next_action, conf_diag = evaluate_confidence_next(
                intent=intent,
                results=results,
                chains=chains,
                snapped=broader_form,
                beads=beads,
                warnings=(sres.get("warnings") or []),
            )
            next_action = "answer" if next_action != "ask_clarifying" else next_action

    warnings = sres.get("warnings") or []

    out = {
        "ok": True,
        "request": mem_req,
        "snapped": sres.get("snapped_query") or typed_form,
        "results": results,
        "chains": chains,
        "grounding": {
            "required": bool(grounding_required),
            "achieved": bool(grounding_achieved),
            "reason": grounding_reason,
        },
        "confidence": confidence,
        "next_action": next_action,
        "warnings": warnings,
    }
    if explain:
        out["explain"] = {
            "search": sres.get("explain") or {},
            "reason_fallback_used": bool(reason_payload is not None),
            "confidence_diagnostics": conf_diag,
        }
    return out
