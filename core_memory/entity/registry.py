from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_entity_alias(value: str | None) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"[\s\-_/]+", " ", s)
    s = re.sub(r"[^a-z0-9\s]+", "", s)
    s = re.sub(r"\b(inc|corp|llc|ltd|co|company)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace(" ", "")


def _new_entity_id(normalized_label: str) -> str:
    digest = hashlib.sha1(normalized_label.encode("utf-8")).hexdigest()[:12]
    return f"entity-{digest}"


def ensure_entity_registry_for_index(index: dict[str, Any]) -> None:
    entities = index.get("entities")
    if not isinstance(entities, dict):
        index["entities"] = {}
    alias_map = index.get("entity_aliases")
    if not isinstance(alias_map, dict):
        index["entity_aliases"] = {}


def _find_entity_id(index: dict[str, Any], normalized: str) -> str | None:
    if not normalized:
        return None
    ensure_entity_registry_for_index(index)
    alias_map = index.get("entity_aliases") or {}
    eid = str(alias_map.get(normalized) or "").strip()
    if eid:
        return eid
    for entity_id, row in (index.get("entities") or {}).items():
        if not isinstance(row, dict):
            continue
        if normalize_entity_alias(str(row.get("normalized_label") or row.get("label") or "")) == normalized:
            return str(entity_id)
        aliases = [normalize_entity_alias(str(x)) for x in (row.get("aliases") or [])]
        if normalized in aliases:
            return str(entity_id)
    return None


def upsert_canonical_entity(
    index: dict[str, Any],
    *,
    label: str,
    aliases: list[str] | None = None,
    confidence: float = 0.7,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_entity_registry_for_index(index)

    raw_label = str(label or "").strip()
    normalized = normalize_entity_alias(raw_label)
    if not normalized:
        return {"ok": False, "error": "empty_label"}

    entity_id = _find_entity_id(index, normalized)
    created = False
    entities = index.get("entities") or {}

    if not entity_id:
        entity_id = _new_entity_id(normalized)
        created = True

    row = dict((entities.get(entity_id) or {}))
    if not row:
        row = {
            "id": entity_id,
            "label": raw_label or normalized,
            "normalized_label": normalized,
            "aliases": [],
            "confidence": float(max(0.0, min(1.0, confidence))),
            "provenance": [],
            "created_at": _now(),
            "updated_at": _now(),
            "status": "active",
        }

    alias_set = {normalize_entity_alias(str(a)) for a in (row.get("aliases") or [])}
    alias_set.add(normalized)
    for a in (aliases or []):
        n = normalize_entity_alias(str(a))
        if n:
            alias_set.add(n)
    if raw_label:
        n_label = normalize_entity_alias(raw_label)
        if n_label:
            alias_set.add(n_label)

    alias_list = sorted(a for a in alias_set if a)
    row["aliases"] = alias_list
    row["normalized_label"] = normalized
    if raw_label and (not row.get("label") or row.get("label") == row.get("normalized_label")):
        row["label"] = raw_label
    row["confidence"] = max(float(row.get("confidence") or 0.0), float(max(0.0, min(1.0, confidence))))
    row["updated_at"] = _now()

    prov = dict(provenance or {})
    if prov:
        prov.setdefault("ts", _now())
        prov_rows = list(row.get("provenance") or [])
        key = (
            str(prov.get("kind") or ""),
            str(prov.get("bead_id") or ""),
            str(prov.get("source") or ""),
        )
        seen = {
            (
                str((r or {}).get("kind") or ""),
                str((r or {}).get("bead_id") or ""),
                str((r or {}).get("source") or ""),
            )
            for r in prov_rows
            if isinstance(r, dict)
        }
        if key not in seen:
            prov_rows.append(prov)
        row["provenance"] = prov_rows[-50:]

    entities[entity_id] = row
    index["entities"] = entities

    alias_map = index.get("entity_aliases") or {}
    for a in alias_list:
        alias_map[a] = entity_id
    index["entity_aliases"] = alias_map

    return {
        "ok": True,
        "entity_id": entity_id,
        "created": created,
        "entity": row,
    }


def resolve_entity_id(index: dict[str, Any], alias: str | None) -> str | None:
    normalized = normalize_entity_alias(alias)
    if not normalized:
        return None
    return _find_entity_id(index, normalized)


def sync_bead_entities_for_index(
    index: dict[str, Any],
    bead: dict[str, Any],
    *,
    source: str = "bead_entities",
) -> dict[str, Any]:
    ensure_entity_registry_for_index(index)
    entities_raw = [str(x).strip() for x in (bead.get("entities") or []) if str(x).strip()]
    if not entities_raw:
        bead.setdefault("entity_ids", [])
        return {"ok": True, "linked": 0, "entity_ids": list(bead.get("entity_ids") or []), "created": 0}

    linked: list[str] = []
    created = 0
    bead_id = str(bead.get("id") or "")
    for text in entities_raw:
        res = upsert_canonical_entity(
            index,
            label=text,
            aliases=[text],
            confidence=0.72,
            provenance={
                "kind": "bead",
                "bead_id": bead_id,
                "source": source,
            },
        )
        if not res.get("ok"):
            continue
        eid = str(res.get("entity_id") or "")
        if not eid:
            continue
        if bool(res.get("created")):
            created += 1
        if eid not in linked:
            linked.append(eid)

    bead["entity_ids"] = linked
    return {"ok": True, "linked": len(linked), "entity_ids": linked, "created": created}


def load_entity_registry(root: str | Path) -> dict[str, Any]:
    p = Path(root) / ".beads" / "index.json"
    if not p.exists():
        return {"entities": {}, "entity_aliases": {}}
    try:
        import json

        idx = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(idx, dict):
            return {"entities": {}, "entity_aliases": {}}
        ensure_entity_registry_for_index(idx)
        return {
            "entities": dict(idx.get("entities") or {}),
            "entity_aliases": dict(idx.get("entity_aliases") or {}),
        }
    except Exception:
        return {"entities": {}, "entity_aliases": {}}
