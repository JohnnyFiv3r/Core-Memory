"""Phase 8: core-memory init wizard, doctor profiles, config commands, and demo."""

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
        mode=None,
        preset=None,
        global_config=False,
        force=False,
        profile=None,
        json_output=False,
        keep=False,
    )
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


# ---------------------------------------------------------------------------
# 8a — Preset / mode init (backward compat)
# ---------------------------------------------------------------------------

class TestPresetMode(unittest.TestCase):
    def test_preset_local_writes_config(self):
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="local")
            with patch("sys.stdout"):
                init_command(args)
            self.assertTrue((Path(tmpdir) / ".core-memory.yaml").exists())

    def test_preset_local_config_correct_backend(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="local")
            with patch("sys.stdout"):
                init_command(args)
            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["backend"], "json")
            self.assertEqual(config["graph_backend"], "kuzu")

    def test_preset_neo4j_config_correct(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="neo4j")
            with patch("sys.stdout"):
                init_command(args)
            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["graph_backend"], "neo4j")
            self.assertEqual(config["neo4j"]["uri"], "bolt://localhost:7687")

    def test_preset_sqlite_config_correct(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="sqlite")
            with patch("sys.stdout"):
                init_command(args)
            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["backend"], "sqlite")

    def test_unknown_preset_exits(self):
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, preset="totally_unknown")
            with patch("sys.stdout"), self.assertRaises(SystemExit) as cm:
                init_command(args)
            self.assertEqual(cm.exception.code, 1)


# ---------------------------------------------------------------------------
# 8b-1 — Mode-based wizard
# ---------------------------------------------------------------------------

