from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Candidate:
    bead_id: str
    sem_score: float = 0.0
    sem_rank: int = 10**9
    lex_score: float = 0.0
    lex_rank: int = 10**9
    fused_score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)
