"""Adjudication, execution, scoring, and baseline orchestration."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmarks.contracts import BenchmarkShortcutFlags

from .constants import (
    ADJUDICATION_RUN_SCHEMA,
    ADJUDICATION_SCHEMA,
    BASELINE_SCHEMA,
    HARD_INVARIANT_KEYS,
    REPORT_SCHEMA,
    SEMANTIC_SCORE_KEYS,
    THRESHOLDS_SCHEMA,
)
from .models import ModelRequest, ResolvedModel, RunStatus, RuntimeAdapter, RuntimeCaseResult, selection_relationship
from .pack import gold_checksum, load_pack, runtime_input, validate_pack
from .providers import (
    ModelBlockedError,
    ModelExecutionError,
    ModelRegistry,
    bind_author_model,
    complete_json,
)
from .runtime import CoreMemorySemanticRuntimeAdapter
from .statistics import metric_interval


def _source_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if len(commit) == 40 else "unknown"


def _invariants() -> dict[str, dict[str, Any]]:
    return {key: {"passed": True, "failures": 0} for key in HARD_INVARIANT_KEYS}


def _fail_invariant(invariants: dict[str, dict[str, Any]], key: str) -> None:
    row = invariants.setdefault(key, {"passed": True, "failures": 0})
    row["passed"] = False
    row["failures"] = int(row.get("failures") or 0) + 1


def _shortcut_dict(flags: BenchmarkShortcutFlags) -> dict[str, Any]:
    return flags.to_dict()


def _empty_model_record(requested: str | None) -> dict[str, Any]:
    return {
        "requested": str(requested or ""),
        "resolved": "",
        "provider": "",
        "model": "",
        "adapter": "",
        "baseline_eligible": False,
    }


def _base_report(
    *,
    repo_root: Path,
    visibility: str,
    pack_checksum: str,
    case_count: int,
    bucket_counts: dict[str, int],
    author_model: str | None,
    judge_model: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA,
        "source_commit": _source_commit(repo_root),
        "pack": {
            "visibility": visibility,
            "checksum": pack_checksum,
            "case_count": case_count,
            "bucket_counts": dict(sorted(bucket_counts.items())),
        },
        "runtime_configuration": {
            "adapter": "",
            "author_role_bindings": {},
            "author_environment_bindings": {},
            "semantic_roles_exercised": [],
            "test_adapter_used": False,
        },
        "models": {
            "author": _empty_model_record(author_model),
            "judge": _empty_model_record(judge_model),
            "relationship": "unresolved",
        },
        "versions": {"prompt": "", "judge_prompt": "judge.semantic.v1", "schemas": [REPORT_SCHEMA]},
        "shortcut_flags": BenchmarkShortcutFlags().to_dict(),
        "contaminated": False,
        "status": RunStatus.FAILED.value,
        "status_reason": "",
        "hard_invariants": _invariants(),
        "semantic_metrics": {},
        "semantic_metric_intervals": {},
        "latency_ms": {"total": 0.0, "author": 0.0, "judge": 0.0},
        "tokens": {"input": 0, "output": 0},
        "cost_usd": 0.0,
        "warnings": [],
        "limitations": [],
    }


def _write_status(report: dict[str, Any], status: RunStatus, reason: str) -> dict[str, Any]:
    report["status"] = status.value
    report["status_reason"] = reason
    return report


def _resolve_models(
    registry: ModelRegistry,
    author_model: str | None,
    judge_model: str | None,
) -> tuple[ResolvedModel, Any, ResolvedModel, Any]:
    author, author_adapter = registry.resolve(author_model)
    judge, judge_adapter = registry.resolve(judge_model)
    return author, author_adapter, judge, judge_adapter


def _citation_ids(candidate: dict[str, Any]) -> set[str]:
    raw = candidate.get("evidence_refs")
    if raw is None:
        raw = candidate.get("citations")
    out: set[str] = set()
    for row in raw or []:
        if isinstance(row, dict):
            value = row.get("evidence_id") or row.get("id")
        else:
            value = row
        normalized = str(value or "").strip()
        if normalized:
            out.add(normalized)
    return out


def _nested_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nested_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nested_objects(child)


def _parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _temporal_interval_errors(candidate: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pairs = (
        ("valid_from", "valid_to"),
        ("event_time_from", "event_time_to"),
        ("knowledge_time_from", "knowledge_time_to"),
    )
    for index, row in enumerate(_nested_objects(candidate)):
        for lower_key, upper_key in pairs:
            if lower_key not in row and upper_key not in row:
                continue
            lower = _parse_timestamp(row.get(lower_key))
            upper = _parse_timestamp(row.get(upper_key))
            timezone_mismatch = (
                lower is not None and upper is not None and (lower.tzinfo is None) != (upper.tzinfo is None)
            )
            if lower is None or upper is None or timezone_mismatch or lower > upper:
                errors.append(f"object[{index}]:{lower_key}:{upper_key}")
    return errors


def _revision_chain_errors(case: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    id_keys = ("revision_id", "assertion_id", "claim_id", "artifact_id")
    target_keys = ("supersedes", "supersedes_id", "revises", "revises_id", "retracts", "retracts_id")
    known_ids = {
        str(value).strip()
        for value in (case.get("expected_structural_behavior") or {}).get("known_revision_ids") or []
        if str(value).strip()
    }
    ids: set[str] = set()
    edges: list[tuple[str, str]] = []
    errors: list[str] = []
    for index, row in enumerate(_nested_objects(candidate)):
        source = next((str(row.get(key) or "").strip() for key in id_keys if str(row.get(key) or "").strip()), "")
        if source:
            if source in ids:
                errors.append(f"duplicate_revision_id:{source}")
            ids.add(source)
        for key in target_keys:
            if key not in row:
                continue
            raw_targets = row.get(key)
            targets = raw_targets if isinstance(raw_targets, list) else [raw_targets]
            if not source:
                errors.append(f"revision_source_missing:object[{index}]:{key}")
                continue
            for target in targets:
                target_id = str(target or "").strip()
                if not target_id:
                    errors.append(f"revision_target_missing:{source}:{key}")
                else:
                    edges.append((source, target_id))

    allowed_targets = ids | known_ids
    graph: dict[str, set[str]] = {}
    for source, target in edges:
        if target not in allowed_targets:
            errors.append(f"revision_target_unknown:{source}:{target}")
        graph.setdefault(source, set()).add(target)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(target) for target in graph.get(node, ()) if target in graph):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    if any(visit(node) for node in sorted(graph)):
        errors.append("revision_cycle")
    return sorted(set(errors))


def _candidate_tenant_ids(candidate: dict[str, Any]) -> set[str]:
    return {
        str(row.get("tenant_id") or "").strip()
        for row in _nested_objects(candidate)
        if str(row.get("tenant_id") or "").strip()
    }


def _structural_evaluation(
    case: dict[str, Any],
    runtime_result: RuntimeCaseResult,
    invariants: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    candidate = runtime_result.candidate
    expected = dict(case.get("expected_structural_behavior") or {})
    required_fields = [str(item) for item in expected.get("required_output_fields") or []]
    forbidden_fields = [str(item) for item in expected.get("forbidden_output_fields") or []]
    missing = sorted(field for field in required_fields if field not in candidate)
    present_forbidden = sorted(field for field in forbidden_fields if field in candidate)

    admissible_ids = {
        str(row.get("evidence_id") or "").strip()
        for row in case.get("admissible_evidence") or []
        if isinstance(row, dict)
    }
    citations = _citation_ids(candidate)
    invalid_citations = sorted(citations - admissible_ids)
    requires_citations = bool(expected.get("requires_citations"))
    if invalid_citations or (requires_citations and not citations):
        _fail_invariant(invariants, "admissible_evidence_only")

    tenant_id = str((case.get("task_input") or {}).get("tenant_id") or "").strip()
    cross_tenant = False
    if tenant_id:
        cross_tenant = any(
            str(row.get("tenant_id") or tenant_id) != tenant_id
            for row in case.get("admissible_evidence") or []
            if isinstance(row, dict)
        )
        cross_tenant = cross_tenant or any(value != tenant_id for value in _candidate_tenant_ids(candidate))
    if cross_tenant:
        _fail_invariant(invariants, "tenant_isolation")

    temporal_errors = _temporal_interval_errors(candidate)
    revision_errors = _revision_chain_errors(case, candidate)
    schema_valid = not missing and not present_forbidden and not temporal_errors and not revision_errors
    if not schema_valid:
        _fail_invariant(invariants, "schema_and_structural_integrity")

    if runtime_result.deterministic_fallback_used:
        _fail_invariant(invariants, "no_deterministic_semantic_fallback")
    for failure in runtime_result.hard_invariant_failures:
        if failure in invariants:
            _fail_invariant(invariants, failure)
    return {
        "schema_valid": schema_valid,
        "missing_required_fields": missing,
        "forbidden_fields_present": present_forbidden,
        "citations_valid": not invalid_citations and (bool(citations) or not requires_citations),
        "invalid_citations": invalid_citations,
        "tenant_isolation_valid": not cross_tenant,
        "temporal_intervals_valid": not temporal_errors,
        "temporal_interval_errors": temporal_errors,
        "revision_chain_valid": not revision_errors,
        "revision_chain_errors": revision_errors,
    }


def _judge_request(case: dict[str, Any], gold: dict[str, Any], candidate: dict[str, Any]) -> ModelRequest:
    return ModelRequest(
        role="benchmark_judge",
        prompt_version="judge.semantic.v1",
        payload={
            "instruction": (
                "Evaluate only whether the candidate is supported by the admissible evidence and accepted gold. "
                "Return a JSON object with scores, unsupported_additions, and critique."
            ),
            "runtime_input": runtime_input(case),
            "accepted_gold": gold,
            "candidate": candidate,
            "score_keys": list(SEMANTIC_SCORE_KEYS),
        },
    )


def _normalized_scores(judgment: dict[str, Any]) -> dict[str, float]:
    raw = judgment.get("scores")
    if not isinstance(raw, dict):
        raise ModelExecutionError("judge_scores_missing")
    scores: dict[str, float] = {}
    for key in SEMANTIC_SCORE_KEYS:
        if key not in raw:
            continue
        try:
            value = float(raw[key])
        except (TypeError, ValueError):
            raise ModelExecutionError(f"judge_score_invalid:{key}") from None
        if value < 0 or value > 1:
            raise ModelExecutionError(f"judge_score_out_of_range:{key}")
        scores[key] = value
    if not scores:
        raise ModelExecutionError("judge_scores_empty")
    return scores


def _aggregate_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for row in rows:
        for key, value in (row.get("semantic_scores") or {}).items():
            values.setdefault(str(key), []).append(float(value))
    return {key: round(sum(items) / len(items), 6) for key, items in sorted(values.items()) if items}


def _aggregate_metric_intervals(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int | str]]:
    values: dict[str, list[float]] = {}
    for row in rows:
        for key, value in (row.get("semantic_scores") or {}).items():
            values.setdefault(str(key), []).append(float(value))
    return {
        key: metric_interval(items, seed=index) for index, (key, items) in enumerate(sorted(values.items())) if items
    }


def run_pack(
    *,
    pack_path: Path,
    visibility: str,
    author_model: str | None,
    judge_model: str | None,
    repo_root: Path,
    registry: ModelRegistry | None = None,
    runtime: RuntimeAdapter | None = None,
) -> dict[str, Any]:
    registry = registry or ModelRegistry.default()
    validation = validate_pack(
        pack_path,
        visibility=visibility,
        repo_root=repo_root,
        require_accepted=True,
    )
    report = _base_report(
        repo_root=repo_root,
        visibility=visibility,
        pack_checksum=validation.pack_checksum,
        case_count=validation.case_count,
        bucket_counts=validation.bucket_counts,
        author_model=author_model,
        judge_model=judge_model,
    )
    if not validation.valid:
        report["warnings"] = (
            [{"code": issue.code} for issue in validation.issues]
            if visibility == "private"
            else [issue.to_dict() for issue in validation.issues]
        )
        if validation.contaminated:
            report["contaminated"] = True
            report["shortcut_flags"]["gold_fields_in_runtime_input"] = True
            report["shortcut_flags"]["is_faithful"] = False
            _fail_invariant(report["hard_invariants"], "no_benchmark_contamination")
            return _write_status(report, RunStatus.DISQUALIFIED, "pack_contamination")
        return _write_status(report, RunStatus.FAILED, "pack_validation_failed")

    pack = load_pack(pack_path, visibility=visibility)
    report["versions"]["prompt"] = str(pack.manifest.get("prompt_version") or "")
    report["versions"]["schemas"] = list(pack.manifest.get("schema_versions") or []) + [REPORT_SCHEMA]
    try:
        author, _author_adapter, judge, judge_adapter = _resolve_models(registry, author_model, judge_model)
    except ModelBlockedError as exc:
        return _write_status(report, RunStatus.BLOCKED, exc.code)

    report["models"] = {
        "author": author.to_dict(),
        "judge": judge.to_dict(),
        "relationship": selection_relationship(author, judge),
    }
    runtime = runtime or CoreMemorySemanticRuntimeAdapter()
    report["runtime_configuration"]["adapter"] = runtime.name
    report["runtime_configuration"]["test_adapter_used"] = bool(
        not author.baseline_eligible or not judge.baseline_eligible or runtime.name.startswith("test-only")
    )

    started = time.perf_counter()
    per_case: list[dict[str, Any]] = []
    all_shortcuts = BenchmarkShortcutFlags()
    role_bindings = {role: author.identifier for role in runtime.semantic_roles}
    environment_bindings: dict[str, str] = {}
    roles_exercised: set[str] = set()
    for case in pack.cases:
        case_id = str(case.get("case_id") or "")
        state_before_author = runtime.state_digest()
        try:
            with bind_author_model(author) as environment_bindings:
                runtime_result = runtime.execute(case, author)
        except (ModelBlockedError, ModelExecutionError) as exc:
            # No candidate is retained when author execution fails.
            if runtime.state_digest() != state_before_author:
                _fail_invariant(report["hard_invariants"], "provider_failure_writes_no_semantics")
            report["warnings"].append({"code": exc.code, "case_id": case_id if visibility == "public" else ""})
            report["latency_ms"]["total"] = round((time.perf_counter() - started) * 1000.0, 3)
            return _write_status(report, RunStatus.FAILED, "author_runtime_failed")

        if (
            runtime_result.model_identifier != author.identifier
            or "author_model_mismatch" in runtime_result.hard_invariant_failures
        ):
            report["contaminated"] = True
            report["shortcut_flags"]["runtime_semantic_bypass"] = True
            report["shortcut_flags"]["is_faithful"] = False
            _fail_invariant(report["hard_invariants"], "no_benchmark_contamination")
            return _write_status(report, RunStatus.DISQUALIFIED, "author_model_routing_mismatch")
        if not set(runtime_result.semantic_roles_exercised).issubset(set(runtime.semantic_roles)):
            return _write_status(report, RunStatus.FAILED, "runtime_reported_unknown_semantic_role")

        report["latency_ms"]["author"] += runtime_result.latency_ms
        report["tokens"]["input"] += runtime_result.input_tokens
        report["tokens"]["output"] += runtime_result.output_tokens
        report["cost_usd"] += runtime_result.cost_usd
        roles_exercised.update(runtime_result.semantic_roles_exercised)
        all_shortcuts = BenchmarkShortcutFlags(
            **{
                key: bool(all_shortcuts.to_dict().get(key)) or bool(runtime_result.shortcut_flags.to_dict().get(key))
                for key in BenchmarkShortcutFlags.__dataclass_fields__
            }
        )
        structural = _structural_evaluation(case, runtime_result, report["hard_invariants"])
        state_before_judge = runtime.state_digest()
        try:
            judgment, receipts = complete_json(
                judge_adapter,
                judge,
                _judge_request(case, pack.gold[case_id], runtime_result.candidate),
            )
            scores = _normalized_scores(judgment)
        except ModelExecutionError as exc:
            report["warnings"].append({"code": exc.code, "case_id": case_id if visibility == "public" else ""})
            report["latency_ms"]["total"] = round((time.perf_counter() - started) * 1000.0, 3)
            return _write_status(report, RunStatus.FAILED, "judge_runtime_failed")
        state_after_judge = runtime.state_digest()
        if state_after_judge != state_before_judge:
            all_shortcuts = BenchmarkShortcutFlags(
                **{
                    **{
                        key: bool(all_shortcuts.to_dict().get(key))
                        for key in BenchmarkShortcutFlags.__dataclass_fields__
                    },
                    "judge_wrote_engine_state": True,
                }
            )

        report["latency_ms"]["judge"] += sum(item.latency_ms for item in receipts)
        report["tokens"]["input"] += sum(item.input_tokens for item in receipts)
        report["tokens"]["output"] += sum(item.output_tokens for item in receipts)
        report["cost_usd"] += sum(item.cost_usd for item in receipts)
        per_case.append(
            {
                "case_id": case_id,
                "suite": str(case.get("suite") or ""),
                "behavioral_buckets": list(case.get("behavioral_buckets") or []),
                "candidate": runtime_result.candidate,
                "structural": structural,
                "semantic_scores": scores,
                "unsupported_additions": list(judgment.get("unsupported_additions") or []),
                "critique": str(judgment.get("critique") or ""),
            }
        )

    report["runtime_configuration"]["author_role_bindings"] = dict(sorted(role_bindings.items()))
    report["runtime_configuration"]["author_environment_bindings"] = dict(sorted(environment_bindings.items()))
    report["runtime_configuration"]["semantic_roles_exercised"] = sorted(roles_exercised)
    report["shortcut_flags"] = _shortcut_dict(all_shortcuts)
    report["semantic_metrics"] = _aggregate_metrics(per_case)
    report["semantic_metric_intervals"] = _aggregate_metric_intervals(per_case)
    report["latency_ms"] = {key: round(float(value), 3) for key, value in report["latency_ms"].items()}
    report["latency_ms"]["total"] = round((time.perf_counter() - started) * 1000.0, 3)
    report["cost_usd"] = round(float(report["cost_usd"]), 8)
    if visibility == "public":
        report["cases"] = per_case
    if not all_shortcuts.is_faithful():
        report["contaminated"] = True
        _fail_invariant(report["hard_invariants"], "no_benchmark_contamination")
        return _write_status(report, RunStatus.DISQUALIFIED, "harness_contamination")
    return _write_status(report, RunStatus.COMPLETED, "scored")


def adjudicate_pack(
    *,
    pack_path: Path,
    visibility: str,
    judge_model: str | None,
    repo_root: Path,
    registry: ModelRegistry | None = None,
) -> dict[str, Any]:
    registry = registry or ModelRegistry.default()
    validation = validate_pack(
        pack_path,
        visibility=visibility,
        repo_root=repo_root,
        require_accepted=False,
    )
    output: dict[str, Any] = {
        "schema_version": ADJUDICATION_RUN_SCHEMA,
        "status": RunStatus.FAILED.value,
        "status_reason": "",
        "pack_visibility": visibility,
        "pack_checksum": validation.pack_checksum,
        "judge": _empty_model_record(judge_model),
        "test_adapter_used": False,
        "records": [],
        "warnings": [issue.to_dict() for issue in validation.issues],
    }
    if not validation.valid:
        status = RunStatus.DISQUALIFIED if validation.contaminated else RunStatus.FAILED
        output["status"] = status.value
        output["status_reason"] = "pack_contamination" if validation.contaminated else "pack_validation_failed"
        return output
    try:
        judge, judge_adapter = registry.resolve(judge_model)
    except ModelBlockedError as exc:
        output["status"] = RunStatus.BLOCKED.value
        output["status_reason"] = exc.code
        return output
    output["judge"] = judge.to_dict()
    output["test_adapter_used"] = not judge.baseline_eligible
    pack = load_pack(pack_path, visibility=visibility)
    records: list[dict[str, Any]] = []
    for case in pack.cases:
        case_id = str(case.get("case_id") or "")
        request = ModelRequest(
            role="gold_adjudicator",
            prompt_version="judge.gold_adjudication.v1",
            payload={
                "instruction": "Critique the proposed gold against only the admissible evidence. Return JSON.",
                "runtime_input": runtime_input(case),
                "proposed_gold": pack.gold[case_id],
            },
        )
        try:
            critique, _receipts = complete_json(judge_adapter, judge, request)
        except ModelExecutionError as exc:
            output["status"] = RunStatus.FAILED.value
            output["status_reason"] = exc.code
            output["records"] = []
            return output
        records.append(
            {
                "schema_version": ADJUDICATION_SCHEMA,
                "case_id": case_id,
                "judge_model": judge.identifier,
                "judge_critique": critique,
                "human_decision": "pending",
                "accepted_gold_checksum": "",
                "proposed_gold_checksum": gold_checksum(pack.gold[case_id]),
            }
        )
    output["records"] = records
    output["status"] = RunStatus.COMPLETED.value
    output["status_reason"] = "judge_critiques_ready_for_human_decision"
    return output


def _report_checksum(report: dict[str, Any]) -> str:
    canonical = json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sanitized_report(report: dict[str, Any]) -> dict[str, Any]:
    def safe_messages(items: Any, default_code: str) -> list[dict[str, str]]:
        sanitized: list[dict[str, str]] = []
        for item in items or []:
            if isinstance(item, dict):
                code = str(item.get("code") or default_code)
            else:
                code = default_code
            sanitized.append({"code": code})
        return sanitized

    return {
        "report_checksum": _report_checksum(report),
        "source_commit": report.get("source_commit"),
        "pack": dict(report.get("pack") or {}),
        "models": dict(report.get("models") or {}),
        "versions": dict(report.get("versions") or {}),
        "status": report.get("status"),
        "hard_invariants": dict(report.get("hard_invariants") or {}),
        "semantic_metrics": dict(report.get("semantic_metrics") or {}),
        "semantic_metric_intervals": dict(report.get("semantic_metric_intervals") or {}),
        "latency_ms": dict(report.get("latency_ms") or {}),
        "tokens": dict(report.get("tokens") or {}),
        "cost_usd": report.get("cost_usd", 0.0),
        "warnings": safe_messages(report.get("warnings"), "sanitized_warning"),
        "limitations": safe_messages(report.get("limitations"), "sanitized_limitation"),
    }


def build_baseline(
    *,
    public_report: dict[str, Any],
    private_report: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "schema_version": BASELINE_SCHEMA,
        "status": RunStatus.COMPLETED.value,
        "status_reason": "evaluated",
        "accepted": False,
        "gate_results": {"hard_invariants": True, "absolute_floors": True, "non_regression": True},
        "public": _sanitized_report(public_report),
        "private": _sanitized_report(private_report),
        "thresholds_checksum": _report_checksum(thresholds),
    }
    for report, expected_visibility in (
        (public_report, "public"),
        (private_report, "private"),
    ):
        if report.get("schema_version") != REPORT_SCHEMA:
            output["status"] = RunStatus.FAILED.value
            output["status_reason"] = "report_schema_invalid"
            return output
        if str((report.get("pack") or {}).get("visibility") or "") != expected_visibility:
            output["status"] = RunStatus.FAILED.value
            output["status_reason"] = "report_visibility_invalid"
            return output
        if report.get("contaminated") or not bool((report.get("shortcut_flags") or {}).get("is_faithful")):
            output["status"] = RunStatus.DISQUALIFIED.value
            output["status_reason"] = "contaminated_report"
            return output
        if bool((report.get("runtime_configuration") or {}).get("test_adapter_used")):
            output["status"] = RunStatus.DISQUALIFIED.value
            output["status_reason"] = "test_adapter_not_baseline_eligible"
            return output
        if report.get("status") != RunStatus.COMPLETED.value:
            output["status"] = str(report.get("status") or RunStatus.FAILED.value)
            output["status_reason"] = "source_report_not_completed"
            return output

    if thresholds.get("schema_version") != THRESHOLDS_SCHEMA:
        output["status"] = RunStatus.FAILED.value
        output["status_reason"] = "threshold_schema_invalid"
        return output
    if str((thresholds.get("approval") or {}).get("status") or "") != "approved":
        output["status"] = RunStatus.BLOCKED.value
        output["status_reason"] = "thresholds_not_user_approved"
        return output

    for report in (public_report, private_report):
        if any(not bool(row.get("passed")) for row in (report.get("hard_invariants") or {}).values()):
            output["gate_results"]["hard_invariants"] = False
        metrics = dict(report.get("semantic_metrics") or {})
        for key, rule in (thresholds.get("metrics") or {}).items():
            if not isinstance(rule, dict):
                output["status"] = RunStatus.FAILED.value
                output["status_reason"] = f"threshold_rule_invalid:{key}"
                return output
            actual = float(metrics.get(key, 0.0))
            floor = float(rule.get("absolute_floor", 0.0))
            if actual < floor:
                output["gate_results"]["absolute_floors"] = False
            reference_interval = rule.get("reference_interval")
            actual_interval = (report.get("semantic_metric_intervals") or {}).get(key)
            if isinstance(reference_interval, dict) and isinstance(actual_interval, dict):
                reference_lower = float(reference_interval.get("lower") or 0.0)
                actual_upper = float(actual_interval.get("upper") or 0.0)
                max_regression = float(rule.get("max_regression") or 0.0)
                if actual_upper < reference_lower - max_regression:
                    output["gate_results"]["non_regression"] = False
            elif "reference" in rule and "max_regression" in rule:
                reference = float(rule.get("reference") or 0.0)
                max_regression = float(rule.get("max_regression") or 0.0)
                if reference - actual > max_regression:
                    output["gate_results"]["non_regression"] = False

    output["accepted"] = all(bool(value) for value in output["gate_results"].values())
    return output
