from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from core_memory.migration.consolidation import (
    MANIFEST_SCHEMA_VERSION,
    REGISTRY_SCHEMA_VERSION,
    load_authority_registry,
    load_inventory_report,
    merge_inventory_reports,
    verify_consolidated_manifest,
)
from core_memory.migration.sql_inventory import scan_postgresql, scan_sqlite, validate_sql_output_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "inventory_legacy_memory.py"


def _create_sqlite(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE source_events (
            event_id TEXT PRIMARY KEY,
            event_time TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        INSERT INTO source_events VALUES
            ('evt-1', '2026-08-01T10:00:00Z', '{}'),
            ('evt-2', '2026-08-02T10:00:00Z', '{}');

        CREATE TABLE claims (
            id TEXT NOT NULL,
            event_time TEXT NOT NULL,
            receipt_id TEXT
        );
        INSERT INTO claims VALUES
            ('claim-1', '2026-08-02T11:00:00Z', 'receipt-1'),
            ('claim-1', '2026-08-03T11:00:00Z', NULL),
            ('claim-2', '2026-08-04T11:00:00Z', 'receipt-2');

        CREATE TABLE side_effect_queue (
            job_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );
        INSERT INTO side_effect_queue VALUES ('job-1', '2026-08-04T12:00:00Z');
        """
    )
    connection.commit()
    connection.close()


def _write_report(path: Path, report: Any) -> None:
    path.write_text(json.dumps(report.to_dict(), sort_keys=True) + "\n", encoding="utf-8")


def _registry(authorities: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "scope_id": "test-scope",
        "authorities": authorities,
    }


def _active_sql_authority() -> dict[str, Any]:
    return {
        "authority_id": "test-sqlite",
        "kind": "sql",
        "deployment_status": "active",
        "source_label": "test-sqlite-authority",
        "tenant_workspace_classification": "test-tenant",
        "required_scanner": "sql",
        "visibility": "public",
        "authority_classification": "legacy_sql_authority",
        "provenance_classification": "unknown",
        "proposed_future_importer": "PR-10A through PR-10C SQL transforms",
        "planned_deletion_pr": "PR-10H",
        "evidence": {"fixture": "sqlite"},
    }


def _inactive_hosted_authority() -> dict[str, Any]:
    return {
        "authority_id": "test-hosted-postgres",
        "kind": "hosted",
        "deployment_status": "not_configured",
        "source_label": "hosted-postgres-capability",
        "tenant_workspace_classification": "all-workspaces-static",
        "required_scanner": "declaration",
        "visibility": "public",
        "authority_classification": "supported_hosted_sql_surface",
        "provenance_classification": "unknown",
        "proposed_future_importer": "PR-10A through PR-10C SQL transforms",
        "planned_deletion_pr": "PR-10H",
        "evidence": {"configuration_selectors": ["CORE_MEMORY_POSTGRES_DSN"]},
    }


def test_sqlite_scan_is_read_only_and_classifies_counts_times_receipts_and_duplicates(tmp_path: Path):
    database = tmp_path / "legacy.sqlite"
    _create_sqlite(database)
    before = (database.stat().st_size, database.stat().st_mtime_ns, hashlib.sha256(database.read_bytes()).hexdigest())

    report = scan_sqlite(
        database.resolve(),
        source_label="test-sqlite-authority",
        tenant_workspace_classification="test-tenant",
    )

    after = (database.stat().st_size, database.stat().st_mtime_ns, hashlib.sha256(database.read_bytes()).hexdigest())
    assert before == after
    assert report.complete
    assert report.scanner == "sql"
    assert report.to_dict()["summary"]["unclassified_count"] == 0
    records = {record.locator: record for record in report.records}
    assert set(records) == {"main.claims", "main.side_effect_queue", "main.source_events"}
    assert records["main.source_events"].authority_classification == "observed_event_store"
    assert records["main.source_events"].provenance_classification == "observed_source"
    claims = records["main.claims"]
    assert claims.record_count == 3
    assert claims.duplicate_id_count == 1
    assert claims.min_event_time == "2026-08-02T11:00:00Z"
    assert claims.max_event_time == "2026-08-04T11:00:00Z"
    assert claims.provenance_classification == "unknown"
    assert claims.details["receipt_count"] == 2
    assert records["main.side_effect_queue"].authority_classification == "legacy_queue"
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert str(database) not in encoded
    assert "file:" not in encoded


def test_sqlite_scan_rejects_relative_missing_symlink_and_output_overwrite(tmp_path: Path):
    database = tmp_path / "legacy.sqlite"
    _create_sqlite(database)
    symlink = tmp_path / "linked.sqlite"
    symlink.symlink_to(database)

    with pytest.raises(ValueError, match="sqlite_path_must_be_absolute"):
        scan_sqlite(
            Path("legacy.sqlite"),
            source_label="test",
            tenant_workspace_classification="test",
        )
    with pytest.raises(ValueError, match="sqlite_path_not_file"):
        scan_sqlite(
            (tmp_path / "missing.sqlite").resolve(),
            source_label="test",
            tenant_workspace_classification="test",
        )
    with pytest.raises(ValueError, match="sqlite_symlink_refused"):
        scan_sqlite(
            symlink.absolute(),
            source_label="test",
            tenant_workspace_classification="test",
        )
    with pytest.raises(ValueError, match="inventory_output_overwrites_sqlite"):
        validate_sql_output_path(database.resolve(), sqlite_path=database.resolve())


def test_sqlite_scan_refuses_uncheckpointed_wal_instead_of_returning_stale_counts(tmp_path: Path):
    database = (tmp_path / "active.sqlite").resolve()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("CREATE TABLE source_events (event_id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO source_events VALUES ('evt-in-wal')")
    connection.commit()
    try:
        assert database.with_name(f"{database.name}-wal").stat().st_size > 0
        with pytest.raises(ValueError, match="sqlite_uncheckpointed_sidecar_refused"):
            scan_sqlite(
                database,
                source_label="active-sqlite",
                tenant_workspace_classification="test-tenant",
            )
    finally:
        connection.close()


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]):
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakePostgres:
    def __init__(self, *, read_only: str = "on"):
        self.read_only = read_only
        self.queries: list[tuple[str, tuple[Any, ...]]] = []
        self.rollback_count = 0
        self.closed = False

    def execute(self, query: str, params: Any = ()) -> _FakeCursor:
        normalized = " ".join(query.split())
        params_tuple = tuple(params)
        self.queries.append((normalized, params_tuple))
        if normalized == "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY":
            return _FakeCursor([])
        if normalized == "SHOW transaction_read_only":
            return _FakeCursor([(self.read_only,)])
        if normalized == "SHOW transaction_isolation":
            return _FakeCursor([("repeatable read",)])
        if "FROM information_schema.tables" in normalized:
            return _FakeCursor([("public", "memory_events", "BASE TABLE"), ("public", "claims", "BASE TABLE")])
        if "FROM information_schema.columns" in normalized:
            if params_tuple[-1] == "memory_events":
                return _FakeCursor([("event_id",), ("event_time",), ("payload",)])
            return _FakeCursor([("id",), ("event_time",), ("receipt_id",)])
        if "pg_total_relation_size" in normalized:
            return _FakeCursor([(2048,)])
        if "SUM(group_count - 1)" in normalized:
            return _FakeCursor([(1 if '"claims"' in normalized else 0,)])
        if "MIN(" in normalized and "MAX(" in normalized:
            return _FakeCursor([("2026-08-01T00:00:00Z", "2026-08-04T00:00:00Z")])
        if "receipt_id" in normalized and "CAST(" in normalized:
            return _FakeCursor([(2,)])
        if normalized.startswith("SELECT COUNT(*)"):
            return _FakeCursor([(3 if '"claims"' in normalized else 2,)])
        raise AssertionError(f"unexpected query: {normalized}")

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.closed = True


def test_postgresql_scan_enforces_read_only_before_catalog_or_data_queries_and_redacts_dsn():
    connection = _FakePostgres()
    dsn = "postgresql://inventory:super-secret@db.example/core_memory"

    report = scan_postgresql(
        dsn,
        source_label="hosted-postgres",
        tenant_workspace_classification="tenant-a",
        schemas=("public",),
        connect=lambda supplied: connection if supplied == dsn else None,
    )

    assert connection.queries[0][0] == "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert connection.queries[1][0] == "SHOW transaction_read_only"
    assert connection.queries[2][0] == "SHOW transaction_isolation"
    assert all(query.startswith(("BEGIN TRANSACTION", "SHOW ", "SELECT ")) for query, _params in connection.queries)
    assert connection.rollback_count >= 1
    assert connection.closed
    assert {record.locator for record in report.records} == {"public.claims", "public.memory_events"}
    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert dsn not in encoded
    assert "super-secret" not in encoded
    assert "db.example" not in encoded


def test_postgresql_scan_fails_closed_when_transaction_is_not_read_only():
    connection = _FakePostgres(read_only="off")

    with pytest.raises(ValueError, match="postgresql_read_only_not_enforced"):
        scan_postgresql(
            "postgresql://inventory:secret@db.example/core_memory",
            source_label="hosted-postgres",
            tenant_workspace_classification="tenant-a",
            connect=lambda _dsn: connection,
        )

    assert [query for query, _params in connection.queries] == [
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        "SHOW transaction_read_only",
    ]
    assert connection.rollback_count >= 1
    assert connection.closed


def test_sql_connection_failures_do_not_leak_targets_or_credentials(tmp_path: Path):
    database = tmp_path / "private-name.sqlite"
    _create_sqlite(database)

    def fail_sqlite(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        raise sqlite3.OperationalError(str(database))

    with pytest.raises(ValueError) as sqlite_error:
        scan_sqlite(
            database.resolve(),
            source_label="local-sqlite",
            tenant_workspace_classification="tenant-a",
            connect=fail_sqlite,
        )
    assert str(sqlite_error.value) == "sqlite_connect_failed:OperationalError"
    assert str(database) not in str(sqlite_error.value)

    dsn = "postgresql://inventory:super-secret@db.example/core_memory"

    def fail_postgres(_dsn: str) -> Any:
        raise RuntimeError(dsn)

    with pytest.raises(ValueError) as postgres_error:
        scan_postgresql(
            dsn,
            source_label="hosted-postgres",
            tenant_workspace_classification="tenant-a",
            connect=fail_postgres,
        )
    assert str(postgres_error.value) == "postgresql_connect_failed:RuntimeError"
    assert "super-secret" not in str(postgres_error.value)
    assert "db.example" not in str(postgres_error.value)


def test_merge_and_verify_require_exactly_one_complete_declared_report(tmp_path: Path):
    database = tmp_path / "legacy.sqlite"
    _create_sqlite(database)
    report = scan_sqlite(
        database.resolve(),
        source_label="test-sqlite-authority",
        tenant_workspace_classification="test-tenant",
    )
    report_path = (tmp_path / "sql-report.json").resolve()
    _write_report(report_path, report)
    loaded = load_inventory_report(report_path)
    registry = _registry([_active_sql_authority(), _inactive_hosted_authority()])

    manifest = merge_inventory_reports(registry, [loaded])

    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["complete"] is True
    assert manifest["summary"] == {
        "accessible_authority_count": 1,
        "active_authority_count": 1,
        "authority_count": 2,
        "inaccessible_authority_count": 0,
        "not_configured_authority_count": 1,
        "retired_authority_count": 0,
        "source_manifest_count": 1,
        "unclassified_authority_count": 0,
        "undeclared_report_count": 0,
    }
    assert verify_consolidated_manifest(manifest) == []
    records = {row["locator"]: row for row in manifest["records"]}
    assert records["authority:test-sqlite"]["recoverability_status"] == "accessible_read_only"
    assert records["authority:test-hosted-postgres"]["recoverability_status"] == "not_configured"
    encoded = json.dumps(manifest, sort_keys=True)
    assert str(report_path) not in encoded
    assert str(database) not in encoded

    missing = merge_inventory_reports(registry, [])
    assert missing["complete"] is False
    assert missing["summary"]["inaccessible_authority_count"] == 1
    assert "manifest_incomplete" in verify_consolidated_manifest(missing)

    tampered_summary = json.loads(json.dumps(manifest))
    tampered_summary["summary"]["accessible_authority_count"] = 0
    without_fingerprint = dict(tampered_summary)
    without_fingerprint.pop("content_fingerprint")
    tampered_summary["content_fingerprint"] = hashlib.sha256(
        json.dumps(without_fingerprint, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert "manifest_summary_invalid" in verify_consolidated_manifest(tampered_summary)


def test_registry_rejects_nonempty_credential_fields_even_when_json_quoted(tmp_path: Path):
    registry = _registry([_inactive_hosted_authority()])
    registry["authorities"][0]["evidence"]["api_key"] = "must-not-commit"
    registry_path = (tmp_path / "unsafe-registry.json").resolve()
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="consolidated_manifest_contains_credential"):
        load_authority_registry(registry_path)


def test_merge_rejects_undeclared_reports_and_registry_rejects_fake_active_declarations(tmp_path: Path):
    database = tmp_path / "legacy.sqlite"
    _create_sqlite(database)
    report = scan_sqlite(
        database.resolve(),
        source_label="unexpected-sqlite",
        tenant_workspace_classification="test-tenant",
    )
    report_path = (tmp_path / "unexpected.json").resolve()
    _write_report(report_path, report)
    manifest = merge_inventory_reports(_registry([_active_sql_authority()]), [load_inventory_report(report_path)])

    assert manifest["complete"] is False
    assert manifest["summary"]["inaccessible_authority_count"] == 1
    assert manifest["summary"]["undeclared_report_count"] == 1

    invalid = _registry([_active_sql_authority()])
    invalid["authorities"][0]["required_scanner"] = "declaration"
    registry_path = (tmp_path / "registry.json").resolve()
    registry_path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError, match="active_authority_requires_read_only_report"):
        load_authority_registry(registry_path)

    tampered = json.loads(report_path.read_text(encoding="utf-8"))
    tampered["summary"]["record_count"] += 1
    report_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="inventory_report_record_count_invalid"):
        load_inventory_report(report_path)


def test_merge_rejects_reports_built_with_different_classification_maps(tmp_path: Path):
    database = tmp_path / "legacy.sqlite"
    _create_sqlite(database)
    first = scan_sqlite(
        database.resolve(),
        source_label="test-sqlite-authority",
        tenant_workspace_classification="test-tenant",
    ).to_dict()
    second = json.loads(json.dumps(first))
    second["source_label"] = "second-sqlite-authority"
    second["tenant_workspace_classification"] = "second-tenant"
    second["classification_checksum"] = "0" * 64
    for row in second["records"]:
        row["source_label"] = second["source_label"]
        row["tenant_workspace_classification"] = second["tenant_workspace_classification"]
    first_path = (tmp_path / "first.json").resolve()
    second_path = (tmp_path / "second.json").resolve()
    first_path.write_text(json.dumps(first), encoding="utf-8")
    second_path.write_text(json.dumps(second), encoding="utf-8")
    second_authority = _active_sql_authority()
    second_authority["authority_id"] = "second-sqlite"
    second_authority["source_label"] = "second-sqlite-authority"
    second_authority["tenant_workspace_classification"] = "second-tenant"

    manifest = merge_inventory_reports(
        _registry([_active_sql_authority(), second_authority]),
        [load_inventory_report(first_path), load_inventory_report(second_path)],
    )

    assert manifest["complete"] is False
    assert {row["code"] for row in manifest["errors"]} == {"inventory_report_classification_mismatch"}


def test_cli_scan_sql_merge_and_verify_round_trip(tmp_path: Path):
    database = (tmp_path / "legacy.sqlite").resolve()
    _create_sqlite(database)
    report_path = (tmp_path / "sql-report.json").resolve()
    registry_path = (tmp_path / "registry.json").resolve()
    manifest_path = (tmp_path / "manifest.json").resolve()
    registry_path.write_text(json.dumps(_registry([_active_sql_authority()])), encoding="utf-8")

    scan = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "scan-sql",
            "--engine",
            "sqlite",
            "--sqlite-path",
            str(database),
            "--source-label",
            "test-sqlite-authority",
            "--tenant-workspace",
            "test-tenant",
            "--out",
            str(report_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert scan.returncode == 0, scan.stderr
    merge = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "merge",
            "--registry",
            str(registry_path),
            "--report",
            str(report_path),
            "--out",
            str(manifest_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert merge.returncode == 0, merge.stderr
    verify = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", "--manifest", str(manifest_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["errors"] == []


def test_committed_registry_and_consolidated_manifest_are_complete_and_private_safe():
    registry_path = (ROOT / "docs" / "migration" / "known-authorities.v1.json").resolve()
    manifest_path = (ROOT / "docs" / "migration" / "legacy-consolidated-inventory.v1.json").resolve()
    registry = load_authority_registry(registry_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry_schema = json.loads(
        (ROOT / "core_memory" / "data" / "legacy_authority_registry.v1.schema.json").read_text(encoding="utf-8")
    )
    manifest_schema = json.loads(
        (ROOT / "core_memory" / "data" / "legacy_inventory_manifest.v1.schema.json").read_text(encoding="utf-8")
    )

    assert registry_schema["$id"] == REGISTRY_SCHEMA_VERSION
    assert manifest_schema["$id"] == MANIFEST_SCHEMA_VERSION
    assert len(registry["authorities"]) == 15
    assert sum(row["deployment_status"] == "active" for row in registry["authorities"]) == 7
    assert verify_consolidated_manifest(manifest) == []
    assert manifest["summary"] == {
        "accessible_authority_count": 7,
        "active_authority_count": 7,
        "authority_count": 15,
        "inaccessible_authority_count": 0,
        "not_configured_authority_count": 8,
        "retired_authority_count": 0,
        "source_manifest_count": 7,
        "unclassified_authority_count": 0,
        "undeclared_report_count": 0,
    }
    assert all(
        set(row) >= {"source_label", "summary", "report_checksum", "visibility"} for row in manifest["source_manifests"]
    )
    assert sum(row["visibility"] == "private" for row in manifest["source_manifests"]) == 6
    encoded = manifest_path.read_text(encoding="utf-8")
    assert "/Users/" not in encoded
    assert "/private/tmp/" not in encoded
    assert "postgresql://" not in encoded
    assert "super-secret" not in encoded
    assert not re.search(r"cm_[0-9a-f]{32}", encoded)
