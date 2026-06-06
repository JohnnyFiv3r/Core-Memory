from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core_memory.graph.root_cause import normalize_causal_hints, root_cause_trace
from core_memory.provider_config import resolve_chat_config
from core_memory.retrieval.contracts import RecallResult, RecallStep


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _read_index(root: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _json_safe_hints(hints: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_causal_hints(hints)
    source_scope = dict(normalized.get("source_scope") or {})
    return {
        "bead_types": sorted(normalized.get("bead_types") or []),
        "relation_families": sorted(normalized.get("relation_families") or []),
        "causal_labels": sorted(normalized.get("causal_labels") or []),
        "causal_direction": normalized.get("causal_direction") or "upstream",
        "keywords": list(normalized.get("keywords") or []),
        "entities": list(normalized.get("entities") or []),
        "anchor_ids": list(normalized.get("anchor_ids") or []),
        "temporal_frame": normalized.get("temporal_frame") or "auto",
        "source_scope": {
            "allowed_source_ids": sorted(source_scope.get("allowed_source_ids") or []),
            "denied_source_ids": sorted(source_scope.get("denied_source_ids") or []),
            "redaction_policy": source_scope.get("redaction_policy") or "redact_evidence",
        },
    }


def normalize_recall_hints(hints: dict[str, Any] | None) -> dict[str, Any]:
    return _json_safe_hints(hints)


def _myelination_bonus_map(root: str | Path) -> dict[str, float]:
    try:
        from core_memory.runtime.observability.myelination import compute_myelination_bonus_map

        payload = compute_myelination_bonus_map(Path(root))
        return dict(payload.get("bonus_by_edge_key") or {}) if payload.get("enabled") else {}
    except Exception:
        return {}


def _url_from_value(value: Any) -> str:
    if isinstance(value, str):
        m = re.search(r"https?://[^\s)>\"]+", value)
        return m.group(0) if m else ""
    if isinstance(value, dict):
        for key in ("url", "uri", "href", "link", "source_url", "web_url"):
            found = _url_from_value(value.get(key))
            if found:
                return found
        for child in value.values():
            found = _url_from_value(child)
            if found:
                return found
    if isinstance(value, (list, tuple)):
        for child in value:
            found = _url_from_value(child)
            if found:
                return found
    return ""


def _source_tokens(bead: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in (
        "source_id",
        "source_ref",
        "source_event_id",
        "document_id",
        "raw_source_object_id",
        "ragie_document_id",
        "transcript_id",
        "conversation_id",
        "source_thread_id",
        "source_session_id",
    ):
        value = _text(bead.get(key))
        if value:
            tokens.add(value)
    hydration = bead.get("hydration_ref") if isinstance(bead.get("hydration_ref"), dict) else {}
    for key in ("store", "ref", "id", "uri", "url"):
        value = _text(hydration.get(key))
        if value:
            tokens.add(value)
    for source in _clean_list(bead.get("source_refs")):
        if isinstance(source, dict):
            for key in ("source_id", "source_ref", "ref", "id", "uri", "url"):
                value = _text(source.get(key))
                if value:
                    tokens.add(value)
        elif _text(source):
            tokens.add(_text(source))
    return tokens


def _availability(bead: dict[str, Any], hints: dict[str, Any]) -> tuple[str, str]:
    source_scope = dict((hints or {}).get("source_scope") or {})
    allowed = set(source_scope.get("allowed_source_ids") or [])
    denied = set(source_scope.get("denied_source_ids") or [])
    tokens = _source_tokens(bead)
    if denied and tokens.intersection(denied):
        return "redacted", "denied_source"
    if allowed and not tokens.intersection(allowed):
        return "redacted", "not_in_allowed_sources"
    return "available", ""


def extract_source_citations(root: str | Path, bead_ids: list[str], hints: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    index = _read_index(root)
    beads = index.get("beads") if isinstance(index.get("beads"), dict) else {}
    normalized_hints = normalize_recall_hints(hints)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(bead_id: str, *, source_ref: str, label: str, source_kind: str, url: str = "", metadata: dict[str, Any] | None = None) -> None:
        bead = beads.get(bead_id) if isinstance(beads, dict) else {}
        availability, reason = _availability(bead if isinstance(bead, dict) else {}, normalized_hints)
        key = (bead_id, source_ref, label)
        if key in seen:
            return
        seen.add(key)
        meta = dict(metadata or {})
        if reason:
            meta["redaction_reason"] = reason
        out.append(
            {
                "citation_id": f"src_{len(out) + 1}",
                "bead_id": bead_id,
                "claim_id": _text(meta.pop("claim_id", "")),
                "source_ref": source_ref,
                "label": label or source_ref or bead_id,
                "url": "" if availability == "redacted" else url,
                "availability": availability,
                "source_kind": source_kind or "external",
                "metadata": meta,
            }
        )

    for bead_id in [x for x in dict.fromkeys(_text(x) for x in bead_ids if _text(x)) if x]:
        bead = beads.get(bead_id) if isinstance(beads, dict) else {}
        if not isinstance(bead, dict):
            continue
        kind = _text(bead.get("source_kind") or bead.get("data_type_flag") or bead.get("type") or "external")
        url = _url_from_value(bead.get("source_attribution") or bead.get("hydration_ref") or bead.get("metadata") or bead)
        hydration = bead.get("hydration_ref") if isinstance(bead.get("hydration_ref"), dict) else {}
        primary_ref = _text(bead.get("source_ref") or hydration.get("ref"))
        if not primary_ref:
            primary_ref = _text(bead.get("source_id") or bead.get("document_id") or bead.get("transcript_id") or bead_id)
        label = _text(bead.get("document_name") or bead.get("title") or primary_ref)
        add(bead_id, source_ref=primary_ref, label=label, source_kind=kind, url=url)

        for ref in _clean_list(bead.get("section_refs")):
            if not isinstance(ref, dict):
                continue
            section_ref = _text(ref.get("chunk_ref") or ref.get("section_id") or ref.get("label"))
            if section_ref:
                add(bead_id, source_ref=section_ref, label=_text(ref.get("label") or section_ref), source_kind="document", url=_url_from_value(ref), metadata={"section_ref": ref})

        for turn_id in _clean_list(bead.get("message_refs") or bead.get("source_turn_ids")):
            if _text(turn_id):
                add(bead_id, source_ref=_text(turn_id), label=f"Transcript turn {_text(turn_id)}", source_kind="transcript")

        for claim in _clean_list(bead.get("claims")):
            if isinstance(claim, dict) and _text(claim.get("id")):
                add(bead_id, source_ref=primary_ref, label=label, source_kind=kind, url=url, metadata={"claim_id": _text(claim.get("id"))})

    return out[:50]


def _claims_for_paths(index: dict[str, Any], bead_ids: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    beads = index.get("beads") if isinstance(index.get("beads"), dict) else {}
    active: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    disputed: list[dict[str, Any]] = []
    for bead_id in sorted(bead_ids):
        bead = beads.get(bead_id) if isinstance(beads, dict) else {}
        if not isinstance(bead, dict):
            continue
        for claim in _clean_list(bead.get("claims")):
            if not isinstance(claim, dict):
                continue
            active.append(
                {
                    "claim_id": _text(claim.get("id")),
                    "bead_id": bead_id,
                    "summary": _text(claim.get("value") or claim.get("summary") or claim.get("reason_text")),
                    "confidence": claim.get("confidence"),
                    "claim_kind": _text(claim.get("claim_kind")),
                }
            )
        for update in _clean_list(bead.get("claim_updates")):
            if not isinstance(update, dict):
                continue
            row = {
                "claim_id": _text(update.get("target_claim_id") or update.get("claim_id")),
                "replacement_claim_id": _text(update.get("replacement_claim_id")),
                "bead_id": bead_id,
                "decision": _text(update.get("decision")),
                "reason": _text(update.get("reason_text") or update.get("reason")),
            }
            decision = row["decision"].lower()
            if decision in {"supersede", "superseded", "retract", "invalidate"}:
                superseded.append(row)
            elif decision in {"conflict", "dispute", "contradict"}:
                disputed.append(row)
    return active, superseded, disputed


def build_state_packet(
    *,
    root: str | Path,
    query: str,
    result: RecallResult,
    attribution: dict[str, Any],
    hints: dict[str, Any] | None,
) -> dict[str, Any]:
    index = _read_index(root)
    paths = [p for p in _clean_list(attribution.get("causal_paths")) if isinstance(p, dict)]
    path_bead_ids: set[str] = {e.bead_id for e in result.evidence if _text(e.bead_id)}
    for path in paths:
        path_bead_ids.update(_text(x) for x in _clean_list(path.get("nodes")) if _text(x))
    active_claims, superseded_claims, disputed_claims = _claims_for_paths(index, path_bead_ids)
    citations = extract_source_citations(root, sorted(path_bead_ids), hints)
    availability_counts: dict[str, int] = {}
    for citation in citations:
        key = _text(citation.get("availability")) or "unknown"
        availability_counts[key] = availability_counts.get(key, 0) + 1
    temporal_frame = _text((attribution.get("diagnostics") or {}).get("temporal_frame") or (normalize_recall_hints(hints).get("temporal_frame")) or "current_truth")
    trace_package = attribution.get("trace_package") if isinstance(attribution.get("trace_package"), dict) else {}
    trace_ids = [_text(t.get("trace_id")) for t in _clean_list(trace_package.get("candidate_traces")) if isinstance(t, dict) and _text(t.get("trace_id"))]
    confidence = "low"
    if paths:
        best = max(float(p.get("current_truth_confidence") or p.get("confidence") or 0.0) for p in paths)
        confidence = "high" if best >= 0.75 else ("moderate" if best >= 0.45 else "low")
    constraints = []
    beads = index.get("beads") if isinstance(index.get("beads"), dict) else {}
    for bead_id in sorted(path_bead_ids):
        bead = beads.get(bead_id) if isinstance(beads, dict) else {}
        for constraint in _clean_list((bead or {}).get("constraints") if isinstance(bead, dict) else []):
            if _text(constraint):
                constraints.append({"bead_id": bead_id, "constraint": constraint})
    return {
        "schema_version": "core_memory.state_packet.v1",
        "query": query,
        "temporal_frame": temporal_frame,
        "anchor_ids": list(attribution.get("anchor_ids") or []),
        "trace_ids": trace_ids,
        "active_claims": active_claims,
        "superseded_claims": superseded_claims,
        "disputed_claims": disputed_claims,
        "resolved_goals": [g.to_dict() for g in result.resolved_goals],
        "constraints": constraints[:20],
        "live_causal_paths": paths[:8],
        "root_causes": list(attribution.get("root_causes") or []),
        "source_availability": [{"availability": k, "count": v} for k, v in sorted(availability_counts.items())],
        "source_citations": citations,
        "uncertainty": {
            "confidence": confidence,
            "open_questions": [] if paths else ["No causal paths were found from the selected anchors."],
        },
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = _text(text)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _fallback_execute_decision(
    *,
    state_packet: dict[str, Any],
    trace_package: dict[str, Any],
    llm_diag: dict[str, Any],
    warning: str,
) -> dict[str, Any]:
    traces = [t for t in _clean_list(trace_package.get("candidate_traces")) if isinstance(t, dict)]
    roots = [r for r in _clean_list(state_packet.get("root_causes")) if isinstance(r, dict)]
    primary = traces[0] if traces else {}
    cause = roots[0] if roots else {}
    explanation = _text(primary.get("summary"))
    if not explanation and cause:
        explanation = f"{_text(cause.get('title')) or _text(cause.get('bead_id'))} is the strongest available upstream cause."
    if not explanation:
        explanation = "I found related evidence, but Core Memory could not reconstruct a confident causal explanation."
    return {
        "schema_version": "core_memory.execute_decision.v1",
        "mode": "causal_recall",
        "selected_explanation": explanation,
        "temporal_frame": _text(state_packet.get("temporal_frame") or "current_truth"),
        "primary_trace_ids": [_text(primary.get("trace_id"))] if _text(primary.get("trace_id")) else [],
        "secondary_trace_ids": [],
        "rejected_trace_ids": [],
        "root_causes": roots[:3],
        "confidence": (state_packet.get("uncertainty") or {}).get("confidence") or "low",
        "citations": list(state_packet.get("source_citations") or [])[:8],
        "open_questions": list((state_packet.get("uncertainty") or {}).get("open_questions") or []),
        "llm": {**llm_diag, "available": False},
        "warnings": [warning],
    }


def execute_state_packet(*, state_packet: dict[str, Any], trace_package: dict[str, Any]) -> dict[str, Any]:
    cfg = resolve_chat_config()
    llm_diag = {
        "available": bool(cfg.provider),
        "provider": cfg.adapter or cfg.provider,
        "model": cfg.model,
        "config_source": cfg.source,
    }
    if not cfg.provider:
        return _fallback_execute_decision(state_packet=state_packet, trace_package=trace_package, llm_diag=llm_diag, warning="execute_llm_unavailable")

    prompt = (
        "You are the Execute phase of Core Memory causal recall. Return strict JSON only.\n"
        "Rules: use only the provided trace_package and state_packet; do not invent causes; "
        "distinguish historical from current-truth claims; cite bead ids, claim ids, and source citations.\n\n"
        f"trace_package:\n{json.dumps(trace_package, ensure_ascii=True, sort_keys=True)[:12000]}\n\n"
        f"state_packet:\n{json.dumps(state_packet, ensure_ascii=True, sort_keys=True)[:12000]}\n\n"
        "Return this shape: {\"selected_explanation\": string, \"temporal_frame\": string, "
        "\"primary_trace_ids\": [string], \"secondary_trace_ids\": [string], "
        "\"rejected_trace_ids\": [{\"trace_id\": string, \"reason\": string}], "
        "\"root_causes\": [{\"bead_id\": string, \"role\": string, \"confidence\": string}], "
        "\"confidence\": \"high|moderate|low\", \"citations\": [object], \"open_questions\": [string]}."
    )
    try:
        from core_memory.llm_client import chat_complete
        raw = chat_complete(prompt, config=cfg, max_tokens=900, temperature=0)
    except Exception:
        return _fallback_execute_decision(state_packet=state_packet, trace_package=trace_package, llm_diag=llm_diag, warning="execute_llm_unavailable")

    parsed = _extract_json_object(raw)
    if not parsed:
        return _fallback_execute_decision(state_packet=state_packet, trace_package=trace_package, llm_diag=llm_diag, warning="execute_llm_parse_failed")

    return {
        "schema_version": "core_memory.execute_decision.v1",
        "mode": "causal_recall",
        "selected_explanation": _text(parsed.get("selected_explanation")),
        "temporal_frame": _text(parsed.get("temporal_frame") or state_packet.get("temporal_frame") or "current_truth"),
        "primary_trace_ids": [_text(x) for x in _clean_list(parsed.get("primary_trace_ids")) if _text(x)],
        "secondary_trace_ids": [_text(x) for x in _clean_list(parsed.get("secondary_trace_ids")) if _text(x)],
        "rejected_trace_ids": [dict(x) for x in _clean_list(parsed.get("rejected_trace_ids")) if isinstance(x, dict)],
        "root_causes": [dict(x) for x in _clean_list(parsed.get("root_causes")) if isinstance(x, dict)] or list(state_packet.get("root_causes") or [])[:3],
        "confidence": _text(parsed.get("confidence") or "moderate"),
        "citations": [dict(x) for x in _clean_list(parsed.get("citations")) if isinstance(x, dict)] or list(state_packet.get("source_citations") or [])[:8],
        "open_questions": [_text(x) for x in _clean_list(parsed.get("open_questions")) if _text(x)],
        "llm": {**llm_diag, "available": True},
        "warnings": [],
    }


def _ordered_tier_path(existing: list[str], *, include_source: bool) -> list[str]:
    wanted = ["semantic", "causal", "trace", "state", "execute"] + (["source"] if include_source else [])
    present = set(existing or [])
    present.update({"semantic", "causal", "trace", "state", "execute"})
    if include_source:
        present.add("source")
    return [tier for tier in wanted if tier in present]


def _append_step(result: RecallResult, tier: str, query: str, count: int, why: str) -> None:
    result.steps.append(RecallStep(tier=tier, query=query, status="ok", result_count=int(count), why=why))


def should_run_causal_pipeline(query: str, effort: str, intent: str | None = None) -> bool:
    if str(effort or "").lower() == "low":
        return False
    intent_l = _text(intent).lower()
    if intent_l == "causal":
        return True
    if str(effort or "").lower() != "high":
        return False
    return bool(
        re.search(
            r"\b(why|because|root[- ]cause|what caused|what causes|what changed|caused by|cause of|led to|drivers? of)\b",
            query.lower(),
        )
    )


def attach_causal_recall_pipeline(
    result: RecallResult,
    *,
    root: str | Path,
    query: str,
    hints: dict[str, Any] | None = None,
    max_depth: int = 6,
    max_paths: int = 20,
) -> RecallResult:
    normalized_hints = normalize_recall_hints(hints)
    anchor_ids = [_text(x) for x in normalized_hints.get("anchor_ids", []) if _text(x)]
    anchor_ids.extend([_text(e.bead_id) for e in result.evidence if _text(e.bead_id)])
    anchor_ids = list(dict.fromkeys(anchor_ids))
    if not anchor_ids:
        result.warnings.append("causal_recall_no_anchors")
        return result

    attribution = root_cause_trace(
        Path(root),
        anchor_ids=anchor_ids[:12],
        query=query,
        hints=normalized_hints,
        myelination_bonus=_myelination_bonus_map(root),
        max_depth=max_depth,
        max_paths=max_paths,
        max_causes=8,
        beam_width=8,
        temporal_frame=normalized_hints.get("temporal_frame") or "auto",
        include_flow=True,
    )
    result.root_cause_attribution = {k: v for k, v in attribution.items() if k != "trace_package"}
    result.trace_package = dict(attribution.get("trace_package") or {})
    state_packet = build_state_packet(root=root, query=query, result=result, attribution=attribution, hints=normalized_hints)
    result.state_packet = state_packet
    result.source_citations = list(state_packet.get("source_citations") or [])
    decision = execute_state_packet(state_packet=state_packet, trace_package=result.trace_package)
    result.execute_decision = decision

    causal_paths = [p for p in _clean_list(attribution.get("causal_paths")) if isinstance(p, dict)]
    explanation = _text(decision.get("selected_explanation"))
    if causal_paths and explanation:
        result.answer = explanation
        result.why = "execute_decision"
        if result.status != "failed":
            result.status = "answered"
    for warning in _clean_list(decision.get("warnings")):
        if _text(warning) and _text(warning) not in result.warnings:
            result.warnings.append(_text(warning))
    for warning in _clean_list(attribution.get("warnings")):
        if isinstance(warning, dict):
            kind = _text(warning.get("kind"))
            if kind and kind not in result.warnings:
                result.warnings.append(kind)

    _append_step(result, "causal", query, len(causal_paths), "compatibility alias for root-cause trace package")
    result.steps[-1].metadata = {"alias_for": "trace"}
    _append_step(result, "trace", query, len(causal_paths), "root-cause trace package assembled")
    _append_step(result, "state", query, len(state_packet.get("trace_ids") or []), "state packet assembled")
    _append_step(result, "execute", query, 1, "execute decision completed")
    result.tier_path = _ordered_tier_path(result.tier_path, include_source=bool(result.sources or result.source_citations))
    result.metadata["hints"] = normalized_hints
    result.metadata["causal_recall"] = {
        "enabled": True,
        "path_count": len(attribution.get("causal_paths") or []),
        "root_cause_count": len(attribution.get("root_causes") or []),
    }
    return result
