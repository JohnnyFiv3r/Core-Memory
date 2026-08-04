"""Read-only legacy-state inventory contracts."""

from .consolidation import (
    MANIFEST_SCHEMA_VERSION,
    REGISTRY_SCHEMA_VERSION,
    load_authority_registry,
    load_inventory_report,
    merge_inventory_reports,
    validate_authority_registry,
    verify_consolidated_manifest,
)
from .inventory import (
    INVENTORY_SCHEMA_VERSION,
    InventoryRecord,
    InventoryReport,
    scan_code,
    scan_filesystem,
    validate_code_output_path,
)
from .sql_inventory import scan_postgresql, scan_sqlite, validate_sql_output_path

__all__ = [
    "INVENTORY_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "REGISTRY_SCHEMA_VERSION",
    "InventoryRecord",
    "InventoryReport",
    "load_authority_registry",
    "load_inventory_report",
    "merge_inventory_reports",
    "scan_code",
    "scan_filesystem",
    "scan_postgresql",
    "scan_sqlite",
    "validate_code_output_path",
    "validate_authority_registry",
    "validate_sql_output_path",
    "verify_consolidated_manifest",
]
