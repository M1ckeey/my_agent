"""Research nodes and the LangGraph topology.

Each graph node is a module-level function. ResearchGraph supplies optional
implementations through RunnableConfig, so the compiled graph is shared.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from tools import plan_research

from ..context import ContextManager, Summarizer
from ..state import ResearchFinding
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
    planned = services.planner(state.topic)
    questions = list(planned) if not isinstance(planned, str) else _parse_plan(planned)
    next_state = ResearchState(topic=state.topic)
    next_state.add_subquestions(questions)
    _log(services, f"plan：生成 {len(questions)} 个子问题，进入并行 research")
    return {
        "subquestions": next_state.subquestions,
        "frontier": next_state.frontier,
        "status": "planned",
    }


def research(state: ResearchState, *, config: RunnableConfig) -> dict[str, Any]:
    """Run pending queries and associate findings with subquestions."""
    services = _services(config)
    task_items = [
        item for item in state.frontier if str(item.get("query", "")).strip()
    ]
    tasks = [str(item.get("query", "")).strip() for item in task_items]
    _log(services, f"research：并行提交 {len(tasks)} 个子任务")
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
    subquestion_by_query = {
        str(item.get("query", "")).strip(): str(item.get("subquestion_id", ""))
        for item in task_items
    }
    findings = [
        replace(
            finding,
            subquestion_id=(
                finding.subquestion_id
                or subquestion_by_query.get(finding.query, "")
            ),
        )
        for finding in findings
    ]
    return {
        "findings": list(state.findings) + findings,
        "frontier": [],
        "depth": state.depth + len(tasks),
        "token_used": state.token_used + sum(item.token_used for item in findings),
        "status": "researched",
    }


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
    if signal == "revise" and (
        _state_value(state, "depth") >= _state_value(state, "max_depth")
        or _state_value(state, "token_used") >= _state_value(state, "max_tokens")
        or not any(
            isinstance(item, dict) and str(item.get("query", "")).strip()
            for item in (_state_value(state, "next_queries") or [])
        )
    ):
        return "stop"
    return signal


def revise(state: ResearchState) -> dict[str, Any]:
    """Append Critic queries to the research frontier."""
    frontier = list(state.frontier)
    for item in state.next_queries:
        query = str(item.get("query", "")).strip()
        if query:
            frontier.append(
                {
                    "subquestion_id": str(item.get("subquestion_id", "")),
                    "query": query,
                }
            )
    return {"frontier": frontier, "next_queries": [], "status": "revised"}


def write(state: ResearchState, *, config: RunnableConfig) -> dict[str, Any]:
    """Compress findings and prepare the Markdown report."""
    services = _services(config)
    _log(services, f"write：整理 {len(state.findings)} 条 findings")
    raw_findings = list(state.findings)
    compressed = services.context_manager.compress(
        raw_findings,
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
    return {"citations": citations, "status": "done"}


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
builder.add_edge("research", "critic")
builder.add_conditional_edges(
    "critic",
    route_critic,
    {"revise": "revise", "stop": "write"},
)
builder.add_edge("revise", "research")
builder.add_edge("write", "validate")
builder.add_edge("validate", END)

graph = builder.compile()


class ResearchGraph:
    """Compatibility runner with injectable node implementations for tests."""

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
        self.graph = graph

    def _config(self) -> RunnableConfig:
        return {"configurable": {"research_services": self.services}}

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

    def run(self, topic: str, *, user_instructions: str = "") -> ResearchState:
        """Run the compiled workflow and return its final research state."""
        initial = ResearchState(
            topic=topic,
            user_instructions=user_instructions,
            max_tokens=int(os.getenv("RESEARCH_MAX_TOKENS", "20000")),
            max_depth=int(os.getenv("RESEARCH_MAX_DEPTH", "20")),
        )
        result = self.graph.invoke(initial, config=self._config())
        return ResearchState(**result)


def create_graph(**kwargs: Any) -> ResearchGraph:
    """Create a runner for the compiled research workflow."""
    return ResearchGraph(**kwargs)


__all__ = ["ResearchGraph", "ResearchServices", "builder", "graph", "create_graph"]
