from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_MEMORY = ROOT / "core_memory"

ALLOWED_DIRECT_CHAT_FILES = {
    "core_memory/llm_client.py",
    "core_memory/provider_config.py",
    "core_memory/runtime/semantic_tasks/runtime.py",
}

FORBIDDEN_DIRECT_CHAT_PATTERNS = (
    "from core_memory.llm_client import chat_complete",
    "from .llm_client import chat_complete",
    "import core_memory.llm_client",
    "resolve_chat_config(",
    "chat_complete(",
)

FORBIDDEN_PYDANTICAI_PATTERNS = (
    "import pydantic_ai",
    "from pydantic_ai import",
)


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _python_files() -> list[Path]:
    return sorted(CORE_MEMORY.rglob("*.py"))


def test_llm_chat_provider_calls_stay_behind_semantic_task_runtime_boundary():
    """Semantic paths must not bypass the task runtime for direct chat calls."""

    violations: list[str] = []
    for path in _python_files():
        rel = _relative(path)
        if rel in ALLOWED_DIRECT_CHAT_FILES:
            continue
        source = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_DIRECT_CHAT_PATTERNS:
            if pattern in source:
                violations.append(f"{rel}: contains {pattern!r}")

    assert not violations, "Direct chat-provider calls must route through semantic tasks:\n" + "\n".join(violations)


def test_pydanticai_imports_stay_inside_optional_integration_boundary():
    """Core modules must not side-load the optional PydanticAI dependency."""

    violations: list[str] = []
    for path in _python_files():
        rel = _relative(path)
        if rel.startswith("core_memory/integrations/pydanticai/"):
            continue
        source = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PYDANTICAI_PATTERNS:
            if pattern in source:
                violations.append(f"{rel}: contains {pattern!r}")

    assert not violations, "PydanticAI imports must remain inside integrations/pydanticai:\n" + "\n".join(violations)
