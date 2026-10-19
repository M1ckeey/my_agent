"""Research nodes and the LangGraph topology.

Each graph node is a module-level function. ResearchGraph injects services
through RunnableConfig and compiles the topology with its own checkpointer.
"""

from __future__ import annotations

import os
import json
from uuid import uuid4
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from tools import plan_research

from ..context import ContextManager, Summarizer
from ..state import ResearchFinding
from .checkpoint import memory_checkpointer
from .lg_nodes import (
    _default_research,
    _default_validate,
    _default_write,
    _llm_critic,
    _normalize_critic_result,
    _parse_plan,
    _state_value,
)
from .lg_states import ResearchState, ResearchSubQuestion

Planner = Callable[[str], str | Sequence[ResearchSubQuestion]]
Researcher = Callable[[list[str]], Sequence[ResearchFinding] | str]
Writer = Callable[[ResearchState], str]
Validator = Callable[[ResearchState], Sequence[dict[str, Any]]]
Critic = Callable[
    [ResearchState],
    str | tuple[str, Sequence[dict[str, Any]]] | dict[str, Any],
]


@dataclass(frozen=True)
class ResearchServices:
    """Implementations used by nodes for one graph invocation."""

    planner: Planner
    researcher: Researcher
    writer: Writer
    critic: Critic
    validator: Validator
    context_manager: ContextManager
    summarizer: Summarizer | None
    verbose: bool


def _services(config: RunnableConfig | None) -> ResearchServices:
    return (config or {}).get("configurable", {}).get(
        "research_services", DEFAULT_SERVICES
    )


def _log(services: ResearchServices, message: str) -> None:
    if services.verbose:
        print(f"[graph] {message}", flush=True)


def plan(state: ResearchState, *, config: RunnableConfig) -> dict[str, Any]:
    """Split the topic into subquestions and queue their initial queries."""
    services = _services(config)
    _log(services, "plan：拆分研究问题")
    context = "\n".join(
        f"- {turn['topic']}: {turn['report'][:500]}"
        for turn in state.conversation[-3:]
    )
    planning_request = state.topic
    if context or state.user_instructions:
        planning_request += (
            f"\n\n用户要求：{state.user_instructions}"
            f"\n同一会话的近期研究：\n{context}"
        )
    planned = services.planner(planning_request)
    questions = list(planned) if not isinstance(planned, str) else _parse_plan(planned)
    next_state = ResearchState(topic=state.topic)
    next_state.add_subquestions(questions)
    _log(services, f"plan：生成 {len(questions)} 个子问题，进入逐查询 research")
    return {
        "subquestions": next_state.subquestions,
        "frontier": next_state.frontier,
        "status": "planned",
    }


def _query_key(subquestion_id: str, query: str) -> str:
    return json.dumps([subquestion_id, query], ensure_ascii=False)


def research(state: ResearchState, *, config: RunnableConfig) -> dict[str, Any]:
    """Complete one query per checkpoint so completed work survives interruption."""
    services = _services(config)
    if state.budget_exhausted or not state.frontier:
        return {"status": "researched"}
    item = state.frontier[0]
    remaining = state.frontier[1:]
    query = str(item.get("query", "")).strip()
    subquestion_id = str(item.get("subquestion_id", ""))
    key = _query_key(subquestion_id, query)
    if not query or key in state.completed_queries:
        return {"frontier": remaining, "status": "researched"}
    tasks = [query]
    _log(services, f"research：处理查询 {state.depth + 1}: {query}")
    research_result = services.researcher(tasks) if tasks else []
    if isinstance(research_result, str):
        findings = [
            ResearchFinding(
                content=research_result,
                source="researcher",
                source_type="researcher",
                query=tasks[0] if tasks else "",
            )
        ]
    else:
        findings = list(research_result)
    findings = [
        replace(
            finding,
            subquestion_id=finding.subquestion_id or subquestion_id,
        )
        for finding in findings
    ]
    return {
        "findings": list(state.findings) + findings,
        "frontier": remaining,
        "completed_queries": list(state.completed_queries) + [key],
        "depth": state.depth + 1,
        "token_used": state.token_used + sum(item.token_used for item in findings),
        "status": "researched",
    }


def route_research(state: ResearchState | dict[str, Any]) -> str:
    """Process the next query only while the research budget permits."""
    if (
        _state_value(state, "frontier")
        and _state_value(state, "depth") < _state_value(state, "max_depth")
        and _state_value(state, "token_used") < _state_value(state, "max_tokens")
    ):
        return "research"
    return "critic"


