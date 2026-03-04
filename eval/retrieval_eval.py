from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup

ROOT = Path("/home/node/.openclaw/workspace/memory")
KPI_FILE = Path("/home/node/.openclaw/workspace/eval/kpi_set.json")


def _rr(results: list[str], expected: set[str]) -> float:
    for i, rid in enumerate(results, start=1):
        if rid in expected:
            return 1.0 / i
    return 0.0


def main() -> int:
    cases = json.loads(KPI_FILE.read_text(encoding="utf-8"))
    recalls = []
    rrs = []
    lats = []
    deterministic_ok = True

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

    recall5 = sum(recalls) / max(1, len(recalls))
    mrr = sum(rrs) / max(1, len(rrs))
    med_lat = statistics.median(lats) if lats else 0.0

    print(json.dumps({
        "cases": len(cases),
        "recall_at_5": round(recall5, 4),
        "mrr": round(mrr, 4),
        "median_latency_s": round(med_lat, 4),
        "deterministic": deterministic_ok,
    }, indent=2))

    ok = recall5 >= 0.60 and mrr >= 0.50 and med_lat <= 0.50 and deterministic_ok
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
