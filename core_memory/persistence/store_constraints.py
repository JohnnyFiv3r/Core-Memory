from __future__ import annotations

import re
import shlex
from typing import Any


def active_constraints_for_store(store: Any, limit: int = 100) -> list[dict]:
    """Return active constraints from decision/design_principle/goal beads."""
    index = store._read_json(store.beads_dir / "index.json")
    beads = list(index.get("beads", {}).values())
    beads = sorted(beads, key=lambda b: b.get("created_at", ""), reverse=True)
    rows: list[dict] = []
    for b in beads:
        if str(b.get("status", "")).lower() in {"superseded"}:
            continue
        if b.get("type") not in {"decision", "design_principle", "goal"}:
            continue
        constraints = b.get("constraints") or []
        if not constraints:
            tags = {str(t).strip().lower() for t in (b.get("tags") or [])}
            if "sidecar" in tags and "turn-finalized" in tags:
                continue
            text = " ".join([b.get("title", "")] + list(b.get("summary") or []))
            constraints = store.extract_constraints(text)
        if not constraints:
            continue
        rows.append(
            {
                "bead_id": b.get("id"),
                "type": b.get("type"),
                "title": b.get("title"),
                "constraints": constraints[:5],
                "created_at": b.get("created_at"),
            }
        )
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def _constraint_hits(constraint: str, plan_tokens: set[str]) -> bool:
    c = re.sub(r"\s+", " ", constraint.lower()).strip()
    if not c:
        return False
    ctoks = {t for t in re.findall(r"[a-z0-9_\-]+", c) if len(t) > 2}
    if not ctoks:
        return False
    return len(ctoks.intersection(plan_tokens)) >= max(1, min(2, len(ctoks) // 3))


def check_plan_constraints_for_store(store: Any, plan: str, limit: int = 20) -> dict:
    """Advisory compliance check: map active constraints to satisfied/violated/unknown."""
    plan_text = (plan or "").lower().strip()
    plan_tokens = set(shlex.split(plan_text)) if plan_text else set()
    active = active_constraints_for_store(store, limit=limit)
    satisfied = []
    violated = []
    unknown = []

    for row in active:
        row_s = {"bead_id": row["bead_id"], "title": row["title"], "constraints": []}
        row_v = {"bead_id": row["bead_id"], "title": row["title"], "constraints": []}
        row_u = {"bead_id": row["bead_id"], "title": row["title"], "constraints": []}
        for c in row.get("constraints", []):
            cl = c.lower()
            has_not = any(x in cl for x in ["must not", "never", "do not", "avoid"])
            hit = _constraint_hits(c, plan_tokens)
            if has_not:
                if hit:
                    row_v["constraints"].append(c)
                else:
                    row_s["constraints"].append(c)
            else:
                if hit:
                    row_s["constraints"].append(c)
                else:
                    row_u["constraints"].append(c)
        if row_s["constraints"]:
            satisfied.append(row_s)
        if row_v["constraints"]:
            violated.append(row_v)
        if row_u["constraints"]:
            unknown.append(row_u)

    return {
        "ok": True,
        "mode": "advisory",
        "plan": plan,
        "active_constraints": len(active),
        "satisfied": satisfied,
        "violated": violated,
        "unknown": unknown,
        "recommendation": "review" if violated else "proceed",
    }


__all__ = ["active_constraints_for_store", "check_plan_constraints_for_store"]
