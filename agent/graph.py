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

import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from langgraph.graph import END, START, StateGraph

from tools import plan_research

from .context import ContextManager, Summarizer
from .llm import call_llm, final_text, get_client
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
        if result.findings:
            findings.extend(
                replace(
                    finding,
                    token_used=(
                        finding.token_used
                        or (result.token_used if index == 0 else 0)
                    ),
                )
                for index, finding in enumerate(result.findings)
            )
        if result.ok and result.answer and not result.findings:
            findings.append(
                ResearchFinding(
                    content=result.answer,
                    source="subagent",
                    source_type="subagent",
                    query=result.task,
                    task_id=result.task,
                    token_used=result.token_used,
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
                    token_used=result.token_used,
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
            if finding.subquestion_id == question.id
            or finding.task_id == question.question
            or finding.query == question.question
        ]
        if not matched:
            lines.append("信息不足。")
        else:
            for finding in matched:
                lines.append(f"- {finding.content}（来源：{finding.source}）")
        lines.append("")
    return "\n".join(lines).strip()


def _deterministic_critic(
    state: ResearchState,
) -> tuple[str, list[dict[str, Any]]]:
    """默认的确定性 Critic：有待查查询就继续，否则停止。"""
    if state.budget_exhausted or not state.frontier:
        return "stop", []
    return "continue", []


def _llm_critic(state: ResearchState) -> tuple[str, list[dict[str, Any]]]:
    """让模型根据当前证据判断是否继续研究。失败时回退到确定性规则。"""
    if state.budget_exhausted or not state.frontier:
        return "stop", []

    findings = "\n".join(
        f"- [{finding.subquestion_id or 'unknown'}] {finding.content[:1200]}"
        for finding in state.findings
    )
    payload = {
        "topic": state.topic,
        "subquestions": [question.__dict__ for question in state.subquestions],
        "findings": findings,
        "frontier": state.frontier,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是研究流程 Critic。只根据输入判断证据是否足够。"
                "只输出 JSON，不要 markdown："
                '{"signal":"stop|continue|revise",'
                '"next_queries":[{"subquestion_id":"q1","query":"..."}]}。'
                "stop 表示证据足够；continue 表示继续处理现有 frontier；"
                "revise 表示需要补充查询。补充查询必须具体且不可重复。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        response = call_llm(
            get_client(),
            os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            messages,
            tools=[],
            max_tokens=800,
        )
        text = final_text(response).strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        data = json.loads(text)
        signal = str(data.get("signal", "stop"))
        queries = data.get("next_queries", [])
        if signal not in {"stop", "continue", "revise"} or not isinstance(queries, list):
            raise ValueError("Critic 返回格式不正确")
        return signal, queries
    except Exception:
        return _deterministic_critic(state)


def _default_validate(state: ResearchState) -> Sequence[dict[str, Any]]:
    """检查子问题覆盖、来源存在性，以及报告内容是否来自 findings。"""
    sources = {finding.source for finding in state.findings}
    citations: list[dict[str, Any]] = []
    for question in state.subquestions:
        matched = [
            finding for finding in state.findings
            if finding.subquestion_id == question.id
            or finding.task_id == question.question
            or finding.query == question.question
        ]
        citations.append(
            {
                "type": "coverage",
                "subquestion_id": question.id,
                "source": question.question,
                "exists": bool(matched),
                "supported": bool(matched),
                "note": "" if matched else "子问题没有对应研究发现",
            }
        )
    pattern = re.compile(r"来源：([^）\n]+)")
    for match in pattern.finditer(state.report):
        source = match.group(1).strip()
        source_findings = [finding for finding in state.findings if finding.source == source]
        report_text = state.report
        supported = any(finding.content[:120] in report_text for finding in source_findings)
        citations.append(
            {
                "type": "citation",
                "source": source,
                "exists": source in sources,
                "supported": supported,
                "note": (
                    ""
                    if source in sources and supported
                    else "来源未出现在研究发现中"
                    if source not in sources
                    else "报告论断未匹配到该来源材料"
                ),
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
        self.critic = critic or _llm_critic
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
        task_items = [
            item for item in frontier if str(item.get("query", "")).strip()
        ]
        tasks = [str(item.get("query", "")).strip() for item in task_items]
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
            "token_used": state.token_used + sum(
                finding.token_used for finding in findings
            ),
            "status": "researched",
        }

    def _critic(self, state: ResearchState) -> dict[str, Any]:
        if state.budget_exhausted:
            signal, next_queries = "stop", []
        else:
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
            or _state_value(state, "token_used") >= _state_value(state, "max_tokens")
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
            max_tokens=int(os.getenv("RESEARCH_MAX_TOKENS", "20000")),
            max_depth=int(os.getenv("RESEARCH_MAX_DEPTH", "20")),
        )
        result = self.graph.invoke(initial)
        return ResearchState(**result)


def create_graph(**kwargs: Any) -> ResearchGraph:
    """创建外层研究图。"""
    return ResearchGraph(**kwargs)


__all__ = ["ResearchGraph", "create_graph"]
