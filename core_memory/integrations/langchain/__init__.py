"""LangChain integration for Core Memory.

Provides BaseMemory and BaseRetriever implementations that bridge
LangChain chains/agents to Core Memory's causal memory system.

Usage:
    pip install core-memory[langchain]

    from core_memory.integrations.langchain import CoreMemory, CoreMemoryRetriever

    # As conversation memory in a chain
    memory = CoreMemory(root="./memory", session_id="chat-001")
    chain = ConversationChain(llm=llm, memory=memory)

    # As a retriever in a RAG chain
    retriever = CoreMemoryRetriever(root="./memory")
    qa_chain = RetrievalQA.from_chain_type(llm=llm, retriever=retriever)
"""
from core_memory.integrations.langchain.memory import CoreMemory
from core_memory.integrations.langchain.retriever import CoreMemoryRetriever

__all__ = ["CoreMemory", "CoreMemoryRetriever"]
