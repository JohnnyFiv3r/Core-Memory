"""Worldline derivation — continuity threads projected from existing structures.

A worldline is a thread of beads through time. Core Memory already contains
three kinds implicitly; this module materialises them as a read-side
projection without any schema change:

- **claim** — a fact's supersede chain: claim rows grouped by (subject, slot),
  ordered by ``chain_seq``. The worldline of what was believed and when.
- **entity** — an entity's appearances: beads carrying the entity (alias-merged
  via the registry), ordered by creation time. The worldline of a thing.
- **goal** — a goal bead plus the beads linked to it by active associations
  (outcomes arrive via the goal lifecycle's ``resolves`` edges). The worldline
  of an intention.

Worldlines feed the continuity-depth metric ("worldline participation") and
the Worldline Lens. They are derived, never stored — the graph is the
substrate, worldlines are a perspective over it.

Layering: imports persistence only; entity grouping reads the registry
structures directly from the index rather than importing ``entity/``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from core_memory.persistence.store_claim_ops import read_all_claim_rows

WORLDLINE_KINDS = ("claim", "entity", "goal")

# Association statuses excluded from goal-thread membership.
_INACTIVE = {"retracted", "superseded", "inactive"}


def _read_index(root: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _wl_id(kind: str, key: str) -> str:
    digest = hashlib.sha256(f"{kind}:{key}".encode("utf-8")).hexdigest()[:12]
    return f"wl-{kind}-{digest}"


def _bead_created_at(beads: dict[str, Any], bead_id: str) -> str:
    bead = beads.get(bead_id)
    return str((bead or {}).get("created_at") or "") if isinstance(bead, dict) else ""


def _span(beads: dict[str, Any], bead_ids: list[str]) -> dict[str, str]:
    stamps = sorted(s for s in (_bead_created_at(beads, b) for b in bead_ids) if s)
    return {"from": stamps[0] if stamps else "", "to": stamps[-1] if stamps else ""}


def _norm_entity(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _claim_worldlines(root: str | Path, beads: dict[str, Any]) -> list[dict[str, Any]]:
    all_claims, _updates = read_all_claim_rows(str(root))
    by_slot: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for claim in all_claims:
        if not isinstance(claim, dict):
            continue
        subject = str(claim.get("subject") or "").strip()
        slot = str(claim.get("slot") or "").strip()
        if not subject or not slot or not str(claim.get("source_bead_id") or "").strip():
            continue
        by_slot.setdefault((subject, slot), []).append(claim)

    out: list[dict[str, Any]] = []
    for (subject, slot), rows in sorted(by_slot.items()):
        def _order(claim: dict[str, Any]) -> tuple[int, str]:
            try:
                seq = int(claim.get("chain_seq")) if claim.get("chain_seq") is not None else 0
            except (TypeError, ValueError):
                seq = 0
            bid = str(claim.get("source_bead_id") or "")
            return (seq, str(claim.get("observed_at") or claim.get("created_at") or _bead_created_at(beads, bid)))

        bead_ids: list[str] = []
        for claim in sorted(rows, key=_order):
            bid = str(claim.get("source_bead_id") or "")
            if bid and bid not in bead_ids:
                bead_ids.append(bid)
        if not bead_ids:
            continue
        key = f"{subject}/{slot}"
        out.append({
            "id": _wl_id("claim", key),
            "kind": "claim",
            "key": key,
            "label": f"{subject} · {slot}",
            "bead_ids": bead_ids,
            "length": len(bead_ids),
            "span": _span(beads, bead_ids),
            "status": "active",
        })
    return out


def _entity_worldlines(index: dict[str, Any], beads: dict[str, Any]) -> list[dict[str, Any]]:
    entities = index.get("entities") if isinstance(index.get("entities"), dict) else {}
    aliases = index.get("entity_aliases") if isinstance(index.get("entity_aliases"), dict) else {}

    # normalized text → canonical key (registry entity id when known).
    canonical: dict[str, str] = {}
    labels: dict[str, str] = {}
    for normalized, eid in aliases.items():
        canonical[_norm_entity(normalized)] = str(eid)
    for eid, row in entities.items():
        if not isinstance(row, dict):
            continue
        for field in ("normalized_label", "label"):
            text = _norm_entity(str(row.get(field) or ""))
            if text:
                canonical.setdefault(text, str(eid))
        labels[str(eid)] = str(row.get("label") or row.get("normalized_label") or eid)

    threads: dict[str, list[tuple[str, str]]] = {}
    thread_labels: dict[str, str] = {}
    for bead_id, bead in beads.items():
        if not isinstance(bead, dict):
            continue
        for raw in (bead.get("entities") or []):
            text = _norm_entity(str(raw or ""))
            if not text:
                continue
            key = canonical.get(text, text)
            threads.setdefault(key, []).append((_bead_created_at(beads, str(bead_id)), str(bead_id)))
            thread_labels.setdefault(key, labels.get(key, str(raw)))

    out: list[dict[str, Any]] = []
    for key in sorted(threads):
        ordered: list[str] = []
        for _at, bid in sorted(threads[key]):
            if bid not in ordered:
                ordered.append(bid)
        out.append({
            "id": _wl_id("entity", key),
            "kind": "entity",
            "key": key,
            "label": thread_labels.get(key, key),
            "bead_ids": ordered,
            "length": len(ordered),
            "span": _span(beads, ordered),
            "status": "active",
        })
    return out


def _goal_worldlines(index: dict[str, Any], beads: dict[str, Any]) -> list[dict[str, Any]]:
    linked: dict[str, list[str]] = {}
    resolved: set[str] = set()
    for assoc in (index.get("associations") or []):
        if not isinstance(assoc, dict):
            continue
        status = str(assoc.get("status") or "active").strip().lower() or "active"
        if status in _INACTIVE:
            continue
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
        tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
        if not src or not tgt:
            continue
        for goal_id, other in ((tgt, src), (src, tgt)):
            bead = beads.get(goal_id)
            if isinstance(bead, dict) and str(bead.get("type") or "").strip().lower() == "goal":
                linked.setdefault(goal_id, []).append(other)
                if str(assoc.get("relationship") or "").strip().lower() == "resolves" and goal_id == tgt:
                    resolved.add(goal_id)

    out: list[dict[str, Any]] = []
    for goal_id, bead in sorted(beads.items()):
        if not isinstance(bead, dict) or str(bead.get("type") or "").strip().lower() != "goal":
            continue
        members = [goal_id]
        for bid in sorted(set(linked.get(goal_id, [])), key=lambda b: _bead_created_at(beads, b)):
            if bid not in members:
                members.append(bid)
        out.append({
            "id": _wl_id("goal", goal_id),
            "kind": "goal",
            "key": goal_id,
            "label": str(bead.get("title") or goal_id),
            "bead_ids": members,
            "length": len(members),
            "span": _span(beads, members),
            "status": "resolved" if goal_id in resolved else "open",
        })
    return out


def derive_worldlines(
    root: str | Path,
    *,
    kinds: list[str] | None = None,
    min_length: int = 1,
) -> dict[str, Any]:
    """Derive worldlines from the canonical index.

    Returns ``{"ok", "worldlines": [...], "counts": {kind: n}}``. Each
    worldline: ``{id, kind, key, label, bead_ids (time-ordered), length,
    span: {from, to}, status}``.
    """
    selected = [k for k in (kinds or list(WORLDLINE_KINDS)) if k in WORLDLINE_KINDS]
    index = _read_index(root)
    beads = index.get("beads") if isinstance(index.get("beads"), dict) else {}

    worldlines: list[dict[str, Any]] = []
    if "claim" in selected:
        worldlines.extend(_claim_worldlines(root, beads))
    if "entity" in selected:
        worldlines.extend(_entity_worldlines(index, beads))
    if "goal" in selected:
        worldlines.extend(_goal_worldlines(index, beads))

    min_length = max(1, int(min_length))
    worldlines = [w for w in worldlines if int(w.get("length") or 0) >= min_length]

    counts: dict[str, int] = {}
    for w in worldlines:
        counts[w["kind"]] = counts.get(w["kind"], 0) + 1
    return {"ok": True, "worldlines": worldlines, "counts": counts, "total": len(worldlines)}


def worldline_membership(root: str | Path, *, kinds: list[str] | None = None) -> dict[str, int]:
    """Per-bead worldline participation: bead_id → number of worldlines.

    This is the "worldline participation" input to the continuity-depth
    metric: beads threaded through many continuity structures sit deeper.
    """
    derived = derive_worldlines(root, kinds=kinds)
    counts: dict[str, int] = {}
    for w in derived.get("worldlines") or []:
        for bid in w.get("bead_ids") or []:
            counts[bid] = counts.get(bid, 0) + 1
    return counts
