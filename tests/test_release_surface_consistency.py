from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

import core_memory
from core_memory._version import VERSION as VERSION_MODULE
from core_memory.persistence import store as store_mod


class TestReleaseSurfaceConsistency(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(__file__).resolve().parents[1]
        self.pyproject = self.repo / "pyproject.toml"
        self.readme = self.repo / "README.md"
        self.license = self.repo / "LICENSE"

    def _pyproject_data(self) -> dict:
        return tomllib.loads(self.pyproject.read_text(encoding="utf-8"))

    def test_version_is_single_source(self):
        pp = self._pyproject_data()
        project_version = str((pp.get("project") or {}).get("version") or "")
        self.assertEqual(project_version, core_memory.__version__)
        self.assertEqual(project_version, VERSION_MODULE)
        self.assertFalse(hasattr(store_mod, "VERSION"), "store.py should not own package version authority")

    def test_authorship_and_license_metadata(self):
        pp = self._pyproject_data()
        project = pp.get("project") or {}
        authors = project.get("authors") or []
        maintainers = project.get("maintainers") or []

        self.assertIn({"name": "John Inniger", "email": "john@linelead.io"}, authors)
        self.assertIn({"name": "John Inniger", "email": "john@linelead.io"}, maintainers)

        self.assertEqual({"text": "Apache-2.0"}, project.get("license"))

        readme_text = self.readme.read_text(encoding="utf-8")
        self.assertRegex(readme_text, r"license-Apache%202\.0|Apache-2\.0 License")

        license_text = self.license.read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Copyright 2026 John Inniger", license_text)

    def test_no_mit_residue_in_active_surfaces(self):
        active_paths = [
            self.pyproject,
            self.readme,
            self.license,
            *self.repo.glob("core_memory/**/*.py"),
            *self.repo.glob("docs/**/*.md"),
        ]
        blockers: list[str] = []
        for path in active_paths:
            rel = path.relative_to(self.repo)
            if str(rel).startswith("docs/archive/"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"\bMIT\b|MIT License", text):
                blockers.append(str(rel))

        self.assertEqual([], blockers, f"Found MIT residue in active surfaces: {blockers}")


if __name__ == "__main__":
    unittest.main()
