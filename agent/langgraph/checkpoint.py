"""Checkpoint backends for local runs and persistent research threads."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def _serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("agent.langgraph.lg_states", "ResearchState"),
            ("agent.langgraph.lg_states", "ResearchSubQuestion"),
            ("agent.state", "ResearchFinding"),
        ]
    )


def memory_checkpointer() -> InMemorySaver:
    return InMemorySaver(serde=_serializer())


@contextmanager
def open_checkpointer(backend: str | None = None) -> Iterator[Any]:
    """Open a saver for the lifetime of a graph run or CLI session."""
    selected = (backend or os.getenv("RESEARCH_CHECKPOINT_BACKEND", "memory")).lower()
    if selected == "memory":
        yield memory_checkpointer()
        return
    if selected != "postgres":
        raise ValueError("RESEARCH_CHECKPOINT_BACKEND 必须是 memory 或 postgres")
    database_url = os.getenv("RESEARCH_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("PostgreSQL 模式需要 RESEARCH_DATABASE_URL 或 DATABASE_URL")
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL 模式需要安装 requirements.txt 中的 langgraph-checkpoint-postgres"
        ) from exc
    with Connection.connect(
        database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as connection:
        saver = PostgresSaver(connection, serde=_serializer())
        saver.setup()
        yield saver
