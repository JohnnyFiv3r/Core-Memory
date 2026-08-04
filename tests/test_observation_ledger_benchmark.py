from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

import benchmarks.observation_ledger.providers as providers_module
from benchmarks.contracts import BenchmarkShortcutFlags
from benchmarks.observation_ledger.cli import main
from benchmarks.observation_ledger.constants import (
    ADJUDICATION_SCHEMA,
    AUTHOR_MODEL_ENV_KEYS,
    CASE_SCHEMA,
    GOLD_SCHEMA,
    REPORT_SCHEMA,
    THRESHOLDS_SCHEMA,
)
from benchmarks.observation_ledger.models import ModelRequest, ModelSelection, RuntimeCaseResult
from benchmarks.observation_ledger.pack import (
    evidence_checksum,
    file_sha256,
    gold_checksum,
    validate_pack,
)
from benchmarks.observation_ledger.providers import ModelExecutionError, ModelRegistry, ProviderModelAdapter
from benchmarks.observation_ledger.runner import adjudicate_pack, build_baseline, run_pack
from benchmarks.observation_ledger.statistics import seeded_bootstrap_mean_interval, wilson_interval
from benchmarks.observation_ledger.testing import ScriptedModelAdapter, ScriptedRuntimeAdapter

ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _case(visibility: str = "public", *, contaminated: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema_version": CASE_SCHEMA,
        "case_id": f"{visibility}-observation-001",
        "suite": "observations",
        "behavioral_buckets": ["statement_request"],
        "privacy_classification": visibility,
        "source_events": [
            {
                "event_id": "event-1",
                "tenant_id": "tenant-a",
                "event_time": "2026-08-03T12:00:00Z",
                "content": "We decided to ship the migration on Friday.",
            }
        ],
        "admissible_evidence": [
            {
                "evidence_id": "evidence-1",
                "source_event_id": "event-1",
                "tenant_id": "tenant-a",
                "content": "We decided to ship the migration on Friday.",
            }
        ],
        "task_input": {
            "tenant_id": "tenant-a",
            "task_type": "turn_memory_authoring",
            "prompt": "Annotate the observed event as JSON.",
            "output_schema": "agent_authored_updates.v1",
        },
        "expected_structural_behavior": {
            "required_output_fields": ["label", "summary", "evidence_refs", "assertions"],
            "forbidden_output_fields": ["gold"],
            "requires_citations": True,
            "assertions_may_be_empty": True,
        },
    }
    row["evidence_checksum"] = evidence_checksum(row)
    if contaminated:
        row["gold"] = {"label": "decision"}
    return row


def _gold(case_id: str) -> dict[str, Any]:
    return {
        "schema_version": GOLD_SCHEMA,
        "case_id": case_id,
        "semantic_expectations": {
            "acceptable_labels": ["decision"],
            "supported_propositions": ["The migration is planned for Friday."],
        },
        "forbidden_propositions": ["The migration completed."],
    }


