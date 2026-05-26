"""Phase 8: core-memory init wizard and setup doctor tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _make_args(**kwargs):
    args = types.SimpleNamespace(
        root=".",
        preset=None,
        global_config=False,
        force=False,
    )
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


class TestPresetMode(unittest.TestCase):
    def test_preset_local_writes_config(self):
        from core_memory.cli_handlers_setup import init_command

        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="local")
            with patch("sys.stdout"):
                init_command(args)

            config_path = Path(tmpdir) / ".core-memory.yaml"
            self.assertTrue(config_path.exists())

    def test_preset_local_config_correct_backend(self):
        import yaml
        from core_memory.cli_handlers_setup import init_command

        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="local")
            with patch("sys.stdout"):
                init_command(args)

            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["backend"], "json")
            self.assertEqual(config["graph_backend"], "kuzu")

    def test_preset_neo4j_config_correct(self):
        import yaml
        from core_memory.cli_handlers_setup import init_command

        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="neo4j")
            with patch("sys.stdout"):
                init_command(args)

            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["graph_backend"], "neo4j")
            self.assertEqual(config["neo4j"]["uri"], "bolt://localhost:7687")

    def test_preset_sqlite_config_correct(self):
        import yaml
        from core_memory.cli_handlers_setup import init_command

        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="sqlite")
            with patch("sys.stdout"):
                init_command(args)

            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["backend"], "sqlite")

    def test_unknown_preset_exits(self):
        from core_memory.cli_handlers_setup import init_command

        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="totally_unknown")
            with patch("sys.stdout"), self.assertRaises(SystemExit) as cm:
                init_command(args)
            self.assertEqual(cm.exception.code, 1)


class TestIdempotency(unittest.TestCase):
    def test_second_run_skips_without_force(self):
        from core_memory.cli_handlers_setup import init_command
        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="local")
            with patch("sys.stdout"):
                init_command(args)

            # Mutate the written config
            config_path = Path(tmpdir) / ".core-memory.yaml"
            cfg = yaml.safe_load(config_path.read_text())
            cfg["backend"] = "sqlite"
            config_path.write_text(yaml.dump(cfg))

            # Second run without --force should skip
            captured = []
            with patch("builtins.print", side_effect=lambda *a, **k: captured.append(str(a))):
                init_command(args)

            output = " ".join(captured)
            self.assertIn("skipped", output.lower())

            # Config must be unchanged
            cfg2 = yaml.safe_load(config_path.read_text())
            self.assertEqual(cfg2["backend"], "sqlite")

    def test_force_overwrites_existing(self):
        from core_memory.cli_handlers_setup import init_command
        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="local")
            with patch("sys.stdout"):
                init_command(args)

            # Manually change value
            config_path = Path(tmpdir) / ".core-memory.yaml"
            cfg = yaml.safe_load(config_path.read_text())
            cfg["backend"] = "sqlite"
            config_path.write_text(yaml.dump(cfg))

            # Run with --force and different preset
            force_args = _make_args(root=tmpdir, preset="neo4j", force=True)
            with patch("sys.stdout"):
                init_command(force_args)

            cfg2 = yaml.safe_load(config_path.read_text())
            self.assertEqual(cfg2["graph_backend"], "neo4j")


class TestGlobalFlag(unittest.TestCase):
    def test_global_writes_to_home_dir(self):
        from core_memory.cli_handlers_setup import init_command, _USER_CONFIG_PATH

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home_config = Path(tmpdir) / "config.yaml"

            with patch("core_memory.cli_handlers_setup._USER_CONFIG_PATH", fake_home_config):
                args = _make_args(root=tmpdir, preset="local", global_config=True)
                with patch("sys.stdout"):
                    init_command(args)

            self.assertTrue(fake_home_config.exists())
            # Project-local config should NOT have been written
            self.assertFalse((Path(tmpdir) / ".core-memory.yaml").exists())


class TestSettingsLoader(unittest.TestCase):
    def test_defaults_returned_when_no_config(self):
        from core_memory.config.settings import load_settings

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core_memory.config.settings._USER_CONFIG_PATH", Path(tmpdir) / "nonexistent.yaml"):
                with patch("core_memory.config.settings.find_project_config", return_value=None):
                    cfg = load_settings()

        self.assertEqual(cfg["backend"], "json")
        self.assertIn("memory", cfg)

    def test_project_config_overrides_defaults(self):
        import yaml
        from core_memory.config.settings import load_settings

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".core-memory.yaml"
            config_path.write_text(yaml.dump({"backend": "sqlite", "graph_backend": "neo4j"}))

            with patch("core_memory.config.settings._USER_CONFIG_PATH", Path(tmpdir) / "no.yaml"):
                cfg = load_settings(root=Path(tmpdir))

        self.assertEqual(cfg["backend"], "sqlite")
        self.assertEqual(cfg["graph_backend"], "neo4j")
        # Defaults for unset keys must still be present
        self.assertIn("memory", cfg)

    def test_env_var_overrides_config_file(self):
        import yaml
        from core_memory.config.settings import load_settings

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".core-memory.yaml"
            config_path.write_text(yaml.dump({"backend": "sqlite"}))

            with patch("core_memory.config.settings._USER_CONFIG_PATH", Path(tmpdir) / "no.yaml"):
                with patch.dict("os.environ", {"CORE_MEMORY_BACKEND": "json"}):
                    cfg = load_settings(root=Path(tmpdir))

        self.assertEqual(cfg["backend"], "json")

    def test_user_global_loaded_as_lower_priority(self):
        import yaml
        from core_memory.config.settings import load_settings

        with tempfile.TemporaryDirectory() as tmpdir:
            user_cfg = Path(tmpdir) / "user_config.yaml"
            user_cfg.write_text(yaml.dump({"backend": "sqlite"}))

            # project-local wins
            project_cfg = Path(tmpdir) / ".core-memory.yaml"
            project_cfg.write_text(yaml.dump({"backend": "neo4j"}))

            with patch("core_memory.config.settings._USER_CONFIG_PATH", user_cfg):
                cfg = load_settings(root=Path(tmpdir))

        self.assertEqual(cfg["backend"], "neo4j")


class TestExpandedDoctor(unittest.TestCase):
    def test_doctor_returns_structured_report(self):
        from core_memory.cli_handlers_setup import expanded_doctor

        with tempfile.TemporaryDirectory() as tmpdir:
            beads_dir = Path(tmpdir) / ".beads"
            beads_dir.mkdir()

            report = expanded_doctor(tmpdir)

        self.assertIn("storage", report)
        self.assertIn("graph_traversal", report)
        self.assertIn("dreamer", report)
        self.assertIn("rolling_window", report)
        self.assertIn("ok", report)

    def test_doctor_storage_error_when_missing_beads(self):
        from core_memory.cli_handlers_setup import expanded_doctor

        with tempfile.TemporaryDirectory() as tmpdir:
            report = expanded_doctor(tmpdir)

        self.assertEqual(report["storage"]["status"], "error")
        self.assertFalse(report["ok"])

    def test_doctor_ok_true_when_beads_dir_present(self):
        from core_memory.cli_handlers_setup import expanded_doctor

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".beads").mkdir()
            report = expanded_doctor(tmpdir)

        self.assertEqual(report["storage"]["status"], "ok")

    def test_doctor_command_exits_1_on_error(self):
        from core_memory.cli_handlers_setup import doctor_command

        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir)
            with patch("builtins.print"), self.assertRaises(SystemExit) as cm:
                doctor_command(args)
            self.assertEqual(cm.exception.code, 1)

    def test_doctor_command_exits_0_on_ok(self):
        from core_memory.cli_handlers_setup import doctor_command

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".beads").mkdir()
            args = _make_args(root=tmpdir)
            with patch("builtins.print"):
                # Should not raise SystemExit
                doctor_command(args)


if __name__ == "__main__":
    unittest.main()