def critic(state: ResearchState, *, config: RunnableConfig) -> dict[str, Any]:
    """Choose whether to add queries or write the report."""
    services = _services(config)
    if state.budget_exhausted:
        signal, next_queries = "stop", []
    else:
        signal, next_queries = _normalize_critic_result(services.critic(state))
    _log(
        services,
        f"critic：{signal}，findings={len(state.findings)}，"
        f"next_queries={len(next_queries)}",
    )
    if signal not in {"revise", "stop"}:
        raise ValueError(
            f"Critic 返回了未知信号 {signal!r}，必须是 revise 或 stop"
        )
    return {
        "critic_signal": signal,
        "next_queries": next_queries,
        "status": "critic",
    }


def route_critic(state: ResearchState | dict[str, Any]) -> str:
    """Only revisit research when a valid new query fits the budget."""
    signal = str(_state_value(state, "critic_signal") or "stop")
    completed = set(_state_value(state, "completed_queries") or [])
    queued = {
        _query_key(str(item.get("subquestion_id", "")), str(item.get("query", "")).strip())
        for item in (_state_value(state, "frontier") or [])
        if isinstance(item, dict)
    }
    novel_query = any(
        isinstance(item, dict)
        and (query := str(item.get("query", "")).strip())
        and _query_key(str(item.get("subquestion_id", "")), query) not in completed | queued
        for item in (_state_value(state, "next_queries") or [])
    )
    if signal == "revise" and (
        _state_value(state, "depth") >= _state_value(state, "max_depth")
        or _state_value(state, "token_used") >= _state_value(state, "max_tokens")
        or not novel_query
    ):
        return "stop"
    return signal


def revise(state: ResearchState) -> dict[str, Any]:
    """Append Critic queries to the research frontier."""
    frontier = list(state.frontier)
    seen = set(state.completed_queries)
    seen.update(
        _query_key(str(item.get("subquestion_id", "")), str(item.get("query", "")).strip())
        for item in frontier
    )
    for item in state.next_queries:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query", "")).strip()
        subquestion_id = str(item.get("subquestion_id", ""))
        key = _query_key(subquestion_id, query)
        if query and key not in seen:
            seen.add(key)
            frontier.append(
                {
                    "subquestion_id": subquestion_id,
                    "query": query,
                }
            )
    return {"frontier": frontier, "next_queries": [], "status": "revised"}


def write(state: ResearchState, *, config: RunnableConfig) -> dict[str, Any]:
    """Compress findings and prepare the Markdown report."""
    services = _services(config)
    _log(services, f"write：整理 {len(state.findings)} 条 findings")
    raw_findings = list(state.findings)
    # The project RAG corpus contains the evaluation questions themselves.
    # Keep those files as evidence only when the user actually asks about this repo.
    local_topic = any(
        marker in state.topic.lower()
        for marker in ("本项目", "这个项目", "此项目", "本仓库", "这个仓库", "my_agent", "readme.md", "docs/")
    )
    report_findings = [
        finding for finding in raw_findings
        if local_topic or not finding.source.startswith("file:")
    ]
    compressed = services.context_manager.compress(
        report_findings,
        topic=state.topic,
        state=state,
        summarizer=services.summarizer,
    )
    write_state = ResearchState(
        **{**state.__dict__, "findings": compressed, "raw_findings": raw_findings}
    )
    return {
        "report": services.writer(write_state),
        "findings": compressed,
        "raw_findings": raw_findings,
        "status": "written",
    }


def validate(state: ResearchState, *, config: RunnableConfig) -> dict[str, Any]:
    """Check subquestion coverage and cited source support."""
    services = _services(config)
    citations = list(services.validator(state))
    _log(services, f"validate：检查 {len(citations)} 条引用")
    conversation = list(state.conversation[-4:])
    conversation.append({"topic": state.topic, "report": state.report[:1500]})
    return {"citations": citations, "conversation": conversation, "status": "done"}


DEFAULT_SERVICES = ResearchServices(
    planner=plan_research,
    researcher=_default_research,
    writer=_default_write,
    critic=_llm_critic,
    validator=_default_validate,
    context_manager=ContextManager(),
    summarizer=None,
    verbose=True,
)

# The workflow is laid out here so its nodes and edges can be read at a glance.
builder = StateGraph(ResearchState)
builder.add_node(plan)
builder.add_node(research)
builder.add_node(critic)
builder.add_node(revise)
builder.add_node(write)
builder.add_node(validate)

builder.add_edge(START, "plan")
builder.add_edge("plan", "research")
builder.add_conditional_edges("research", route_research, {"research": "research", "critic": "critic"})
builder.add_conditional_edges(
    "critic",
    route_critic,
    {"revise": "revise", "stop": "write"},
)
builder.add_edge("revise", "research")
builder.add_edge("write", "validate")
builder.add_edge("validate", END)

