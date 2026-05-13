from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


_ENTITY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "here",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "this",
    "those",
    "to",
    "was",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "you",
    "your",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_entity_alias(value: str | None) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"[\s\-_/]+", " ", s)
    s = re.sub(r"[^a-z0-9\s]+", "", s)
    s = re.sub(r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|co|company)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace(" ", "")


def _is_valid_entity_alias(raw_label: str, normalized: str) -> bool:
    raw = str(raw_label or "").strip()
    norm = str(normalized or "").strip().lower()
    if not raw or not norm:
        return False
    if norm in _ENTITY_STOPWORDS:
        return False
    # A single ordinary word is usually noise unless it has stronger shape.
    if len(norm) < 4 and not any(ch.isdigit() for ch in norm):
        return False
    return True


def _new_entity_id(normalized_label: str) -> str:
    digest = hashlib.sha1(normalized_label.encode("utf-8")).hexdigest()[:12]
    return f"entity-{digest}"


def _clean_text(value: Any, *, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit].strip()


def _clean_list(value: Any, *, limit: int = 8, item_limit: int = 120) -> list[str]:
    rows = value if isinstance(value, list) else ([] if value is None else [value])
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            row = row.get("label") or row.get("name") or row.get("value") or row.get("text") or ""
        s = _clean_text(row, limit=item_limit)
        if not s:
            continue
        key = normalize_entity_alias(s)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _existing_entity_context(index: dict[str, Any], *, limit: int = 80) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity_id, row in (index.get("entities") or {}).items():
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "id": str(entity_id),
                "label": _clean_text(row.get("label") or row.get("normalized_label"), limit=120),
                "aliases": _clean_list(row.get("aliases"), limit=12, item_limit=80),
            }
        )
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def _entity_text_for_bead(bead: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("title", "detail"):
        value = _clean_text(bead.get(field), limit=1200)
        if value:
            parts.append(f"{field}: {value}")
    for field in ("summary", "retrieval_facts", "supporting_facts", "topics"):
        values = _clean_list(bead.get(field), limit=12, item_limit=240)
        if values:
            parts.append(f"{field}: " + "; ".join(values))
    entities = _clean_list(bead.get("entities"), limit=20, item_limit=120)
    if entities:
        parts.append("current_bead_entities: " + "; ".join(entities))
    return "\n".join(parts)[:6000]


_ENTITY_PROMPT = """You are the Core Memory entity extraction judge for one finalized memory bead.

Return JSON only with this shape:
{{
  "entities": [
    {{"label":"canonical display name", "aliases":["surface form or alias"], "kind":"person|organization|project|product|place|system|dataset|concept|other", "evidence":"short exact/near-exact support", "confidence":0.0}}
  ]
}}

Rules:
- Extract durable named entities, project/product names, people, organizations, places, systems, datasets, and stable domain concepts that are useful for future retrieval.
- Prefer canonical names and reuse an existing entity label/alias when the bead clearly refers to it.
- Include aliases only when grounded by the bead text or existing registry context.
- Do not emit generic nouns, verbs, adjectives, broad topics, or instruction words as entities.
- Do not invent people, organizations, dates, IDs, or aliases.
- If the turn is just a question/retrieval request with no durable named entity, return an empty list.
- Keep the list short and deduplicated.

Existing registry context:
{registry_context}

Bead text:
{bead_text}
"""


def _parse_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.strip().startswith("json"):
            raw = raw.strip()[4:]
    try:
        obj = json.loads(raw.strip())
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _llm_judge_entities_anthropic(index: dict[str, Any], bead: dict[str, Any]) -> dict[str, Any] | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic  # type: ignore

        client = anthropic.Anthropic(api_key=key)
        model = os.getenv("CORE_MEMORY_ENTITY_EXTRACTOR_MODEL") or os.getenv("CORE_MEMORY_BEAD_FIELD_MODEL") or "claude-haiku-4-5-20251001"
        resp = client.messages.create(
            model=model,
            max_tokens=650,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": _ENTITY_PROMPT.format(
                        registry_context=json.dumps(_existing_entity_context(index), ensure_ascii=False),
                        bead_text=_entity_text_for_bead(bead),
                    ),
                }
            ],
        )
        return _parse_json(resp.content[0].text)
    except Exception as exc:
        logger.debug("anthropic entity extraction judge failed: %s", exc)
        return None


