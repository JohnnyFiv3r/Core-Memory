"""Reranking stage for retrieval pipeline.

Second-stage reranking using coverage, structure, chain support, and query intent.
NOTE: Candidate for splitting into rerank_features.py if it grows further.
Currently manageable at ~200 lines.
"""

import json
from pathlib import Path

from core_memory.policy.incidents import incident_match_strength

from .config import (
    LOW_INFO_ALNUM_RATIO_MIN,
    LOW_INFO_SUMMARY_LEN,
    LOW_INFO_TEMPLATES,
    LOW_INFO_TITLE_LEN,
    W_COVERAGE,
    W_EDGE_SUPPORT,
    W_FUSED,
    W_INCIDENT,
    W_PENALTY,
    W_STRUCTURAL,
    INTENT_WEIGHT_OVERRIDES,
)


def _tokenize(text: str) -> list[str]:
    stop = {"the", "and", "for", "with", "that", "this", "what", "when", "why", "did", "was", "are"}
    toks = []
    for t in (text or "").lower().replace("_", " ").replace("-", " ").split():
        if len(t) < 3:
            continue
        if t in stop:
            continue
        if t.endswith("ing") and len(t) > 5:
            t = t[:-3]
        elif t.endswith("ed") and len(t) > 4:
            t = t[:-2]
        elif t.endswith("s") and len(t) > 4:
            t = t[:-1]
        toks.append(t)
    return toks


def _ratio_alnum(s: str) -> float:
    if not s:
        return 0.0
    n = sum(1 for c in s if c.isalnum() or c.isspace())
    return n / max(1, len(s))


def _weighted_coverage(bead: dict, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    title_t = set(_tokenize(str(bead.get("title") or "")))
    summ_t = set(_tokenize(" ".join(bead.get("summary") or [])))
    tags_t = set(_tokenize(" ".join(bead.get("tags") or [])))
    cov_title = len(query_tokens.intersection(title_t)) / max(1, len(query_tokens))
    cov_summ = len(query_tokens.intersection(summ_t)) / max(1, len(query_tokens))
    cov_tags = len(query_tokens.intersection(tags_t)) / max(1, len(query_tokens))
    return max(0.0, min(1.0, (0.5 * cov_title) + (0.35 * cov_summ) + (0.15 * cov_tags)))


def _infer_domain_tags_from_text(text: str) -> set[str]:
    t = (text or "").lower()
    tags: set[str] = set()
    if any(k in t for k in ["core-memory", "bead", "flush", "compaction", "archive", "rolling window", "session_flush", "finalize_and_process_turn", "memory-pass", "retrieval"]):
        tags.add("core_memory_pipeline")
    if any(k in t for k in ["disney", "magic kingdom", "genie", "itinerary", "ride", "fantasyland", "tiana", "pirates"]):
        tags.add("disney_planner")
    if any(k in t for k in ["cloudflare", "tunnel", "dns", "port", "gateway", "websocket", "18888", "8788", "8080"]):
        tags.add("infra_network")
    if any(k in t for k in ["gauntlet", "confidence", "grounding", "rerank", "structural", "semantic", "lexical"]):
        tags.add("retrieval_quality")
    if any(k in t for k in ["slice", "plan", "blocked", "unblocked", "complete", "milestone", "next step"]):
        tags.add("process_management")
    if not tags:
        tags.add("unknown")
    return tags


def _infer_query_domain_tags(query: str) -> set[str]:
    return _infer_domain_tags_from_text(query)


def _infer_bead_domain_tags(bead: dict) -> set[str]:
    text = " ".join([
        str(bead.get("title") or ""),
        " ".join(bead.get("summary") or []),
        " ".join(bead.get("retrieval_facts") or []),
        " ".join(bead.get("topics") or []),
        " ".join(bead.get("entities") or []),
        " ".join(bead.get("entity_ids") or []),
    ])
    return _infer_domain_tags_from_text(text)


def _bridge_pattern_bonus(query: str, bead: dict) -> tuple[float, str]:
    q = (query or "").lower()
    btxt = " ".join([
        str(bead.get("title") or ""),
        " ".join(bead.get("summary") or []),
        " ".join(bead.get("retrieval_facts") or []),
    ]).lower()

    # setup -> ready -> response
    if any(k in q for k in ["ready", "configured", "ingested", "aligned"]) and any(k in btxt for k in ["ready", "configured", "aligned", "guardrail"]):
        return 0.18, "setup_ready_response"
    # diagnose -> fix -> verify
    if any(k in q for k in ["why", "failed", "error", "pending", "blocked"]) and any(k in btxt for k in ["diagnos", "root cause", "fixed", "resolved", "unblocked", "done"]):
        return 0.22, "diagnose_fix_verify"
    # constraint -> substitution
    if any(k in q for k in ["swap", "replace", "constraint", "height", "requirement"]) and any(k in btxt for k in ["swap", "replace", "height", "requirement", "fallback"]):
        return 0.16, "constraint_substitution"
    return 0.0, ""


def _load_structural_adjacency(root: Path) -> tuple[dict[str, set[str]], dict]:
    """Load structural adjacency from both bead_graph and index associations.

    This allows semantic-curated associations to contribute to structural grounding.
    Returns adjacency + diagnostics for query-level debug.
    """
    adj: dict[str, set[str]] = {}
    diag = {
        "graph_structural_edges": 0,
        "assoc_edges_total": 0,
        "assoc_edges_survived_floor": 0,
        "assoc_floor": 0.45,
    }

    # 1) bead_graph structural immutable edges
    snap = root / ".beads" / "bead_graph.json"
    if snap.exists():
        try:
            g = json.loads(snap.read_text(encoding="utf-8"))
            edge_head = g.get("edge_head") or {}
            for e in edge_head.values():
                if str(e.get("class") or "") != "structural":
                    continue
                if not bool(e.get("immutable", False)):
                    continue
                s = str(e.get("src_id") or "")
                d = str(e.get("dst_id") or "")
                if not s or not d:
                    continue
                adj.setdefault(s, set()).add(d)
                adj.setdefault(d, set()).add(s)
                diag["graph_structural_edges"] += 1
        except Exception:
            pass

    # 2) index associations (semantic/imported/open-eval edges)
    idx_file = root / ".beads" / "index.json"
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text(encoding="utf-8"))
            for a in (idx.get("associations") or []):
                if not isinstance(a, dict):
                    continue
                diag["assoc_edges_total"] += 1
                s = str(a.get("source_bead") or a.get("source_bead_id") or "")
                d = str(a.get("target_bead") or a.get("target_bead_id") or "")
                if not s or not d:
                    continue
                # confidence gate to avoid very weak noisy links
                try:
                    conf = float(a.get("confidence") if a.get("confidence") is not None else 0.0)
                except Exception:
                    conf = 0.0
                if conf < float(diag["assoc_floor"]):
                    continue
                diag["assoc_edges_survived_floor"] += 1
                adj.setdefault(s, set()).add(d)
                adj.setdefault(d, set()).add(s)
        except Exception:
            pass

    return adj, diag


