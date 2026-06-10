"""Canonical edge-scoring weights for causal graph traversal.

All traversal paths (Python BFS, Neo4j, Kuzu, Graphiti) should use these
constants so scoring semantics are consistent regardless of backend.

The constants were originally scattered across retrieval/agent.py and
graph/core.py.  Centralising them here (domain logic layer) allows both
retrieval/ code and graph/ code to import without violating the layering law.
"""
from __future__ import annotations

from typing import Any

# Per-relationship base weights.  Causal/semantic edges carry topical signal;
# temporal/entity edges do not.
RELATIONSHIP_HOP_WEIGHT: dict[str, float] = {
    # Causal — strongest signal for multi-hop retrieval
    "caused_by": 0.90, "causes": 0.90, "enables": 0.90, "results_in": 0.90,
    "led_to": 0.90, "resolves": 0.88, "diagnoses": 0.88,
    # Semantic — strong topical signal
    "supports": 0.85, "refines": 0.85, "supersedes": 0.85, "derived_from": 0.80,
    "contradicts": 0.82, "validates": 0.82, "informed_by": 0.80,
    # Weak / generic
    "associated_with": 0.60, "related_to": 0.60, "shared_entity": 0.55,
    # Temporal — low signal (adjacency, not topical relevance)
    "follows": 0.35, "precedes": 0.35, "sequential_turn": 0.35,
    "continues": 0.45, "next_turn": 0.35, "prev_turn": 0.35,
}
DEFAULT_HOP_WEIGHT: float = 0.70    # unknown / generic relationship
HOP_DECAY: float = 0.80             # multiplicative per-hop decay

# Provenance multipliers: how much to trust each edge source.
PROVENANCE_FACTOR: dict[str, float] = {
    "agent_judged": 1.00,
    "model_inferred": 0.85,   # LLM output, not explicitly reviewed
    "preview_classifier": 0.60,  # heuristic token-overlap fallback
    "heuristic": 0.65,
}
DEFAULT_PROVENANCE_FACTOR: float = 0.75

# Directed relationships: the edge has a natural source→target direction.
# Reverse traversal is penalised but not blocked.
DIRECTIONAL_RELS: frozenset[str] = frozenset({
    "caused_by", "causes", "enables", "results_in", "led_to",
    "derived_from", "refines", "supersedes", "resolves", "diagnoses",
})
REVERSE_DIRECTION_FACTOR: float = 0.65

# Causal-class relationships: edges that assert cause/effect rather than
# topical similarity or temporal adjacency. Used to detect causal structure
# in a retrieved candidate set (structural trigger for the causal pipeline).
CAUSAL_RELS: frozenset[str] = frozenset({
    "caused_by", "causes", "enables", "results_in", "led_to",
    "resolves", "diagnoses",
})


def score_edge(
    rel: str,
    *,
    confidence: float = 0.85,
    provenance: str = "model_inferred",
    edge_class: str = "",
) -> float:
    """Single-edge score: rel_weight × confidence × provenance_factor.

    Does not include hop-decay — callers apply ``HOP_DECAY`` per hop if needed.
    """
    r = str(rel or "").strip().lower()
    rel_weight = RELATIONSHIP_HOP_WEIGHT.get(r, DEFAULT_HOP_WEIGHT)
    ec = str(edge_class or "").strip().lower()
    pv = str(provenance or "model_inferred").strip().lower()
    prov_key = ec if ec in PROVENANCE_FACTOR else pv
    prov_factor = PROVENANCE_FACTOR.get(prov_key, DEFAULT_PROVENANCE_FACTOR)
    conf = max(0.0, min(1.0, float(confidence)))
    return rel_weight * conf * prov_factor


def normalize_backend_chain(chain: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw graph-backend traversal chain to the canonical format.

    Graph backends (Neo4j, Kuzu) return chains shaped like::

        {"nodes": [{"id": ..., "type": ..., "title": ...}],
         "edges": [{"rel": ..., "src": ..., "tgt": ..., "confidence": ...}]}

    The downstream ``trace_request`` consumer in ``retrieval/pipeline/canonical.py``
    expects::

        {"path": [bead_id, ...], "score": float, "edges": [...]}

    This function:

    1. Derives ``path`` (ordered bead IDs) from ``nodes`` when absent.
    2. Normalises edge dicts: ``tgt`` → ``dst`` (Neo4j uses "tgt", Python
       path uses "dst") so downstream edge iteration is uniform.
    3. Computes a ``score`` via :func:`score_edge` when absent, using the
       same weights table as the Python BFS path.
    4. Leaves all other keys untouched so backend-specific metadata survives.
    """
    out = dict(chain)

    # 1. path: extract ordered bead IDs from nodes when the Python-style path
    #    field is absent (all graph backends use "nodes" instead).
    if not out.get("path"):
        nodes = list(out.get("nodes") or [])
        ids = [str(n.get("id") or n.get("bead_id") or "").strip() for n in nodes if n]
        out["path"] = [p for p in ids if p]

    # 2. edges: normalise "tgt" key → "dst" for field-name consistency.
    normalised: list[dict[str, Any]] = []
    for edge in (out.get("edges") or []):
        e = dict(edge)
        if "tgt" in e and "dst" not in e:
            e["dst"] = e.pop("tgt")
        normalised.append(e)
    out["edges"] = normalised

    # 3. score: compute when absent using the canonical per-edge weights.
    #    Score = mean per-edge score so chains of different lengths are
    #    comparable.  When a backend provides confidence, it is used; otherwise
    #    we assume a default of 0.85 (model-inferred edge).
    if not out.get("score"):
        n = len(normalised)
        if n > 0:
            total = 0.0
            for edge in normalised:
                try:
                    conf = float(edge.get("confidence") if edge.get("confidence") is not None else 0.85)
                    conf = max(0.0, min(1.0, conf))
                except (TypeError, ValueError):
                    conf = 0.85
                total += score_edge(
                    str(edge.get("rel") or ""),
                    confidence=conf,
                    provenance=str(edge.get("provenance") or "model_inferred"),
                    edge_class=str(edge.get("edge_class") or ""),
                )
            out["score"] = round(total / n, 6)
        else:
            out["score"] = 0.0

    return out
