"""Storylines — worldline backbones joined with interpretive overlays.

The product primitive: ``storyline = backbone + overlay``.

- **backbone** — a derived worldline (claim chain / entity thread / goal arc):
  grounded, evidence-backed, never stored, never generated. "What happened."
- **overlay** — accepted ``storyline_overlay.v1`` records from the dreamer's
  decide flow: interpretive, confidence-carrying, versioned, falsifiable.
  "What it means."
- **tensions** — computed, never stored: competing active overlays on the
  same backbone, and claim worldlines whose slot currently resolves to
  conflict.
- **evidence** — every backbone bead is hydratable through the standard
  source-recovery surfaces.

THE ONE-WAY RULE: overlays are read here and only here on the derivation
side. Nothing in backbone derivation (worldlines, claims, entities,
associations) may ever consume overlay records — interpretation must never
become input to history. Tests assert backbone output is byte-identical with
and without the overlays file present.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.graph.worldlines import derive_worldlines
from core_memory.persistence.store_claim_ops import resolve_current_state
from core_memory.schema.storyline_overlay import validate_storyline_overlay

OVERLAYS_FILENAME = "overlays.jsonl"


def overlays_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / OVERLAYS_FILENAME


def read_all_overlays(root: str | Path) -> list[dict[str, Any]]:
    """All overlay records, append order, invalid rows skipped."""
    path = overlays_path(root)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        ok, _, _ = validate_storyline_overlay(row)
        if ok:
            out.append(row)
    return out


def read_active_overlays(root: str | Path) -> list[dict[str, Any]]:
    """Overlays not superseded by a later record."""
    rows = read_all_overlays(root)
    superseded = {str(r.get("supersedes_overlay_id") or "") for r in rows}
    superseded.discard("")
    return [r for r in rows if str(r.get("id") or "") not in superseded]


def derive_storylines(
    root: str | Path,
    *,
    kinds: list[str] | None = None,
    min_length: int = 1,
    include_superseded: bool = False,
) -> dict[str, Any]:
    """Join worldline backbones with their overlays and computed tensions.

    Returns ``{ok, storylines: [...], counts, total}``. Each storyline:
    ``{id, backbone, overlays, overlay_history_count, tensions, evidence}``
    where ``id`` is the backbone worldline id (a storyline IS its backbone,
    interpreted).
    """
    derived = derive_worldlines(root, kinds=kinds, min_length=min_length)
    worldlines = list(derived.get("worldlines") or [])

    all_overlays = read_all_overlays(root)
    superseded_ids = {str(r.get("supersedes_overlay_id") or "") for r in all_overlays}
    superseded_ids.discard("")

    by_worldline: dict[str, list[dict[str, Any]]] = {}
    history_count: dict[str, int] = {}
    for overlay in all_overlays:
        active = str(overlay.get("id") or "") not in superseded_ids
        for wl_id in overlay.get("supporting_worldline_ids") or []:
            wl = str(wl_id)
            history_count[wl] = history_count.get(wl, 0) + 1
            if active or include_superseded:
                by_worldline.setdefault(wl, []).append(
                    {**overlay, "status": "active" if active else "superseded"}
                )

    storylines: list[dict[str, Any]] = []
    for w in worldlines:
        overlays = by_worldline.get(w["id"], [])
        active_overlays = [o for o in overlays if o.get("status") == "active"]

        # Display naming: an accepted overlay may carry a reviewed title — the
        # storyline's name. Without one the storyline falls back to its
        # backbone label (entity text / subject·slot / goal title). Reading
        # overlay titles here is derivation-side-legal: storylines are the one
        # place overlays are joined in (the one-way rule protects backbone
        # derivation, not this projection).
        overlay_title = ""
        for o in sorted(active_overlays, key=lambda r: str(r.get("created_at") or ""), reverse=True):
            candidate_title = str(o.get("title") or "").strip()
            if candidate_title:
                overlay_title = candidate_title
                break

        tensions: list[dict[str, Any]] = []
        if len(active_overlays) > 1:
            tensions.append({
                "kind": "competing_overlays",
                "overlay_ids": [str(o.get("id") or "") for o in active_overlays],
                "detail": f"{len(active_overlays)} active interpretations over one backbone.",
            })
        if w.get("kind") == "claim" and "/" in str(w.get("key") or ""):
            subject, slot = str(w["key"]).split("/", 1)
            try:
                resolution = resolve_current_state(str(root), subject, slot)
            except Exception:
                resolution = {}
            if str(resolution.get("status") or "") == "conflict":
                tensions.append({
                    "kind": "claim_conflict",
                    "subject": subject,
                    "slot": slot,
                    "detail": "The backbone's claim slot currently resolves to conflict.",
                })

        storylines.append({
            "id": w["id"],
            "label": overlay_title or str(w.get("label") or w.get("key") or w["id"]),
            "title": overlay_title,
            "backbone": w,
            "overlays": overlays,
            "overlay_history_count": history_count.get(w["id"], 0),
            "tensions": tensions,
            "evidence": {"bead_count": len(w.get("bead_ids") or [])},
        })

    counts: dict[str, int] = {"with_overlays": 0, "with_tensions": 0}
    for s in storylines:
        if s["overlays"]:
            counts["with_overlays"] += 1
        if s["tensions"]:
            counts["with_tensions"] += 1

    return {
        "ok": True,
        "storylines": storylines,
        "counts": counts,
        "total": len(storylines),
        "overlay_records": len(all_overlays),
    }
