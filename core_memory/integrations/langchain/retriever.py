"""LangChain BaseRetriever implementation for Core Memory.

Allows Core Memory to be used as a retriever in RAG chains,
bridging causal memory search into LangChain's retrieval interface.
"""
from __future__ import annotations

from typing import Any

try:
    from langchain_core.callbacks import CallbackManagerForRetrieverRun
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever
except ImportError:
    raise ImportError(
        "LangChain integration requires langchain-core. "
        "Install with: pip install core-memory[langchain]"
    )

from core_memory.retrieval.tools import memory as memory_tools


class CoreMemoryRetriever(BaseRetriever):
    """LangChain retriever backed by Core Memory's search pipeline.

    Translates LangChain retriever queries into Core Memory typed searches,
    returning bead results as LangChain Document objects.

    Args:
        root: Path to memory root directory.
        k: Number of results to return.
        explain: Whether to include explanation in metadata.
        search_mode: "search" for typed search, "reason" for causal reasoning.
    """

    root: str = "."
    k: int = 8
    explain: bool = True
    search_mode: str = "search"

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        """Retrieve relevant beads as LangChain Documents."""
        if self.search_mode == "reason":
            result = memory_tools.reason(
                query=query,
                root=self.root,
                k=self.k,
                explain=self.explain,
            )
        else:
            result = memory_tools.search(
                form_submission={"query_text": query, "k": self.k},
                root=self.root,
                explain=self.explain,
            )

        documents = []
        for item in result.get("results") or []:
            # Build document content from bead fields
            title = item.get("title") or item.get("retrieval_title") or ""
            summary = item.get("summary") or []
            if isinstance(summary, list):
                summary_text = " ".join(str(s) for s in summary)
            else:
                summary_text = str(summary)
            detail = item.get("detail") or ""

            content_parts = [f"[{item.get('type', '')}] {title}"]
            if summary_text:
                content_parts.append(summary_text)
            if detail:
                content_parts.append(detail)

            metadata: dict[str, Any] = {
                "bead_id": item.get("bead_id") or item.get("id") or "",
                "type": item.get("type") or "",
                "status": item.get("status") or "",
                "score": item.get("score", 0.0),
                "source": "core_memory",
            }
            if item.get("created_at"):
                metadata["created_at"] = item["created_at"]

            documents.append(Document(page_content="\n".join(content_parts), metadata=metadata))

        return documents