class TestModeWizard(unittest.TestCase):
    def test_mode_local_writes_kuzu_graph_backend(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, mode="local")
            with patch("sys.stdout"):
                init_command(args)
            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["graph_backend"], "kuzu")
            self.assertEqual(config["mode"], "local")

    def test_mode_mcp_writes_integration_mcp(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, mode="mcp")
            with patch("sys.stdout"):
                init_command(args)
            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["integration"], "mcp")
            self.assertEqual(config["graph_backend"], "kuzu")

    def test_mode_app_writes_sqlite_backend(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, mode="app")
            with patch("sys.stdout"):
                init_command(args)
            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["backend"], "sqlite")
            self.assertEqual(config["graph_backend"], "kuzu")

    def test_mode_production_writes_neo4j_and_postgres(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, mode="production")
            with patch("sys.stdout"):
                init_command(args)
            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["graph_backend"], "neo4j")
            self.assertEqual(config["backend"], "postgres")
            self.assertIn("neo4j", config)

    def test_mode_beats_preset_when_both_given(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            # --mode mcp should win over --preset local
            args = _make_args(root=tmpdir, mode="mcp", preset="local")
            with patch("sys.stdout"):
                init_command(args)
            config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
            self.assertEqual(config["integration"], "mcp")

    def test_unknown_mode_exits(self):
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, mode="totally_unknown")
            with patch("sys.stdout"), self.assertRaises(SystemExit) as cm:
                init_command(args)
            self.assertEqual(cm.exception.code, 1)

    def test_all_non_production_modes_use_kuzu(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        for mode in ("local", "mcp", "app"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmpdir:
                args = _make_args(root=tmpdir, mode=mode)
                with patch("sys.stdout"):
                    init_command(args)
                config = yaml.safe_load((Path(tmpdir) / ".core-memory.yaml").read_text())
                self.assertEqual(config["graph_backend"], "kuzu",
                                 f"mode={mode} should default to kuzu graph")


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency(unittest.TestCase):
    def test_second_run_skips_without_force(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, mode="local")
            with patch("sys.stdout"):
                init_command(args)
            config_path = Path(tmpdir) / ".core-memory.yaml"
            cfg = yaml.safe_load(config_path.read_text())
            cfg["backend"] = "sqlite"
            config_path.write_text(__import__("yaml").dump(cfg))
            captured = []
            with patch("builtins.print", side_effect=lambda *a, **k: captured.append(str(a))):
                init_command(args)
            self.assertIn("skipped", " ".join(captured).lower())
            cfg2 = yaml.safe_load(config_path.read_text())
            self.assertEqual(cfg2["backend"], "sqlite")

    def test_force_overwrites_existing(self):
        import yaml
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, mode="local")
            with patch("sys.stdout"):
                init_command(args)
            config_path = Path(tmpdir) / ".core-memory.yaml"
            cfg = yaml.safe_load(config_path.read_text())
            cfg["backend"] = "sqlite"
            config_path.write_text(__import__("yaml").dump(cfg))
            force_args = _make_args(root=tmpdir, mode="production", force=True)
            with patch("sys.stdout"):
                init_command(force_args)
            cfg2 = yaml.safe_load(config_path.read_text())
            self.assertEqual(cfg2["graph_backend"], "neo4j")


# ---------------------------------------------------------------------------
# Global flag
# ---------------------------------------------------------------------------

class TestGlobalFlag(unittest.TestCase):
    def test_global_writes_to_home_dir(self):
        from core_memory.cli.handlers.setup import init_command
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home_config = Path(tmpdir) / "config.yaml"
            with patch("core_memory.cli.handlers.setup._USER_CONFIG_PATH", fake_home_config):
                args = _make_args(root=tmpdir, mode="local", global_config=True)
                with patch("sys.stdout"):
                    init_command(args)
            self.assertTrue(fake_home_config.exists())
            self.assertFalse((Path(tmpdir) / ".core-memory.yaml").exists())


# ---------------------------------------------------------------------------
# Settings loader
# ---------------------------------------------------------------------------

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
            (Path(tmpdir) / ".core-memory.yaml").write_text(yaml.dump({"backend": "sqlite", "graph_backend": "neo4j"}))
            with patch("core_memory.config.settings._USER_CONFIG_PATH", Path(tmpdir) / "no.yaml"):
                cfg = load_settings(root=Path(tmpdir))
        self.assertEqual(cfg["backend"], "sqlite")
        self.assertEqual(cfg["graph_backend"], "neo4j")
        self.assertIn("memory", cfg)

    def test_env_var_overrides_config_file(self):
        import yaml
        from core_memory.config.settings import load_settings
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".core-memory.yaml").write_text(yaml.dump({"backend": "sqlite"}))
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
            (Path(tmpdir) / ".core-memory.yaml").write_text(yaml.dump({"backend": "neo4j"}))
            with patch("core_memory.config.settings._USER_CONFIG_PATH", user_cfg):
                cfg = load_settings(root=Path(tmpdir))
        self.assertEqual(cfg["backend"], "neo4j")

    def test_provenance_tracks_sources(self):
        import yaml
        from core_memory.config.settings import load_settings_with_provenance
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".core-memory.yaml"
            config_path.write_text(yaml.dump({"backend": "sqlite"}))
            with patch("core_memory.config.settings._USER_CONFIG_PATH", Path(tmpdir) / "no.yaml"):
                cfg, provenance = load_settings_with_provenance(root=Path(tmpdir))
        self.assertEqual(cfg["backend"], "sqlite")
        # Provenance label for project-local key is the file path
        self.assertEqual(provenance.get("backend"), str(config_path))
        self.assertEqual(provenance.get("graph_backend"), "default")

    def test_provenance_env_var_wins(self):
        from core_memory.config.settings import load_settings_with_provenance
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core_memory.config.settings._USER_CONFIG_PATH", Path(tmpdir) / "no.yaml"):
                with patch("core_memory.config.settings.find_project_config", return_value=None):
                    with patch.dict("os.environ", {"CORE_MEMORY_BACKEND": "postgres"}):
                        cfg, provenance = load_settings_with_provenance()
        self.assertEqual(cfg["backend"], "postgres")
        self.assertIn("CORE_MEMORY_BACKEND", provenance.get("backend", ""))