# 保留模块级 graph 供旧导入路径使用；它也必须带 checkpoint。
# CLI 的 PostgreSQL saver 由 checkpoint.open_checkpointer() 创建，再注入 ResearchGraph。
graph = builder.compile(checkpointer=memory_checkpointer())


class ResearchGraph:
    """Run the graph with an injected PostgreSQL or in-memory checkpointer."""

    def __init__(
        self,
        *,
        planner: Planner | None = None,
        researcher: Researcher | None = None,
        writer: Writer | None = None,
        critic: Critic | None = None,
        validator: Validator | None = None,
        context_manager: ContextManager | None = None,
        summarizer: Summarizer | None = None,
        verbose: bool = True,
        checkpointer: Any | None = None,
        interrupt_after: list[str] | None = None,
    ) -> None:
        self.services = ResearchServices(
            planner=planner or plan_research,
            researcher=researcher or _default_research,
            writer=writer or _default_write,
            critic=critic or _llm_critic,
            validator=validator or _default_validate,
            context_manager=context_manager or ContextManager(),
            summarizer=summarizer,
            verbose=verbose,
        )
        # Checkpoints are written by LangGraph after each node, not inside plan/research.
        # The CLI injects PostgresSaver here; tests and local runs use InMemorySaver.
        self.checkpointer = checkpointer if checkpointer is not None else memory_checkpointer()
        self.graph = builder.compile(
            checkpointer=self.checkpointer,
            interrupt_after=interrupt_after,
        )

    def _config(
        self, thread_id: str | None = None, *, max_depth: int = 20
    ) -> RunnableConfig:
        configurable: dict[str, Any] = {"research_services": self.services}
        if thread_id:
            configurable["thread_id"] = thread_id
        return {
            "configurable": configurable,
            "recursion_limit": max(100, 4 * max_depth + 10),
        }

    def _plan(self, state: ResearchState) -> dict[str, Any]:
        return plan(state, config=self._config())

    def _research(self, state: ResearchState) -> dict[str, Any]:
        return research(state, config=self._config())

    def _critic(self, state: ResearchState) -> dict[str, Any]:
        return critic(state, config=self._config())

    def _route_critic(self, state: ResearchState | dict[str, Any]) -> str:
        return route_critic(state)

    def _revise(self, state: ResearchState) -> dict[str, Any]:
        return revise(state)

    def _write(self, state: ResearchState) -> dict[str, Any]:
        return write(state, config=self._config())

    def _validate(self, state: ResearchState) -> dict[str, Any]:
        return validate(state, config=self._config())

    def run(
        self, topic: str, *, user_instructions: str = "", thread_id: str | None = None
    ) -> ResearchState:
        """Run the compiled workflow and return its final research state."""
        thread_id = thread_id or uuid4().hex
        previous = self.graph.get_state(self._config(thread_id))
        if previous.values and previous.next:
            raise ValueError(f"线程 {thread_id} 有未完成的研究，请先恢复")
        old = ResearchState(**previous.values) if previous.values else None
        if old and old.state_version != 1:
            raise ValueError(f"不支持的状态版本：{old.state_version}")
        initial = ResearchState(
            topic=topic,
            run_id=uuid4().hex,
            thread_id=thread_id,
            conversation=list(old.conversation) if old else [],
            user_instructions=user_instructions,
            max_tokens=int(os.getenv("RESEARCH_MAX_TOKENS", "20000")),
            max_depth=int(os.getenv("RESEARCH_MAX_DEPTH", "20")),
        )
        result = self.graph.invoke(
            initial, config=self._config(thread_id, max_depth=initial.max_depth)
        )
        return ResearchState(**result)

    def get_state(self, thread_id: str) -> tuple[ResearchState, tuple[str, ...]]:
        snapshot = self.graph.get_state(self._config(thread_id))
        if not snapshot.values:
            raise ValueError(f"找不到线程：{thread_id}")
        state = ResearchState(**snapshot.values)
        if state.state_version != 1:
            raise ValueError(f"不支持的状态版本：{state.state_version}")
        return state, tuple(snapshot.next)

    def resume(self, thread_id: str) -> ResearchState:
        state, pending = self.get_state(thread_id)
        if not pending:
            return state
        result = self.graph.invoke(
            None, config=self._config(thread_id, max_depth=state.max_depth)
        )
        return ResearchState(**result)


def create_graph(**kwargs: Any) -> ResearchGraph:
    """Create a runner for the compiled research workflow."""
    return ResearchGraph(**kwargs)


__all__ = ["ResearchGraph", "ResearchServices", "builder", "graph", "create_graph"]
