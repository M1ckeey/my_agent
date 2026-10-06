"""外层研究流程图。

当前图结构：

    START
      |
     plan
      |
   research
      |
    critic
    /  |  \
   /   |   \
stop  revise  continue
  |      |       |
 write    |    research
  |       |
validate  |
  |       |
 END <----+

``continue`` 继续处理 frontier 中的下一个查询；
``revise`` 先把 Critic 产生的补充查询放回 frontier，再回到 ``research``；
``stop`` 结束研究，进入 ``write → validate``。

每个节点仍然可以复用现有工具和内层 ReAct 子 Agent，后续再增加 checkpoint。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from langgraph.graph import END, START, StateGraph

from tools import plan_research

from .context import ContextManager, Summarizer
from .research_state import ResearchState, ResearchSubQuestion
from .state import ResearchFinding
from .subagent import run_subagents

Planner = Callable[[str], str | Sequence[ResearchSubQuestion]]
Researcher = Callable[[list[str]], Sequence[ResearchFinding] | str]
Writer = Callable[[ResearchState], str]
Validator = Callable[
    [ResearchState],
    Sequence[dict[str, Any]],
]
Critic = Callable[
    [ResearchState],
    str | tuple[str, Sequence[dict[str, Any]]] | dict[str, Any],
]


def _parse_plan(text: str) -> list[ResearchSubQuestion]:
    """解析现有 ``plan_research`` 的文本结果。"""
    questions: list[ResearchSubQuestion] = []
    pattern = re.compile(r"^\s*\d+\.\s+\[([^\]]+)\]\s+(.+?)\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            questions.append(
                ResearchSubQuestion(
                    id=match.group(1).strip(),
                    question=match.group(2).strip(),
                )
            )
    if not questions:
        # 规划器异常时保留主题本身，避免外层图静默空跑。
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), text)
        if first_line:
            questions.append(ResearchSubQuestion(id="q1", question=first_line))
    return questions


def _default_research(tasks: list[str]) -> Sequence[ResearchFinding]:
    """用现有并行子 Agent 完成一轮外层研究。"""
    results = run_subagents(tasks)
    findings: list[ResearchFinding] = []
    for result in results:
        findings.extend(result.findings)
        if result.ok and result.answer and not result.findings:
            findings.append(
                ResearchFinding(
                    content=result.answer,
                    source="subagent",
                    source_type="subagent",
                    query=result.task,
                    task_id=result.task,
                )
            )
        elif not result.ok and result.error and not result.findings:
            findings.append(
                ResearchFinding(
                    content=f"子任务未完成：{result.error}",
                    source="subagent",
                    source_type="subagent",
                    query=result.task,
                    task_id=result.task,
                )
            )
    return findings


def _default_write(state: ResearchState) -> str:
    """把 findings 整理成最小 Markdown 报告。"""
    findings = ContextManager().compact(state.findings)
    lines = [f"# {state.topic}", ""]
    for question in state.subquestions:
        lines.extend([f"## {question.question}", ""])
        matched = [
            finding for finding in findings
            if finding.task_id == question.question
            or finding.query == question.question
        ]
        if not matched:
            lines.append("信息不足。")
        else:
            for finding in matched:
                lines.append(f"- {finding.content}（来源：{finding.source}）")
        lines.append("")
    return "\n".join(lines).strip()


def _default_critic(
    state: ResearchState,
) -> tuple[str, list[dict[str, Any]]]:
    """默认的确定性 Critic：有待查查询就继续，否则停止。"""
    if state.budget_exhausted or not state.frontier:
        return "stop", []
    return "continue", []


def _default_validate(state: ResearchState) -> Sequence[dict[str, Any]]:
    """检查报告中的来源是否存在于 findings。"""
    sources = {finding.source for finding in state.findings}
    citations: list[dict[str, Any]] = []
    pattern = re.compile(r"来源：([^）\n]+)")
    for match in pattern.finditer(state.report):
        source = match.group(1).strip()
        citations.append(
            {
                "source": source,
                "exists": source in sources,
                "note": "" if source in sources else "来源未出现在研究发现中",
            }
        )
    return citations


def _state_value(state: ResearchState | dict[str, Any], name: str) -> Any:
    """兼容 LangGraph 传入 dataclass 或 dict 两种状态形式。"""
    return getattr(state, name) if hasattr(state, name) else state.get(name)


def _normalize_critic_result(
    result: str | tuple[str, Sequence[dict[str, Any]]] | dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(result, str):
        return result, []
    if isinstance(result, tuple):
        signal, queries = result
        return str(signal), list(queries)
    return str(result.get("signal", "stop")), list(result.get("next_queries", []))


class ResearchGraph:
    """最小外层研究图，保留节点函数注入以便测试和逐步替换。"""

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
        self.planner = planner or plan_research
        self.researcher = researcher or _default_research
        self.writer = writer or _default_write
        self.critic = critic or _default_critic
        self.validator = validator or _default_validate
        self.context_manager = context_manager or ContextManager()
        self.summarizer = summarizer
        self.verbose = verbose
        self.graph = self._build()

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[graph] {message}", flush=True)

    def _build(self):
        builder = StateGraph(ResearchState)
        builder.add_node("plan", self._plan)
        builder.add_node("research", self._research)
        builder.add_node("critic", self._critic)
        builder.add_node("revise", self._revise)
        builder.add_node("write", self._write)
        builder.add_node("validate", self._validate)
        builder.add_edge(START, "plan")
        builder.add_edge("plan", "research")
        builder.add_edge("research", "critic")
        builder.add_conditional_edges(
            "critic",
            self._route_critic,
            {
                "continue": "research",
                "revise": "revise",
                "stop": "write",
            },
        )
        builder.add_edge("revise", "research")
        builder.add_edge("write", "validate")
        builder.add_edge("validate", END)
        return builder.compile()

    def _plan(self, state: ResearchState) -> dict[str, Any]:
        self._log("plan：拆分研究问题")
        planned = self.planner(state.topic)
        questions = (
            list(planned)
            if not isinstance(planned, str)
            else _parse_plan(planned)
        )
        next_state = ResearchState(topic=state.topic)
        next_state.add_subquestions(questions)
        self._log(f"plan：生成 {len(questions)} 个子问题，进入并行 research")
        return {
            "subquestions": next_state.subquestions,
            "frontier": next_state.frontier,
            "status": "planned",
        }

    def _research(self, state: ResearchState) -> dict[str, Any]:
        frontier = list(state.frontier)
        tasks = [
            str(item.get("query", "")).strip()
            for item in frontier
            if str(item.get("query", "")).strip()
        ]
        self._log(f"research：并行提交 {len(tasks)} 个子任务")
        research_result = self.researcher(tasks) if tasks else []
        if isinstance(research_result, str):
            findings = [
                ResearchFinding(
                    content=research_result,
                    source="researcher",
                    source_type="researcher",
                    query=task,
                )
            ]
        else:
            findings = list(research_result)
        return {
            "findings": list(state.findings) + findings,
            "frontier": [],
            "depth": state.depth + len(tasks),
            "status": "researched",
        }

    def _critic(self, state: ResearchState) -> dict[str, Any]:
        signal, next_queries = _normalize_critic_result(self.critic(state))
        self._log(
            f"critic：{signal}，findings={len(state.findings)}，"
            f"next_queries={len(next_queries)}"
        )
        if signal not in {"continue", "revise", "stop"}:
            raise ValueError(
                f"Critic 返回了未知信号 {signal!r}，"
                "必须是 continue、revise 或 stop"
            )
        return {
            "critic_signal": signal,
            "next_queries": next_queries,
            "status": "critic",
        }

    def _route_critic(self, state: ResearchState | dict[str, Any]) -> str:
        signal = str(_state_value(state, "critic_signal") or "stop")
        if signal == "continue" and (
            _state_value(state, "depth") >= _state_value(state, "max_depth")
            or not _state_value(state, "frontier")
        ):
            return "stop"
        return signal

    def _revise(self, state: ResearchState) -> dict[str, Any]:
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
        return {
            "frontier": frontier,
            "next_queries": [],
            "status": "revised",
        }

    def _write(self, state: ResearchState) -> dict[str, Any]:
        self._log(f"write：整理 {len(state.findings)} 条 findings")
        raw_findings = list(state.findings)
        compressed = self.context_manager.compress(
            raw_findings,
            topic=state.topic,
            state=state,
            summarizer=self.summarizer,
        )
        write_state = ResearchState(
            **{
                **state.__dict__,
                "findings": compressed,
                "raw_findings": raw_findings,
            }
        )
        report = self.writer(write_state)
        return {
            "report": report,
            "findings": compressed,
            "raw_findings": raw_findings,
            "status": "written",
        }

    def _validate(self, state: ResearchState) -> dict[str, Any]:
        citations = list(self.validator(state))
        self._log(f"validate：检查 {len(citations)} 条引用")
        return {
            "citations": citations,
            "status": "done",
        }

    def run(self, topic: str, *, user_instructions: str = "") -> ResearchState:
        """运行最小外层图，并把 LangGraph 的结果还原为 ResearchState。"""
        initial = ResearchState(
            topic=topic,
            user_instructions=user_instructions,
        )
        result = self.graph.invoke(initial)
        return ResearchState(**result)


def create_graph(**kwargs: Any) -> ResearchGraph:
    """创建外层研究图。"""
    return ResearchGraph(**kwargs)


__all__ = ["ResearchGraph", "create_graph"]
