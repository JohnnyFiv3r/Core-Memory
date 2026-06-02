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
from core_memory.retrieval.visible_corpus import build_visible_corpus


def _enrich_anchor_payload(root: str, anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich anchor rows into richer bead-like payloads using one local lookup pass."""
    by_id: dict[str, dict[str, Any]] = {}
    for row in build_visible_corpus(root):
        bid = str(row.get("bead_id") or "")
        bead = row.get("bead") if isinstance(row.get("bead"), dict) else {}
        if bid:
            by_id[bid] = bead

    out: list[dict[str, Any]] = []
    for a in anchors:
        bid = str(a.get("bead_id") or a.get("id") or "")
        bead = by_id.get(bid, {})
        merged = dict(a or {})
        if bead:
            merged.setdefault("title", bead.get("title"))
            merged.setdefault("type", bead.get("type"))
            merged.setdefault("summary", bead.get("summary"))
            merged.setdefault("detail", bead.get("detail"))
            merged.setdefault("created_at", bead.get("created_at"))
            merged.setdefault("status", bead.get("status"))
            merged.setdefault("tags", bead.get("tags"))
            merged.setdefault("session_id", bead.get("session_id"))
        out.append(merged)
    return out


class CoreMemoryRetriever(BaseRetriever):
    """LangChain retriever backed by Core Memory's search pipeline.

    Translates LangChain retriever queries into Core Memory typed searches,
    returning bead results as LangChain Document objects.

    Args:
        root: Path to memory root directory.
        k: Number of results to return.
        explain: Whether to include explanation in metadata.
    """

    root: str = "."
    k: int = 8
    explain: bool = True

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        """Retrieve relevant beads as LangChain Documents."""
        result = memory_tools.search(
            request={"query_text": query, "k": self.k},
            root=self.root,
            explain=self.explain,
        )

        enriched = _enrich_anchor_payload(self.root, list(result.get("results") or []))

        documents = []
        for item in enriched:
            # Build document content from bead fields
            title = item.get("title") or item.get("retrieval_title") or ""
            summary = item.get("summary") or []
            if isinstance(summary, list):
                summary_text = " ".join(str(s) for s in summary)
            else:
                summary_text = str(summary)
            detail = item.get("detail") or ""
            snippet = item.get("snippet") or ""

            content_parts = [f"[{item.get('type', '')}] {title}"]
            if summary_text:
                content_parts.append(summary_text)
            if detail:
                content_parts.append(detail)
            if (not summary_text and not detail) and snippet:
                content_parts.append(str(snippet))

            metadata: dict[str, Any] = {
                "bead_id": item.get("bead_id") or item.get("id") or "",
                "type": item.get("type") or "",
                "status": item.get("status") or "",
                "score": item.get("score", 0.0),
                "source": "core_memory",
            }
            if item.get("created_at"):
                metadata["created_at"] = item["created_at"]
            if item.get("session_id"):
                metadata["session_id"] = item["session_id"]
            if item.get("tags"):
                metadata["tags"] = item["tags"]

            documents.append(Document(page_content="\n".join(content_parts), metadata=metadata))

        return documents
