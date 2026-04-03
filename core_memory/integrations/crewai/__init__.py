"""CrewAI integration for Core Memory.

Maps Core Memory beads to CrewAI's memory types:
- ShortTermMemory → open/candidate beads (recent, not yet promoted)
- LongTermMemory → promoted/archived beads (validated, durable)
- EntityMemory → beads with populated entities fields

Usage:
    pip install core-memory[crewai]

    from core_memory.integrations.crewai import (
        CoreMemoryShortTerm,
        CoreMemoryLongTerm,
        CoreMemoryEntity,
    )
"""
from core_memory.integrations.crewai.memory import (
    CoreMemoryShortTerm,
    CoreMemoryLongTerm,
    CoreMemoryEntity,
)

__all__ = ["CoreMemoryShortTerm", "CoreMemoryLongTerm", "CoreMemoryEntity"]
