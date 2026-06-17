from __future__ import annotations

import importlib.util
import re
from pathlib import Path


def test_openclaw_plugin_python_modules_are_importable():
    repo_root = Path(__file__).resolve().parents[1]
    plugin_path = repo_root / "plugins" / "openclaw-core-memory-bridge" / "index.js"
    text = plugin_path.read_text(encoding="utf-8")
    modules = sorted(set(re.findall(r'"(core_memory\.integrations\.[^"]+)"', text)))

    assert modules
    assert "core_memory.integrations.openclaw_agent_end_bridge" not in modules
    assert "core_memory.integrations.openclaw_read_bridge" not in modules
    assert "core_memory.integrations.openclaw_compaction_queue" not in modules

    for module in modules:
        assert importlib.util.find_spec(module) is not None, f"{module} is not importable"
