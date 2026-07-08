from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_test_suite_health.py"


def _load_guard_module():
    spec = importlib.util.spec_from_file_location("test_suite_health", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["test_suite_health"] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard_module()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_detects_backend_skip_without_optional_marker(tmp_path: Path):
    _write(
        tmp_path / "tests" / "test_qdrant_backend.py",
        "\n".join(
            [
                "import unittest",
                "",
                "@unittest.skipUnless(False, 'qdrant-client not installed')",
                "class TestQdrant(unittest.TestCase):",
                "    def test_backend(self):",
                "        pass",
            ]
        )
        + "\n",
    )

    violations = guard.run_checks(tmp_path)

    assert {v.detail.get("missing_markers") for v in violations} == {"optional_backend,qdrant"}


def test_allows_backend_skip_when_marked(tmp_path: Path):
    _write(
        tmp_path / "tests" / "test_kuzu_backend.py",
        "\n".join(
            [
                "import pytest",
                "import unittest",
                "",
                "pytestmark = [pytest.mark.optional_backend, pytest.mark.kuzu]",
                "",
                "@unittest.skipUnless(False, 'kuzu not installed')",
                "class TestKuzu(unittest.TestCase):",
                "    def test_backend(self):",
                "        pass",
            ]
        )
        + "\n",
    )

    assert guard.run_checks(tmp_path) == []


def test_detects_live_skip_without_live_marker(tmp_path: Path):
    _write(
        tmp_path / "tests" / "test_neo4j_live.py",
        "\n".join(
            [
                "import pytest",
                "",
                "pytestmark = [pytest.mark.skipif(True, reason='NEO4J_URI not set')]",
                "",
                "def test_live():",
                "    pass",
            ]
        )
        + "\n",
    )

    violations = guard.run_checks(tmp_path)

    assert [v.check for v in violations] == ["backend_skip_marker"]
    assert violations[0].detail["missing_markers"] == "neo4j_live"


def test_detects_skip_and_xfail_without_reason_text(tmp_path: Path):
    _write(
        tmp_path / "tests" / "test_reasonless.py",
        "\n".join(
            [
                "import pytest",
                "",
                "@pytest.mark.skip",
                "def test_skipped():",
                "    pass",
                "",
                "@pytest.mark.xfail()",
                "def test_xfail():",
                "    pass",
            ]
        )
        + "\n",
    )

    violations = guard.run_checks(tmp_path)

    assert [v.check for v in violations] == ["skip_reason", "skip_reason"]


def test_detects_stale_marker_descriptions(tmp_path: Path):
    _write(
        tmp_path / "pyproject.toml",
        "\n".join(
            [
                "[tool.pytest.ini_options]",
                "markers = [",
                '  "facade: tests targeted for Phase 4 removal",',
                '  "mixin_assembly: cleanup-removal marker",',
                "]",
            ]
        )
        + "\n",
    )

    violations = guard.run_checks(tmp_path)

    assert [v.check for v in violations] == ["marker_description", "marker_description"]


def test_current_suite_health_guard_is_clean():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--fail-on-violations",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