def _chain_features(beads: dict, center_id: str, adj: dict[str, set[str]]) -> dict:
    center = beads.get(center_id) or {}
    nbrs = sorted(list(adj.get(center_id) or set()))
    two_hop: set[str] = set()
    for n in nbrs:
        for t in (adj.get(n) or set()):
            if t != center_id and t not in nbrs:
                two_hop.add(str(t))
    one_hop = [center_id] + nbrs + sorted(list(two_hop))
    types = [str((beads.get(i) or {}).get("type") or "") for i in one_hop]

    has_decision = 1 if any(t in {"decision", "precedent"} for t in types) else 0
    has_evidence = 1 if any(t in {"evidence", "lesson"} for t in types) else 0
    has_outcome = 1 if any(t == "outcome" for t in types) else 0

    structural_edge_count_clipped = min(3, len(nbrs))
    has_grounding_structural_edge = 1 if structural_edge_count_clipped > 0 else 0
    two_hop_count_clipped = min(4, len(two_hop))

    is_superseded = 1 if str(center.get("status") or "") == "superseded" else 0
    has_active_chain_support = 1 if (is_superseded and structural_edge_count_clipped > 0) else 0

    return {
        "chain_has_decision": has_decision,
        "chain_has_evidence": has_evidence,
        "chain_has_outcome": has_outcome,
        "has_grounding_structural_edge": has_grounding_structural_edge,
        "structural_edge_count_clipped": structural_edge_count_clipped,
        "structural_two_hop_count_clipped": two_hop_count_clipped,
        "is_superseded": is_superseded,
        "has_active_chain_support": has_active_chain_support,
    }


def _low_info_score(bead: dict) -> float:
    title = str(bead.get("title") or "")
    summary = " ".join(bead.get("summary") or [])
    low_title = 1.0 if len(title.strip()) < LOW_INFO_TITLE_LEN else 0.0
    low_summary = 1.0 if len(summary.strip()) < LOW_INFO_SUMMARY_LEN else 0.0
    low_alnum = 1.0 if _ratio_alnum(title + " " + summary) < LOW_INFO_ALNUM_RATIO_MIN else 0.0
    templ = 1.0 if any(t in (title + " " + summary).lower() for t in LOW_INFO_TEMPLATES) else 0.0
    return max(0.0, min(1.0, (0.3 * low_title) + (0.25 * low_summary) + (0.2 * low_alnum) + (0.25 * templ)))


