"""Consolidate read-only inventory reports against an explicit authority registry."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .inventory import INVENTORY_SCHEMA_VERSION, _inventory_id, _normalized_time, _sha256_bytes

REGISTRY_SCHEMA_VERSION = "core_memory.legacy_authority_registry.v1"
MANIFEST_SCHEMA_VERSION = "core_memory.legacy_inventory_manifest.v1"

_SCANNERS = {"static_code", "filesystem", "sql", "declaration"}
_KINDS = {"repository", "filesystem", "sql", "hosted"}
_STATUSES = {"active", "not_configured", "retired"}
_VISIBILITIES = {"public", "private"}
_PROVENANCE = {
    "observed_source",
    "llm_authored_with_receipt",
    "llm_authored_without_receipt",
    "deterministic_derived",
    "human_authored",
    "projection_only",
    "unknown",
}
_DELETION_PR_RE = re.compile(r"^PR-[0-9]{2}[A-Z]$")
_SENSITIVE_KEY_RE = re.compile(r"(?i)(?:^|[_-])(password|passwd|secret|token|api[_-]?key|dsn)(?:$|[_-])")


@dataclass(frozen=True)
class LoadedReport:
    payload: dict[str, Any]
    checksum: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (
            str(self.payload["source_label"]),
            str(self.payload["tenant_workspace_classification"]),
            str(self.payload["scanner"]),
        )


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_committed_payload(payload: Any) -> None:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    forbidden = (
        "/Users/",
        "/home/",
        "/private/tmp/",
        "postgresql://",
        "postgres://",
        "bolt://",
        "neo4j://",
    )
    if any(term.lower() in encoded.lower() for term in forbidden):
        raise ValueError("consolidated_manifest_contains_private_locator")

    def reject_sensitive_values(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if _SENSITIVE_KEY_RE.search(str(key)) and item not in (None, "", False, 0, [], {}):
                    raise ValueError("consolidated_manifest_contains_credential")
                reject_sensitive_values(item)
        elif isinstance(value, list):
            for item in value:
                reject_sensitive_values(item)

    reject_sensitive_values(payload)


def _validate_authority(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("authority_registry_row_not_object")
    required = {
        "authority_id",
        "kind",
        "deployment_status",
        "source_label",
        "tenant_workspace_classification",
        "required_scanner",
        "visibility",
        "authority_classification",
        "provenance_classification",
        "proposed_future_importer",
        "planned_deletion_pr",
        "evidence",
    }
    missing = sorted(key for key in required if key not in row)
    if missing:
        raise ValueError(f"authority_registry_fields_missing:{','.join(missing)}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,79}", str(row["authority_id"])):
        raise ValueError("authority_registry_id_invalid")
    if str(row["deployment_status"]) not in _STATUSES:
        raise ValueError("authority_registry_status_invalid")
    if str(row["kind"]) not in _KINDS:
        raise ValueError("authority_registry_kind_invalid")
    if str(row["required_scanner"]) not in _SCANNERS:
        raise ValueError("authority_registry_scanner_invalid")
    if str(row["visibility"]) not in _VISIBILITIES:
        raise ValueError("authority_registry_visibility_invalid")
    if str(row["provenance_classification"]) not in _PROVENANCE:
        raise ValueError("authority_registry_provenance_invalid")
    if not str(row["authority_classification"] or "").strip() or str(row["authority_classification"]) == "unknown":
        raise ValueError("authority_registry_classification_invalid")
    if not str(row["source_label"] or "").strip() or not str(row["tenant_workspace_classification"] or "").strip():
        raise ValueError("authority_registry_source_identity_invalid")
    if not str(row["proposed_future_importer"] or "").strip():
        raise ValueError("authority_registry_importer_invalid")
    if not _DELETION_PR_RE.fullmatch(str(row["planned_deletion_pr"])):
        raise ValueError("authority_registry_deletion_pr_invalid")
    if not isinstance(row["evidence"], dict):
        raise ValueError("authority_registry_evidence_invalid")
    status = str(row["deployment_status"])
    scanner = str(row["required_scanner"])
    if status == "active" and scanner == "declaration":
        raise ValueError("active_authority_requires_read_only_report")
    if status != "active" and scanner != "declaration":
        raise ValueError("inactive_authority_must_be_declaration")
    return row


def validate_authority_registry(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ValueError("authority_registry_schema_invalid")
    if not str(payload.get("scope_id") or "").strip():
        raise ValueError("authority_registry_scope_missing")
    rows = payload.get("authorities")
    if not isinstance(rows, list) or not rows:
        raise ValueError("authority_registry_rows_invalid")
    authorities = [_validate_authority(row) for row in rows]
    ids = [str(row["authority_id"]) for row in authorities]
    if len(ids) != len(set(ids)):
        raise ValueError("authority_registry_id_duplicate")
    active_keys = [
        (str(row["source_label"]), str(row["tenant_workspace_classification"]), str(row["required_scanner"]))
        for row in authorities
        if row["deployment_status"] == "active"
    ]
    if len(active_keys) != len(set(active_keys)):
        raise ValueError("authority_registry_active_source_duplicate")
    _safe_committed_payload(payload)
    return payload


def load_authority_registry(path: Path) -> dict[str, Any]:
    requested = Path(path)
    if not requested.is_absolute():
        raise ValueError("authority_registry_path_must_be_absolute")
    return validate_authority_registry(json.loads(requested.read_text(encoding="utf-8")))


def load_inventory_report(path: Path) -> LoadedReport:
    requested = Path(path)
    if not requested.is_absolute():
        raise ValueError("inventory_report_path_must_be_absolute")
    raw = requested.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise ValueError("inventory_report_schema_invalid")
    if payload.get("read_only") is not True:
        raise ValueError("inventory_report_not_read_only")
    if str(payload.get("scanner")) not in _SCANNERS - {"declaration"}:
        raise ValueError("inventory_report_scanner_invalid")
    if not isinstance(payload.get("records"), list) or not isinstance(payload.get("summary"), dict):
        raise ValueError("inventory_report_shape_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("classification_checksum") or "")):
        raise ValueError("inventory_report_classification_checksum_invalid")
    if not str(payload.get("classification_schema_version") or "").strip():
        raise ValueError("inventory_report_classification_schema_invalid")
    records = payload["records"]
    summary = payload["summary"]
    if int(summary.get("record_count") or 0) != len(records):
        raise ValueError("inventory_report_record_count_invalid")
    actual_unclassified = sum(not str(row.get("authority_classification") or "").strip() for row in records)
    if int(summary.get("unclassified_count") or 0) != actual_unclassified:
        raise ValueError("inventory_report_unclassified_count_invalid")
    source_label = str(payload.get("source_label") or "")
    tenant = str(payload.get("tenant_workspace_classification") or "")
    if not source_label or not tenant:
        raise ValueError("inventory_report_source_identity_invalid")
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("inventory_report_record_invalid")
        if str(row.get("source_label")) != source_label or str(row.get("tenant_workspace_classification")) != tenant:
            raise ValueError("inventory_report_record_source_mismatch")
    return LoadedReport(payload=payload, checksum=hashlib.sha256(raw).hexdigest())


def _report_stats(reports: Iterable[LoadedReport]) -> dict[str, Any]:
    rows = [record for report in reports for record in report.payload["records"]]
    event_times = sorted(
        normalized
        for row in rows
        for value in (row.get("min_event_time"), row.get("max_event_time"))
        if (normalized := _normalized_time(value))
    )
    return {
        "byte_count": sum(int(row.get("byte_count") or 0) for row in rows),
        "duplicate_id_count": sum(int(row.get("duplicate_id_count") or 0) for row in rows),
        "max_event_time": event_times[-1] if event_times else None,
        "min_event_time": event_times[0] if event_times else None,
        "parse_failure_count": sum(int(row.get("parse_failure_count") or 0) for row in rows),
        "reader_symbols": sorted({str(item) for row in rows for item in row.get("reader_symbols") or []}),
        "record_count": sum(int(row.get("record_count") or 0) for row in rows),
        "writer_symbols": sorted({str(item) for row in rows for item in row.get("writer_symbols") or []}),
    }


def _authority_record(authority: dict[str, Any], reports: list[LoadedReport], *, access_status: str) -> dict[str, Any]:
    stats = _report_stats(reports)
    report_checksums = sorted(report.checksum for report in reports)
    stable_payload = {
        "authority_id": authority["authority_id"],
        "classification": authority["authority_classification"],
        "deployment_status": authority["deployment_status"],
        "report_checksums": report_checksums,
        "stats": stats,
    }
    stable_hash = _sha256_bytes(_canonical_bytes(stable_payload))
    return {
        "inventory_id": _inventory_id("consolidated", str(authority["authority_id"]), stable_hash),
        "source_label": authority["source_label"],
        "tenant_workspace_classification": authority["tenant_workspace_classification"],
        "locator": f"authority:{authority['authority_id']}",
        "locator_kind": f"{authority['kind']}_authority",
        "authority_classification": authority["authority_classification"],
        "record_kind": "consolidated_authority",
        "record_count": stats["record_count"],
        "byte_count": stats["byte_count"],
        "min_event_time": stats["min_event_time"],
        "max_event_time": stats["max_event_time"],
        "stable_hash": stable_hash,
        "parse_failure_count": stats["parse_failure_count"],
        "duplicate_id_count": stats["duplicate_id_count"],
        "recoverability_status": access_status,
        "provenance_classification": authority["provenance_classification"],
        "reader_symbols": stats["reader_symbols"],
        "writer_symbols": stats["writer_symbols"],
        "proposed_future_importer": authority["proposed_future_importer"],
        "planned_deletion_pr": authority["planned_deletion_pr"],
        "details": {
            "access_status": access_status,
            "deployment_status": authority["deployment_status"],
            "report_checksums": report_checksums,
            "required_scanner": authority["required_scanner"],
            "source_manifest_count": len(reports),
            "visibility": authority["visibility"],
        },
    }


def merge_inventory_reports(registry: dict[str, Any], reports: Iterable[LoadedReport]) -> dict[str, Any]:
    registry = validate_authority_registry(registry)
    report_rows = list(reports)
    report_by_key: dict[tuple[str, str, str], list[LoadedReport]] = {}
    for report in report_rows:
        report_by_key.setdefault(report.key, []).append(report)
    consumed: set[str] = set()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    classification_versions = {
        (
            str(report.payload["classification_schema_version"]),
            str(report.payload["classification_checksum"]),
        )
        for report in report_rows
    }
    if len(classification_versions) > 1:
        errors.append({"code": "inventory_report_classification_mismatch"})
    visibility_by_key: dict[tuple[str, str, str], str] = {}
    for authority in registry["authorities"]:
        status = str(authority["deployment_status"])
        key = (
            str(authority["source_label"]),
            str(authority["tenant_workspace_classification"]),
            str(authority["required_scanner"]),
        )
        matched = report_by_key.get(key, []) if status == "active" else []
        visibility_by_key[key] = str(authority["visibility"])
        if status == "active" and len(matched) != 1:
            errors.append(
                {
                    "code": "active_authority_report_count_invalid",
                    "authority_id": str(authority["authority_id"]),
                }
            )
            access_status = "inaccessible"
        elif status == "active" and not bool(matched[0].payload.get("complete")):
            errors.append(
                {"code": "active_authority_report_incomplete", "authority_id": str(authority["authority_id"])}
            )
            access_status = "inaccessible"
        elif status == "active" and int(matched[0].payload["summary"].get("unclassified_count") or 0):
            errors.append(
                {"code": "active_authority_report_unclassified", "authority_id": str(authority["authority_id"])}
            )
            access_status = "unclassified"
        elif status == "active":
            access_status = "accessible_read_only"
        elif status == "retired":
            access_status = "retired"
        else:
            access_status = "not_configured"
        for report in matched:
            consumed.add(report.checksum)
        records.append(_authority_record(authority, matched, access_status=access_status))

    undeclared = [report for report in report_rows if report.checksum not in consumed]
    for report in undeclared:
        errors.append({"code": "inventory_report_undeclared", "source_label": str(report.payload["source_label"])})

    source_manifests = []
    for report in sorted(report_rows, key=lambda item: item.key):
        source_manifests.append(
            {
                "classification_checksum": report.payload["classification_checksum"],
                "complete": bool(report.payload["complete"]),
                "report_checksum": report.checksum,
                "root_fingerprint": report.payload["root_fingerprint"],
                "scanner": report.payload["scanner"],
                "source_label": report.payload["source_label"],
                "summary": report.payload["summary"],
                "tenant_workspace_classification": report.payload["tenant_workspace_classification"],
                "visibility": visibility_by_key.get(report.key, "undeclared"),
            }
        )

    records.sort(key=lambda row: row["locator"])
    summary = {
        "accessible_authority_count": sum(row["details"]["access_status"] == "accessible_read_only" for row in records),
        "active_authority_count": sum(row["details"]["deployment_status"] == "active" for row in records),
        "authority_count": len(records),
        "inaccessible_authority_count": sum(row["details"]["access_status"] == "inaccessible" for row in records),
        "not_configured_authority_count": sum(row["details"]["access_status"] == "not_configured" for row in records),
        "retired_authority_count": sum(row["details"]["access_status"] == "retired" for row in records),
        "source_manifest_count": len(source_manifests),
        "unclassified_authority_count": sum(row["details"]["access_status"] == "unclassified" for row in records),
        "undeclared_report_count": len(undeclared),
    }
    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "scope_id": registry["scope_id"],
        "read_only": True,
        "complete": not errors,
        "registry_checksum": _sha256_bytes(_canonical_bytes(registry)),
        "summary": summary,
        "source_manifests": source_manifests,
        "records": records,
        "errors": errors,
    }
    payload["content_fingerprint"] = _sha256_bytes(_canonical_bytes(payload))
    _safe_committed_payload(payload)
    return payload


def verify_consolidated_manifest(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        return ["manifest_schema_invalid"]
    fingerprint = str(payload.get("content_fingerprint") or "")
    without_fingerprint = dict(payload)
    without_fingerprint.pop("content_fingerprint", None)
    errors: list[str] = []
    if fingerprint != _sha256_bytes(_canonical_bytes(without_fingerprint)):
        errors.append("manifest_fingerprint_invalid")
    if payload.get("read_only") is not True:
        errors.append("manifest_not_read_only")
    if payload.get("complete") is not True:
        errors.append("manifest_incomplete")
    summary_value = payload.get("summary")
    summary: dict[str, Any] = summary_value if isinstance(summary_value, dict) else {}
    records_value = payload.get("records")
    records: list[dict[str, Any]] = (
        [row for row in records_value if isinstance(row, dict)] if isinstance(records_value, list) else []
    )
    sources_value = payload.get("source_manifests")
    sources: list[dict[str, Any]] = (
        [row for row in sources_value if isinstance(row, dict)] if isinstance(sources_value, list) else []
    )
    manifest_errors = payload.get("errors")
    error_rows: list[dict[str, Any]] = (
        [row for row in manifest_errors if isinstance(row, dict)] if isinstance(manifest_errors, list) else []
    )
    if not isinstance(summary_value, dict) or not isinstance(records_value, list) or len(records) != len(records_value):
        errors.append("manifest_shape_invalid")
    if not isinstance(sources_value, list) or len(sources) != len(sources_value):
        errors.append("manifest_shape_invalid")
    if not isinstance(manifest_errors, list) or len(error_rows) != len(manifest_errors):
        errors.append("manifest_shape_invalid")
    details: list[dict[str, Any]] = []
    for row in records:
        raw_details = row.get("details")
        if isinstance(raw_details, dict):
            details.append(raw_details)
        else:
            details.append({})
            errors.append("manifest_shape_invalid")
    access_states = [str(item.get("access_status")) for item in details]
    deployment_states = [str(item.get("deployment_status")) for item in details]
    expected_summary = {
        "accessible_authority_count": access_states.count("accessible_read_only"),
        "active_authority_count": deployment_states.count("active"),
        "authority_count": len(records),
        "inaccessible_authority_count": access_states.count("inaccessible"),
        "not_configured_authority_count": access_states.count("not_configured"),
        "retired_authority_count": access_states.count("retired"),
        "source_manifest_count": len(sources),
        "unclassified_authority_count": access_states.count("unclassified"),
        "undeclared_report_count": sum(row.get("code") == "inventory_report_undeclared" for row in error_rows),
    }
    if summary != expected_summary:
        errors.append("manifest_summary_invalid")
    locators = [str(row.get("locator") or "") for row in records]
    inventory_ids = [str(row.get("inventory_id") or "") for row in records]
    report_checksums = [str(row.get("report_checksum") or "") for row in sources]
    if len(locators) != len(set(locators)) or len(inventory_ids) != len(set(inventory_ids)):
        errors.append("manifest_authority_duplicate")
    if len(report_checksums) != len(set(report_checksums)):
        errors.append("manifest_source_report_duplicate")
    classification_versions = {str(row.get("classification_checksum") or "") for row in sources}
    if len(classification_versions) > 1:
        errors.append("manifest_classification_mismatch")
    for key in (
        "inaccessible_authority_count",
        "unclassified_authority_count",
        "undeclared_report_count",
    ):
        if int(summary.get(key) or 0):
            errors.append(f"manifest_{key}_nonzero")
    if manifest_errors:
        errors.append("manifest_errors_present")
    try:
        _safe_committed_payload(payload)
    except ValueError as exc:
        errors.append(str(exc))
    return sorted(set(errors))
