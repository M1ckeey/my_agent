"""Agent 的运行时状态——ReAct 的「记忆」全在这里。

同一份轨迹有两个视图：

  messages  OpenAI chat 格式的对话历史，每次请求原样发给模型
  steps     结构化轨迹，给人看、给日志用、给后面阶段做评测用

两个视图不冗余：messages 是机器要的，steps 是人要的。阶段二接上下文压缩时，
大概率是压缩 messages 但保留 steps。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Step:
    """一个完整的 ReAct 步：想什么 -> 做什么 -> 观察到什么。"""

    index: int
    thought: str
    action: str
    action_input: str
    observation: str | None = None  # finish 那一步没有 observation

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
class AgentState:
    task: str
    messages: list[dict] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
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

    def trace(self) -> str:
        """把整条轨迹拼成人看的文本。"""
        return "\n\n".join(step.render() for step in self.steps)
