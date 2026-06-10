"""Edge lifecycle: usage recording and reinforcement folding.

The "maintain" leg of the causal-memory thesis for associations. Claims have
supersession chains and contradiction pressure; this module gives causal edges
an analogous lifecycle:

- **record** — recall() appends the edges that actually contributed to a
  delivered answer to ``.beads/events/edge-usage.jsonl`` (fire-and-forget,
  mirroring the retrieval-feedback / myelination pattern: the read path logs,
  it never mutates the index).
- **fold** — at session flush, ``fold_edge_usage`` aggregates the usage log
  into the canonical association rows (``reinforcement_count`` /
  ``last_reinforced_at``) under the store lock, then truncates the log.
- **score** — traversal consumes the folded fields via
  ``graph.edge_weights.effective_edge_multiplier``: reinforced edges rank
  higher, unused edges decay toward a floor, edges through superseded beads
  are penalised.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from core_memory.persistence.io_utils import append_jsonl, store_lock

EDGE_USAGE_SCHEMA = "edge_usage.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _usage_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "edge-usage.jsonl"


def _read_index(root: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _assoc_endpoints(assoc: dict[str, Any]) -> tuple[str, str, str]:
    src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
    tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
    rel = str(assoc.get("relationship") or "").strip().lower()
    return src, tgt, rel


def _is_active(assoc: dict[str, Any]) -> bool:
    status = str(assoc.get("status") or "active").strip().lower() or "active"
    return status not in {"retracted", "superseded", "inactive"}


def collect_used_edge_pairs(
    root: str | Path,
    delivered_bead_ids: list[str],
    causal_paths: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str, str]]:
    """Return (src, dst, rel) for active associations that contributed to recall.

    An edge "contributed" when:
    - both its endpoints appear in the delivered evidence set, or
    - it lies on a causal path selected by the attribution pipeline (matched
      against a real association in either orientation, so because-derived
      read-time pseudo-edges are never reinforced).
    """
    delivered = {str(b) for b in delivered_bead_ids if str(b or "").strip()}
    index = _read_index(root)
    assocs = [a for a in (index.get("associations") or []) if isinstance(a, dict) and _is_active(a)]

    by_pair: dict[tuple[str, str], tuple[str, str, str]] = {}
    for assoc in assocs:
        src, tgt, rel = _assoc_endpoints(assoc)
        if src and tgt and rel:
            by_pair[(src, tgt)] = (src, tgt, rel)

    used: dict[tuple[str, str, str], None] = {}

    if len(delivered) >= 2:
        for (src, tgt), key in by_pair.items():
            if src in delivered and tgt in delivered:
                used[key] = None

    for path in causal_paths or []:
        if not isinstance(path, dict):
            continue
        for edge in (path.get("edges") or []):
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("src") or "")
            dst = str(edge.get("dst") or edge.get("tgt") or "")
            if not src or not dst:
                continue
            key = by_pair.get((src, dst)) or by_pair.get((dst, src))
            if key is not None:
                used[key] = None

    return list(used)


def record_edge_usage(
    root: str | Path,
    *,
    pairs: Iterable[tuple[str, str, str]],
    source: str = "recall",
    query: str = "",
) -> int:
    """Append one usage event for the given edges. Returns edges recorded."""
    rows = [[str(s), str(d), str(r)] for s, d, r in pairs if str(s) and str(d)]
    if not rows:
        return 0
    append_jsonl(
        _usage_path(root),
        {
            "schema": EDGE_USAGE_SCHEMA,
            "ts": _now(),
            "source": str(source or "recall"),
            "query_hash": hashlib.sha256(str(query or "").encode("utf-8")).hexdigest()[:16],
            "edges": rows,
        },
    )
    return len(rows)


def fold_edge_usage(root: str | Path, *, max_rows: int = 20000) -> dict[str, Any]:
    """Fold recorded edge usage into association reinforcement fields.

    Aggregates the usage log, then under the store lock increments
    ``reinforcement_count`` and refreshes ``last_reinforced_at`` on each
    matching association row (matched in either orientation) and truncates the
    log. Idempotent across crashes in the safe direction: the log is cleared
    only after the index write succeeds, so a crash between the two replays
    usage rather than losing it.
    """
    usage_path = _usage_path(root)
    if not usage_path.exists():
        return {"ok": True, "events": 0, "edges_reinforced": 0}

    counts: dict[tuple[str, str, str], int] = {}
    events = 0
    try:
        lines = usage_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {"ok": False, "error": "usage_log_unreadable"}
    for line in lines[:max_rows]:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        edges = row.get("edges") if isinstance(row, dict) else None
        if not isinstance(edges, list):
            continue
        events += 1
        for e in edges:
            if not isinstance(e, list) or len(e) < 2:
                continue
            src, dst = str(e[0]), str(e[1])
            rel = str(e[2]).strip().lower() if len(e) > 2 else ""
            if src and dst:
                counts[(src, dst, rel)] = counts.get((src, dst, rel), 0) + 1

    if not counts:
        try:
            usage_path.write_text("", encoding="utf-8")
        except Exception:
            pass
        return {"ok": True, "events": events, "edges_reinforced": 0}

    reinforced = 0
    now = _now()
    idx_file = Path(root) / ".beads" / "index.json"
    with store_lock(Path(root)):
        try:
            index = json.loads(idx_file.read_text(encoding="utf-8"))
        except Exception:
            return {"ok": False, "error": "index_unreadable"}
        for assoc in (index.get("associations") or []):
            if not isinstance(assoc, dict) or not _is_active(assoc):
                continue
            src, tgt, rel = _assoc_endpoints(assoc)
            n = counts.get((src, tgt, rel), 0) + counts.get((tgt, src, rel), 0)
            if n <= 0:
                continue
            assoc["reinforcement_count"] = int(assoc.get("reinforcement_count") or 0) + int(n)
            assoc["last_reinforced_at"] = now
            reinforced += 1
        idx_file.write_text(json.dumps(index, indent=2), encoding="utf-8")
        try:
            usage_path.write_text("", encoding="utf-8")
        except Exception:
            pass

    return {"ok": True, "events": events, "edges_reinforced": reinforced}
