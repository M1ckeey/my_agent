"""Default research node implementations and shared routing helpers."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ..context import ContextManager
from ..llm import call_llm, final_text, get_client
from ..state import ResearchFinding
from ..subagent import run_subagents
from .lg_prompts import CRITIC_SYSTEM_PROMPT
from .lg_states import ResearchState, ResearchSubQuestion


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
    """模型判断失败时结束补查，保留当前研究材料。"""
    return "stop", []


def _llm_critic(state: ResearchState) -> tuple[str, list[dict[str, Any]]]:
    """让模型根据当前证据判断是否继续研究。失败时回退到确定性规则。"""
    if state.budget_exhausted:
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
            "content": CRITIC_SYSTEM_PROMPT,
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
        if signal not in {"stop", "revise"} or not isinstance(queries, list):
            raise ValueError("Critic 返回格式不正确")
        if signal == "revise":
            queries = [
                item for item in queries
                if isinstance(item, dict) and str(item.get("query", "")).strip()
            ]
            if not queries:
                raise ValueError("Critic 选择 revise 时必须提供补充查询")
            return signal, queries
        return "stop", []
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


