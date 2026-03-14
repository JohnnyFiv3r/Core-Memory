from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.tools.memory_reason import memory_reason

ROOT = Path("/home/node/.openclaw/workspace/memory")
KPI_FILE = Path("/home/node/.openclaw/workspace/eval/kpi_set.json")


def _rr(results: list[str], expected: set[str]) -> float:
    for i, rid in enumerate(results, start=1):
        if rid in expected:
            return 1.0 / i
    return 0.0


def _causal_grounding_components(reason_out: dict, root: Path) -> dict:
    cits = reason_out.get("citations") or []
    chains = reason_out.get("chains") or []
    has_decision_like = any(str(c.get("type") or "") in {"decision", "precedent"} for c in cits)
    has_evidence_like = any(str(c.get("type") or "") in {"evidence", "lesson", "outcome"} for c in cits)

    # Structural signal from returned chain edges OR radius-1 structural links among cited beads.
    has_structural_edges = any(len(c.get("edges") or []) > 0 for c in chains)
    has_structural_links = False
    cited_ids = {str(c.get("bead_id") or "") for c in cits if c.get("bead_id")}
    if cited_ids:
        idx_file = root / ".beads" / "index.json"
        if idx_file.exists():
            idx = json.loads(idx_file.read_text(encoding="utf-8"))
            beads = idx.get("beads") or {}
            allowed = {"supports", "derived_from", "supersedes", "superseded_by", "contradicts", "resolves"}
            for bid in cited_ids:
                b = beads.get(bid) or {}
                for l in (b.get("links") or []):
                    if not isinstance(l, dict):
                        continue
                    rel = str(l.get("type") or "")
                    tgt = str(l.get("bead_id") or "")
                    if rel in allowed and tgt in cited_ids:
                        has_structural_links = True
                        break
                if has_structural_links:
                    break

    has_structural = bool(has_structural_edges or has_structural_links)
    grounded = bool(has_decision_like and has_evidence_like and has_structural)
    return {
        "grounded": grounded,
        "has_decision_like": bool(has_decision_like),
        "has_evidence_like": bool(has_evidence_like),
        "has_structural": bool(has_structural),
        "has_structural_edges": bool(has_structural_edges),
        "has_structural_links": bool(has_structural_links),
    }


def _low_info_rate(reason_out: dict) -> float:
    cits = reason_out.get("citations") or []
    if not cits:
        return 1.0
    def low(c: dict) -> bool:
        t = str(c.get("title") or "").lower()
        return (not t) or ("[[reply_to_current]]" in t) or ("auto-compaction complete" in t)
    return sum(1 for c in cits if low(c)) / max(1, len(cits))


def main() -> int:
    cases = json.loads(KPI_FILE.read_text(encoding="utf-8"))
    recalls = []
    rrs = []
    lats = []
    deterministic_ok = True
    low_info_rates = []
    grounded_hits = []
    details = []

    for c in cases:
        q = c["query"]
        exp = set(c.get("expected_ids") or [])

        start = time.perf_counter()
        r1 = hybrid_lookup(ROOT, q, k=5)
        lats.append(time.perf_counter() - start)
        ids1 = [x.get("bead_id") for x in (r1.get("results") or [])]

        hit = any(i in exp for i in ids1)
        recalls.append(1.0 if hit else 0.0)
        rrs.append(_rr(ids1, exp))

        # determinism replay check (5x)
        for _ in range(5):
            r2 = hybrid_lookup(ROOT, q, k=5)
            ids2 = [x.get("bead_id") for x in (r2.get("results") or [])]
            if ids2 != ids1:
                deterministic_ok = False
                break

        m = memory_reason(q, k=5, root=str(ROOT), debug=False)
        low_info = _low_info_rate(m)
        comps = _causal_grounding_components(m, ROOT)
        low_info_rates.append(low_info)
        grounded_hits.append(1.0 if comps.get("grounded") else 0.0)
        details.append({
            "id": c.get("id"),
            "query": q,
            "top_ids": ids1,
            "hit_at_5": hit,
            "rr": _rr(ids1, exp),
            "low_info_rate": round(low_info, 4),
            "grounding": comps,
        })

    recall5 = sum(recalls) / max(1, len(recalls))
    mrr = sum(rrs) / max(1, len(rrs))
    med_lat = statistics.median(lats) if lats else 0.0
    p95_lat = statistics.quantiles(lats, n=20)[-1] if len(lats) >= 2 else (lats[0] if lats else 0.0)
    low_info = sum(low_info_rates) / max(1, len(low_info_rates))
    grounded = sum(grounded_hits) / max(1, len(grounded_hits))

    print(json.dumps({
        "cases": len(cases),
        "recall_at_5": round(recall5, 4),
        "mrr": round(mrr, 4),
        "median_latency_s": round(med_lat, 4),
        "p95_latency_s": round(p95_lat, 4),
        "deterministic": deterministic_ok,
        "low_info_citation_rate": round(low_info, 4),
        "causal_grounding_rate": round(grounded, 4),
        "details": details,
    }, indent=2))

    ok = (
        recall5 >= 0.60
        and mrr >= 0.50
        and med_lat <= 0.50
        and p95_lat <= 1.0
        and deterministic_ok
        and low_info <= 0.50
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
