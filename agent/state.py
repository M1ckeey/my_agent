"""Agent 的运行时状态：ReAct 的记忆、轨迹和结构化研究发现。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Step:
    """一个完整的 ReAct 步骤：想法、动作和观察结果。"""

    index: int
    thought: str
    action: str
    action_input: str
    observation: str | None = None

    def render(self) -> str:
        lines = [
            f"Thought: {self.thought}",
            f"Action: {self.action}",
            f"Action Input: {self.action_input}",
        ]
        if self.observation is not None:
            lines.append(f"Observation: {self.observation}")
        return "\n".join(lines)


@dataclass
class ResearchFinding:
    """一条可供后续整理和压缩的结构化研究发现。"""

    content: str
    source: str
    source_type: str
    query: str = ""
    task_id: str = ""
    confidence: float = 0.5


@dataclass
class AgentState:
    task: str
    messages: list[dict] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    findings: list[ResearchFinding] = field(default_factory=list)
    final_answer: str | None = None

    @property
    def finished(self) -> bool:
        return self.final_answer is not None

    def add_step(
        self,
        *,
        thought: str,
        action: str,
        action_input: str,
        observation: str | None = None,
    ) -> Step:
        step = Step(
            index=len(self.steps) + 1,
            thought=thought,
            action=action,
            action_input=action_input,
            observation=observation,
        )
        self.steps.append(step)
        return step

    def add_finding(
        self,
        *,
        content: str,
        source: str,
        source_type: str,
        query: str = "",
        task_id: str = "",
        confidence: float = 0.5,
    ) -> ResearchFinding:
        finding = ResearchFinding(
            content=content,
            source=source,
            source_type=source_type,
            query=query,
            task_id=task_id,
            confidence=confidence,
        )
        self.findings.append(finding)
        return finding

    def trace(self) -> str:
        return "\n\n".join(step.render() for step in self.steps)


__all__ = ["AgentState", "ResearchFinding", "Step"]
