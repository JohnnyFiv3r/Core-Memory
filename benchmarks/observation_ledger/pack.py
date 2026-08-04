"""Evaluation-pack loading, checksums, privacy rules, and contamination checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .constants import (
    ADJUDICATION_SCHEMA,
    CASE_SCHEMA,
    FORBIDDEN_RUNTIME_KEYS,
    GOLD_SCHEMA,
    PACK_SCHEMA,
    SUITES,
    VISIBILITIES,
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "detail": self.detail}


@dataclass(frozen=True)
class PackValidation:
    valid: bool
    issues: tuple[ValidationIssue, ...]
    pack_checksum: str = ""
    case_count: int = 0
    bucket_counts: dict[str, int] = field(default_factory=dict)

    @property
    def contaminated(self) -> bool:
        return any(issue.code.startswith("contamination:") for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "observation_ledger.pack_validation.v1",
            "valid": self.valid,
            "contaminated": self.contaminated,
            "pack_checksum": self.pack_checksum,
            "case_count": self.case_count,
            "bucket_counts": dict(sorted(self.bucket_counts.items())),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class EvaluationPack:
    root: Path
    manifest: dict[str, Any]
    visibility: str
    checksum: str
    cases: tuple[dict[str, Any], ...]
    gold: dict[str, dict[str, Any]]
    adjudications: dict[str, dict[str, Any]]


def canonical_json_hash(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_checksum(case: dict[str, Any]) -> str:
    return canonical_json_hash(case.get("admissible_evidence") or [])


def gold_checksum(gold: dict[str, Any]) -> str:
    return canonical_json_hash(gold)


def _load_document(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    raise ValueError(f"unsupported_document_type:{path.suffix.lower()}")


def _manifest_path(root: Path) -> Path:
    for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("pack_manifest_missing")


def _safe_relative(root: Path, raw: Any) -> Path | None:
    text = str(raw or "").strip()
    candidate = Path(text)
    if not text or candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_RUNTIME_KEYS:
                return normalized
            found = _contains_forbidden_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _contains_forbidden_key(child)
            if found:
                return found
    return None


def runtime_input(case: dict[str, Any]) -> dict[str, Any]:
    """Return the only fields the product author is allowed to receive."""

    return {
        "source_events": list(case.get("source_events") or []),
        "admissible_evidence": list(case.get("admissible_evidence") or []),
        "task_input": dict(case.get("task_input") or {}),
    }


def _load_records(root: Path, manifest: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in manifest.get(field_name) or []:
        path = _safe_relative(root, raw)
        if path is None:
            raise ValueError(f"unsafe_manifest_path:{field_name}:{raw}")
        payload = _load_document(path)
        candidates = payload if isinstance(payload, list) else [payload]
        for row in candidates:
            if not isinstance(row, dict):
                raise ValueError(f"record_not_object:{field_name}:{raw}")
            records.append(dict(row))
    return records


def _pack_checksum(root: Path, manifest_path: Path, listed: list[str]) -> str:
    digest = hashlib.sha256()
    digest.update(manifest_path.read_bytes())
    for relative in sorted(listed):
        path = _safe_relative(root, relative)
        if path is None or not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_pack(
    pack_path: Path,
    *,
    visibility: str,
    repo_root: Path,
    require_accepted: bool = True,
) -> PackValidation:
    issues: list[ValidationIssue] = []
    requested_path = Path(pack_path)
    root = requested_path.resolve()
    visibility_n = str(visibility or "").strip().lower()

    if visibility_n not in VISIBILITIES:
        issues.append(ValidationIssue("visibility_invalid", detail=visibility_n))
    if visibility_n == "private":
        if not requested_path.is_absolute():
            issues.append(ValidationIssue("private_pack_path_not_absolute", path=str(requested_path)))
        try:
            root.relative_to(repo_root.resolve())
        except ValueError:
            pass
        else:
            issues.append(ValidationIssue("private_pack_inside_repository", path=str(root)))
    if not root.is_dir():
        issues.append(ValidationIssue("pack_directory_missing", path=str(root)))
        return PackValidation(False, tuple(issues))

    try:
        manifest_path = _manifest_path(root)
        manifest = _load_document(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        issues.append(ValidationIssue("manifest_unreadable", path=str(root), detail=exc.__class__.__name__))
        return PackValidation(False, tuple(issues))
    if not isinstance(manifest, dict):
        return PackValidation(False, (ValidationIssue("manifest_not_object", path=str(manifest_path)),))

    if manifest.get("schema_version") != PACK_SCHEMA:
        issues.append(ValidationIssue("manifest_schema_invalid", path=manifest_path.name))
    if str(manifest.get("visibility") or "") != visibility_n:
        issues.append(ValidationIssue("manifest_visibility_mismatch", path=manifest_path.name))
    if not str(manifest.get("pack_id") or "").strip():
        issues.append(ValidationIssue("pack_id_missing", path=manifest_path.name))
    if not str(manifest.get("prompt_version") or "").strip():
        issues.append(ValidationIssue("prompt_version_missing", path=manifest_path.name))

    listed: list[str] = []
    for field_name in ("cases", "gold", "adjudications"):
        values = manifest.get(field_name)
        if not isinstance(values, list):
            issues.append(ValidationIssue(f"manifest_{field_name}_not_list", path=manifest_path.name))
            continue
        if field_name in {"cases", "gold"} and not values:
            issues.append(ValidationIssue(f"manifest_{field_name}_empty", path=manifest_path.name))
        for raw in values:
            listed.append(str(raw or ""))
            path = _safe_relative(root, raw)
            if path is None:
                issues.append(ValidationIssue("manifest_path_unsafe", path=str(raw)))
            elif not path.is_file():
                issues.append(ValidationIssue("manifest_file_missing", path=str(raw)))

    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict):
        issues.append(ValidationIssue("manifest_checksums_not_object", path=manifest_path.name))
        checksums = {}
    if set(str(key) for key in checksums) != set(listed):
        issues.append(ValidationIssue("manifest_checksum_paths_mismatch", path=manifest_path.name))
    for relative in listed:
        path = _safe_relative(root, relative)
        if path is None or not path.is_file():
            continue
        if str(checksums.get(relative) or "") != file_sha256(path):
            issues.append(ValidationIssue("manifest_checksum_mismatch", path=relative))

    pack_checksum = _pack_checksum(root, manifest_path, listed)
    try:
        cases = _load_records(root, manifest, "cases")
        gold_rows = _load_records(root, manifest, "gold")
        adjudication_rows = _load_records(root, manifest, "adjudications")
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        issues.append(ValidationIssue("pack_record_unreadable", detail=exc.__class__.__name__))
        return PackValidation(False, tuple(issues), pack_checksum=pack_checksum)

    case_ids: set[str] = set()
    bucket_counts: dict[str, int] = {}
    for index, case in enumerate(cases):
        path_label = f"cases[{index}]"
        case_id = str(case.get("case_id") or "").strip()
        if case.get("schema_version") != CASE_SCHEMA:
            issues.append(ValidationIssue("case_schema_invalid", path=path_label))
        if not case_id:
            issues.append(ValidationIssue("case_id_missing", path=path_label))
        elif case_id in case_ids:
            issues.append(ValidationIssue("case_id_duplicate", path=path_label, detail=case_id))
        case_ids.add(case_id)
        suite = str(case.get("suite") or "").strip()
        if suite not in SUITES:
            issues.append(ValidationIssue("case_suite_invalid", path=path_label, detail=suite))
        if str(case.get("privacy_classification") or "") != visibility_n:
            issues.append(ValidationIssue("case_privacy_mismatch", path=path_label, detail=case_id))
        buckets = case.get("behavioral_buckets")
        if not isinstance(buckets, list) or not buckets:
            issues.append(ValidationIssue("case_behavioral_buckets_invalid", path=path_label))
        else:
            for bucket in buckets:
                bucket_n = str(bucket or "").strip()
                if bucket_n:
                    bucket_counts[bucket_n] = bucket_counts.get(bucket_n, 0) + 1
        source_events = case.get("source_events")
        if not isinstance(source_events, list):
            issues.append(ValidationIssue("case_source_events_invalid", path=path_label))
            source_events = []
        admissible_evidence = case.get("admissible_evidence")
        if not isinstance(admissible_evidence, list):
            issues.append(ValidationIssue("case_evidence_invalid", path=path_label))
            admissible_evidence = []
        task_input = case.get("task_input")
        if not isinstance(task_input, dict):
            issues.append(ValidationIssue("case_task_input_invalid", path=path_label))
            task_input = {}
        if not isinstance(case.get("expected_structural_behavior"), dict):
            issues.append(ValidationIssue("case_structural_expectation_invalid", path=path_label))
        if str(case.get("evidence_checksum") or "") != evidence_checksum(case):
            issues.append(ValidationIssue("case_evidence_checksum_mismatch", path=path_label))

        tenant_id = str(task_input.get("tenant_id") or "").strip()
        event_ids: set[str] = set()
        event_content: dict[str, str] = {}
        for event_index, event in enumerate(source_events):
            event_path = f"{path_label}.source_events[{event_index}]"
            if not isinstance(event, dict):
                issues.append(ValidationIssue("source_event_not_object", path=event_path))
                continue
            event_id = str(event.get("event_id") or "").strip()
            if not event_id or event_id in event_ids:
                issues.append(ValidationIssue("source_event_id_invalid", path=event_path))
                continue
            event_ids.add(event_id)
            event_content[event_id] = str(event.get("content") or "")
            event_tenant = str(event.get("tenant_id") or tenant_id).strip()
            if tenant_id and event_tenant != tenant_id:
                issues.append(ValidationIssue("source_event_tenant_mismatch", path=event_path))

        evidence_ids: set[str] = set()
        for evidence_index, evidence in enumerate(admissible_evidence):
            evidence_path = f"{path_label}.admissible_evidence[{evidence_index}]"
            if not isinstance(evidence, dict):
                issues.append(ValidationIssue("evidence_not_object", path=evidence_path))
                continue
            evidence_id = str(evidence.get("evidence_id") or "").strip()
            source_event_id = str(evidence.get("source_event_id") or "").strip()
            if not evidence_id or evidence_id in evidence_ids:
                issues.append(ValidationIssue("evidence_id_invalid", path=evidence_path))
            evidence_ids.add(evidence_id)
            if source_event_id not in event_ids:
                issues.append(ValidationIssue("evidence_source_event_unknown", path=evidence_path))
            evidence_tenant = str(evidence.get("tenant_id") or tenant_id).strip()
            if tenant_id and evidence_tenant != tenant_id:
                issues.append(ValidationIssue("evidence_tenant_mismatch", path=evidence_path))

            spans: list[Any] = []
            if "span" in evidence:
                spans.append(evidence.get("span"))
            if "spans" in evidence:
                raw_spans = evidence.get("spans")
                if isinstance(raw_spans, list):
                    spans.extend(raw_spans)
                else:
                    issues.append(ValidationIssue("evidence_spans_invalid", path=evidence_path))
            for span_index, span in enumerate(spans):
                span_path = f"{evidence_path}.spans[{span_index}]"
                if not isinstance(span, dict):
                    issues.append(ValidationIssue("evidence_span_not_object", path=span_path))
                    continue
                start = span.get("start")
                end = span.get("end")
                content = event_content.get(source_event_id, "")
                if (
                    not isinstance(start, int)
                    or isinstance(start, bool)
                    or not isinstance(end, int)
                    or isinstance(end, bool)
                    or start < 0
                    or end <= start
                    or end > len(content)
                ):
                    issues.append(ValidationIssue("evidence_span_out_of_bounds", path=span_path))

        forbidden = _contains_forbidden_key(case)
        if forbidden:
            issues.append(
                ValidationIssue(
                    "contamination:gold_field_in_case_input",
                    path=path_label,
                    detail=forbidden,
                )
            )

    gold_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(gold_rows):
        case_id = str(row.get("case_id") or "").strip()
        path_label = f"gold[{index}]"
        if row.get("schema_version") != GOLD_SCHEMA:
            issues.append(ValidationIssue("gold_schema_invalid", path=path_label))
        if not case_id or case_id in gold_by_id:
            issues.append(ValidationIssue("gold_case_id_invalid", path=path_label))
        if not isinstance(row.get("semantic_expectations"), dict):
            issues.append(ValidationIssue("gold_semantic_expectations_invalid", path=path_label))
        gold_by_id[case_id] = row

    adjudications_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(adjudication_rows):
        case_id = str(row.get("case_id") or "").strip()
        path_label = f"adjudications[{index}]"
        if row.get("schema_version") != ADJUDICATION_SCHEMA:
            issues.append(ValidationIssue("adjudication_schema_invalid", path=path_label))
        if not case_id or case_id in adjudications_by_id:
            issues.append(ValidationIssue("adjudication_case_id_invalid", path=path_label))
        decision = str(row.get("human_decision") or "").strip().lower()
        if decision not in {"pending", "accepted", "revised", "rejected"}:
            issues.append(ValidationIssue("adjudication_human_decision_invalid", path=path_label))
        if not str(row.get("judge_model") or "").strip():
            issues.append(ValidationIssue("adjudication_judge_model_missing", path=path_label))
        if row.get("judge_critique") is None:
            issues.append(ValidationIssue("adjudication_judge_critique_missing", path=path_label))
        adjudications_by_id[case_id] = row

    if set(gold_by_id) != case_ids:
        issues.append(ValidationIssue("gold_case_set_mismatch"))
    if require_accepted and set(adjudications_by_id) != case_ids:
        issues.append(ValidationIssue("adjudication_case_set_mismatch"))
    if require_accepted:
        for case_id in sorted(case_ids):
            adjudication = adjudications_by_id.get(case_id) or {}
            decision = str(adjudication.get("human_decision") or "").strip().lower()
            if decision not in {"accepted", "revised"}:
                issues.append(ValidationIssue("adjudication_not_accepted", detail=case_id))
            expected_checksum = gold_checksum(gold_by_id.get(case_id) or {})
            if str(adjudication.get("accepted_gold_checksum") or "") != expected_checksum:
                issues.append(ValidationIssue("adjudication_gold_checksum_mismatch", detail=case_id))

    return PackValidation(
        valid=not issues,
        issues=tuple(issues),
        pack_checksum=pack_checksum,
        case_count=len(cases),
        bucket_counts=bucket_counts,
    )


def load_pack(pack_path: Path, *, visibility: str) -> EvaluationPack:
    root = Path(pack_path).resolve()
    manifest_path = _manifest_path(root)
    manifest = _load_document(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("manifest_not_object")
    listed = [str(path) for field_name in ("cases", "gold", "adjudications") for path in manifest.get(field_name) or []]
    cases = tuple(_load_records(root, manifest, "cases"))
    gold = {str(row.get("case_id") or ""): row for row in _load_records(root, manifest, "gold")}
    adjudications = {str(row.get("case_id") or ""): row for row in _load_records(root, manifest, "adjudications")}
    return EvaluationPack(
        root=root,
        manifest=dict(manifest),
        visibility=str(visibility),
        checksum=_pack_checksum(root, manifest_path, listed),
        cases=cases,
        gold=gold,
        adjudications=adjudications,
    )
