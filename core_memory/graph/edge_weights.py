"""Canonical edge-scoring weights for causal graph traversal.

All traversal paths (Python BFS, Neo4j, Kuzu, Graphiti) should use these
constants so scoring semantics are consistent regardless of backend.

The constants were originally scattered across retrieval/agent.py and
graph/core.py.  Centralising them here (domain logic layer) allows both
retrieval/ code and graph/ code to import without violating the layering law.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from core_memory.schema.normalization import normalize_relation_type

# Per-relationship base weights.  Causal/semantic edges carry topical signal;
# temporal/entity edges do not.
RELATIONSHIP_HOP_WEIGHT: dict[str, float] = {
    # Causal — strongest signal for multi-hop retrieval
    "causes": 0.90, "leads_to": 0.90, "enables": 0.90, "results_in": 0.90,
    "resolves": 0.88, "diagnoses": 0.88,
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

# Provenances that always win over the channel marker: the crawler stamps
# every appended edge edge_class="agent_judged" (the *channel* was
# agent-reviewed), but when the relationship label itself came from the
# preview classifier or another heuristic, the low-trust discount must apply
# regardless of channel.
LOW_TRUST_PROVENANCES: frozenset[str] = frozenset({"preview_classifier", "heuristic"})


def resolve_provenance_factor(edge_class: str, provenance: str) -> float:
    """Single source of truth for the provenance multiplier.

    Low-trust provenances override the channel ``edge_class``; otherwise the
    channel marker wins when it is a known provenance key.
    """
    ec = str(edge_class or "").strip().lower()
    pv = str(provenance or "model_inferred").strip().lower()
    if pv in LOW_TRUST_PROVENANCES:
        return PROVENANCE_FACTOR[pv]
    key = ec if ec in PROVENANCE_FACTOR else pv
    return PROVENANCE_FACTOR.get(key, DEFAULT_PROVENANCE_FACTOR)

# Directed relationships: the edge has a natural source→target direction.
# Reverse traversal is penalised but not blocked.
DIRECTIONAL_RELS: frozenset[str] = frozenset({
    "causes", "leads_to", "enables", "results_in",
    "derived_from", "refines", "supersedes", "resolves", "diagnoses",
})
REVERSE_DIRECTION_FACTOR: float = 0.65

# Causal-class relationships: edges that assert cause/effect rather than
# topical similarity or temporal adjacency. Used to detect causal structure
# in a retrieved candidate set (structural trigger for the causal pipeline).
CAUSAL_RELS: frozenset[str] = frozenset({
    "causes", "leads_to", "enables", "results_in",
    "resolves", "diagnoses",
})

# Edge lifecycle (reinforce / decay / supersede) scoring parameters.
# Reinforcement is bounded and logarithmic: heavily-used edges gain at most
# +15%, so usage tunes ranking without letting popularity swamp relevance.
REINFORCEMENT_MAX_BONUS: float = 0.15
REINFORCEMENT_LOG_SCALE: float = 0.05
# Unreinforced edges decay toward a floor, never to zero — an old edge that
# was correct when judged stays retrievable, it just stops outranking
# recently-confirmed structure.
DECAY_HALF_LIFE_DAYS: float = 90.0
DECAY_FLOOR: float = 0.70
# Edges through superseded beads point at stale truth.
SUPERSEDED_ENDPOINT_FACTOR: float = 0.60


def _parse_ts(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def effective_edge_multiplier(assoc: dict[str, Any], *, now: datetime | None = None) -> float:
    """Lifecycle multiplier for one association: reinforcement × recency decay.

    - ``reinforcement_count`` adds a bounded logarithmic bonus
      (≤ ``1 + REINFORCEMENT_MAX_BONUS``).
    - Time since ``last_reinforced_at`` (falling back to ``created_at``)
      decays the edge with half-life ``DECAY_HALF_LIFE_DAYS``, clamped at
      ``DECAY_FLOOR``. Edges with no timestamp do not decay.

    Range: [DECAY_FLOOR, 1 + REINFORCEMENT_MAX_BONUS].
    """
    count = max(0, int(assoc.get("reinforcement_count") or 0))
    bonus = 1.0 + min(REINFORCEMENT_MAX_BONUS, REINFORCEMENT_LOG_SCALE * math.log1p(count))

    ts = _parse_ts(assoc.get("last_reinforced_at")) or _parse_ts(assoc.get("created_at"))
    decay = 1.0
    if ts is not None:
        now_dt = now or datetime.now(timezone.utc)
        age_days = max(0.0, (now_dt - ts).total_seconds() / 86400.0)
        decay = max(DECAY_FLOOR, 2.0 ** (-age_days / DECAY_HALF_LIFE_DAYS))

    return bonus * decay


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
    r = normalize_relation_type(rel)
    rel_weight = RELATIONSHIP_HOP_WEIGHT.get(r, DEFAULT_HOP_WEIGHT)
    prov_factor = resolve_provenance_factor(edge_class, provenance)
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
