"""Read-only static-code and filesystem legacy authority inventory."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .classification import ClassificationMap, load_classification_map

INVENTORY_SCHEMA_VERSION = "core_memory.legacy_inventory.v1"
SCANNER_VERSION = "pr-00c.v1"

_SEMANTIC_TERMS = {
    "artifact",
    "association",
    "bead",
    "causal",
    "claim",
    "dream",
    "entity",
    "goal",
    "grounding",
    "memory",
    "promotion",
    "relationship",
    "retrieval",
    "soul",
    "storyline",
}
_SELECTOR_ENV_TERMS = {
    "ASSOC",
    "BACKEND",
    "BEAD",
    "CLAIM",
    "DREAM",
    "DSN",
    "ENABLE",
    "FALLBACK",
    "GOAL",
    "GRAPH",
    "INDEX",
    "MEMORY_ROOT",
    "MODE",
    "MODEL",
    "PROMOTION",
    "PROVIDER",
    "QUEUE",
    "RECALL",
    "RETRIEVAL",
    "SOUL",
    "STORE",
    "STORY",
    "URI",
    "URL",
    "VECTOR",
}
_FILE_READERS = {
    "_read_json",
    "load_json",
    "open",
    "read_bytes",
    "read_json",
    "read_text",
}
_FILE_WRITERS = {
    "_write_json",
    "append_jsonl",
    "atomic_write_json",
    "open",
    "write",
    "write_bytes",
    "write_json",
    "write_text",
    "writelines",
}
_QUEUE_OPERATIONS = {
    "claim_pending",
    "dequeue",
    "enqueue",
    "heartbeat",
    "lease",
    "mark_complete",
    "mark_failed",
    "retry",
}
_GOVERNED_SEMANTIC_WRITERS = {
    "_write_association_if_missing",
    "add_structural_edge",
    "apply_crawler_updates",
    "resolve_goal_candidate_for_store",
    "transition_goal_state_for_store",
    "write_claim_updates_to_bead",
    "write_claims_to_bead",
    "write_memory_outcome_to_bead",
}
_EXTERNAL_MODULE_PREFIXES = {
    "chromadb",
    "faiss",
    "httpx",
    "kuzu",
    "neo4j",
    "psycopg",
    "qdrant_client",
    "requests",
    "sqlite3",
    "urllib.request",
}
_ARTIFACT_PATH_TERMS = {
    ".beads",
    ".db",
    ".json",
    ".jsonl",
    ".sqlite",
    ".sqlite3",
    "soul.md",
}
_TIME_KEYS = {
    "created_at",
    "event_time",
    "knowledge_time",
    "observed_at",
    "recorded_at",
    "timestamp",
    "updated_at",
}
_ID_KEYS = (
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


@dataclass(frozen=True)
class InventoryRecord:
    inventory_id: str
    source_label: str
    tenant_workspace_classification: str
    locator: str
    locator_kind: str
    authority_classification: str
    record_kind: str
    record_count: int
    byte_count: int
    min_event_time: str | None
    max_event_time: str | None
    stable_hash: str
    parse_failure_count: int
    duplicate_id_count: int
    recoverability_status: str
    provenance_classification: str
    reader_symbols: tuple[str, ...]
    writer_symbols: tuple[str, ...]
    proposed_future_importer: str
    planned_deletion_pr: str
    classification_rule_id: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["reader_symbols"] = list(self.reader_symbols)
        row["writer_symbols"] = list(self.writer_symbols)
        return row


@dataclass(frozen=True)
class InventoryReport:
    scanner: str
    source_label: str
    tenant_workspace_classification: str
    root_fingerprint: str
    classification_schema_version: str
    classification_checksum: str
    records: tuple[InventoryRecord, ...]
    warnings: tuple[dict[str, str], ...] = ()
    complete: bool = True
    scan_metrics: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        authority_counts = Counter(row.authority_classification for row in self.records)
        provenance_counts = Counter(row.provenance_classification for row in self.records)
        return {
            "schema_version": INVENTORY_SCHEMA_VERSION,
            "scanner_version": SCANNER_VERSION,
            "scanner": self.scanner,
            "source_label": self.source_label,
            "tenant_workspace_classification": self.tenant_workspace_classification,
            "root_fingerprint": self.root_fingerprint,
            "classification_schema_version": self.classification_schema_version,
            "classification_checksum": self.classification_checksum,
            "read_only": True,
            "complete": self.complete,
            "summary": {
                "record_count": len(self.records),
                "byte_count": sum(row.byte_count for row in self.records),
                "scanned_object_count": int(self.scan_metrics.get("scanned_object_count") or 0),
                "scanned_byte_count": int(self.scan_metrics.get("scanned_byte_count") or 0),
                "parse_failure_count": sum(row.parse_failure_count for row in self.records),
                "duplicate_id_count": sum(row.duplicate_id_count for row in self.records),
                "unclassified_count": sum(not row.authority_classification for row in self.records),
                "authority_counts": dict(sorted(authority_counts.items())),
                "provenance_counts": dict(sorted(provenance_counts.items())),
            },
            "records": [row.to_dict() for row in self.records],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class _CodeFinding:
    kind: str
    symbol: str
    line: int
    stable_hash: str
    byte_count: int
    details: dict[str, Any]


@dataclass
class _FileStats:
    record_count: int = 0
    parse_failure_count: int = 0
    duplicate_id_count: int = 0
    event_times: list[str] = field(default_factory=list)
    receipt_count: int = 0


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory_id(*parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return f"inv_{hashlib.sha256(raw).hexdigest()[:24]}"


def _safe_label(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name}_missing")
    appears_sensitive = (
        "://" in normalized
        or normalized.startswith(("/", "~"))
        or bool(re.search(r"(?i)(password|passwd|secret|token|api[_-]?key|dsn)\s*[=:]", normalized))
    )
    if appears_sensitive:
        return f"redacted-{field_name}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]}"
    return normalized[:160]


def _root_fingerprint(source_label: str, tenant_workspace: str) -> str:
    return _sha256_bytes(f"{source_label}\x1f{tenant_workspace}".encode("utf-8"))


def _validate_absolute_directory(root: Path) -> Path:
    requested = Path(root)
    if not requested.is_absolute():
        raise ValueError("scan_root_must_be_absolute")
    resolved = requested.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise ValueError("unsafe_broad_scan_root")
    if not resolved.is_dir():
        raise ValueError("scan_root_not_directory")
    return resolved


def validate_output_path(output: Path, *, scan_root: Path) -> Path:
    requested = Path(output)
    if not requested.is_absolute():
        raise ValueError("inventory_output_must_be_absolute")
    resolved = requested.resolve()
    try:
        resolved.relative_to(scan_root.resolve())
    except ValueError:
        return resolved
    raise ValueError("inventory_output_inside_scan_root")


def validate_code_output_path(output: Path, *, code_root: Path, includes: Iterable[Path]) -> Path:
    requested = Path(output)
    if not requested.is_absolute():
        raise ValueError("inventory_output_must_be_absolute")
    resolved_output = requested.resolve()
    resolved_root = _validate_absolute_directory(code_root)
    include_rows = list(includes)
    if not include_rows:
        raise ValueError("code_include_missing")
    for raw in include_rows:
        target = (resolved_root / raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            target.relative_to(resolved_root)
        except ValueError:
            raise ValueError("code_include_outside_root") from None
        if target.is_dir():
            try:
                resolved_output.relative_to(target)
            except ValueError:
                continue
            raise ValueError("inventory_output_inside_code_include")
        if resolved_output == target:
            raise ValueError("inventory_output_overwrites_code_input")
    return resolved_output


def _safe_relative(root: Path, candidate: Path) -> str:
    return candidate.resolve().relative_to(root.resolve()).as_posix()


def _module_name(relative: str) -> str:
    parts = list(Path(relative).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _prefixed_locator(prefix: str, relative: str) -> str:
    normalized = str(prefix or "").strip().strip("/")
    return f"{normalized}/{relative}" if normalized else relative


def _call_name(node: ast.Call) -> str:
    current: ast.AST = node.func
    parts: list[str] = []
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _node_hash(node: ast.AST) -> str:
    return _sha256_bytes(ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8"))


def _string_constant(node: ast.AST | None) -> str:
    return str(node.value) if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _path_template(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)):
        return str(node.value)
    if isinstance(node, ast.Name):
        return "{" + node.id + "}"
    if isinstance(node, ast.Attribute):
        return "{" + node.attr + "}"
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                parts.append("{expr}")
        return "".join(parts)
    if isinstance(node, ast.Call):
        call = _call_name(node).rsplit(".", 1)[-1]
        if call == "Path" and node.args:
            return _path_template(node.args[0])
        if call in {"joinpath", "with_name"} and isinstance(node.func, ast.Attribute):
            base = _path_template(node.func.value)
            additions = [_path_template(arg) for arg in node.args]
            return "/".join(part.strip("/") for part in [base, *additions] if part)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _path_template(node.left)
        right = _path_template(node.right)
        return "/".join(part.strip("/") for part in (left, right) if part)
    return ""


def _artifact_template_for_call(node: ast.Call, short_name: str) -> str:
    if isinstance(node.func, ast.Attribute) and short_name in {
        "open",
        "read_bytes",
        "read_text",
        "replace",
        "write_bytes",
        "write_text",
    }:
        return _path_template(node.func.value)
    return _path_template(node.args[0]) if node.args else ""


def _open_mode(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        positional_index = 0
    else:
        positional_index = 1
    if len(node.args) > positional_index:
        mode = _string_constant(node.args[positional_index])
        if mode:
            return mode
    for keyword in node.keywords:
        if keyword.arg == "mode":
            return _string_constant(keyword.value) or "r"
    return "r"


def _env_key_from_node(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name.endswith(("getenv", "environ.get")) and node.args:
            return _string_constant(node.args[0])
    if isinstance(node, ast.Subscript):
        base = node.value
        if isinstance(base, ast.Attribute) and base.attr == "environ":
            return _string_constant(node.slice)
    return ""


def _is_selector_env(key: str) -> bool:
    upper = key.upper()
    return upper.startswith("CORE_MEMORY_") and any(term in upper for term in _SELECTOR_ENV_TERMS)


def _semantic_context(path: str, symbol: str) -> bool:
    normalized_path = path.replace("\\", "/").lower()
    if normalized_path.startswith("core_memory/"):
        normalized_path = normalized_path[len("core_memory/") :]
    elif normalized_path.startswith("core_memory."):
        normalized_path = normalized_path[len("core_memory.") :]
    normalized_symbol = symbol.lower()
    if normalized_symbol.startswith("core_memory."):
        normalized_symbol = normalized_symbol[len("core_memory.") :]
    normalized = f"{normalized_path} {normalized_symbol}"
    return any(term in normalized for term in _SEMANTIC_TERMS)


class _CodeVisitor(ast.NodeVisitor):
    def __init__(self, module: str):
        self.module = module
        self.stack: list[str] = []
        self.findings: list[_CodeFinding] = []
        self._artifact_keys: set[tuple[str, str]] = set()

    @property
    def symbol(self) -> str:
        suffix = ".".join(self.stack)
        return f"{self.module}.{suffix}" if suffix else self.module

    def _add(self, kind: str, node: ast.AST, **details: Any) -> None:
        self.findings.append(
            _CodeFinding(
                kind=kind,
                symbol=self.symbol,
                line=int(getattr(node, "lineno", 1) or 1),
                stable_hash=_node_hash(node),
                byte_count=len(ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")),
                details={key: value for key, value in details.items() if value not in (None, "", [])},
            )
        )

    def _visit_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.stack.append(node.name)
        normalized_name = node.name.lower()
        semantic = _semantic_context(self.module, self.symbol)
        if "fallback" in normalized_name and semantic:
            self._add("semantic_fallback", node, definition=node.name)
        is_resolver = (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and ("resolver" in normalized_name or normalized_name.startswith("resolve"))
        ) or (isinstance(node, ast.ClassDef) and normalized_name.endswith("resolver"))
        if is_resolver and semantic:
            self._add("semantic_resolver", node, definition=node.name)
        if isinstance(node, ast.ClassDef):
            if normalized_name.endswith(("queue", "jobstore")):
                self._add("queue_class", node, definition=node.name)
            elif normalized_name.endswith(("store", "repository", "index")):
                self._add("store_class", node, definition=node.name)
            elif normalized_name.endswith("backend"):
                self._add("external_backend", node, definition=node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast visitor protocol
        self._visit_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_definition(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_definition(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        call = _call_name(node)
        short_name = call.rsplit(".", 1)[-1]
        env_key = _env_key_from_node(node)
        if _is_selector_env(env_key):
            self._add("environment_selector", node, environment_variable=env_key, call=call)
        if short_name in _QUEUE_OPERATIONS:
            self._add("queue_operation", node, call=call)
        if short_name in _GOVERNED_SEMANTIC_WRITERS:
            self._add("governed_semantic_writer", node, call=call)

        if short_name in {"execute", "executemany"} and node.args:
            statement = _string_constant(node.args[0]).strip().split(None, 1)
            verb = statement[0].upper() if statement else "UNKNOWN"
            if verb in {"SELECT", "PRAGMA", "EXPLAIN", "WITH"}:
                kind = "sql_reader"
            elif verb in {"ALTER", "CREATE", "DELETE", "DROP", "INSERT", "REPLACE", "UPDATE"}:
                kind = "sql_writer"
            else:
                kind = "sql_operation"
            self._add(kind, node, call=call, sql_verb=verb)

        file_kind = ""
        if short_name == "open":
            mode = _open_mode(node)
            file_kind = "file_writer" if any(flag in mode for flag in "wax+") else "file_reader"
        elif short_name in _FILE_READERS:
            file_kind = "file_reader"
        elif short_name in _FILE_WRITERS:
            file_kind = "file_writer"
        if file_kind:
            template = _artifact_template_for_call(node, short_name)
            self._add(file_kind, node, call=call, artifact_template=template)
            if template:
                self._artifact_keys.add((self.symbol, template))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        env_key = _env_key_from_node(node)
        if _is_selector_env(env_key):
            self._add("environment_selector", node, environment_variable=env_key, operation="subscript")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            if any(alias.name == prefix or alias.name.startswith(f"{prefix}.") for prefix in _EXTERNAL_MODULE_PREFIXES):
                self._add("external_backend", node, imported_module=alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = str(node.module or "")
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in _EXTERNAL_MODULE_PREFIXES):
            self._add("external_backend", node, imported_module=module)

    def add_path_references(self, tree: ast.AST) -> None:
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, (ast.BinOp, ast.Call)):
                continue
            path_parent: ast.AST | None = parents.get(node)
            if (
                isinstance(node, ast.BinOp)
                and isinstance(path_parent, ast.BinOp)
                and isinstance(path_parent.op, ast.Div)
            ):
                continue
            template = _path_template(node)
            if not template or not any(term in template.lower() for term in _ARTIFACT_PATH_TERMS):
                continue
            owner = self.module
            ancestor: ast.AST | None = path_parent
            while ancestor is not None:
                if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    owner = f"{self.module}.{ancestor.name}"
                    break
                ancestor = parents.get(ancestor)
            if (owner, template) in self._artifact_keys:
                continue
            self.findings.append(
                _CodeFinding(
                    kind="artifact_reference",
                    symbol=owner,
                    line=int(getattr(node, "lineno", 1) or 1),
                    stable_hash=_node_hash(node),
                    byte_count=len(ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")),
                    details={"artifact_template": template},
                )
            )


def _iter_included_python_files(root: Path, includes: Iterable[Path], excludes: Iterable[Path]) -> list[Path]:
    excluded: list[Path] = []
    for raw in excludes:
        candidate = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ValueError("code_exclude_outside_root") from None
        excluded.append(candidate)
    output: set[Path] = set()
    include_rows = list(includes)
    if not include_rows:
        raise ValueError("code_include_missing")
    for raw in include_rows:
        candidate = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ValueError("code_include_outside_root") from None
        if not candidate.exists():
            raise ValueError("code_include_missing_path")
        paths = [candidate] if candidate.is_file() else candidate.rglob("*.py")
        for path in paths:
            if path.suffix != ".py" or path.is_symlink():
                continue
            resolved = path.resolve()
            if any(resolved == skip or skip in resolved.parents for skip in excluded):
                continue
            output.add(resolved)
    return sorted(output, key=lambda item: _safe_relative(root, item))


def _code_record(
    finding: _CodeFinding,
    *,
    relative: str,
    path: Path,
    source_label: str,
    tenant_workspace: str,
    classification_map: ClassificationMap,
) -> InventoryRecord:
    classification = classification_map.classify_code(finding.kind, relative, finding.symbol)
    readers = (finding.symbol,) if finding.kind in {"file_reader", "sql_reader"} else ()
    writers = (
        (finding.symbol,)
        if finding.kind in {"file_writer", "governed_semantic_writer", "queue_operation", "sql_writer"}
        else ()
    )
    return InventoryRecord(
        inventory_id=_inventory_id("code", relative, finding.symbol, finding.kind, finding.stable_hash),
        source_label=source_label,
        tenant_workspace_classification=tenant_workspace,
        locator=relative,
        locator_kind="python_symbol",
        authority_classification=classification.authority_classification,
        record_kind=finding.kind,
        record_count=1,
        byte_count=finding.byte_count,
        min_event_time=None,
        max_event_time=None,
        stable_hash=finding.stable_hash,
        parse_failure_count=0,
        duplicate_id_count=0,
        recoverability_status="source_controlled",
        provenance_classification=classification.provenance_classification,
        reader_symbols=readers,
        writer_symbols=writers,
        proposed_future_importer=classification.proposed_future_importer,
        planned_deletion_pr=classification.planned_deletion_pr,
        classification_rule_id=classification.rule_id,
        details={"line": finding.line, **finding.details},
    )


def scan_code(
    root: Path,
    *,
    includes: Iterable[Path],
    excludes: Iterable[Path] = (),
    source_label: str,
    tenant_workspace_classification: str,
    classification_path: Path | None = None,
    locator_prefix: str = "",
) -> InventoryReport:
    resolved_root = _validate_absolute_directory(root)
    safe_source = _safe_label(source_label, field_name="source_label")
    safe_tenant = _safe_label(tenant_workspace_classification, field_name="tenant_workspace")
    classification_map = load_classification_map(classification_path)
    records: list[InventoryRecord] = []
    warnings: list[dict[str, str]] = []
    complete = True
    python_files = _iter_included_python_files(resolved_root, includes, excludes)
    for path in python_files:
        relative = _prefixed_locator(locator_prefix, _safe_relative(resolved_root, path))
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            complete = False
            digest = _sha256_file(path) if path.is_file() else _sha256_bytes(relative.encode("utf-8"))
            classification = classification_map.classify_code("artifact_reference", relative, _module_name(relative))
            records.append(
                InventoryRecord(
                    inventory_id=_inventory_id("code-parse-failure", relative, digest),
                    source_label=safe_source,
                    tenant_workspace_classification=safe_tenant,
                    locator=relative,
                    locator_kind="python_file",
                    authority_classification=classification.authority_classification,
                    record_kind="python_parse_failure",
                    record_count=0,
                    byte_count=path.stat().st_size if path.exists() else 0,
                    min_event_time=None,
                    max_event_time=None,
                    stable_hash=digest,
                    parse_failure_count=1,
                    duplicate_id_count=0,
                    recoverability_status="source_controlled_parse_failure",
                    provenance_classification="human_authored",
                    reader_symbols=(),
                    writer_symbols=(),
                    proposed_future_importer=classification.proposed_future_importer,
                    planned_deletion_pr=classification.planned_deletion_pr,
                    classification_rule_id=classification.rule_id,
                    details={"error_class": exc.__class__.__name__},
                )
            )
            warnings.append({"code": "python_parse_failure", "locator": relative})
            continue
        visitor = _CodeVisitor(_module_name(relative))
        visitor.visit(tree)
        visitor.add_path_references(tree)
        for finding in visitor.findings:
            records.append(
                _code_record(
                    finding,
                    relative=relative,
                    path=path,
                    source_label=safe_source,
                    tenant_workspace=safe_tenant,
                    classification_map=classification_map,
                )
            )
    records.sort(key=lambda row: (row.locator, str(row.details.get("line") or 0), row.record_kind, row.inventory_id))
    return InventoryReport(
        scanner="static_code",
        source_label=safe_source,
        tenant_workspace_classification=safe_tenant,
        root_fingerprint=_root_fingerprint(safe_source, safe_tenant),
        classification_schema_version=classification_map.schema_version,
        classification_checksum=classification_map.checksum,
        records=tuple(records),
        warnings=tuple(warnings),
        complete=complete,
        scan_metrics={
            "scanned_object_count": len(python_files),
            "scanned_byte_count": sum(path.stat().st_size for path in python_files),
        },
    )


def _normalized_time(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _walk_values(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def _candidate_records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return [payload]
    container_keys = (
        "artifacts",
        "associations",
        "beads",
        "candidates",
        "claims",
        "entries",
        "goals",
        "jobs",
        "storylines",
        "updates",
    )
    rows: list[Any] = []
    for key in container_keys:
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(value)
        elif isinstance(value, dict):
            rows.extend(value.values())
    return rows or [payload]


def _record_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return next((str(value.get(key) or "").strip() for key in _ID_KEYS if str(value.get(key) or "").strip()), "")


def _accumulate_payload(stats: _FileStats, payload: Any, seen_ids: set[str]) -> None:
    rows = _candidate_records(payload)
    stats.record_count += len(rows)
    for row in rows:
        record_id = _record_id(row)
        if record_id:
            if record_id in seen_ids:
                stats.duplicate_id_count += 1
            seen_ids.add(record_id)
        if isinstance(row, dict) and any(row.get(key) for key in ("receipt_id", "semantic_receipt", "model_receipt")):
            stats.receipt_count += 1
        for nested in _walk_values(row):
            if not isinstance(nested, dict):
                continue
            for key in _TIME_KEYS:
                normalized = _normalized_time(nested.get(key))
                if normalized:
                    stats.event_times.append(normalized)


def _json_stats(path: Path, *, jsonl: bool) -> _FileStats:
    stats = _FileStats()
    seen_ids: set[str] = set()
    if jsonl:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    stats.parse_failure_count += 1
                    continue
                _accumulate_payload(stats, payload, seen_ids)
        return stats
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError:
        stats.parse_failure_count = 1
        return stats
    _accumulate_payload(stats, payload, seen_ids)
    return stats


def _iter_files(root: Path) -> list[Path]:
    output: list[Path] = []
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directory_names[:] = sorted(name for name in directory_names if not (current_path / name).is_symlink())
        for name in sorted(file_names):
            output.append(current_path / name)
        for name in sorted(os.listdir(current)):
            candidate = current_path / name
            if candidate.is_symlink() and candidate not in output:
                output.append(candidate)
    return sorted(set(output), key=lambda item: item.relative_to(root).as_posix())


def _filesystem_record(
    path: Path,
    *,
    root: Path,
    source_label: str,
    tenant_workspace: str,
    classification_map: ClassificationMap,
) -> tuple[InventoryRecord, dict[str, str] | None, bool]:
    relative = path.relative_to(root).as_posix()
    classification = classification_map.classify_filesystem(relative)
    if path.is_symlink():
        target = os.readlink(path)
        digest = _sha256_bytes(target.encode("utf-8"))
        return (
            InventoryRecord(
                inventory_id=_inventory_id("filesystem-symlink", relative, digest),
                source_label=source_label,
                tenant_workspace_classification=tenant_workspace,
                locator=relative,
                locator_kind="symlink",
                authority_classification=classification.authority_classification,
                record_kind="external_reference",
                record_count=1,
                byte_count=path.lstat().st_size,
                min_event_time=None,
                max_event_time=None,
                stable_hash=digest,
                parse_failure_count=0,
                duplicate_id_count=0,
                recoverability_status="external_reference_not_followed",
                provenance_classification=classification.provenance_classification,
                reader_symbols=(),
                writer_symbols=(),
                proposed_future_importer=classification.proposed_future_importer,
                planned_deletion_pr=classification.planned_deletion_pr,
                classification_rule_id=classification.rule_id,
                details={"symlink_target_fingerprint": _sha256_bytes(target.encode("utf-8"))},
            ),
            None,
            True,
        )

    byte_count = path.stat().st_size
    suffix = path.suffix.lower()
    warning: dict[str, str] | None
    try:
        digest = _sha256_file(path)
        if suffix == ".jsonl":
            stats = _json_stats(path, jsonl=True)
            record_kind = "jsonl_records"
        elif suffix == ".json":
            stats = _json_stats(path, jsonl=False)
            record_kind = "json_records"
        else:
            stats = _FileStats(record_count=1 if byte_count else 0)
            record_kind = "database_handle" if suffix in {".db", ".sqlite", ".sqlite3"} else "file_artifact"
    except (OSError, UnicodeDecodeError) as exc:
        digest = _sha256_bytes(f"unreadable:{relative}:{byte_count}".encode("utf-8"))
        stats = _FileStats(parse_failure_count=1)
        record_kind = "unreadable_file"
        warning = {"code": "filesystem_file_unreadable", "locator": relative, "error_class": exc.__class__.__name__}
        recoverability = "unreadable"
        complete = False
    else:
        warning = {"code": "filesystem_parse_failure", "locator": relative} if stats.parse_failure_count else None
        recoverability = (
            "requires_read_only_sql_scan"
            if suffix in {".db", ".sqlite", ".sqlite3"}
            else "partially_recoverable"
            if stats.parse_failure_count
            else "source_copyable"
        )
        complete = True

    provenance = classification.provenance_classification
    if stats.record_count and stats.receipt_count == stats.record_count:
        provenance = "llm_authored_with_receipt"
    elif 0 < stats.receipt_count < stats.record_count:
        provenance = "unknown"
    event_times = sorted(stats.event_times)
    return (
        InventoryRecord(
            inventory_id=_inventory_id("filesystem", relative, digest),
            source_label=source_label,
            tenant_workspace_classification=tenant_workspace,
            locator=relative,
            locator_kind="filesystem_path",
            authority_classification=classification.authority_classification,
            record_kind=record_kind,
            record_count=stats.record_count,
            byte_count=byte_count,
            min_event_time=event_times[0] if event_times else None,
            max_event_time=event_times[-1] if event_times else None,
            stable_hash=digest,
            parse_failure_count=stats.parse_failure_count,
            duplicate_id_count=stats.duplicate_id_count,
            recoverability_status=recoverability,
            provenance_classification=provenance,
            reader_symbols=(),
            writer_symbols=(),
            proposed_future_importer=classification.proposed_future_importer,
            planned_deletion_pr=classification.planned_deletion_pr,
            classification_rule_id=classification.rule_id,
            details={"receipt_count": stats.receipt_count},
        ),
        warning,
        complete,
    )


def scan_filesystem(
    root: Path,
    *,
    workspace_root: Path,
    source_label: str,
    tenant_workspace_classification: str,
    classification_path: Path | None = None,
) -> InventoryReport:
    resolved_root = _validate_absolute_directory(root)
    resolved_workspace = _validate_absolute_directory(workspace_root)
    if resolved_root == resolved_workspace:
        raise ValueError("filesystem_scan_refuses_workspace_root")
    if resolved_root in resolved_workspace.parents:
        raise ValueError("filesystem_scan_refuses_workspace_ancestor")
    safe_source = _safe_label(source_label, field_name="source_label")
    safe_tenant = _safe_label(tenant_workspace_classification, field_name="tenant_workspace")
    classification_map = load_classification_map(classification_path)
    records: list[InventoryRecord] = []
    warnings: list[dict[str, str]] = []
    complete = True
    for path in _iter_files(resolved_root):
        record, warning, row_complete = _filesystem_record(
            path,
            root=resolved_root,
            source_label=safe_source,
            tenant_workspace=safe_tenant,
            classification_map=classification_map,
        )
        records.append(record)
        if warning:
            warnings.append(warning)
        complete = complete and row_complete
    return InventoryReport(
        scanner="filesystem",
        source_label=safe_source,
        tenant_workspace_classification=safe_tenant,
        root_fingerprint=_root_fingerprint(safe_source, safe_tenant),
        classification_schema_version=classification_map.schema_version,
        classification_checksum=classification_map.checksum,
        records=tuple(records),
        warnings=tuple(warnings),
        complete=complete,
        scan_metrics={
            "scanned_object_count": len(records),
            "scanned_byte_count": sum(row.byte_count for row in records),
        },
    )
