"""Schema + loaders for the causal-chain reconstruction benchmark.

A fixture row describes a synthetic causal history:

    {
      "id": "linear_chain_basic",
      "gold_id": "linear_chain_basic",
      "query": "why did checkout latency spike",
      "bucket_labels": ["linear_chain"],
      "beads": [
        {"key": "root", "type": "decision", "title": "...", "summary": ["..."],
         "entities": ["..."], "topics": ["..."]},
        ...
      ],
      "edges": [
        {"source_key": "cause", "target_key": "effect",
         "relationship": "causes", "confidence": 0.9}
      ],
      "distractor_keys": ["dashboard"],
      "k": 8
    }

The matching gold row records the expected reconstruction:

    {
      "id": "linear_chain_basic",
      "gold_root_cause_key": "root",
      "gold_chain_keys": ["outcome", "intermediate", "root"],
      "expected_grounding": "full",
      "bucket_labels": ["linear_chain"]
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REQUIRED_FIXTURE_KEYS = {"id", "query", "bucket_labels", "gold_id", "beads", "edges"}
REQUIRED_GOLD_KEYS = {"id", "gold_root_cause_key", "bucket_labels"}
ALLOWED_GROUNDING = {"full", "partial", "none"}


@dataclass(frozen=True)
class CausalCase:
    id: str
    query: str
    intent: str
    bucket_labels: tuple[str, ...]
    gold_id: str
    beads: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    distractor_keys: tuple[str, ...]
    k: int


@dataclass(frozen=True)
class CausalGold:
    id: str
    gold_root_cause_key: str
    gold_chain_keys: tuple[str, ...] = field(default_factory=tuple)
    expected_grounding: str = "full"
    bucket_labels: tuple[str, ...] = field(default_factory=tuple)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid_jsonl:{path}:{line_no}:{exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"invalid_jsonl_row_not_object:{path}:{line_no}")
            out.append(row)
    return out


def validate_fixture_row(row: dict[str, Any]) -> tuple[bool, list[str]]:
    errs: list[str] = []
    for k in sorted(REQUIRED_FIXTURE_KEYS - set(row.keys())):
        errs.append(f"missing:{k}")

    if "id" in row and not str(row.get("id") or "").strip():
        errs.append("id_empty")
    if "query" in row and not str(row.get("query") or "").strip():
        errs.append("query_empty")

    buckets = row.get("bucket_labels")
    if not isinstance(buckets, list) or not buckets:
        errs.append("bucket_labels_invalid")

    beads = row.get("beads")
    if not isinstance(beads, list) or not beads:
        errs.append("beads_invalid")
    else:
        keys = [str(b.get("key") or "").strip() for b in beads if isinstance(b, dict)]
        if any(not k for k in keys):
            errs.append("bead_key_empty")
        if len(set(keys)) != len(keys):
            errs.append("bead_key_duplicate")

    edges = row.get("edges")
    if not isinstance(edges, list) or not edges:
        errs.append("edges_invalid")
    else:
        bead_keys = {str(b.get("key") or "").strip() for b in (beads or []) if isinstance(b, dict)}
        for e in edges:
            if not isinstance(e, dict):
                errs.append("edge_not_object")
                continue
            src = str(e.get("source_key") or "").strip()
            tgt = str(e.get("target_key") or "").strip()
            rel = str(e.get("relationship") or "").strip()
            if not src or not tgt or not rel:
                errs.append("edge_incomplete")
            if src and src not in bead_keys:
                errs.append(f"edge_source_unknown:{src}")
            if tgt and tgt not in bead_keys:
                errs.append(f"edge_target_unknown:{tgt}")

    distractors = row.get("distractor_keys") or []
    if not isinstance(distractors, list):
        errs.append("distractor_keys_invalid")

    return (len(errs) == 0, errs)


def validate_gold_row(row: dict[str, Any]) -> tuple[bool, list[str]]:
    errs: list[str] = []
    for k in sorted(REQUIRED_GOLD_KEYS - set(row.keys())):
        errs.append(f"missing:{k}")

    if "gold_root_cause_key" in row and not str(row.get("gold_root_cause_key") or "").strip():
        errs.append("gold_root_cause_key_empty")

    grounding = str(row.get("expected_grounding") or "full").strip()
    if grounding and grounding not in ALLOWED_GROUNDING:
        errs.append("expected_grounding_invalid")

    buckets = row.get("bucket_labels")
    if not isinstance(buckets, list) or not buckets:
        errs.append("bucket_labels_invalid")

    return (len(errs) == 0, errs)


def load_fixture_rows(fixtures_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(fixtures_dir.glob("*.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows


def load_gold_rows(gold_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(gold_dir.glob("*.json")):
        payload = _read_json(path)
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict) and isinstance(payload.get("cases"), list):
            candidates = payload.get("cases") or []
        else:
            raise ValueError(f"gold_invalid_shape:{path}")
        for row in candidates:
            if not isinstance(row, dict):
                raise ValueError(f"gold_row_not_object:{path}")
            case_id = str(row.get("id") or "").strip()
            if not case_id:
                raise ValueError(f"gold_missing_id:{path}")
            out[case_id] = row
    return out


def build_cases(*, fixtures_dir: Path, gold_dir: Path) -> list[tuple[CausalCase, CausalGold]]:
    fixture_rows = load_fixture_rows(fixtures_dir)
    gold_rows = load_gold_rows(gold_dir)

    cases: list[tuple[CausalCase, CausalGold]] = []
    seen_ids: set[str] = set()

    for row in sorted(fixture_rows, key=lambda r: str(r.get("id") or "")):
        ok, errs = validate_fixture_row(row)
        if not ok:
            raise ValueError(f"fixture_invalid:{row.get('id')}:{','.join(errs)}")

        case_id = str(row.get("id") or "").strip()
        if case_id in seen_ids:
            raise ValueError(f"fixture_duplicate_id:{case_id}")
        seen_ids.add(case_id)

        gold_id = str(row.get("gold_id") or "").strip()
        if gold_id not in gold_rows:
            raise ValueError(f"gold_missing_for_fixture:{case_id}:{gold_id}")

        g_row = gold_rows[gold_id]
        g_ok, g_errs = validate_gold_row(g_row)
        if not g_ok:
            raise ValueError(f"gold_invalid:{gold_id}:{','.join(g_errs)}")

        fx = CausalCase(
            id=case_id,
            query=str(row.get("query") or "").strip(),
            intent=str(row.get("intent") or "causal").strip() or "causal",
            bucket_labels=tuple(sorted(str(x) for x in (row.get("bucket_labels") or []))),
            gold_id=gold_id,
            beads=tuple(dict(b) for b in (row.get("beads") or []) if isinstance(b, dict)),
            edges=tuple(dict(e) for e in (row.get("edges") or []) if isinstance(e, dict)),
            distractor_keys=tuple(str(x) for x in (row.get("distractor_keys") or []) if str(x).strip()),
            k=max(1, int(row.get("k") or 8)),
        )
        gd = CausalGold(
            id=str(g_row.get("id") or "").strip(),
            gold_root_cause_key=str(g_row.get("gold_root_cause_key") or "").strip(),
            gold_chain_keys=tuple(str(x) for x in (g_row.get("gold_chain_keys") or []) if str(x).strip()),
            expected_grounding=str(g_row.get("expected_grounding") or "full").strip() or "full",
            bucket_labels=tuple(sorted(str(x) for x in (g_row.get("bucket_labels") or []))),
        )
        cases.append((fx, gd))

    return cases
