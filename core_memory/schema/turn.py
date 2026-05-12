from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

TurnRole = Literal["user", "assistant", "other"]
_ALLOWED_ROLES = {"user", "assistant", "other"}


@dataclass
class Turn:
    """Canonical multi-speaker conversation turn."""

    speaker: str
    content: str = ""
    ts: datetime | str | None = None
    role: TurnRole = "other"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.speaker = str(self.speaker or "").strip()
        if not self.speaker:
            raise ValueError("Turn.speaker must be non-empty")
        if self.role not in _ALLOWED_ROLES:
            raise ValueError("Turn.role must be one of: user, assistant, other")
        self.content = str(self.content or "")
        if self.metadata is None:
            self.metadata = {}
        if not isinstance(self.metadata, dict):
            raise ValueError("Turn.metadata must be a dict")

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        if isinstance(self.ts, datetime):
            out["ts"] = self.ts.isoformat()
        return out


def coerce_turn(value: Turn | dict[str, Any]) -> Turn:
    if isinstance(value, Turn):
        return value
    if isinstance(value, dict):
        return Turn(
            speaker=str(value.get("speaker") or ""),
            content=str(value.get("content") or ""),
            ts=value.get("ts"),
            role=value.get("role") or "other",
            metadata=dict(value.get("metadata") or {}),
        )
    raise TypeError("turns must contain Turn objects or dicts")


def normalize_turns(turns: list[Turn | dict[str, Any]] | tuple[Turn | dict[str, Any], ...] | None) -> list[Turn]:
    if turns is None:
        raise TypeError("process_turn_finalized() requires turns=[...]")
    if not isinstance(turns, (list, tuple)):
        raise TypeError("turns must be a list of Turn objects")
    out = [coerce_turn(t) for t in turns]
    if not out:
        raise ValueError("capture requires at least one turn")
    return out


def turns_from_shortcut(
    *,
    user: str = "",
    assistant: str = "",
    as_user: str | None = None,
    as_assistant: str | None = None,
) -> list[Turn]:
    return [
        Turn(speaker=as_user or "user", role="user", content=str(user or "")),
        Turn(speaker=as_assistant or "assistant", role="assistant", content=str(assistant or "")),
    ]


def serialize_turns(turns: list[Turn | dict[str, Any]] | tuple[Turn | dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [t.to_dict() for t in normalize_turns(turns)]


def turn_speakers(turns: list[Turn | dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for turn in normalize_turns(turns):
        if turn.speaker not in seen:
            seen.add(turn.speaker)
            out.append(turn.speaker)
    return out


def role_content(turns: list[Turn | dict[str, Any]], role: str) -> str:
    return "\n".join(t.content for t in normalize_turns(turns) if t.role == role)


def user_content(turns: list[Turn | dict[str, Any]]) -> str:
    return role_content(turns, "user")


def assistant_content(turns: list[Turn | dict[str, Any]]) -> str:
    return role_content(turns, "assistant")


def turns_summary(turns: list[Turn | dict[str, Any]]) -> str:
    rows = []
    for t in normalize_turns(turns):
        rows.append(f"{t.speaker} [{t.role}]: {t.content}")
    return "\n".join(rows)


LEGACY_TURN_KWARGS_ERROR = (
    "process_turn_finalized() no longer accepts 'user_query' or 'assistant_final'.\n"
    "Pass a turn list instead:\n"
    " from core_memory import Turn\n"
    " process_turn_finalized(\n"
    "   turns=[\n"
    "     Turn(speaker='user', role='user', content=user_query),\n"
    "     Turn(speaker='assistant', role='assistant', content=assistant_final),\n"
    "   ],\n"
    "   ...\n"
    " )\n"
    "Or use the higher-level Memory.capture() shortcut:\n"
    " m.capture(user=user_query, assistant=assistant_final)\n"
    "See docs/concepts/turn_schema.md for the full migration."
)


def reject_legacy_turn_kwargs(kwargs: dict[str, Any], *, surface: str = "process_turn_finalized") -> None:
    if "user_query" in kwargs or "assistant_final" in kwargs:
        if surface == "process_turn_finalized":
            raise TypeError(LEGACY_TURN_KWARGS_ERROR)
        raise TypeError(
            f"{surface}() no longer accepts 'user_query' or 'assistant_final'. "
            "Pass turns=[Turn(...), ...] instead. See docs/concepts/turn_schema.md."
        )
    if kwargs:
        unknown = ", ".join(sorted(str(k) for k in kwargs))
        raise TypeError(f"{surface}() got unexpected keyword argument(s): {unknown}")
