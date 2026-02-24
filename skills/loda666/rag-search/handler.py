#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG Search Skill - Minimal RAG retrieval tool

调用现有 search_pipeline.py 执行向量检索
不总结、不改写、不推理，只返回原文检索结果
"""

import sys
import json
from pathlib import Path

# 导入现有检索系统
sys.path.insert(0, '/root/.openclaw/workspace/rag_system/scripts')


def run(query: str, top_k: int = 5) -> dict:
    """
    执行向量检索
    
    Args:
        query: 查询文本
        top_k: 返回结果数量
    
    Returns:
        {
            "results": [
                {
                    "content": "原文内容",
                    "source": "文件名",
                    "page": 1,
                    "score": 0.87
                }
            ],
            "error": null
        }
    """
    try:
        from search_pipeline import SearchPipeline, LiteVectorStore
        from embedding_client import QwenEmbeddingClient, QwenRerankClient
        
        db_path = "/root/.openclaw/workspace/rag_system/data/vectors.db"
        
        # Step 1: 向量召回
        store = LiteVectorStore(db_path)
        emb_client = QwenEmbeddingClient()
        query_emb = emb_client.embed_text(query)
        recall = store.search(query_emb, n_results=min(top_k * 4, 20))
        
        # 准备文档
        documents = [r['content'][:800] for r in recall]
        
        # Step 2: 尝试 Rerank
        rerank_client = QwenRerankClient()
        reranked = rerank_client.rerank(query, documents, top_n=top_k)
        
        # 如果 Rerank 失败，使用召回结果
        if reranked and len(reranked) > 0:
            results = []
            for item in reranked:
                idx = item.get('index', -1)
                if 0 <= idx < len(recall):
                    r = recall[idx]
                    results.append({
                        "content": r['content'],
                        "source": r['metadata'].get('file_name', '')[:30] if r.get('metadata') else "",
                        "clause": "",
                        "regulation_level": r['metadata'].get('regulation_level', '') if r.get('metadata') else "",
                        "score": round(item['score'], 4)
                    })
        else:
            # 回退到向量召回结果
            results = []
            for r in recall[:top_k]:
                results.append({
                    "content": r['content'],
                    "source": r['metadata'].get('file_name', '')[:30] if r.get('metadata') else "",
                    "clause": "",
                    "regulation_level": r['metadata'].get('regulation_level', '') if r.get('metadata') else "",
                    "score": round(r['score'], 4)
                })
        
        return {
            "results": results,
            "error": None,
            "query": query,
            "count": len(results),
            "method": "rerank" if reranked and len(reranked) > 0 else "recall"
        }
        
    except Exception as e:
        return {
            "results": [],
            "error": str(e),
            "query": query,
            "count": 0,
            "method": "error"
        }


if __name__ == "__main__":
    # 命令行测试
    if len(sys.argv) < 2:
        print("用法: python handler.py <查询内容> [top_k]")
        sys.exit(1)
    
    query = sys.argv[1]
    top_k = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    
    result = run(query, top_k)
    print(json.dumps(result, ensure_ascii=False, indent=2))