def rerank_candidates(root: Path, query: str, candidates: list[dict], intent_class: str = "remember") -> dict:
    idx_file = root / ".beads" / "index.json"
    if not idx_file.exists():
        return {"ok": True, "results": candidates, "debug": []}

    idx = json.loads(idx_file.read_text(encoding="utf-8"))
    beads = idx.get("beads") or {}
    q_tokens = set(_tokenize(query))
    adj, adj_diag = _load_structural_adjacency(root)

    ow = INTENT_WEIGHT_OVERRIDES.get(str(intent_class or "remember"), {})
    w_structural = float(ow.get("W_STRUCTURAL", W_STRUCTURAL))
    w_edge = float(ow.get("W_EDGE_SUPPORT", W_EDGE_SUPPORT))
    w_cov = float(ow.get("W_COVERAGE", W_COVERAGE))
    w_inc = float(ow.get("W_INCIDENT", W_INCIDENT))

    q_domains = _infer_query_domain_tags(query)

    out = []
    dbg = []
    for c in candidates:
        bid = str(c.get("bead_id") or "")
        bead = beads.get(bid) or {}

        ch = _chain_features(beads, bid, adj)
        coverage = _weighted_coverage(bead, q_tokens)
        low_info = _low_info_score(bead)
        incident_strength = incident_match_strength(query, str(bead.get("incident_id") or ""), root)

        # Domain alignment (soft): never hard-filter mismatches.
        b_domains = _infer_bead_domain_tags(bead)
        overlap_domains = sorted(list(q_domains.intersection(b_domains)))
        domain_alignment_score = 1.0 if overlap_domains else 0.0
        domain_penalty = 0.12 if not overlap_domains else 0.0

        # Bridge attempt for cross-domain matches.
        bridge_bonus, bridge_pattern = (0.0, "")
        if not overlap_domains:
            bridge_bonus, bridge_pattern = _bridge_pattern_bonus(query, bead)

        structural_quality = (ch["chain_has_decision"] + ch["chain_has_evidence"] + ch["chain_has_outcome"]) / 3.0
        edge_support = (0.5 * ch["has_grounding_structural_edge"]) + (0.5 * (ch["structural_edge_count_clipped"] / 3.0))
        two_hop_support = float(ch.get("structural_two_hop_count_clipped") or 0.0) / 4.0
        superseded_penalty = 1.0 if (ch["is_superseded"] == 1 and ch["has_active_chain_support"] == 0) else 0.0
        penalties = (0.6 * low_info) + (0.4 * superseded_penalty) + domain_penalty

        fused = float(c.get("fused_score") or 0.0)
        # Structural lift should be meaningfully competitive with lexical overlap.
        structural_add = (structural_quality * (w_structural * 1.15)) + (edge_support * (w_edge * 1.35)) + (0.15 * two_hop_support)
        score = (
            (fused * W_FUSED)
            + structural_add
            + (coverage * w_cov)
            + (incident_strength * w_inc)
            + (0.08 * domain_alignment_score)
            + bridge_bonus
            - (penalties * W_PENALTY)
        )
        if ch["has_grounding_structural_edge"]:
            score += 0.22
        score = max(0.0, min(1.0, float(score)))

        features = {
            **ch,
            "query_term_coverage": round(coverage, 4),
            "low_info_score": round(low_info, 4),
            "incident_match_strength": round(incident_strength, 4),
            "query_domains": sorted(list(q_domains)),
            "bead_domains": sorted(list(b_domains)),
            "domain_overlap": overlap_domains,
            "domain_alignment_score": round(domain_alignment_score, 4),
            "bridge_pattern": bridge_pattern,
            "bridge_bonus": round(bridge_bonus, 4),
            "two_hop_support": round(two_hop_support, 4),
            "structural_add": round(structural_add, 4),
        }

        c2 = dict(c)
        c2["rerank_score"] = round(score, 4)
        c2["features"] = features
        c2["derived"] = {
            "intent_class": str(intent_class or "remember"),
            "structural_quality": round(structural_quality, 4),
            "edge_support": round(edge_support, 4),
            "penalties": round(penalties, 4),
            "superseded_penalty": round(superseded_penalty, 4),
            "domain_alignment": "direct" if overlap_domains else ("bridged" if bridge_pattern else "weak"),
            "bridge_rationale": bridge_pattern,
            "weights": {
                "W_FUSED": W_FUSED,
                "W_STRUCTURAL": w_structural,
                "W_EDGE_SUPPORT": w_edge,
                "W_COVERAGE": w_cov,
                "W_INCIDENT": w_inc,
                "W_PENALTY": W_PENALTY,
            },
        }
        out.append(c2)
        dbg.append({
            "bead_id": bid,
            "fused_score": fused,
            "rerank_score": c2["rerank_score"],
            "features": features,
            "derived": c2["derived"],
            "assoc_diag": adj_diag,
        })

    out = sorted(
        out,
        key=lambda r: (
            -float(r.get("rerank_score") or 0.0),
            -float(r.get("fused_score") or 0.0),
            -float(r.get("sem_score") or 0.0),
            -float(r.get("lex_score") or 0.0),
            str(r.get("bead_id") or ""),
        ),
    )

    for i, r in enumerate(out, start=1):
        r["rerank_rank"] = i
        r["rerank_tie_break_policy"] = "rerank>fused>sem>lex>bead_id"

    return {"ok": True, "results": out, "debug": dbg, "adjacency_diag": adj_diag}