# ---------------------------------------------------------------------------
# 8b-2 — Doctor profiles
# ---------------------------------------------------------------------------

class TestDoctorProfiles(unittest.TestCase):
    def test_local_profile_hides_mcp_check(self):
        from core_memory.cli_handlers_setup import expanded_doctor
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".beads").mkdir()
            report = expanded_doctor(tmpdir, profile="local")
        self.assertNotIn("mcp", report)

    def test_mcp_profile_includes_mcp_check(self):
        from core_memory.cli_handlers_setup import expanded_doctor
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".beads").mkdir()
            report = expanded_doctor(tmpdir, profile="mcp")
        self.assertIn("mcp", report)

    def test_local_profile_graph_is_ok_not_error(self):
        from core_memory.cli_handlers_setup import expanded_doctor
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".beads").mkdir()
            report = expanded_doctor(tmpdir, profile="local")
        # graph_traversal should never be "error" for local profile
        graph = report.get("graph_traversal", {})
        self.assertNotEqual(graph.get("status"), "error")

    def test_production_profile_does_not_hide_graph(self):
        from core_memory.cli_handlers_setup import expanded_doctor
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".beads").mkdir()
            report = expanded_doctor(tmpdir, profile="production")
        self.assertIn("graph_traversal", report)

    def test_cap_severity_downgrades_error_to_warning(self):
        from core_memory.cli_handlers_setup import _cap_severity
        self.assertEqual(_cap_severity("error", "warning"), "warning")
        self.assertEqual(_cap_severity("warning", "warning"), "warning")
        self.assertEqual(_cap_severity("info", "warning"), "info")

    def test_cap_severity_allows_error_at_error_max(self):
        from core_memory.cli_handlers_setup import _cap_severity
        self.assertEqual(_cap_severity("error", "error"), "error")

    def test_doctor_json_flag_outputs_json(self):
        import io
        from core_memory.cli_handlers_setup import doctor_command
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".beads").mkdir()
            args = _make_args(root=tmpdir, json_output=True)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                doctor_command(args)
        data = json.loads(buf.getvalue())
        self.assertIn("ok", data)

    def test_doctor_human_output_contains_status_icon(self):
        import io
        from core_memory.cli_handlers_setup import _format_doctor_human, expanded_doctor
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".beads").mkdir()
            report = expanded_doctor(tmpdir)
        output = _format_doctor_human(report)
        self.assertIn("✓", output)

    def test_human_format_includes_impact_and_fix_for_warning(self):
        from core_memory.cli_handlers_setup import _format_doctor_human
        report = {
            "profile": "mcp",
            "ok": False,
            "mcp": {
                "status": "error",
                "summary": "not registered",
                "impact": "Claude cannot connect",
                "fix": "core-memory mcp install claude",
            },
        }
        output = _format_doctor_human(report)
        self.assertIn("Impact:", output)
        self.assertIn("Fix:", output)
        self.assertIn("core-memory mcp install claude", output)


# ---------------------------------------------------------------------------
# 8b-3 — Config commands
# ---------------------------------------------------------------------------

