"""外层研究流程状态。

``AgentState`` 服务于单个 Agent 的 ReAct 对话；``ResearchState`` 服务于
整场研究流程。两者分开保存，后续可以直接把 ResearchState 交给 LangGraph。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .state import ResearchFinding


@dataclass
class ResearchSubQuestion:
    """外层 Planner 产出的一个子问题。"""

    id: str
    question: str
    rationale: str = ""


@dataclass
class ResearchState:
    """一场研究任务的外层状态，不保存 Agent 的原始 messages。"""

    topic: str
    user_instructions: str = ""

    # Planner / Researcher 状态
    subquestions: list[ResearchSubQuestion] = field(default_factory=list)
    findings: list[ResearchFinding] = field(default_factory=list)
    raw_findings: list[ResearchFinding] = field(default_factory=list)
    frontier: list[dict[str, Any]] = field(default_factory=list)

    # 循环与预算状态
    depth: int = 0
    token_used: int = 0
    max_tokens: int = 20000
    max_depth: int = 20
    critic_signal: str = ""
    next_queries: list[dict[str, Any]] = field(default_factory=list)

    # 输出状态
    report: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"
    error: str | None = None

    def add_subquestions(self, questions: list[ResearchSubQuestion]) -> None:
        """保存子问题，并把每个子问题的初始查询放入 frontier。"""
        self.subquestions.extend(questions)
        self.frontier.extend(
            {"subquestion_id": question.id, "query": question.question}
            for question in questions
        )

    def add_findings(self, findings: list[ResearchFinding]) -> None:
        """追加外层研究材料；去重和压缩由 ContextManager 负责。"""
        self.findings.extend(findings)

    def add_query(self, query: str, *, subquestion_id: str = "") -> None:
        """把一个补充查询加入待研究队列。"""
        query = query.strip()
        if query:
            self.frontier.append(
                {"subquestion_id": subquestion_id, "query": query}
            )

    def pop_query(self) -> dict[str, Any] | None:
        """取出下一个待研究查询，并推进外层研究深度。"""
        if not self.frontier:
            return None
        if self.depth >= self.max_depth:
            return None
        self.depth += 1
        return self.frontier.pop(0)

    def apply_critic(
        self,
        signal: str,
        *,
        next_queries: list[dict[str, Any]] | None = None,
    ) -> None:
        """保存 Critic 路由结果，并把补充查询放回 frontier。"""
        if signal not in {"continue", "revise", "stop"}:
            raise ValueError(
                "critic signal 必须是 continue、revise 或 stop"
            )
        self.critic_signal = signal
        self.next_queries = list(next_queries or [])
        if signal == "revise":
            for item in self.next_queries:
                self.add_query(
                    str(item.get("query", "")),
                    subquestion_id=str(item.get("subquestion_id", "")),
                )

    @property
    def budget_exhausted(self) -> bool:
        """是否达到外层最大研究深度。"""
        return self.depth >= self.max_depth or self.token_used >= self.max_tokens

    @property
    def finished(self) -> bool:
        """是否已经生成报告或进入失败状态。"""
        return bool(self.report) or self.status in {"done", "failed"}


__all__ = ["ResearchState", "ResearchSubQuestion"]
