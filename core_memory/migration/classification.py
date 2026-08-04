"""Versioned, human-authored classification rules for legacy inventory."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

CLASSIFICATION_SCHEMA_VERSION = "core_memory.legacy_inventory_classification.v1"
_DELETION_PR_RE = re.compile(r"^PR-[0-9]{2}[A-Z]$")


@dataclass(frozen=True)
class Classification:
    authority_classification: str
    provenance_classification: str
    proposed_future_importer: str
    planned_deletion_pr: str
    rule_id: str


@dataclass(frozen=True)
class ClassificationMap:
    payload: dict[str, Any]
    checksum: str

    @property
    def schema_version(self) -> str:
        return str(self.payload["schema_version"])

    @property
    def provenance_classes(self) -> frozenset[str]:
        return frozenset(str(item) for item in self.payload["provenance_classes"])

    def classify_code(self, finding_kind: str, path: str, symbol: str) -> Classification:
        finding_classes = self.payload["finding_classes"]
        authority = str(finding_classes[finding_kind])
        domain = self._code_domain(finding_kind, path, symbol)
        return Classification(
            authority_classification=authority,
            provenance_classification="human_authored",
            proposed_future_importer=str(domain["proposed_future_importer"]),
            planned_deletion_pr=str(domain["planned_deletion_pr"]),
            rule_id=str(domain["id"]),
        )

    def classify_filesystem(self, relative_path: str) -> Classification:
        normalized = relative_path.replace("\\", "/").lower()
        suffix = Path(normalized).suffix
        for rule in self.payload["filesystem_rules"]:
            terms = [str(item).lower() for item in rule.get("path_terms") or []]
            suffixes = [str(item).lower() for item in rule.get("suffixes") or []]
            if (terms and any(term in normalized for term in terms)) or (suffixes and suffix in suffixes):
                return self._filesystem_classification(rule)
        return self._filesystem_classification(self.payload["filesystem_default"])

    def _code_domain(self, finding_kind: str, path: str, symbol: str) -> dict[str, Any]:
        if finding_kind == "environment_selector":
            forced = "configuration"
        elif finding_kind in {"queue_class", "queue_operation"}:
            forced = "queue_jobs"
        else:
            forced = ""
        normalized_path = path.replace("\\", "/").lower()
        normalized_symbol = symbol.lower()
        for rule in self.payload["domain_dispositions"]:
            if forced and str(rule["id"]) == forced:
                return cast(dict[str, Any], rule)
            path_terms = [str(item).lower() for item in rule.get("path_terms") or []]
            symbol_terms = [str(item).lower() for item in rule.get("symbol_terms") or []]
            if any(term in normalized_path for term in path_terms) or any(
                term in normalized_symbol for term in symbol_terms
            ):
                return cast(dict[str, Any], rule)
        return cast(dict[str, Any], self.payload["default_disposition"])

    def _filesystem_classification(self, row: dict[str, Any]) -> Classification:
        return Classification(
            authority_classification=str(row["authority_classification"]),
            provenance_classification=str(row["provenance_classification"]),
            proposed_future_importer=str(row["proposed_future_importer"]),
            planned_deletion_pr=str(row["planned_deletion_pr"]),
            rule_id=str(row["id"]),
        )


def _validate_disposition(row: Any, provenance_classes: set[str], *, filesystem: bool) -> None:
    if not isinstance(row, dict):
        raise ValueError("classification_disposition_not_object")
    required = {"id", "proposed_future_importer", "planned_deletion_pr"}
    if filesystem:
        required |= {"authority_classification", "provenance_classification"}
    missing = sorted(key for key in required if not str(row.get(key) or "").strip())
    if missing:
        raise ValueError(f"classification_disposition_missing:{','.join(missing)}")
    if not _DELETION_PR_RE.fullmatch(str(row["planned_deletion_pr"])):
        raise ValueError("classification_deletion_pr_invalid")
    if filesystem and str(row["provenance_classification"]) not in provenance_classes:
        raise ValueError("classification_provenance_invalid")


def _validate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("classification_not_object")
    if payload.get("schema_version") != CLASSIFICATION_SCHEMA_VERSION:
        raise ValueError("classification_schema_invalid")
    provenance_rows = payload.get("provenance_classes")
    if not isinstance(provenance_rows, list) or not provenance_rows:
        raise ValueError("classification_provenance_classes_invalid")
    provenance_classes = {str(item) for item in provenance_rows}
    if "unknown" not in provenance_classes or "human_authored" not in provenance_classes:
        raise ValueError("classification_required_provenance_missing")
    finding_classes = payload.get("finding_classes")
    if not isinstance(finding_classes, dict) or not finding_classes:
        raise ValueError("classification_finding_classes_invalid")
    if any(not str(key).strip() or not str(value).strip() for key, value in finding_classes.items()):
        raise ValueError("classification_finding_class_empty")

    seen_ids: set[str] = set()
    for row in payload.get("domain_dispositions") or []:
        _validate_disposition(row, provenance_classes, filesystem=False)
        rule_id = str(row["id"])
        if rule_id in seen_ids:
            raise ValueError("classification_rule_id_duplicate")
        seen_ids.add(rule_id)
    _validate_disposition(payload.get("default_disposition"), provenance_classes, filesystem=False)
    seen_ids.clear()
    for row in payload.get("filesystem_rules") or []:
        _validate_disposition(row, provenance_classes, filesystem=True)
        rule_id = str(row["id"])
        if rule_id in seen_ids:
            raise ValueError("classification_rule_id_duplicate")
        seen_ids.add(rule_id)
    _validate_disposition(payload.get("filesystem_default"), provenance_classes, filesystem=True)
    return payload


def load_classification_map(path: Path | None = None) -> ClassificationMap:
    if path is None:
        raw = files("core_memory").joinpath("data/legacy_inventory_classification.v1.json").read_bytes()
    else:
        raw = Path(path).read_bytes()
    payload = _validate(json.loads(raw.decode("utf-8")))
    checksum = hashlib.sha256(raw).hexdigest()
    return ClassificationMap(payload=payload, checksum=checksum)