def _llm_judge_entities_openai(index: dict[str, Any], bead: dict[str, Any]) -> dict[str, Any] | None:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI  # type: ignore

        client = OpenAI(api_key=key)
        model = os.getenv("CORE_MEMORY_ENTITY_EXTRACTOR_MODEL") or os.getenv("CORE_MEMORY_BEAD_FIELD_MODEL") or "gpt-4o-mini"
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=650,
            messages=[
                {
                    "role": "user",
                    "content": _ENTITY_PROMPT.format(
                        registry_context=json.dumps(_existing_entity_context(index), ensure_ascii=False),
                        bead_text=_entity_text_for_bead(bead),
                    ),
                }
            ],
        )
        return _parse_json(resp.choices[0].message.content or "")
    except Exception as exc:
        logger.debug("openai entity extraction judge failed: %s", exc)
        return None


def _normalize_entity_judge_output(obj: dict[str, Any] | None, bead: dict[str, Any], *, mode: str) -> dict[str, Any]:
    rows = (obj or {}).get("entities") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        rows = []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            row = {"label": row, "aliases": [row], "confidence": 0.65}
        if not isinstance(row, dict):
            continue
        label = _clean_text(row.get("label") or row.get("name"), limit=120)
        normalized = normalize_entity_alias(label)
        if not _is_valid_entity_alias(label, normalized) or normalized in seen:
            continue
        aliases = _clean_list(row.get("aliases"), limit=12, item_limit=120)
        if label and normalize_entity_alias(label) not in {normalize_entity_alias(a) for a in aliases}:
            aliases.insert(0, label)
        try:
            confidence = float(row.get("confidence", 0.72))
        except Exception:
            confidence = 0.72
        seen.add(normalized)
        out.append(
            {
                "label": label,
                "aliases": aliases[:12],
                "kind": _clean_text(row.get("kind"), limit=40) or "other",
                "evidence": _clean_text(row.get("evidence"), limit=240),
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )
        if len(out) >= 20:
            break
    return {"mode": mode, "entities": out}


def judge_bead_entities_for_index(index: dict[str, Any], bead: dict[str, Any]) -> dict[str, Any]:
    """LLM-first entity extraction/canonicalization for one bead.

    The live write path should prefer this judged pass so entity labels and aliases
    are canonicalized consistently. The deterministic fallback is intentionally
    narrow and uses existing bead-provided labels rather than broad regex NER.
    """
    mode = str(os.getenv("CORE_MEMORY_ENTITY_EXTRACTOR_MODE") or "auto").strip().lower()
    if mode not in {"auto", "llm", "heuristic", "off"}:
        mode = "auto"
    if mode in {"auto", "llm"} and _entity_text_for_bead(bead):
        obj = _llm_judge_entities_anthropic(index, bead)
        if obj is None:
            obj = _llm_judge_entities_openai(index, bead)
        if isinstance(obj, dict):
            return _normalize_entity_judge_output(obj, bead, mode="llm")
        if mode == "llm":
            fallback = _normalize_entity_judge_output({"entities": list(bead.get("entities") or [])}, bead, mode="llm_failed_fallback")
            return fallback
    return _normalize_entity_judge_output({"entities": list(bead.get("entities") or [])}, bead, mode="heuristic")


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
    if not _is_valid_entity_alias(raw_label, normalized):
        return {"ok": False, "error": "invalid_label"}

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
    judged = judge_bead_entities_for_index(index, bead)
    mentions = list(judged.get("entities") or [])
    if not mentions:
        bead.setdefault("entity_ids", [])
        return {"ok": True, "linked": 0, "entity_ids": list(bead.get("entity_ids") or []), "created": 0}

    linked: list[str] = []
    created = 0
    bead_id = str(bead.get("id") or "")
    canonical_labels: list[str] = []
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        label = str(mention.get("label") or "").strip()
        if not label:
            continue
        aliases = [str(x) for x in (mention.get("aliases") or []) if str(x).strip()]
        try:
            confidence = float(mention.get("confidence", 0.72))
        except Exception:
            confidence = 0.72
        res = upsert_canonical_entity(
            index,
            label=label,
            aliases=aliases or [label],
            confidence=confidence,
            provenance={
                "kind": "bead",
                "bead_id": bead_id,
                "source": source,
                "judge": str(judged.get("mode") or "heuristic"),
                "entity_kind": str(mention.get("kind") or "other"),
                "evidence": str(mention.get("evidence") or "")[:240],
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
        canonical_labels.append(label)

    bead["entity_ids"] = linked
    if canonical_labels:
        bead["entities"] = canonical_labels
    return {"ok": True, "linked": len(linked), "entity_ids": linked, "created": created, "judge": str(judged.get("mode") or "heuristic")}


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
