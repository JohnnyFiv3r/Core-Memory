from __future__ import annotations

import importlib.util
import json
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


def test_openclaw_plugin_registers_streaming_message_fallback_hooks():
    repo_root = Path(__file__).resolve().parents[1]
    plugin_path = repo_root / "plugins" / "openclaw-core-memory-bridge" / "index.js"
    text = plugin_path.read_text(encoding="utf-8")

    assert 'api.on("agent_end"' in text
    assert 'api.on("message_received"' in text
    assert 'api.on("message_sent"' in text
    assert "enableMessageTurnFallback" in text
    assert "fallback_result" in text


def test_openclaw_plugin_loads_skill_instructions_from_core_memory_repo():
    repo_root = Path(__file__).resolve().parents[1]
    plugin_path = repo_root / "plugins" / "openclaw-core-memory-bridge" / "index.js"
    text = plugin_path.read_text(encoding="utf-8")

    assert "cfg.coreMemoryRepo" in text
    assert 'docs", "integrations", "openclaw", "core-memory-skill-instructions.md"' in text
    assert "../../docs/integrations/openclaw/core-memory-skill-instructions.md" not in text


def test_openclaw_plugin_injects_generated_agent_authoring_contract():
    repo_root = Path(__file__).resolve().parents[1]
    plugin_path = repo_root / "plugins" / "openclaw-core-memory-bridge" / "index.js"
    text = plugin_path.read_text(encoding="utf-8")

    assert "spawnSync" in text
    assert "core_memory.schema.agent_authoring_spec" in text
    assert "BEAD_AUTHORING_SPEC" in text
    assert "Agent-Authored Turn Memory Contract" in text


def test_openclaw_plugin_manifest_allows_message_fallback_config():
    repo_root = Path(__file__).resolve().parents[1]
    manifest_path = repo_root / "plugins" / "openclaw-core-memory-bridge" / "openclaw.plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    props = manifest["configSchema"]["properties"]

    assert props["enableMessageTurnFallback"]["type"] == "boolean"
    assert props["messageTurnFallbackDelayMs"]["type"] == "number"
    assert props["enableHostedCoreMemoryClone"]["type"] == "boolean"
    assert props["hostedCoreMemoryUrl"]["type"] == "string"
    assert props["enableLocalCoreMemoryWrite"]["type"] == "boolean"
