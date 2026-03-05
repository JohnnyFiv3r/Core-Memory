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

    grounding_required = bool(mem_req["constraints"].get("require_structural")) or intent == "causal"
    grounding_achieved = bool(chains)
    grounding_reason = "grounded" if grounding_achieved else ("not_required" if not grounding_required else "no_structural_edges_found")

    # If grounding requested but not achieved, run reasoner for structural proof attempt,
    # while preserving never-empty results contract from typed search.
    reason_payload = None
    if grounding_required and not grounding_achieved:
        reason_payload = memory_reason(raw_query, root=root, k=max(6, mem_req["k"]), debug=bool(explain), explain=False)
        rchains = reason_payload.get("chains") or []
        if rchains:
            chains = rchains[:3]
            grounding_achieved = True
            grounding_reason = "grounded_via_reasoner"

    # never-empty contract (if corpus has beads): keep typed results, fallback to reason citations
    if not results:
        if reason_payload is None:
            reason_payload = memory_reason(raw_query, root=root, k=max(6, mem_req["k"]), debug=bool(explain), explain=False)
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

    confidence = sres.get("confidence") or ("medium" if results else "low")
    next_action = sres.get("suggested_next") or ("answer" if results else "ask_clarifying")

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
        "warnings": sres.get("warnings") or [],
    }
    if explain:
        out["explain"] = {
            "search": sres.get("explain") or {},
            "reason_fallback_used": bool(reason_payload is not None),
        }
    return out
