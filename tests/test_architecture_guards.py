from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_architecture_guards.py"
BASELINE = ROOT / "scripts" / "architecture_guards_baseline.json"


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