def _write_pack(
    root: Path,
    *,
    visibility: str = "public",
    accepted: bool = True,
    contaminated: bool = False,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    case = _case(visibility, contaminated=contaminated)
    gold = _gold(case["case_id"])
    adjudication = {
        "schema_version": ADJUDICATION_SCHEMA,
        "case_id": case["case_id"],
        "judge_model": "human-reviewed-judge",
        "judge_critique": {"acceptable": True},
        "human_decision": "accepted",
        "accepted_gold_checksum": gold_checksum(gold),
    }
    files: dict[str, Any] = {
        "inputs/case-001.json": case,
        "gold/case-001.json": gold,
    }
    if accepted:
        files["adjudications/case-001.json"] = adjudication
    for relative, payload in files.items():
        _write_json(root / relative, payload)
    manifest = {
        "schema_version": "observation_ledger.pack.v1",
        "pack_id": f"framework-{visibility}-pack",
        "visibility": visibility,
        "prompt_version": "framework-smoke.v1",
        "schema_versions": [CASE_SCHEMA, GOLD_SCHEMA, ADJUDICATION_SCHEMA],
        "cases": ["inputs/case-001.json"],
        "gold": ["gold/case-001.json"],
        "adjudications": ["adjudications/case-001.json"] if accepted else [],
        "checksums": {relative: file_sha256(root / relative) for relative in files},
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")
    return root


def _replace_case(pack: Path, case: dict[str, Any]) -> None:
    case_path = pack / "inputs" / "case-001.json"
    _write_json(case_path, case)
    manifest_path = pack / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["checksums"]["inputs/case-001.json"] = file_sha256(case_path)
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")


def _judge_response() -> dict[str, Any]:
    return {
        "scores": {
            "evidence_entailment": 1.0,
            "label_summary_faithfulness": 1.0,
            "relevance": 1.0,
            "sufficiency": 1.0,
            "appropriate_ambiguity_abstention": 1.0,
        },
        "unsupported_additions": [],
        "critique": "Candidate is supported by the observed event.",
    }


def _registry(*responses: dict[str, Any] | str, on_complete=None) -> ModelRegistry:
    registry = ModelRegistry.default()
    registry.register(
        "test",
        ScriptedModelAdapter(list(responses), on_complete=on_complete),
    )
    return registry


def _runtime(
    *,
    model_identifier: str = "test:author-model",
    fallback: bool = False,
    shortcuts: BenchmarkShortcutFlags | None = None,
    on_execute=None,
    candidate: dict[str, Any] | None = None,
) -> ScriptedRuntimeAdapter:
    return ScriptedRuntimeAdapter(
        result=RuntimeCaseResult(
            candidate=candidate
            or {
                "label": "decision",
                "summary": "The migration is planned for Friday.",
                "evidence_refs": ["evidence-1"],
                "assertions": [],
            },
            semantic_roles_exercised=("turn_memory_authoring",),
            model_identifier=model_identifier,
            latency_ms=2.0,
            hard_invariant_failures=(),
            shortcut_flags=shortcuts or BenchmarkShortcutFlags(),
            deterministic_fallback_used=fallback,
        ),
        on_execute=on_execute,
    )


def test_public_and_private_packs_share_the_same_schema(tmp_path: Path):
    public = _write_pack(tmp_path / "public", visibility="public")
    private = _write_pack(tmp_path / "private", visibility="private")

    public_result = validate_pack(public, visibility="public", repo_root=ROOT)
    private_result = validate_pack(private.resolve(), visibility="private", repo_root=ROOT)

    assert public_result.valid, public_result.to_dict()
    assert private_result.valid, private_result.to_dict()
    assert public_result.case_count == private_result.case_count == 1


def test_private_pack_must_be_absolute_and_outside_repository(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    inside = _write_pack(repo / "private-pack", visibility="private")
    result = validate_pack(inside, visibility="private", repo_root=repo)
    assert {issue.code for issue in result.issues} >= {"private_pack_inside_repository"}

    monkeypatch.chdir(tmp_path)
    relative = Path("private-relative")
    _write_pack(tmp_path / relative, visibility="private")
    result = validate_pack(relative, visibility="private", repo_root=repo)
    assert "private_pack_path_not_absolute" in {issue.code for issue in result.issues}


def test_gold_in_case_input_is_contamination(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack", contaminated=True)

    validation = validate_pack(pack, visibility="public", repo_root=ROOT)
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(),
    )

    assert validation.contaminated
    assert report["status"] == "disqualified"
    assert report["shortcut_flags"]["gold_fields_in_runtime_input"]


def test_pack_validates_event_ids_tenants_and_evidence_spans(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    case = _case()
    case["source_events"].append(
        {
            "event_id": "event-1",
            "tenant_id": "tenant-other",
            "content": "duplicate",
        }
    )
    case["admissible_evidence"][0]["tenant_id"] = "tenant-other"
    case["admissible_evidence"][0]["span"] = {"start": 0, "end": 10_000}
    case["evidence_checksum"] = evidence_checksum(case)
    _replace_case(pack, case)

    result = validate_pack(pack, visibility="public", repo_root=ROOT)
    codes = {issue.code for issue in result.issues}

    assert not result.valid
    assert codes >= {
        "source_event_id_invalid",
        "evidence_tenant_mismatch",
        "evidence_span_out_of_bounds",
    }


def test_validate_never_resolves_a_model(tmp_path: Path, capsys):
    pack = _write_pack(tmp_path / "pack")

    class ExplodingRegistry:
        def resolve(self, _value):
            raise AssertionError("validate attempted model access")

    exit_code = main(
        ["validate", "--pack", str(pack), "--visibility", "public"],
        registry=ExplodingRegistry(),  # type: ignore[arg-type]
        repo_root=ROOT,
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_missing_selector_and_credentials_block_without_substitution(tmp_path: Path, monkeypatch):
    pack = _write_pack(tmp_path / "pack")
    missing_selector = run_pack(
        pack_path=pack,
        visibility="public",
        author_model=None,
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(),
    )
    assert missing_selector["status"] == "blocked"
    assert missing_selector["status_reason"] == "missing_model_selector"

    for key in (
        "OPENAI_API_KEY",
        "CORE_MEMORY_CHAT_API_KEY",
        "CORE_MEMORY_LLM_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    missing_credentials = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="openai:gpt-explicit",
        judge_model="openai:gpt-explicit",
        repo_root=ROOT,
        runtime=_runtime(model_identifier="openai:gpt-explicit"),
    )
    assert missing_credentials["status"] == "blocked"
    assert missing_credentials["status_reason"] == "missing_credentials:openai"
    assert missing_credentials["models"]["author"]["resolved"] == ""


def test_invalid_provider_blocks_and_never_substitutes(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="unknown:model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(),
    )
    assert report["status"] == "blocked"
    assert report["status_reason"] == "unsupported_provider:unknown"


def test_same_model_run_succeeds_and_binds_every_author_role(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    observed: dict[str, str] = {}

    def capture_environment(_case, _author):
        observed.update({key: str(os.environ.get(key) or "") for key in AUTHOR_MODEL_ENV_KEYS})

    runtime = _runtime(on_execute=capture_environment)
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:author-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=runtime,
    )

    assert report["status"] == "completed"
    assert report["models"]["relationship"] == "same_model"
    assert report["runtime_configuration"]["test_adapter_used"] is True
    assert set(observed.values()) == {"author-model"}
    assert set(report["runtime_configuration"]["author_role_bindings"].values()) == {"test:author-model"}
    assert report["cases"][0]["candidate"]["assertions"] == []


def test_same_provider_different_models_is_descriptive_not_a_gate(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(),
    )
    assert report["status"] == "completed"
    assert report["models"]["relationship"] == "same_provider"


def test_runtime_fallback_is_completed_with_hard_invariant_failure(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(fallback=True),
    )
    assert report["status"] == "completed"
    assert not report["hard_invariants"]["no_deterministic_semantic_fallback"]["passed"]


def test_temporal_and_revision_chain_errors_fail_structural_invariant(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    candidate = {
        "label": "decision",
        "summary": "The migration is planned for Friday.",
        "evidence_refs": ["evidence-1"],
        "valid_from": "2026-08-04T00:00:00Z",
        "valid_to": "2026-08-03T00:00:00Z",
        "assertions": [
            {"claim_id": "claim-1", "supersedes": "claim-2"},
            {"claim_id": "claim-2", "supersedes": "claim-1"},
        ],
    }
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(candidate=candidate),
    )

    structural = report["cases"][0]["structural"]
    assert report["status"] == "completed"
    assert not report["hard_invariants"]["schema_and_structural_integrity"]["passed"]
    assert not structural["temporal_intervals_valid"]
    assert not structural["revision_chain_valid"]
    assert "revision_cycle" in structural["revision_chain_errors"]


def test_runtime_shortcut_disqualifies_an_otherwise_scored_run(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(shortcuts=BenchmarkShortcutFlags(direct_semantic_preload=True)),
    )
    assert report["status"] == "disqualified"
    assert report["shortcut_flags"]["direct_semantic_preload"]


def test_judge_state_mutation_is_detected_and_disqualified(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    runtime = _runtime()

    def mutate_state(_request):
        runtime.state["judge-write"] = True

    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response(), on_complete=mutate_state),
        runtime=runtime,
    )
    assert report["status"] == "disqualified"
    assert report["shortcut_flags"]["judge_wrote_engine_state"]


def test_provider_failure_retains_no_semantic_output(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")

    def fail_author(_case, _author):
        raise ModelExecutionError("provider_request_failed:TimeoutError")

    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(on_execute=fail_author),
    )
    assert report["status"] == "failed"
    assert "cases" not in report
    assert report["hard_invariants"]["provider_failure_writes_no_semantics"]["passed"]


def test_provider_failure_after_state_write_fails_no_semantics_invariant(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    runtime = _runtime()

    def fail_after_write(_case, _author):
        runtime.state["semantic-write"] = True
        raise ModelExecutionError("provider_request_failed:TimeoutError")

    runtime.on_execute = fail_after_write
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=runtime,
    )

    assert report["status"] == "failed"
    assert not report["hard_invariants"]["provider_failure_writes_no_semantics"]["passed"]


def test_provider_errors_never_expose_credentials(monkeypatch):
    secret = "super-secret-provider-key"
    monkeypatch.setenv("GOOGLE_API_KEY", secret)

    def fail_provider(*_args, **_kwargs):
        raise RuntimeError(f"provider echoed {secret}")

    monkeypatch.setattr(providers_module, "chat_complete", fail_provider)
    adapter = ProviderModelAdapter()
    resolved = adapter.resolve(ModelSelection.parse("google:explicit-model"))
    with pytest.raises(ModelExecutionError) as caught:
        adapter.complete(ModelRequest(role="judge", prompt_version="v1", payload={}), resolved)

    assert secret not in str(caught.value)


def test_invalid_json_retries_without_semantic_fallback(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    registry = _registry("not-json", _judge_response())
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=registry,
        runtime=_runtime(),
    )

    adapter = registry.adapters["test"]
    assert report["status"] == "completed"
    assert isinstance(adapter, ScriptedModelAdapter)
    assert len(adapter.requests) == 2


def test_exhausted_invalid_json_retries_write_no_scored_cases(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry("bad", "bad", "bad"),
        runtime=_runtime(),
    )

    assert report["status"] == "failed"
    assert report["status_reason"] == "judge_runtime_failed"
    assert "cases" not in report


def test_private_report_contains_no_per_case_or_private_identifier(tmp_path: Path):
    pack = _write_pack(tmp_path / "private-pack", visibility="private")
    report = run_pack(
        pack_path=pack.resolve(),
        visibility="private",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(),
    )
    encoded = json.dumps(report, sort_keys=True)
    assert report["status"] == "completed"
    assert "cases" not in report
    assert "private-observation-001" not in encoded


def test_adjudication_is_judge_critique_then_pending_human_decision(tmp_path: Path):
    pack = _write_pack(tmp_path / "proposed", accepted=False)
    registry = _registry({"acceptable": True, "issues": [], "recommended_changes": []})
    output = adjudicate_pack(
        pack_path=pack,
        visibility="public",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=registry,
    )
    assert output["status"] == "completed"
    assert output["records"][0]["human_decision"] == "pending"
    assert output["records"][0]["accepted_gold_checksum"] == ""
    adapter = registry.adapters["test"]
    assert isinstance(adapter, ScriptedModelAdapter)
    assert "root" not in adapter.requests[0].payload
    assert "case_id" not in adapter.requests[0].payload["runtime_input"]
    assert "suite" not in adapter.requests[0].payload["runtime_input"]
    assert "proposed_gold" in adapter.requests[0].payload


def test_adjudication_missing_judge_selector_is_blocked(tmp_path: Path):
    pack = _write_pack(tmp_path / "proposed", accepted=False)
    output = adjudicate_pack(
        pack_path=pack,
        visibility="public",
        judge_model=None,
        repo_root=ROOT,
        registry=_registry({"acceptable": True}),
    )

    assert output["status"] == "blocked"
    assert output["status_reason"] == "missing_model_selector"


def test_private_adjudication_cannot_be_written_inside_repository(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    pack = _write_pack(tmp_path / "private-pack", visibility="private")
    out = repo / "private-adjudication.json"
    exit_code = main(
        [
            "adjudicate",
            "--pack",
            str(pack.resolve()),
            "--visibility",
            "private",
            "--judge-model",
            "test:judge-model",
            "--out",
            str(out),
        ],
        registry=_registry({"acceptable": True}),
        repo_root=repo,
    )
    assert exit_code == 2
    assert json.loads(out.read_text())["status_reason"] == "private_adjudication_output_inside_repository"


def test_cli_missing_author_selector_writes_blocked_report(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    out = tmp_path / "blocked.json"
    exit_code = main(
        [
            "run",
            "--pack",
            str(pack),
            "--judge-model",
            "test:judge-model",
            "--out",
            str(out),
        ],
        registry=_registry(_judge_response()),
        runtime=_runtime(),
        repo_root=ROOT,
    )
    assert exit_code == 2
    assert json.loads(out.read_text())["status"] == "blocked"


def _eligible_report(report: dict[str, Any], visibility: str) -> dict[str, Any]:
    eligible = deepcopy(report)
    eligible["pack"]["visibility"] = visibility
    eligible["runtime_configuration"]["test_adapter_used"] = False
    eligible["models"]["author"]["baseline_eligible"] = True
    eligible["models"]["judge"]["baseline_eligible"] = True
    return eligible


def test_test_adapter_can_never_produce_an_accepted_baseline(tmp_path: Path):
    public_pack = _write_pack(tmp_path / "public", visibility="public")
    private_pack = _write_pack(tmp_path / "private", visibility="private")
    public = run_pack(
        pack_path=public_pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(),
    )
    private = run_pack(
        pack_path=private_pack.resolve(),
        visibility="private",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(),
    )
    thresholds = {
        "schema_version": THRESHOLDS_SCHEMA,
        "approval": {"status": "approved", "approved_by": "user"},
        "metrics": {},
    }
    baseline = build_baseline(
        public_report=public,
        private_report=private,
        thresholds=thresholds,
    )
    assert baseline["status"] == "disqualified"
    assert not baseline["accepted"]


def test_baseline_applies_floors_and_sanitizes_private_details(tmp_path: Path):
    public_pack = _write_pack(tmp_path / "public", visibility="public")
    private_pack = _write_pack(tmp_path / "private", visibility="private")
    public_raw = run_pack(
        pack_path=public_pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(),
    )
    private_raw = run_pack(
        pack_path=private_pack.resolve(),
        visibility="private",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(),
    )
    public = _eligible_report(public_raw, "public")
    private = _eligible_report(private_raw, "private")
    private["cases"] = [{"case_id": "never-commit-this-private-id"}]
    private["limitations"] = ["never-commit-this-private-id"]
    thresholds = {
        "schema_version": THRESHOLDS_SCHEMA,
        "approval": {"status": "approved", "approved_by": "user"},
        "metrics": {
            "evidence_entailment": {
                "absolute_floor": 0.9,
                "reference": 1.0,
                "max_regression": 0.05,
            }
        },
    }

    baseline = build_baseline(
        public_report=public,
        private_report=private,
        thresholds=thresholds,
    )

    assert baseline["status"] == "completed"
    assert baseline["accepted"]
    assert "cases" not in baseline["private"]
    assert "never-commit-this-private-id" not in json.dumps(baseline)


def test_report_uses_wilson_and_seeded_bootstrap_intervals(tmp_path: Path):
    pack = _write_pack(tmp_path / "pack")
    report = run_pack(
        pack_path=pack,
        visibility="public",
        author_model="test:author-model",
        judge_model="test:judge-model",
        repo_root=ROOT,
        registry=_registry(_judge_response()),
        runtime=_runtime(),
    )

    interval = report["semantic_metric_intervals"]["evidence_entailment"]
    assert interval["method"] == "wilson_95"
    assert interval["sample_size"] == 1
    assert 0 < interval["lower"] < interval["upper"] == 1

    assert wilson_interval(5, 10) == pytest.approx((0.236593090512564, 0.7634069094874361))
    first = seeded_bootstrap_mean_interval([0.1, 0.4, 0.9], seed=17, samples=200)
    second = seeded_bootstrap_mean_interval([0.1, 0.4, 0.9], seed=17, samples=200)
    assert first == second


def test_unapproved_thresholds_block_baseline_acceptance(tmp_path: Path):
    public_pack = _write_pack(tmp_path / "public", visibility="public")
    private_pack = _write_pack(tmp_path / "private", visibility="private")
    public = _eligible_report(
        run_pack(
            pack_path=public_pack,
            visibility="public",
            author_model="test:author-model",
            judge_model="test:judge-model",
            repo_root=ROOT,
            registry=_registry(_judge_response()),
            runtime=_runtime(),
        ),
        "public",
    )
    private = _eligible_report(
        run_pack(
            pack_path=private_pack.resolve(),
            visibility="private",
            author_model="test:author-model",
            judge_model="test:judge-model",
            repo_root=ROOT,
            registry=_registry(_judge_response()),
            runtime=_runtime(),
        ),
        "private",
    )
    baseline = build_baseline(
        public_report=public,
        private_report=private,
        thresholds={
            "schema_version": THRESHOLDS_SCHEMA,
            "approval": {"status": "pending", "approved_by": ""},
            "metrics": {},
        },
    )
    assert baseline["status"] == "blocked"
    assert not baseline["accepted"]


def test_all_committed_json_schemas_are_valid_json():
    schema_dir = ROOT / "benchmarks" / "observation_ledger" / "schemas"
    schemas = [json.loads(path.read_text()) for path in sorted(schema_dir.glob("*.json"))]
    assert len(schemas) == 7
    assert REPORT_SCHEMA in {schema["$id"] for schema in schemas}


def test_shared_shortcut_contract_covers_observation_ledger_contamination():
    flags = BenchmarkShortcutFlags(
        gold_fields_in_runtime_input=True,
        direct_semantic_preload=True,
        runtime_semantic_bypass=True,
        judge_wrote_engine_state=True,
    )
    assert not flags.is_faithful()
    assert all(
        flags.to_dict()[key]
        for key in (
            "gold_fields_in_runtime_input",
            "direct_semantic_preload",
            "runtime_semantic_bypass",
            "judge_wrote_engine_state",
        )
    )
