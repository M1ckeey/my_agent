"""子 agent：把子任务跑成一次独立的、上下文隔离的 ReAct 循环，并支持并行 fan-out。

和主 agent 的关键区别有三条，每一条都是「上下文隔离」这个目的的直接推论：

  1. **自己的 messages。** 子 agent 看不到主对话的任何历史，主 agent 也拿不到它的
     中间工具结果——只有最后那句结论回流。一次 web_search 是 2665 字，四个子 agent
     各查几轮就是几万字；让这些原文顺着子 agent 爬进主上下文，等于把并行省下的
     时间全部还给上下文长度。
  2. **自己的工具集，且不含 delegate。** 这是递归防护：子 agent 再派子 agent，
     调用量按 fan-out 的幂次涨，收益却接近于零（子任务已经是拆好的）。
  3. **不流式打印。** 多个子 agent 同时往一个终端写会糊成一团，所以子 agent 静默
     跑，由 fan-out 统一打「出发 / 完成」两行。

max_steps 默认给 5（主循环是 8）也是同一个道理：子任务的目标是「一个问题查清楚」，
不是「完成一项研究」，步数多了只会绕圈烧钱。5 这个数是实测出来的——一次检索+
一次换个说法的补查+收尾，正好用掉 3~5 步。
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from prompts import SUBAGENT_SYSTEM
from tools import run_tool, to_openai_tools

from .hooks import install_default_hooks, trigger_hooks
from .llm import assistant_message, call_llm, final_text, get_client
from .state import AgentState, ResearchFinding

# 子 agent 能用的工具。**不含 delegate / plan_research**：前者防递归，后者是主
# agent 的职责——子任务已经是拆好的，不需要再拆一次。
SUBAGENT_TOOLS = ("web_search", "arxiv_search", "read_file", "calculator")

# 单条结论的字符上限。提示词里已经要求写短，这里是硬兜底：四个子 agent 的结论会
# 一起进主上下文，一条失控就能把主 agent 挤爆。
_ANSWER_LIMIT = 2000


@dataclass
class SubagentResult:
    """一个子 agent 跑完的结果。失败也返回对象，不抛异常。"""

    task: str
    answer: str = ""
    steps: int = 0
    error: str | None = None
    findings: list[ResearchFinding] = field(default_factory=list)
    retry_count: int = 0
    error_history: list[str] = field(default_factory=list)
    token_used: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


def _verbose() -> bool:
    return os.getenv("SUBAGENT_VERBOSE", "1") not in {"", "0", "false", "False"}


def run_subagent(task: str, *, model: str, max_steps: int) -> SubagentResult:
    """跑一个子任务。用一次独立的、上下文隔离的循环。"""
    client = get_client()
    tools = to_openai_tools(only=SUBAGENT_TOOLS)
    messages: list[dict] = [
        {"role": "system", "content": SUBAGENT_SYSTEM},
        {"role": "user", "content": task},
    ]
    sub_state = AgentState(task=task, messages=messages)
    install_default_hooks()
    trigger_hooks("UserPromptSubmit", task)

    for turn in range(1, max_steps + 1):
        try:
            # on_text 不传 → 静默拼装。并行时多个子 agent 同时流式写终端会糊成一团。
            trigger_hooks("BeforeModel", sub_state)
            message = call_llm(client, model, messages, tools)
            sub_state.token_used += message.total_tokens
        except Exception as exc:  # noqa: BLE001
            # 单个子 agent 的网络失败不能拖垮整批（对应 return_exceptions 语义）
            return SubagentResult(
                task=task,
                steps=turn - 1,
                error=f"{type(exc).__name__}: {exc}",
                findings=sub_state.findings,
            )

        # 没有 tool_calls = 子 agent 认为查清楚了，这条消息就是结论
        if not message.tool_calls:
            answer = final_text(message)
            if not answer:
                return SubagentResult(
                    task=task,
                    steps=turn,
                    error="子 agent 没有给出结论",
                    findings=sub_state.findings,
                )
            continuation = trigger_hooks("Stop", sub_state)
            if continuation is not None:
                messages.append({"role": "user", "content": str(continuation)})
                continue
            return SubagentResult(
                task=task,
                answer=answer,
                steps=turn,
                findings=sub_state.findings,
                token_used=sub_state.token_used,
            )

        messages.append(assistant_message(message))
        # 一轮里的多个 tool_call 逐个执行、逐个回填：每个 tool_call_id 都必须有
        # 且只有一条对应的 tool 消息。子 agent 的工具集里没有 delegate，所以这里
        # 不会递归。
        for call in message.tool_calls:
            blocked = trigger_hooks("PreToolUse", sub_state, call)
            if blocked is not None:
                observation = str(blocked)
            else:
                observation = run_tool(call.function.name, call.function.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": observation,
                }
            )
            if blocked is None:
                trigger_hooks("PostToolUse", sub_state, call, observation)

    return SubagentResult(
        task=task,
        steps=max_steps,
        error=f"达到最大步数 {max_steps}，子任务未完成",
        findings=sub_state.findings,
        token_used=sub_state.token_used,
    )


def run_subagents(
    tasks: list[str],
    *,
    model: str | None = None,
    max_steps: int | None = None,
    max_workers: int | None = None,
    max_retries: int | None = None,
) -> list[SubagentResult]:
    """并行跑一批子任务，返回的结果**顺序与 tasks 一致**。

    顺序保持住是有意的：调用方拿到的第 i 份结论必须对应第 i 个任务，否则聚合时
    会把结论安到错误的标题下面。所以内部按索引回填，而不是按完成先后 append。
    """
    model = model or os.getenv("LLM_MODEL", "deepseek-v4-flash")
    max_steps = max_steps or int(os.getenv("SUBAGENT_MAX_STEPS", "5"))
    max_workers = max_workers or int(os.getenv("SUBAGENT_MAX_WORKERS", "4"))
    max_retries = max(
        0,
        int(os.getenv("SUBAGENT_MAX_RETRIES", "1"))
        if max_retries is None
        else int(max_retries),
    )
    # 起多少个线程就开多少个并发请求，别超过任务数
    workers = max(1, min(max_workers, len(tasks)))

    verbose = _verbose()
    print_lock = threading.Lock()
    results: list[SubagentResult | None] = [None] * len(tasks)

    def report(text: str) -> None:
        # 并发的子 agent 会同时想写这一行，锁住避免两行字符交错
        with print_lock:
            print(text, flush=True)

    if verbose:
        report(
            f"\n[delegate] {len(tasks)} 个子任务并行调研"
            f"（并发 {workers}，每个最多 {max_steps} 步，失败最多重试 {max_retries} 次）"
        )
        for i, task in enumerate(tasks, 1):
            report(f"  · [{i}] 出发：{_limit(task, 60)}")

    def run_with_retries(task: str) -> SubagentResult:
        errors: list[str] = []
        for attempt in range(max_retries + 1):
            try:
                result = run_subagent(task, model=model, max_steps=max_steps)
            except Exception as exc:  # noqa: BLE001
                result = SubagentResult(
                    task=task,
                    error=f"{type(exc).__name__}: {exc}",
                )
            if result.ok:
                result.retry_count = attempt
                result.error_history = errors
                return result
            if result.error:
                errors.append(result.error)
        result.retry_count = max_retries
        result.error_history = errors
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_with_retries, task): i
            for i, task in enumerate(tasks)
        }
        for future in as_completed(futures):
            index = futures[future]
            # 兜底：run_subagent 内部已经把异常转成结果了，但 ThreadPoolExecutor
            # 本身也可能在提交/调度阶段出问题，不能让它冲掉整批
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = SubagentResult(task=tasks[index], error=f"{type(exc).__name__}: {exc}")
            results[index] = result
            if verbose:
                retry_note = f"，重试 {result.retry_count} 次" if result.retry_count else ""
                state = f"{result.steps} 步{retry_note}" if result.ok else f"失败：{result.error}{retry_note}"
                report(f"  · [{index + 1}] 完成 · {state}")

    finished = [r for r in results if r is not None]
    if verbose:
        ok = sum(1 for r in finished if r.ok)
        total_retries = sum(r.retry_count for r in finished)
        report(
            f"[delegate] {ok}/{len(tasks)} 成功，共 {sum(r.steps for r in finished)} 步，"
            f"重试 {total_retries} 次\n"
        )

    return finished


def _limit(text: str, width: int) -> str:
    """按显示宽度截断，给终端提示行用。"""
    text = text.replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


def format_digest(results: list[SubagentResult]) -> str:
    """把一批结果排成回填给主 agent 的文本。

    刻意**不用** LLM 再总结一遍：主 agent 拿到这些结论后的下一轮本来就是聚合，
    再插一次模型调用只是多花一次钱、多丢一层信息。聚合是 ReAct 循环里本来就有的
    那一步，不需要额外造一个节点。
    """
    lines: list[str] = []
    total = len(results)
    for i, r in enumerate(results, 1):
        retry_note = f" · 重试 {r.retry_count} 次" if r.retry_count else ""
        lines.append(
            f"子任务 {i}/{total} · {'完成' if r.ok else '失败'}"
            f" · {r.steps} 步{retry_note}"
        )
        lines.append(f"任务：{r.task}")
        if r.ok:
            answer = r.answer
            if len(answer) > _ANSWER_LIMIT:
                answer = answer[:_ANSWER_LIMIT] + "…（结论过长，已截断）"
            lines.append(f"结论：{answer}")
        else:
            history = "；".join(r.error_history) if r.error_history else (r.error or "未知错误")
            lines.append(f"结论：（无）失败原因：{history}")
        lines.append("")

    failed = [i for i, r in enumerate(results, 1) if not r.ok]
    if failed:
        lines.append(
            f"注意：子任务 {', '.join(map(str, failed))} 没有拿到结论。"
            f"聚合时要如实说明这部分缺失，不要用别的子任务的结论顶替。"
        )
    return "\n".join(lines).strip()
