from __future__ import annotations

from typing import Any

from core_memory.entity.registry import normalize_entity_alias


def _query_word_terms(text: str) -> list[str]:
    out: list[str] = []
    for raw in str(text or "").replace("_", " ").replace("-", " ").split():
        t = "".join(ch for ch in raw.lower() if ch.isalnum())
        if len(t) >= 2:
            out.append(t)
    return out


def infer_query_entity_context(query: str, registry: dict[str, Any] | None) -> dict[str, Any]:
    reg = dict(registry or {})
    alias_map = {str(k): str(v) for k, v in (reg.get("entity_aliases") or {}).items() if str(k)}
    entities = {str(k): dict(v or {}) for k, v in (reg.get("entities") or {}).items()}

    q_words = _query_word_terms(query)
    q_norm = normalize_entity_alias(query)

    matched_aliases: set[str] = set()
    resolved_entity_ids: set[str] = set()

    # direct alias containment against normalized no-space query
    for alias, eid in alias_map.items():
        a = normalize_entity_alias(alias)
        if not a or len(a) < 3:
            continue
        if a in q_norm:
            matched_aliases.add(a)
            resolved_entity_ids.add(str(eid))

    # n-gram alias exact matches
    for n in (1, 2, 3):
        for i in range(0, max(0, len(q_words) - n + 1)):
            phrase = " ".join(q_words[i : i + n])
            norm = normalize_entity_alias(phrase)
            if not norm:
                continue
            eid = alias_map.get(norm)
            if eid:
                matched_aliases.add(norm)
                resolved_entity_ids.add(str(eid))

    labels: list[str] = []
    for eid in sorted(resolved_entity_ids):
        row = entities.get(eid) or {}
        label = str(row.get("label") or row.get("normalized_label") or "").strip()
        if label:
            labels.append(label)

    return {
        "resolved_entity_ids": sorted(resolved_entity_ids),
        "matched_aliases": sorted(matched_aliases),
        "labels": labels,
    }


def expand_query_with_entities(query: str, context: dict[str, Any] | None, registry: dict[str, Any] | None, max_extra_terms: int = 12) -> str:
    base = str(query or "").strip()
    if not base:
        return base

    ctx = dict(context or {})
    reg = dict(registry or {})
    entities = {str(k): dict(v or {}) for k, v in (reg.get("entities") or {}).items()}

    extras: list[str] = []
    for eid in (ctx.get("resolved_entity_ids") or []):
        row = entities.get(str(eid)) or {}
        label = str(row.get("label") or "").strip()
        if label:
            extras.extend(label.split())
        for a in (row.get("aliases") or [])[:4]:
            a_s = str(a or "").strip()
            if a_s:
                extras.extend(a_s.split())

    seen = {t.lower() for t in base.split()}
    added: list[str] = []
    for t in extras:
        if len(added) >= max(0, int(max_extra_terms)):
            break
        tok = str(t or "").strip()
        if not tok:
            continue
        low = tok.lower()
        if low in seen:
            continue
        seen.add(low)
        added.append(tok)

    if not added:
        return base
    return (base + " " + " ".join(added)).strip()


def bead_entity_match_score(bead: dict[str, Any], context: dict[str, Any] | None) -> tuple[float, list[str]]:
    ctx = dict(context or {})
    resolved_ids = {str(x) for x in (ctx.get("resolved_entity_ids") or []) if str(x)}
    aliases = {normalize_entity_alias(str(x)) for x in (ctx.get("matched_aliases") or []) if str(x)}
    aliases = {a for a in aliases if a}

    bead_entity_ids = {str(x) for x in (bead.get("entity_ids") or []) if str(x)}
    hit_ids = sorted(bead_entity_ids.intersection(resolved_ids))
    if hit_ids:
        denom = max(1.0, float(len(resolved_ids)))
        score = min(1.0, 0.7 + (0.3 * (len(hit_ids) / denom)))
        return (score, hit_ids)

    bead_entities = {normalize_entity_alias(str(x)) for x in (bead.get("entities") or []) if str(x)}
    bead_entities = {a for a in bead_entities if a}
    overlap = sorted(bead_entities.intersection(aliases))
    if overlap:
        return (0.72, overlap)

    return (0.0, [])