class TestConfigCommands(unittest.TestCase):
    def test_config_show_prints_output(self):
        from core_memory.cli_handlers_setup import config_show_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir)
            with patch("core_memory.config.settings._USER_CONFIG_PATH", Path(tmpdir) / "no.yaml"):
                captured = []
                with patch("builtins.print", side_effect=lambda *a, **k: captured.append(str(a))):
                    config_show_command(args)
        output = " ".join(captured)
        self.assertIn("backend", output)
        self.assertIn("graph_backend", output)
        self.assertIn("default", output)

    def test_config_set_updates_file(self):
        import yaml
        from core_memory.cli_handlers_setup import config_set_command
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".core-memory.yaml"
            config_path.write_text(yaml.dump({"backend": "json", "graph_backend": "kuzu"}))
            args = _make_args(root=tmpdir)
            args.key = "graph_backend"
            args.value = "neo4j"
            with patch("builtins.print"):
                config_set_command(args)
            updated = yaml.safe_load(config_path.read_text())
            self.assertEqual(updated["graph_backend"], "neo4j")
            # Non-targeted key must be preserved
            self.assertEqual(updated["backend"], "json")

    def test_config_set_dotted_key(self):
        import yaml
        from core_memory.cli_handlers_setup import config_set_command
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / ".core-memory.yaml"
            config_path.write_text(yaml.dump({"backend": "json", "memory": {"dreamer": True}}))
            args = _make_args(root=tmpdir)
            args.key = "memory.dreamer"
            args.value = "false"
            with patch("builtins.print"):
                config_set_command(args)
            updated = yaml.safe_load(config_path.read_text())
            self.assertFalse(updated["memory"]["dreamer"])

    def test_config_validate_catches_neo4j_without_uri(self):
        import io, yaml
        from core_memory.cli_handlers_setup import config_validate_command
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".core-memory.yaml").write_text(
                yaml.dump({"graph_backend": "neo4j"})
            )
            args = _make_args(root=tmpdir)
            with patch("core_memory.config.settings._USER_CONFIG_PATH", Path(tmpdir) / "no.yaml"):
                buf = io.StringIO()
                with patch("sys.stdout", buf), self.assertRaises(SystemExit) as cm:
                    config_validate_command(args)
                self.assertEqual(cm.exception.code, 1)
                data = json.loads(buf.getvalue())
                self.assertFalse(data["ok"])
                self.assertTrue(any("neo4j" in e.lower() for e in data["errors"]))

    def test_config_validate_passes_for_local_mode(self):
        import yaml
        from core_memory.cli_handlers_setup import config_validate_command
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".core-memory.yaml").write_text(
                yaml.dump({"mode": "local", "backend": "json", "graph_backend": "kuzu"})
            )
            args = _make_args(root=tmpdir)
            with patch("core_memory.config.settings._USER_CONFIG_PATH", Path(tmpdir) / "no.yaml"):
                with patch("builtins.print"):
                    # Should not raise
                    config_validate_command(args)


# ---------------------------------------------------------------------------
# 8b-4 — Demo command
# ---------------------------------------------------------------------------

class TestDemoCommand(unittest.TestCase):
    def test_demo_runs_without_error(self):
        from core_memory.cli_handlers_setup import demo_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, keep=True)
            with patch("builtins.print"):
                demo_command(args)

    def test_demo_writes_beads_to_store(self):
        from core_memory.cli_handlers_setup import demo_command, _DEMO_SESSION
        from core_memory.persistence.store import MemoryStore
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, keep=True)
            with patch("builtins.print"):
                demo_command(args)
            memory = MemoryStore(root=tmpdir)
            beads = memory.query(session_id=_DEMO_SESSION, limit=10)
            self.assertGreaterEqual(len(beads), 1)

    def test_demo_cleans_up_without_keep(self):
        from core_memory.cli_handlers_setup import demo_command, _DEMO_SESSION
        from core_memory.persistence.store import MemoryStore
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, keep=False)
            with patch("builtins.print"):
                demo_command(args)
            memory = MemoryStore(root=tmpdir)
            beads = memory.query(session_id=_DEMO_SESSION, limit=10)
            self.assertEqual(len(beads), 0, "demo beads should be cleaned up without --keep")

    def test_demo_exits_0_on_blank_store(self):
        from core_memory.cli_handlers_setup import demo_command
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(root=tmpdir, keep=False)
            with patch("builtins.print"):
                # Must not raise SystemExit
                demo_command(args)


# ---------------------------------------------------------------------------
# Original expanded doctor tests (8a)
# ---------------------------------------------------------------------------

class TestExpandedDoctor(unittest.TestCase):
    def test_doctor_returns_structured_report(self):
        from core_memory.cli_handlers_setup import expanded_doctor
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".beads").mkdir()
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
                doctor_command(args)


if __name__ == "__main__":
    unittest.main()
