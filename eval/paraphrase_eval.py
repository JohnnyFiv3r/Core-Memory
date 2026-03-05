from __future__ import annotations

import itertools
import json
from collections import Counter
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.tools.memory_reason import memory_reason

ROOT = Path("/home/node/.openclaw/workspace/memory")
FIXTURE = Path("/home/node/.openclaw/workspace/eval/fixtures/paraphrase_kpi_pack.json")


def _top_ids(query: str, k: int) -> list[str]:
    out = hybrid_lookup(ROOT, query, k=k)
    return [str(r.get("bead_id") or "") for r in (out.get("results") or []) if r.get("bead_id")]


def _overlap(a: list[str], b: list[str], n: int) -> float:
    sa = set(a[:n])
    sb = set(b[:n])
    return len(sa.intersection(sb)) / max(1, n)


def _chain_signature(out: dict) -> str:
    chains = out.get("chains") or []
    if not chains:
        return "none"
    c = chains[0]
    path = [str(x) for x in (c.get("path") or [])]
    edges = [f"{str(e.get('rel') or '')}:{str(e.get('src') or '')}>{str(e.get('dst') or '')}" for e in (c.get("edges") or [])]
    return "|".join(path + edges) if (path or edges) else "none"


def main() -> int:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    families = fx.get("families") or []

    per_family = []
    all_cons5 = []
    all_cons10 = []

    for fam in families:
        intent_id = str(fam.get("intent_id") or "")
        phr = [str(x) for x in (fam.get("phrasings") or [])]
        runs = []
        for q in phr:
            top5 = _top_ids(q, 5)
            top10 = _top_ids(q, 10)
            reason = memory_reason(q, k=6, root=str(ROOT), debug=False)
            runs.append(
                {
                    "query": q,
                    "top5": top5,
                    "top10": top10,
                    "route": str((reason.get("intent") or {}).get("selected") or ""),
                    "chain_signature": _chain_signature(reason),
                    "cit_types": [str(c.get("type") or "") for c in (reason.get("citations") or [])],
                    "grounding": reason.get("grounding") or {},
                }
            )

        pair5 = []
        pair10 = []
        for a, b in itertools.combinations(runs, 2):
            pair5.append(_overlap(a["top5"], b["top5"], 5))
            pair10.append(_overlap(a["top10"], b["top10"], 10))

        cons5 = sum(pair5) / max(1, len(pair5))
        cons10 = sum(pair10) / max(1, len(pair10))
        all_cons5.append(cons5)
        all_cons10.append(cons10)

        route_counts = Counter([r["route"] for r in runs])
        sig_counts = Counter([r["chain_signature"] for r in runs])
        cit_counts = Counter()
        for r in runs:
            cit_counts.update(r["cit_types"])

        structural_hits = sum(1 for r in runs if bool((r.get("grounding") or {}).get("selected_has_structural")))

        per_family.append(
            {
                "intent_id": intent_id,
                "intent_class": fam.get("intent_class"),
                "paraphrase_consistency_at_5": round(cons5, 4),
                "paraphrase_consistency_at_10": round(cons10, 4),
                "route_stability": {
                    "dominant_route": route_counts.most_common(1)[0][0] if route_counts else "",
                    "dominant_route_ratio": round((route_counts.most_common(1)[0][1] / max(1, len(runs))) if route_counts else 0.0, 4),
                    "chain_signature_dominant_ratio": round((sig_counts.most_common(1)[0][1] / max(1, len(runs))) if sig_counts else 0.0, 4),
                },
                "grounding_structural_ratio": round(structural_hits / max(1, len(runs)), 4),
                "citation_type_mix": dict(cit_counts),
                "runs": runs,
            }
        )

    out = {
        "fixture": str(FIXTURE),
        "families": len(per_family),
        "summary": {
            "paraphrase_consistency_at_5": round(sum(all_cons5) / max(1, len(all_cons5)), 4),
            "paraphrase_consistency_at_10": round(sum(all_cons10) / max(1, len(all_cons10)), 4),
        },
        "per_family": per_family,
    }

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
