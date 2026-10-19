"""Agent 生命周期 Hook。

主循环只负责触发事件，权限检查、日志、上下文管理等扩展
都通过这个注册表接入。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .context import ContextManager

HOOK_EVENTS = (
    "UserPromptSubmit",
    "BeforeModel",
    "PreToolUse",
    "PostToolUse",
    "Stop",
)

Hook = Callable[..., Any]
HOOKS: dict[str, list[Hook]] = {event: [] for event in HOOK_EVENTS}


def register_hook(event: str, callback: Hook) -> Hook:
    """注册 Hook 回调，并返回原回调。"""
    if event not in HOOKS:
        raise ValueError(
            f"未知的 Hook 事件：{event!r}；可用事件：{list(HOOKS)}"
        )
    if not callable(callback):
        raise TypeError("callback 必须是可调用对象")
    HOOKS[event].append(callback)
    return callback


def unregister_hook(event: str, callback: Hook) -> None:
    """移除已注册的回调；未注册时不报错。"""
    if event not in HOOKS:
        raise ValueError(f"未知的 Hook 事件：{event!r}")
    if callback in HOOKS[event]:
        HOOKS[event].remove(callback)


def clear_hooks(event: str | None = None) -> None:
    """清空指定事件或全部事件的回调。"""
    if event is not None and event not in HOOKS:
        raise ValueError(f"未知的 Hook 事件：{event!r}")
    events = HOOKS if event is None else {event: HOOKS[event]}
    for callbacks in events.values():
        callbacks.clear()


def trigger_hooks(event: str, *args: Any) -> Any:
    """按注册顺序执行回调，并返回第一个非 None 的结果。"""
    if event not in HOOKS:
        raise ValueError(f"未知的 Hook 事件：{event!r}")
    first_result = None
    for callback in tuple(HOOKS[event]):
        result = callback(*args)
        if result is not None and first_result is None:
            first_result = result
    return first_result


def _record_finding(state: Any, tool_call: Any, output: str) -> None:
    """默认 PostToolUse Hook：把工具结果保存为 finding。"""
    name = tool_call.function.name
    if output.startswith("错误："):
        return
    if name == "rag_search":
        try:
            payload = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("hits"), list):
            for hit in payload["hits"]:
                if not isinstance(hit, dict) or not isinstance(hit.get("source"), str):
                    continue
                state.add_finding(
                    content=str(hit.get("content", "")),
                    source=hit["source"],
                    source_type="rag_search",
                    query=tool_call.function.arguments or "",
                    task_id=state.task,
                )
            return
    if name in {"web_search", "arxiv_search"}:
        # Both tools render numbered result blocks with the source URL on line two.
        # The optional heading/answer is not source evidence and is discarded.
        for block in re.split(r"(?m)(?=^\d+\.\s)", output):
            lines = block.strip().splitlines()
            if len(lines) < 3 or not re.match(r"^\d+\.\s", lines[0]):
                continue
            source = lines[1].strip()
            if not source.startswith(("https://", "http://")):
                continue
            content = "\n".join([lines[0].split(". ", 1)[-1], *lines[2:]]).strip()
            if content:
                state.add_finding(
                    content=content, source=source, source_type=name,
                    query=tool_call.function.arguments or "", task_id=state.task,
                )
        return
    if name == "read_file":
        header = re.match(r"^(.+?) 第 (\d+)-(\d+) 行", output)
        if header and "\n" in output:
            state.add_finding(
                content=output.split("\n", 1)[1],
                source=f"file:{header.group(1)}#L{header.group(2)}-L{header.group(3)}",
                source_type=name, query=tool_call.function.arguments or "",
                task_id=state.task,
            )
        return
    if name == "calculator":
        state.add_finding(
            content=output, source="calculator", source_type=name,
            query=tool_call.function.arguments or "", task_id=state.task,
        )


def _compact_findings(state: Any) -> None:
    """默认 Stop Hook：整理 findings，但不修改 messages。"""
    state.findings = ContextManager().compact(state.findings)


def install_default_hooks() -> None:
    """安装内置 Hook；重复调用不会重复注册。"""
    defaults = (
        ("PostToolUse", _record_finding),
        ("Stop", _compact_findings),
    )
    for event, callback in defaults:
        if callback not in HOOKS[event]:
            HOOKS[event].append(callback)


__all__ = [
    "HOOKS",
    "HOOK_EVENTS",
    "clear_hooks",
    "install_default_hooks",
    "register_hook",
    "trigger_hooks",
    "unregister_hook",
]
