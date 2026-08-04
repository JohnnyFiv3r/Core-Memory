"""Read-only SQLite and PostgreSQL authority inventory."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .classification import Classification, ClassificationMap, load_classification_map
from .inventory import (
    InventoryRecord,
    InventoryReport,
    _inventory_id,
    _normalized_time,
    _root_fingerprint,
    _safe_label,
    _sha256_bytes,
    _sha256_file,
)

_ID_COLUMNS = (
    "id",
    "bead_id",
    "event_id",
    "claim_id",
    "assertion_id",
    "association_id",
    "artifact_id",
    "job_id",
    "receipt_id",
)
_TIME_COLUMNS = (
    "event_time",
    "observed_at",
    "recorded_at",
    "created_at",
    "knowledge_time",
    "updated_at",
    "timestamp",
)
_RECEIPT_COLUMNS = ("receipt_id", "semantic_receipt", "model_receipt")
_POSTGRES_DSN_RE = re.compile(r"^(postgres|postgresql)://", re.IGNORECASE)


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _qualified(schema: str, table: str) -> str:
    return f"{_quote_identifier(schema)}.{_quote_identifier(table)}" if schema else _quote_identifier(table)


def _first_column(columns: Iterable[str], candidates: Sequence[str]) -> str:
    available = {str(column).lower(): str(column) for column in columns}
    return next((available[item] for item in candidates if item in available), "")


def _stable_table_hash(
    *,
    engine: str,
    schema: str,
    table: str,
    table_type: str,
    columns: Sequence[str],
    record_count: int,
    byte_count: int,
    min_event_time: str | None,
    max_event_time: str | None,
    duplicate_id_count: int,
    receipt_count: int,
) -> str:
    payload = {
        "byte_count": byte_count,
        "columns": sorted(columns),
        "duplicate_id_count": duplicate_id_count,
        "engine": engine,
        "max_event_time": max_event_time,
        "min_event_time": min_event_time,
        "record_count": record_count,
        "receipt_count": receipt_count,
        "schema": schema,
        "table": table,
        "table_type": table_type,
    }
    return _sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _resolved_provenance(classification: Classification, *, record_count: int, receipt_count: int) -> str:
    provenance = classification.provenance_classification
    if provenance not in {"llm_authored_without_receipt", "unknown"}:
        return provenance
    if record_count and receipt_count == record_count:
        return "llm_authored_with_receipt"
    if 0 < receipt_count < record_count:
        return "unknown"
    return provenance


def _sql_record(
    *,
    engine: str,
    schema: str,
    table: str,
    table_type: str,
    columns: Sequence[str],
    record_count: int,
    byte_count: int,
    min_event_time: str | None,
    max_event_time: str | None,
    duplicate_id_count: int,
    receipt_count: int,
    source_label: str,
    tenant_workspace: str,
    target_fingerprint: str,
    classification_map: ClassificationMap,
) -> InventoryRecord:
    classification = classification_map.classify_sql(schema, table, columns)
    locator = f"{schema}.{table}" if schema else table
    stable_hash = _stable_table_hash(
        engine=engine,
        schema=schema,
        table=table,
        table_type=table_type,
        columns=columns,
        record_count=record_count,
        byte_count=byte_count,
        min_event_time=min_event_time,
        max_event_time=max_event_time,
        duplicate_id_count=duplicate_id_count,
        receipt_count=receipt_count,
    )
    return InventoryRecord(
        inventory_id=_inventory_id("sql", engine, target_fingerprint, locator, stable_hash),
        source_label=source_label,
        tenant_workspace_classification=tenant_workspace,
        locator=locator,
        locator_kind="sql_table" if table_type.lower() != "view" else "sql_view",
        authority_classification=classification.authority_classification,
        record_kind="database_table" if table_type.lower() != "view" else "database_view",
        record_count=record_count,
        byte_count=byte_count,
        min_event_time=min_event_time,
        max_event_time=max_event_time,
        stable_hash=stable_hash,
        parse_failure_count=0,
        duplicate_id_count=duplicate_id_count,
        recoverability_status="read_only_queryable",
        provenance_classification=_resolved_provenance(
            classification,
            record_count=record_count,
            receipt_count=receipt_count,
        ),
        reader_symbols=(),
        writer_symbols=(),
        proposed_future_importer=classification.proposed_future_importer,
        planned_deletion_pr=classification.planned_deletion_pr,
        classification_rule_id=classification.rule_id,
        details={
            "column_count": len(columns),
            "engine": engine,
            "receipt_count": receipt_count,
            "table_type": table_type,
            "target_fingerprint": target_fingerprint,
        },
    )


def validate_sqlite_path(path: Path) -> Path:
    requested = Path(path)
    if not requested.is_absolute():
        raise ValueError("sqlite_path_must_be_absolute")
    resolved = requested.resolve()
    if requested.is_symlink():
        raise ValueError("sqlite_symlink_refused")
    if not resolved.is_file():
        raise ValueError("sqlite_path_not_file")
    return resolved


def validate_sql_output_path(output: Path, *, sqlite_path: Path | None = None) -> Path:
    requested = Path(output)
    if not requested.is_absolute():
        raise ValueError("inventory_output_must_be_absolute")
    resolved = requested.resolve()
    if sqlite_path is not None and resolved == sqlite_path.resolve():
        raise ValueError("inventory_output_overwrites_sqlite")
    return resolved


def _sqlite_sidecar_state(path: Path) -> tuple[tuple[str, int, int], ...]:
    state: list[tuple[str, int, int]] = []
    for suffix in ("-wal", "-journal"):
        sidecar = path.with_name(f"{path.name}{suffix}")
        try:
            stat = sidecar.stat()
        except FileNotFoundError:
            continue
        state.append((suffix, stat.st_size, stat.st_mtime_ns))
    return tuple(state)


def _sqlite_table_size(connection: sqlite3.Connection, table: str) -> int | None:
    try:
        row = connection.execute("SELECT COALESCE(SUM(pgsize), 0) FROM dbstat WHERE name = ?", (table,)).fetchone()
    except sqlite3.DatabaseError:
        return None
    return int((row or (0,))[0] or 0)


def _sqlite_scalar(connection: sqlite3.Connection, query: str) -> Any:
    row = connection.execute(query).fetchone()
    return row[0] if row else None


def scan_sqlite(
    path: Path,
    *,
    source_label: str,
    tenant_workspace_classification: str,
    classification_path: Path | None = None,
    connect: Callable[..., sqlite3.Connection] = sqlite3.connect,
) -> InventoryReport:
    resolved_path = validate_sqlite_path(path)
    safe_source = _safe_label(source_label, field_name="source_label")
    safe_tenant = _safe_label(tenant_workspace_classification, field_name="tenant_workspace")
    classification_map = load_classification_map(classification_path)
    target_fingerprint = hashlib.sha256(str(resolved_path).encode("utf-8")).hexdigest()
    uri = f"file:{quote(resolved_path.as_posix(), safe='/')}?mode=ro&immutable=1"
    before = (
        resolved_path.stat().st_size,
        resolved_path.stat().st_mtime_ns,
        _sha256_file(resolved_path),
    )
    sidecars_before = _sqlite_sidecar_state(resolved_path)
    if any(size > 0 for _suffix, size, _mtime in sidecars_before):
        # immutable=1 is the strongest no-write SQLite mode, but it deliberately
        # ignores WAL and rollback-journal content. Refuse rather than report a
        # potentially stale authority snapshot as complete.
        raise ValueError("sqlite_uncheckpointed_sidecar_refused")
    try:
        connection = connect(uri, uri=True)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise ValueError(f"sqlite_connect_failed:{exc.__class__.__name__}") from None
    records: list[InventoryRecord] = []
    warnings: list[dict[str, str]] = []
    dbstat_missing = False
    try:
        try:
            connection.execute("PRAGMA query_only = ON")
            if int(_sqlite_scalar(connection, "PRAGMA query_only") or 0) != 1:
                raise ValueError("sqlite_read_only_not_enforced")
            connection.execute("BEGIN")
            tables = connection.execute(
                "SELECT name, type FROM sqlite_master "
                "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for table_name, table_type in tables:
                table = str(table_name)
                relation = _qualified("", table)
                columns = [str(row[1]) for row in connection.execute(f"PRAGMA table_info({relation})").fetchall()]
                record_count = int(_sqlite_scalar(connection, f"SELECT COUNT(*) FROM {relation}") or 0)
                id_column = _first_column(columns, _ID_COLUMNS)
                duplicate_id_count = 0
                if id_column:
                    identifier = _quote_identifier(id_column)
                    duplicate_id_count = int(
                        _sqlite_scalar(
                            connection,
                            "SELECT COALESCE(SUM(group_count - 1), 0) FROM "
                            f"(SELECT COUNT(*) AS group_count FROM {relation} "
                            f"WHERE {identifier} IS NOT NULL GROUP BY {identifier} HAVING COUNT(*) > 1)",
                        )
                        or 0
                    )
                time_column = _first_column(columns, _TIME_COLUMNS)
                min_event_time = max_event_time = None
                if time_column:
                    time_identifier = _quote_identifier(time_column)
                    row = connection.execute(
                        f"SELECT MIN({time_identifier}), MAX({time_identifier}) FROM {relation}"
                    ).fetchone()
                    min_event_time = _normalized_time((row or (None, None))[0])
                    max_event_time = _normalized_time((row or (None, None))[1])
                receipt_column = _first_column(columns, _RECEIPT_COLUMNS)
                receipt_count = 0
                if receipt_column:
                    receipt_identifier = _quote_identifier(receipt_column)
                    receipt_count = int(
                        _sqlite_scalar(
                            connection,
                            f"SELECT COUNT(*) FROM {relation} "
                            f"WHERE {receipt_identifier} IS NOT NULL AND CAST({receipt_identifier} AS TEXT) <> ''",
                        )
                        or 0
                    )
                table_size = _sqlite_table_size(connection, table)
                if table_size is None:
                    dbstat_missing = True
                    table_size = 0
                records.append(
                    _sql_record(
                        engine="sqlite",
                        schema="main",
                        table=table,
                        table_type=str(table_type),
                        columns=columns,
                        record_count=record_count,
                        byte_count=table_size,
                        min_event_time=min_event_time,
                        max_event_time=max_event_time,
                        duplicate_id_count=duplicate_id_count,
                        receipt_count=receipt_count,
                        source_label=safe_source,
                        tenant_workspace=safe_tenant,
                        target_fingerprint=target_fingerprint,
                        classification_map=classification_map,
                    )
                )
            connection.rollback()
        except ValueError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise ValueError(f"sqlite_scan_failed:{exc.__class__.__name__}") from None
    finally:
        connection.close()
    after = (
        resolved_path.stat().st_size,
        resolved_path.stat().st_mtime_ns,
        _sha256_file(resolved_path),
    )
    sidecars_after = _sqlite_sidecar_state(resolved_path)
    if before != after or sidecars_before != sidecars_after:
        raise ValueError("sqlite_source_changed_during_scan")
    if dbstat_missing:
        warnings.append({"code": "sqlite_dbstat_unavailable", "locator": "redacted-sqlite-target"})
    records.sort(key=lambda row: row.locator)
    return InventoryReport(
        scanner="sql",
        source_label=safe_source,
        tenant_workspace_classification=safe_tenant,
        root_fingerprint=_root_fingerprint(safe_source, f"{safe_tenant}:sqlite:{target_fingerprint}"),
        classification_schema_version=classification_map.schema_version,
        classification_checksum=classification_map.checksum,
        records=tuple(records),
        warnings=tuple(warnings),
        complete=True,
        scan_metrics={"scanned_object_count": len(records), "scanned_byte_count": resolved_path.stat().st_size},
    )


def _rows(connection: Any, query: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
    cursor = connection.execute(query, params)
    return [tuple(row) for row in cursor.fetchall()]


def _scalar(connection: Any, query: str, params: Sequence[Any] = ()) -> Any:
    rows = _rows(connection, query, params)
    return rows[0][0] if rows else None


def _default_postgres_connect(dsn: str) -> Any:
    try:
        psycopg = importlib.import_module("psycopg")
    except ImportError as exc:
        raise ValueError("postgresql_driver_unavailable") from exc
    return psycopg.connect(dsn, autocommit=False)


def scan_postgresql(
    dsn: str,
    *,
    source_label: str,
    tenant_workspace_classification: str,
    schemas: Sequence[str] = (),
    classification_path: Path | None = None,
    connect: Callable[[str], Any] = _default_postgres_connect,
) -> InventoryReport:
    raw_dsn = str(dsn or "").strip()
    if not raw_dsn:
        raise ValueError("postgresql_dsn_missing")
    if not _POSTGRES_DSN_RE.match(raw_dsn):
        raise ValueError("postgresql_dsn_scheme_invalid")
    safe_source = _safe_label(source_label, field_name="source_label")
    safe_tenant = _safe_label(tenant_workspace_classification, field_name="tenant_workspace")
    classification_map = load_classification_map(classification_path)
    target_fingerprint = hashlib.sha256(raw_dsn.encode("utf-8")).hexdigest()
    requested_schemas = tuple(sorted({str(item).strip() for item in schemas if str(item).strip()}))
    try:
        connection = connect(raw_dsn)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"postgresql_connect_failed:{exc.__class__.__name__}") from None
    records: list[InventoryRecord] = []
    try:
        try:
            connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            read_only = str(_scalar(connection, "SHOW transaction_read_only") or "").strip().lower()
            if read_only not in {"on", "true", "1"}:
                raise ValueError("postgresql_read_only_not_enforced")
            isolation = str(_scalar(connection, "SHOW transaction_isolation") or "").strip().lower()
            if isolation != "repeatable read":
                raise ValueError("postgresql_repeatable_read_not_enforced")
            query = (
                "SELECT table_schema, table_name, table_type FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
            )
            params: list[Any] = []
            if requested_schemas:
                placeholders = ", ".join(["%s"] * len(requested_schemas))
                query += f" AND table_schema IN ({placeholders})"
                params.extend(requested_schemas)
            query += " ORDER BY table_schema, table_name"
            for schema_name, table_name, table_type in _rows(connection, query, params):
                schema = str(schema_name)
                table = str(table_name)
                relation = _qualified(schema, table)
                columns = [
                    str(row[0])
                    for row in _rows(
                        connection,
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                        (schema, table),
                    )
                ]
                record_count = int(_scalar(connection, f"SELECT COUNT(*) FROM {relation}") or 0)
                byte_count = (
                    0
                    if str(table_type).lower() == "view"
                    else int(_scalar(connection, "SELECT pg_total_relation_size(%s::regclass)", (relation,)) or 0)
                )
                id_column = _first_column(columns, _ID_COLUMNS)
                duplicate_id_count = 0
                if id_column:
                    identifier = _quote_identifier(id_column)
                    duplicate_id_count = int(
                        _scalar(
                            connection,
                            "SELECT COALESCE(SUM(group_count - 1), 0) FROM "
                            f"(SELECT COUNT(*) AS group_count FROM {relation} "
                            f"WHERE {identifier} IS NOT NULL GROUP BY {identifier} "
                            "HAVING COUNT(*) > 1) AS duplicates",
                        )
                        or 0
                    )
                time_column = _first_column(columns, _TIME_COLUMNS)
                min_event_time = max_event_time = None
                if time_column:
                    time_identifier = _quote_identifier(time_column)
                    time_rows = _rows(
                        connection,
                        f"SELECT MIN({time_identifier}), MAX({time_identifier}) FROM {relation}",
                    )
                    time_row = time_rows[0] if time_rows else (None, None)
                    min_event_time = _normalized_time(time_row[0])
                    max_event_time = _normalized_time(time_row[1])
                receipt_column = _first_column(columns, _RECEIPT_COLUMNS)
                receipt_count = 0
                if receipt_column:
                    receipt_identifier = _quote_identifier(receipt_column)
                    receipt_count = int(
                        _scalar(
                            connection,
                            f"SELECT COUNT(*) FROM {relation} "
                            f"WHERE {receipt_identifier} IS NOT NULL "
                            f"AND CAST({receipt_identifier} AS TEXT) <> ''",
                        )
                        or 0
                    )
                records.append(
                    _sql_record(
                        engine="postgresql",
                        schema=schema,
                        table=table,
                        table_type=str(table_type),
                        columns=columns,
                        record_count=record_count,
                        byte_count=byte_count,
                        min_event_time=min_event_time,
                        max_event_time=max_event_time,
                        duplicate_id_count=duplicate_id_count,
                        receipt_count=receipt_count,
                        source_label=safe_source,
                        tenant_workspace=safe_tenant,
                        target_fingerprint=target_fingerprint,
                        classification_map=classification_map,
                    )
                )
            connection.rollback()
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"postgresql_scan_failed:{exc.__class__.__name__}") from None
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()
    records.sort(key=lambda row: row.locator)
    return InventoryReport(
        scanner="sql",
        source_label=safe_source,
        tenant_workspace_classification=safe_tenant,
        root_fingerprint=_root_fingerprint(safe_source, f"{safe_tenant}:postgresql:{target_fingerprint}"),
        classification_schema_version=classification_map.schema_version,
        classification_checksum=classification_map.checksum,
        records=tuple(records),
        warnings=(),
        complete=True,
        scan_metrics={
            "scanned_object_count": len(records),
            "scanned_byte_count": sum(row.byte_count for row in records),
        },
    )
