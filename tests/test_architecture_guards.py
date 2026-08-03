from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_architecture_guards.py"
BASELINE = ROOT / "scripts" / "architecture_guards_baseline.json"
COMPAT_BASELINE = ROOT / "scripts" / "compat_surface_usage_baseline.json"


def _load_guard_module():
    spec = importlib.util.spec_from_file_location("architecture_guards", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["architecture_guards"] = module
    spec.loader.exec_module(module)
    return module


guards = _load_guard_module()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _exception(**overrides) -> dict:
    row = {
        "id": "CMEX-TEST",
        "invariant_ids": ["CM-SEM-001", "CM-SEM-002"],
        "category": "semantic_fallback",
        "path": "core_memory/policy/fallback.py",
        "symbol": "core_memory.policy.fallback.semantic_memory_fallback",
        "allowed_behavior": "Emit visible compatibility diagnostics.",
        "forbidden_behavior": "Must not write canonical semantics.",
        "justification": "Temporary test exception.",
        "provenance_requirement": "source event and failure receipt",
        "owner": "test-owner",
        "delete_by_pr": "PR-02D",
        "max_occurrences": 1,
        "governed_calls": [],
    }
    row.update(overrides)
    row["fingerprint"] = guards.architecture_exception_fingerprint(row)
    return row


def _architecture_baseline(exceptions: list[dict] | None = None) -> dict:
    return {
        "schema_version": guards.SCHEMA_VERSION,
        "generated_from_commit": "0" * 40,
        "current_phase": "PR-00A",
        "invariants": guards.ARCHITECTURE_INVARIANTS,
        "violation_ids": [],
        "violations": [],
        "exceptions": exceptions or [],
    }


def test_detects_upward_imports(tmp_path: Path):
    _write(tmp_path / "core_memory" / "__init__.py", "")
    _write(tmp_path / "core_memory" / "persistence" / "__init__.py", "")
    _write(
        tmp_path / "core_memory" / "persistence" / "store_add_bead_ops.py",
        "from core_memory.runtime.queue.jobs import enqueue\n",
    )
    _write(tmp_path / "core_memory" / "runtime" / "queue" / "__init__.py", "")
    _write(tmp_path / "core_memory" / "runtime" / "queue" / "jobs.py", "")

    violations = guards.check_upward_imports(tmp_path)

    assert [v.check for v in violations] == ["upward_import"]
    assert violations[0].detail["source_layer"] == "persistence"
    assert violations[0].detail["target_layer"] == "runtime"


def test_detects_root_flat_file_drift(tmp_path: Path):
    _write(tmp_path / "core_memory" / "__init__.py", "")
    _write(tmp_path / "core_memory" / "new_root_helper.py", "")
    _write(tmp_path / "core_memory" / "runtime" / "__init__.py", "")
    _write(tmp_path / "core_memory" / "runtime" / "new_runtime_helper.py", "")

    violations = guards.check_flat_files(tmp_path)

    ids = {v.id for v in violations}
    assert "flat_file:core_memory/new_root_helper.py" in ids
    assert "flat_file:core_memory/runtime/new_runtime_helper.py" in ids


def test_detects_broken_current_doc_links(tmp_path: Path):
    _write(tmp_path / "README.md", "See [missing](docs/missing.md).\n")
    _write(tmp_path / "docs" / "index.md", "See [ok](status.md).\n")
    _write(tmp_path / "docs" / "status.md", "# Status\n")
    _write(tmp_path / "docs" / "archive" / "old.md", "See [ignored](missing.md).\n")

    violations = guards.check_markdown_links(tmp_path)

    assert [v.id for v in violations] == ["markdown_link:README.md:docs/missing.md"]


def test_detects_cleanup_docs_claiming_existing_debt_was_deleted(tmp_path: Path):
    active_path = tmp_path / "core_memory" / "graph" / "api.py"
    _write(active_path, "# compat facade\n")
    _write(tmp_path / "docs" / "cleanup-plan.md", "- [x] `core_memory/graph/api.py` deleted\n")

    violations = guards.check_cleanup_truth(tmp_path)

    assert [v.check for v in violations] == ["cleanup_truth"]
    assert violations[0].detail["active_path"] == "core_memory/graph/api.py"


def test_detects_cleanup_docs_claiming_live_path_has_no_imports(tmp_path: Path):
    active_path = tmp_path / "core_memory" / "retrieval" / "vector_backend.py"
    _write(active_path, "# live vector backend\n")
    _write(
        tmp_path / "docs" / "PRD" / "01-dead-file-removal.md",
        "- [ ] `core_memory/retrieval/vector_backend.py` -- no imports anywhere\n",
    )

    violations = guards.check_cleanup_truth(tmp_path)

    assert [v.check for v in violations] == ["cleanup_truth"]
    assert violations[0].detail["active_path"] == "core_memory/retrieval/vector_backend.py"


def test_detects_cleanup_docs_claiming_public_compat_surface_was_removed(tmp_path: Path):
    _write(tmp_path / "docs" / "status.md", "- [x] `form_submission` removed\n")

    violations = guards.check_cleanup_truth(tmp_path)

    assert [v.check for v in violations] == ["cleanup_truth"]
    assert violations[0].detail["surface_key"] == "typed_search_form_submission_alias"


def test_detects_prd_files_missing_from_index(tmp_path: Path):
    _write(tmp_path / "docs" / "PRD" / "README.md", "| `listed.md` | Listed | Draft |\n")
    _write(tmp_path / "docs" / "PRD" / "listed.md", "# Listed\n")
    _write(tmp_path / "docs" / "PRD" / "missing.md", "# Missing\n")

    violations = guards.check_prd_index(tmp_path)

    assert [v.check for v in violations] == ["prd_index"]
    assert violations[0].detail["prd_file"] == "docs/PRD/missing.md"


def test_cleanup_truth_allows_explicit_public_compat_deprecation_conditions(tmp_path: Path):
    _write(
        tmp_path / "docs" / "compatibility_ledger.md",
        (
            "| `MemoryStore.dream(...)` | Public legacy bridge | "
            "Remove only after a breaking-change/deprecation window. |\n"
        ),
    )

    violations = guards.check_cleanup_truth(tmp_path)

    assert violations == []


def test_detects_compat_surface_usage(tmp_path: Path):
    _write(
        tmp_path / "tests" / "test_new_semantic_task.py",
        "from core_memory.runtime.semantic_tasks import SemanticTaskResult\n",
    )
    _write(
        tmp_path / "core_memory" / "integrations" / "bridge.py",
        "payload = {'form_submission': {'query_text': 'policy'}}\n",
    )

    violations = guards.check_compat_surface_usage(tmp_path)

    surfaces = {v.detail["surface_key"] for v in violations}
    assert surfaces == {"runtime_semantic_tasks", "typed_search_form_submission"}


def test_deterministic_writer_guard_rejects_preview_authority(tmp_path: Path):
    _write(
        tmp_path / "scripts" / "architecture_guards_baseline.json",
        json.dumps(_architecture_baseline()),
    )
    _write(
        tmp_path / "core_memory" / "association" / "crawler_contract.py",
        "def apply():\n    return infer_relationship({}, {})\n",
    )

    violations = guards.check_deterministic_semantic_writers(tmp_path)

    assert [violation.detail["forbidden_call"] for violation in violations] == ["infer_relationship"]


def test_exception_registry_rejects_wildcards_stale_fingerprints_and_nonexpiring_prs(tmp_path: Path):
    row = _exception(path="core_memory/**/*.py", delete_by_pr="PR-00A")
    row["fingerprint"] = "stale"
    _write(
        tmp_path / "scripts" / "architecture_guards_baseline.json",
        json.dumps(_architecture_baseline([row])),
    )

    violations = guards.check_architecture_exception_registry(tmp_path)

    ids = {violation.id for violation in violations}
    assert "architecture_exception:CMEX-TEST:path" in ids
    assert "architecture_exception:CMEX-TEST:delete_by_pr" in ids
    assert "architecture_exception:CMEX-TEST:fingerprint" in ids


def test_exception_registry_reports_the_json_row_and_rejects_expired_rows(tmp_path: Path):
    _write(
        tmp_path / "core_memory" / "policy" / "fallback.py",
        "def semantic_memory_fallback():\n    pass\n",
    )
    row = _exception(delete_by_pr="PR-02D")
    baseline = json.dumps(_architecture_baseline([row]), indent=2)
    _write(tmp_path / "scripts" / "architecture_guards_baseline.json", baseline)

    violations = guards.check_architecture_exception_registry(tmp_path, current_phase="PR-02D")

    expired = next(violation for violation in violations if violation.id.endswith(":expired"))
    assert expired.line == baseline[: baseline.index('"id": "CMEX-TEST"')].count("\n") + 1


def test_committed_exception_registry_can_only_shrink():
    previous_row = _exception(max_occurrences=2)
    previous = _architecture_baseline([previous_row])

    widened_row = _exception(max_occurrences=500)
    widened = guards.compare_exception_registries(_architecture_baseline([widened_row]), previous)
    assert [violation.id for violation in widened] == ["architecture_exception:CMEX-TEST:exception_widened"]

    changed_row = _exception(
        max_occurrences=2,
        allowed_behavior="May manufacture new semantic output.",
    )
    changed = guards.compare_exception_registries(_architecture_baseline([changed_row]), previous)
    assert [violation.id for violation in changed] == ["architecture_exception:CMEX-TEST:exception_widened"]

    reduced_row = _exception(max_occurrences=1)
    assert guards.compare_exception_registries(_architecture_baseline([reduced_row]), previous) == []
    assert guards.compare_exception_registries(_architecture_baseline(), previous) == []

    new_row = _exception(id="CMEX-NEW")
    assert [
        violation.id
        for violation in guards.compare_exception_registries(_architecture_baseline([previous_row, new_row]), previous)
    ] == ["architecture_exception:CMEX-NEW:new_exception"]

    previous_with_call = _architecture_baseline([_exception(governed_calls=["write_claims_to_bead"])])
    assert (
        guards.compare_exception_registries(
            _architecture_baseline([_exception(governed_calls=[])]),
            previous_with_call,
        )
        == []
    )
    added_call = _exception(governed_calls=["add_structural_edge", "write_claims_to_bead"])
    assert [
        violation.id
        for violation in guards.compare_exception_registries(_architecture_baseline([added_call]), previous_with_call)
    ] == ["architecture_exception:CMEX-TEST:exception_widened"]


def test_committed_exception_registry_phase_cannot_move_backward():
    previous = _architecture_baseline()
    previous["current_phase"] = "PR-01A"
    current = _architecture_baseline()

    violations = guards.compare_exception_registries(current, previous)

    assert [violation.id for violation in violations] == ["architecture_exception:current_phase_regression"]


def test_exception_registry_counts_governed_calls_not_function_definitions(tmp_path: Path):
    path = tmp_path / "core_memory" / "claim" / "update_policy.py"
    _write(
        path,
        ("def emit_claim_updates():\n    write_claim_updates_to_bead('root', 'bead', [])\n"),
    )
    row = _exception(
        category="deterministic_semantic_author",
        path="core_memory/claim/update_policy.py",
        symbol="core_memory.claim.update_policy.emit_claim_updates",
        governed_calls=["write_claim_updates_to_bead"],
    )
    _write(
        tmp_path / "scripts" / "architecture_guards_baseline.json",
        json.dumps(_architecture_baseline([row])),
    )

    assert guards.check_architecture_exception_registry(tmp_path) == []

    _write(
        path,
        (
            "def emit_claim_updates():\n"
            "    write_claim_updates_to_bead('root', 'bead', [])\n"
            "    write_claim_updates_to_bead('root', 'bead', [])\n"
        ),
    )
    assert [violation.id for violation in guards.check_architecture_exception_registry(tmp_path)] == [
        "architecture_exception:CMEX-TEST:occurrence_increase"
    ]

    path.unlink()
    assert [violation.id for violation in guards.check_architecture_exception_registry(tmp_path)] == [
        "architecture_exception:CMEX-TEST:stale_target"
    ]


def test_semantic_fallback_must_be_an_exact_registered_exception(tmp_path: Path):
    _write(
        tmp_path / "scripts" / "architecture_guards_baseline.json",
        json.dumps(_architecture_baseline()),
    )
    _write(
        tmp_path / "core_memory" / "policy" / "fallback.py",
        "def semantic_memory_fallback():\n    return {'summary': 'manufactured'}\n",
    )

    violations = guards.check_semantic_fallback_paths(tmp_path)

    assert [violation.detail["category"] for violation in violations] == ["semantic_fallback"]

    row = _exception()
    _write(
        tmp_path / "scripts" / "architecture_guards_baseline.json",
        json.dumps(_architecture_baseline([row])),
    )
    assert guards.check_semantic_fallback_paths(tmp_path) == []


def test_new_legacy_semantic_mutation_call_requires_exact_exception(tmp_path: Path):
    _write(
        tmp_path / "scripts" / "architecture_guards_baseline.json",
        json.dumps(_architecture_baseline()),
    )
    _write(
        tmp_path / "core_memory" / "new_authority.py",
        "def author_claims():\n    write_claims_to_bead('root', 'bead', [])\n",
    )

    violations = guards.check_semantic_mutation_calls(tmp_path)

    assert [violation.detail["mutation_call"] for violation in violations] == ["write_claims_to_bead"]


def test_mutation_exception_only_allows_the_named_call(tmp_path: Path):
    row = _exception(
        category="deterministic_semantic_author",
        path="core_memory/new_authority.py",
        symbol="core_memory.new_authority.author_claims",
        governed_calls=["write_claims_to_bead"],
    )
    _write(
        tmp_path / "scripts" / "architecture_guards_baseline.json",
        json.dumps(_architecture_baseline([row])),
    )
    _write(
        tmp_path / "core_memory" / "new_authority.py",
        ("def author_claims():\n    write_claims_to_bead('root', 'bead', [])\n    add_structural_edge('a', 'b')\n"),
    )

    violations = guards.check_semantic_mutation_calls(tmp_path)

    assert [violation.detail["mutation_call"] for violation in violations] == ["add_structural_edge"]


def test_target_boundary_activates_when_target_package_appears(tmp_path: Path):
    _write(tmp_path / "core_memory" / "ledger" / "__init__.py", "")
    _write(
        tmp_path / "core_memory" / "ledger" / "store.py",
        "from core_memory.persistence.store import MemoryStore\n",
    )

    violations = guards.check_target_boundaries(tmp_path)

    assert [violation.detail["target_root"] for violation in violations] == ["core_memory/ledger"]


def test_target_boundaries_are_allowlists_for_all_core_memory_imports(tmp_path: Path):
    _write(tmp_path / "core_memory" / "ledger" / "__init__.py", "")
    _write(
        tmp_path / "core_memory" / "ledger" / "store.py",
        (
            "from core_memory.schema import Bead\n"
            "from core_memory.integrations.api import ingest\n"
            "from core_memory.retrieval.pipeline import search\n"
            "from core_memory.graph.core import Graph\n"
        ),
    )
    _write(tmp_path / "core_memory" / "semantic" / "__init__.py", "")
    _write(
        tmp_path / "core_memory" / "semantic" / "author.py",
        (
            "from core_memory.ledger.store import Ledger\n"
            "from core_memory.persistence.store import MemoryStore\n"
            "from core_memory.runtime.engine import Engine\n"
        ),
    )

    violations = guards.check_target_boundaries(tmp_path)

    assert {violation.detail["target_root"] for violation in violations} == {
        "core_memory/ledger",
        "core_memory/semantic",
    }
    assert {violation.id.rsplit(":", 1)[-1] for violation in violations} == {
        "core_memory.integrations.api",
        "core_memory.retrieval.pipeline",
        "core_memory.graph.core",
        "core_memory.persistence.store",
        "core_memory.runtime.engine",
    }


def test_baseline_candidate_cannot_overwrite_canonical_baseline(tmp_path: Path):
    _write(
        tmp_path / "scripts" / "architecture_guards_baseline.json",
        json.dumps(_architecture_baseline()),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "--write-baseline-candidate",
            "scripts/architecture_guards_baseline.json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Refusing to overwrite" in result.stderr


def test_baseline_candidate_fails_cleanly_when_canonical_baseline_is_missing(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "--write-baseline-candidate",
            "candidate.json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Cannot create architecture baseline candidate" in result.stderr
    assert "Traceback" not in result.stderr


def test_baseline_candidate_fails_cleanly_when_canonical_baseline_is_malformed(tmp_path: Path):
    _write(tmp_path / "scripts" / "architecture_guards_baseline.json", "{not-json\n")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "--write-baseline-candidate",
            "candidate.json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Cannot create architecture baseline candidate" in result.stderr
    assert "Traceback" not in result.stderr


def test_compat_baseline_allows_reductions_but_fails_increases(tmp_path: Path):
    _write(
        tmp_path / "tests" / "test_search.py",
        "\n".join(
            [
                "payload = {'form_submission': {'query_text': 'policy'}}",
                "other = {'form_submission': {'query_text': 'claim'}}",
            ]
        )
        + "\n",
    )
    violations = guards.check_compat_surface_usage(tmp_path)
    baseline = {
        "allowed_counts": {
            "typed_search_form_submission": {
                "tests/test_search.py": 1,
                "tests/removed.py": 1,
            }
        }
    }

    new, resolved = guards.compare_compat_to_baseline(violations, baseline)

    assert [v.line for v in new] == [2]
    assert resolved == ["typed_search_form_submission:tests/removed.py:1"]


def test_current_baseline_has_no_new_architecture_drift():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--baseline",
            str(BASELINE),
            "--fail-on-new",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_current_compat_surface_baseline_has_no_new_drift():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--compat-baseline",
            str(COMPAT_BASELINE),
            "--fail-on-new-compat",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
