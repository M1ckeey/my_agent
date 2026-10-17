"""Deterministic subprocess worker for the PostgreSQL recovery integration test."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from agent.langgraph.checkpoint import open_checkpointer
from agent.langgraph.lg_builder import ResearchGraph
from agent.langgraph.main import _save_report
from agent.research_state import ResearchSubQuestion
from agent.state import ResearchFinding


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["pause", "interrupt", "finish"])
    parser.add_argument("thread_id")
    parser.add_argument("events", type=Path)
    parser.add_argument("reports", type=Path)
    args = parser.parse_args()

    def record(event: str) -> None:
        with args.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "pid": os.getpid()}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def research(tasks: list[str]) -> list[ResearchFinding]:
        query = tasks[0]
        record(f"start:{query}")
        if args.mode == "interrupt" and query == "second":
            while True:
                time.sleep(1)
        return [
            ResearchFinding(
                content=f"answer {query}",
                source="test",
                source_type="test",
                query=query,
            )
        ]

    with open_checkpointer("postgres") as saver:
        graph = ResearchGraph(
            checkpointer=saver,
            interrupt_after=["plan"] if args.mode == "pause" else None,
            planner=lambda _: [
                ResearchSubQuestion(id="q1", question="first"),
                ResearchSubQuestion(id="q2", question="second"),
            ],
            researcher=research,
            critic=lambda _: "stop",
            verbose=False,
        )
        if args.mode == "pause":
            state = graph.run("process recovery", thread_id=args.thread_id)
            assert state.status == "planned"
            record("paused")
        else:
            state = graph.resume(args.thread_id)
            if args.mode == "finish":
                assert state.status == "done"
                _save_report(state.topic, state, args.reports)
                record("finished")


if __name__ == "__main__":
    main()
