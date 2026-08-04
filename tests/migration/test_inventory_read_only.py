from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core_memory.migration.classification import CLASSIFICATION_SCHEMA_VERSION, load_classification_map
from core_memory.migration.inventory import (
    INVENTORY_SCHEMA_VERSION,
    scan_code,
    scan_filesystem,
    validate_code_output_path,
    validate_output_path,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "inventory_legacy_memory.py"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        stat = path.lstat()
        if path.is_symlink():
            digest = hashlib.sha256(os.readlink(path).encode("utf-8")).hexdigest()
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            digest = "directory"
        snapshot[relative] = (stat.st_mode, stat.st_mtime_ns, digest)
    return snapshot


def _record_by_kind(report, kind: str):
    return next(row for row in report.records if row.record_kind == kind)


def test_static_code_scan_classifies_authorities_symbols_and_paths_without_execution(tmp_path: Path):
    source = tmp_path / "repo" / "core_memory" / "claim" / "authority.py"
    _write(
        source,
        """
import os
import sqlite3
from pathlib import Path

class ClaimStore:
    pass

class ClaimJobQueue:
    pass

def semantic_claim_fallback():
    return {}

def resolve_claim(claims):
    return claims[-1]

def operate(root, connection):
    path = Path(root) / '.beads' / 'index.json'
    payload = path.read_text(encoding='utf-8')
    path.write_text(payload, encoding='utf-8')
    connection.execute('SELECT * FROM claims')
    connection.execute('INSERT INTO claims VALUES (?)', ('x',))
    enqueue({'kind': 'claim'})
    return os.environ.get('CORE_MEMORY_CLAIM_MODEL')
""".lstrip(),
    )
    before = _tree_snapshot(tmp_path / "repo")

    report = scan_code(
        (tmp_path / "repo").resolve(),
        includes=[Path("core_memory")],
        source_label="repository-source",
        tenant_workspace_classification="all-workspaces-static",
    )

    assert report.complete
    assert _tree_snapshot(tmp_path / "repo") == before
    assert {row.record_kind for row in report.records} >= {
        "environment_selector",
        "external_backend",
        "file_reader",
        "file_writer",
        "queue_class",
        "queue_operation",
        "semantic_fallback",
        "semantic_resolver",
        "sql_reader",
        "sql_writer",
        "store_class",
    }
    writer = _record_by_kind(report, "file_writer")
    assert writer.writer_symbols == ("core_memory.claim.authority.operate",)
    assert writer.details["artifact_template"] == "{path}"
    assert writer.planned_deletion_pr == "PR-10J"
    assert report.to_dict()["summary"]["unclassified_count"] == 0
    assert all(row.provenance_classification == "human_authored" for row in report.records)


def test_code_scan_reports_parse_failure_without_executing_or_repairing_file(tmp_path: Path):
    source = tmp_path / "repo" / "core_memory" / "broken.py"
    _write(source, "def broken(:\n")
    before = _tree_snapshot(tmp_path / "repo")

    report = scan_code(
        (tmp_path / "repo").resolve(),
        includes=[Path("core_memory")],
        source_label="repository-source",
        tenant_workspace_classification="all-workspaces-static",
    )

    assert not report.complete
    assert report.records[0].parse_failure_count == 1
    assert report.warnings == ({"code": "python_parse_failure", "locator": "core_memory/broken.py"},)
    assert _tree_snapshot(tmp_path / "repo") == before


def test_filesystem_scan_counts_hashes_times_duplicates_and_parse_failures_read_only(tmp_path: Path):
    workspace = tmp_path / "workspace"
    data = tmp_path / "legacy-data"
    workspace.mkdir()
    data.mkdir()
    _write(
        data / ".beads" / "session-alpha.jsonl",
        "\n".join(
            [
                json.dumps({"id": "bead-1", "event_time": "2026-08-03T10:00:00Z"}),
                json.dumps({"id": "bead-1", "event_time": "2026-08-04T12:00:00+00:00"}),
                "{malformed",
            ]
        )
        + "\n",
    )
    _write(
        data / ".beads" / "events" / "semantic-task-runs.jsonl",
        json.dumps({"id": "run-1", "receipt_id": "receipt-1", "created_at": "2026-08-04T11:00:00Z"}) + "\n",
    )
    _write(data / ".beads" / "index.json", json.dumps({"beads": {"bead-1": {"id": "bead-1"}}}))
    _write(data / "SOUL.md", "# Principles\n")
    (data / "legacy.sqlite").write_bytes(b"not-opened-by-filesystem-scan")
    outside = tmp_path / "outside.jsonl"
    _write(outside, json.dumps({"id": "outside-secret"}) + "\n")
    os.symlink(outside, data / "external-link.jsonl")
    before = _tree_snapshot(data)

    report = scan_filesystem(
        data.resolve(),
        workspace_root=workspace.resolve(),
        source_label="local-legacy-fixture",
        tenant_workspace_classification="tenant-a/workspace-a",
    )
    after = _tree_snapshot(data)

    assert after == before
    assert report.complete
    session = next(row for row in report.records if row.locator.endswith("session-alpha.jsonl"))
    assert session.record_count == 2
    assert session.parse_failure_count == 1
    assert session.duplicate_id_count == 1
    assert session.min_event_time == "2026-08-03T10:00:00Z"
    assert session.max_event_time == "2026-08-04T12:00:00Z"
    assert session.recoverability_status == "partially_recoverable"
    receipt = next(row for row in report.records if row.locator.endswith("semantic-task-runs.jsonl"))
    assert receipt.provenance_classification == "llm_authored_with_receipt"
    database = next(row for row in report.records if row.locator == "legacy.sqlite")
    assert database.recoverability_status == "requires_read_only_sql_scan"
    symlink = next(row for row in report.records if row.locator == "external-link.jsonl")
    assert symlink.locator_kind == "symlink"
    assert symlink.record_count == 1
    assert "outside-secret" not in json.dumps(report.to_dict())


def test_filesystem_reports_are_deterministic_and_redact_sensitive_labels(tmp_path: Path):
    workspace = tmp_path / "workspace"
    data = tmp_path / "data"
    workspace.mkdir()
    data.mkdir()
    _write(data / "events.jsonl", json.dumps({"event_id": "evt-1"}) + "\n")
    secret_label = "postgresql://user:super-secret@db.example/core?token=hidden"

    first = scan_filesystem(
        data.resolve(),
        workspace_root=workspace.resolve(),
        source_label=secret_label,
        tenant_workspace_classification="tenant-a",
    ).to_dict()
    second = scan_filesystem(
        data.resolve(),
        workspace_root=workspace.resolve(),
        source_label=secret_label,
        tenant_workspace_classification="tenant-a",
    ).to_dict()

    encoded = json.dumps(first, sort_keys=True)
    assert first == second
    assert "super-secret" not in encoded
    assert "token=hidden" not in encoded
    assert str(data.resolve()) not in encoded
    assert first["source_label"].startswith("redacted-source_label-")


@pytest.mark.parametrize("unsafe", [Path("/"), Path.home()])
def test_filesystem_scan_rejects_root_and_home(unsafe: Path, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="unsafe_broad_scan_root"):
        scan_filesystem(
            unsafe,
            workspace_root=workspace.resolve(),
            source_label="unsafe",
            tenant_workspace_classification="tenant-a",
        )


def test_scan_roots_and_outputs_must_be_explicit_and_separate(tmp_path: Path):
    workspace = tmp_path / "workspace"
    data = tmp_path / "data"
    workspace.mkdir()
    data.mkdir()
    with pytest.raises(ValueError, match="scan_root_must_be_absolute"):
        scan_filesystem(
            Path("relative-data"),
            workspace_root=workspace.resolve(),
            source_label="local",
            tenant_workspace_classification="tenant-a",
        )
    with pytest.raises(ValueError, match="filesystem_scan_refuses_workspace_root"):
        scan_filesystem(
            workspace.resolve(),
            workspace_root=workspace.resolve(),
            source_label="local",
            tenant_workspace_classification="tenant-a",
        )
    with pytest.raises(ValueError, match="filesystem_scan_refuses_workspace_ancestor"):
        scan_filesystem(
            tmp_path.resolve(),
            workspace_root=workspace.resolve(),
            source_label="local",
            tenant_workspace_classification="tenant-a",
        )
    with pytest.raises(ValueError, match="inventory_output_must_be_absolute"):
        validate_output_path(Path("report.json"), scan_root=data.resolve())
    with pytest.raises(ValueError, match="inventory_output_inside_scan_root"):
        validate_output_path((data / "report.json").resolve(), scan_root=data.resolve())
    assert (
        validate_output_path((tmp_path / "report.json").resolve(), scan_root=data.resolve())
        == (tmp_path / "report.json").resolve()
    )

    code_root = workspace / "code"
    included = code_root / "core_memory"
    docs = code_root / "docs"
    included.mkdir(parents=True)
    docs.mkdir()
    assert (
        validate_code_output_path(
            (docs / "inventory.json").resolve(),
            code_root=code_root.resolve(),
            includes=[Path("core_memory")],
        )
        == (docs / "inventory.json").resolve()
    )
    with pytest.raises(ValueError, match="inventory_output_inside_code_include"):
        validate_code_output_path(
            (included / "inventory.json").resolve(),
            code_root=code_root.resolve(),
            includes=[Path("core_memory")],
        )


def test_classification_map_and_report_schema_are_versioned_json():
    classification = load_classification_map()
    schema = json.loads((ROOT / "core_memory" / "data" / "legacy_inventory_report.v1.schema.json").read_text())

    assert classification.schema_version == CLASSIFICATION_SCHEMA_VERSION
    assert schema["$id"] == INVENTORY_SCHEMA_VERSION
    assert set(classification.provenance_classes) == {
        "observed_source",
        "llm_authored_with_receipt",
        "llm_authored_without_receipt",
        "deterministic_derived",
        "human_authored",
        "projection_only",
        "unknown",
    }


def test_committed_static_inventory_is_complete_exact_and_safe():
    inventory_path = ROOT / "docs" / "migration" / "legacy-static-code-inventory.v1.json"
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    classification = load_classification_map()

    assert payload["schema_version"] == INVENTORY_SCHEMA_VERSION
    assert payload["scanner"] == "static_code"
    assert payload["read_only"] is True
    assert payload["complete"] is True
    assert payload["classification_checksum"] == classification.checksum
    assert payload["source_label"] == "repository-static-authorities"
    assert payload["tenant_workspace_classification"] == "all-workspaces-static"
    assert payload["summary"] == {
        "authority_counts": {
            "configuration_selector": 107,
            "database_operation": 16,
            "database_reader": 13,
            "database_writer": 12,
            "external_store_handle": 38,
            "filesystem_artifact_reference": 228,
            "filesystem_reader": 288,
            "filesystem_writer": 171,
            "resolver_candidate": 11,
            "semantic_fallback": 5,
            "semantic_writer": 8,
            "state_store": 2,
        },
        "byte_count": 578450,
        "duplicate_id_count": 0,
        "parse_failure_count": 0,
        "provenance_counts": {"human_authored": 899},
        "record_count": 899,
        "scanned_byte_count": 3076362,
        "scanned_object_count": 342,
        "unclassified_count": 0,
    }
    assert payload["warnings"] == []

    required_record_fields = {
        "inventory_id",
        "source_label",
        "tenant_workspace_classification",
        "locator",
        "locator_kind",
        "authority_classification",
        "record_kind",
        "record_count",
        "byte_count",
        "min_event_time",
        "max_event_time",
        "stable_hash",
        "parse_failure_count",
        "duplicate_id_count",
        "recoverability_status",
        "provenance_classification",
        "reader_symbols",
        "writer_symbols",
        "proposed_future_importer",
        "planned_deletion_pr",
    }
    assert all(required_record_fields <= row.keys() for row in payload["records"])
    encoded = inventory_path.read_text(encoding="utf-8")
    assert "/Users/" not in encoded
    assert "/private/tmp/" not in encoded
    assert "postgresql://" not in encoded
    assert "password=" not in encoded.lower()


def test_cli_writes_report_only_to_requested_external_output(tmp_path: Path):
    repo = tmp_path / "repo"
    package = repo / "core_memory"
    package.mkdir(parents=True)
    _write(
        package / "store.py",
        "from pathlib import Path\n\ndef read(root):\n    return (Path(root) / '.beads' / 'index.json').read_text()\n",
    )
    output = tmp_path / "reports" / "code.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "scan-code",
            "--root",
            str(repo.resolve()),
            "--include",
            "core_memory",
            "--source-label",
            "test-repository",
            "--tenant-workspace",
            "all-workspaces-static",
            "--out",
            str(output.resolve()),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text())
    assert payload["schema_version"] == INVENTORY_SCHEMA_VERSION
    assert payload["read_only"] is True
    assert not (repo / "report.json").exists()


def test_inventory_script_imports_without_side_effects():
    spec = importlib.util.spec_from_file_location("inventory_legacy_memory", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
