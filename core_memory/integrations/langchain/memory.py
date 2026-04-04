"""LangChain BaseMemory implementation for Core Memory.

Maps LangChain's memory protocol to Core Memory's causal memory system:
- load_memory_variables() → continuity injection + optional search
- save_context() → process_turn_finalized (per-turn write boundary)
- clear() → process_flush (session-end flush boundary)
"""
from __future__ import annotations

import uuid
from typing import Any

try:
    from langchain_core.memory import BaseMemory
except ImportError:
    raise ImportError(
        "LangChain integration requires langchain-core. "
        "Install with: pip install core-memory[langchain]"
    )

from core_memory.integrations.api import IntegrationContext
from core_memory.runtime.engine import process_flush, process_turn_finalized, process_session_start
from core_memory.write_pipeline.continuity_injection import load_continuity_injection


class CoreMemory(BaseMemory):
    """LangChain memory backed by Core Memory's causal bead system.

    Injects rolling-window continuity context into the chain's prompt
    and writes each turn back to Core Memory for future recall.

    Args:
        root: Path to memory root directory.
        session_id: Session identifier for grouping turns.
        memory_key: Key used in load_memory_variables output.
        input_key: Key for user input in save_context.
        output_key: Key for AI output in save_context.
        max_items: Max continuity records to inject.
        return_messages: Whether to return as message objects (not supported, returns str).
    """

    root: str = "."
    session_id: str = "langchain-default"
    memory_key: str = "memory"
    input_key: str = "input"
    output_key: str = "output"
    max_items: int = 80
    return_messages: bool = False
    _turn_counter: int = 0

    class Config:
        arbitrary_types_allowed = True

    @property
    def memory_variables(self) -> list[str]:
        return [self.memory_key]

    def load_memory_variables(self, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        """Load continuity context from Core Memory.

        Returns a dict with {memory_key: str} containing the rolling-window
        continuity injection text.
        """
        try:
            process_session_start(
                root=self.root,
                session_id=self.session_id,
                source="langchain_memory.load_memory_variables",
                max_items=self.max_items,
            )
        except Exception:
            # fail-open on session-start boundary creation
            pass

        result = load_continuity_injection(
            self.root,
            max_items=self.max_items,
            session_id=self.session_id,
            ensure_session_start=True,
        )
        records = result.get("records") or []

        if not records:
            return {self.memory_key: ""}

        lines = []
        for r in records:
            typ = r.get("type", "")
            title = r.get("title", "")
            summary = " ".join(r.get("summary") or []) if isinstance(r.get("summary"), list) else str(r.get("summary", ""))
            lines.append(f"[{typ}] {title}: {summary}")

        return {self.memory_key: "\n".join(lines)}

    def save_context(self, inputs: dict[str, Any], outputs: dict[str, str]) -> None:
        """Write the completed turn to Core Memory."""
        user_query = str(inputs.get(self.input_key) or inputs.get("question") or "")
        assistant_final = str(outputs.get(self.output_key) or outputs.get("answer") or "")

        if not user_query and not assistant_final:
            return

        self._turn_counter += 1
        turn_id = f"lc-turn-{self._turn_counter}-{uuid.uuid4().hex[:6]}"

        ictx = IntegrationContext(
            framework="langchain",
            source="langchain_memory",
            adapter_kind="memory",
            adapter_status="active",
        )

        process_turn_finalized(
            root=self.root,
            session_id=self.session_id,
            turn_id=turn_id,
            transaction_id=f"tx-{turn_id}",
            user_query=user_query,
            assistant_final=assistant_final,
            metadata=ictx.to_metadata(),
        )

    def clear(self) -> None:
        """Session-end boundary hook.

        LangChain's clear is treated as a flush boundary for this session.
        """
        process_flush(
            root=self.root,
            session_id=self.session_id,
            promote=True,
            token_budget=1200,
            max_beads=12,
            source="langchain_clear",
        )
