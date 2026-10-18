"""Search the local RAG snapshot and return source-addressed document chunks."""

from __future__ import annotations

import json

from rag.index import search


def rag_search(query: str, top_k: int = 3) -> str:
    """检索已建索引的项目文档片段，返回文件和行号来源；用于回答本项目资料问题。

    Args:
        query: 检索词或自然语言问题，尽量包含具体概念。
        top_k: 返回片段数量，默认 3，最多 5。
    """
    result = search(query, top_k=top_k)
    if result["stale_files"]:
        raise RuntimeError("RAG 索引已过期；请运行 python -m rag.index build 后再检索")
    return json.dumps(result, ensure_ascii=False)
