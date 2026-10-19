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
        if not result.ok and result.error:
            findings.append(
                ResearchFinding(
                    content=f"子任务未完成：{result.error}",
                    source="subagent",
                    source_type="subagent_error",
                    query=result.task,
                    task_id=result.task,
                    token_used=result.token_used if not result.findings else 0,
                )
            )
    return findings


def _default_write(state: ResearchState) -> str:
    """Summarize source-addressed evidence and keep the excerpts auditable."""
    findings = ContextManager().compact(state.findings)
    summaries = _synthesize_answers(state, findings)
    lines = [f"# {state.topic}", "", "## 研究结论", ""]
    supported_questions = 0
    for question in state.subquestions:
        if any(
            finding.subquestion_id == question.id
            and finding.source.startswith(("http://", "https://", "file:"))
            for finding in findings
        ):
            supported_questions += 1
    lines.append(
        f"已找到可定位材料的子问题：{supported_questions}/{len(state.subquestions)}。"
        "下文按问题整理结论及证据；未覆盖的部分保留为信息缺口。"
        "自动生成的结论仍需人工核对事实忠实性。"
    )
    lines.append("")
    for question in state.subquestions:
        lines.extend([f"## {question.question}", ""])
        matched = [
            finding for finding in findings
            if (finding.subquestion_id == question.id
                or finding.task_id == question.question
                or finding.query == question.question)
            and finding.source.startswith(("http://", "https://", "file:"))
            and finding.content.strip()
        ]
        if not matched:
            lines.append("信息不足：没有可定位的来源材料。")
        else:
            summary = summaries.get(question.id)
            if summary:
                answer, cited_sources = summary
                citations = "".join(f"（来源：{source}）" for source in cited_sources)
                lines.extend([f"**综合回答：** {answer}{citations}", "", "证据摘录：", ""])
            for finding in matched:
                evidence = " ".join(finding.content.split())
                lines.append(f"- {evidence}（来源：{finding.source}）")
        if any(
            finding.source_type == "subagent_error"
            and finding.subquestion_id == question.id
            for finding in findings
        ):
            lines.append("- 研究过程未完成；以上材料可能不完整。")
        lines.append("")
    return "\n".join(lines).strip()


def _synthesize_answers(
    state: ResearchState, findings: list[ResearchFinding]
) -> dict[str, tuple[str, list[str]]]:
    """Ask the model for concise answers; accept only references in the input set."""
    evidence: list[dict[str, str]] = []
    source_by_id: dict[str, str] = {}
    for question in state.subquestions:
        matched = [
            finding for finding in findings
            if finding.subquestion_id == question.id
            and finding.source.startswith(("http://", "https://", "file:"))
        ][:3]
        for finding in matched:
            identifier = f"s{len(evidence) + 1}"
            source_by_id[identifier] = finding.source
            evidence.append({
                "id": identifier, "question_id": question.id,
                "source": finding.source, "text": finding.content[:400],
            })
    if not evidence:
        return {}
    request = {
        "topic": state.topic,
        "questions": [{"id": q.id, "question": q.question} for q in state.subquestions],
        "evidence": evidence,
    }
    try:
        response = call_llm(
            get_client(), os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            [
                {"role": "system", "content": (
                    "你是研究报告写作者。只根据给出的证据回答各子问题，不添加证据中没有的事实。"
                    "尽量逐项回答每个子问题；每条结论必须给出支撑它的证据 id；"
                    "证据不足的子问题不要输出。"
                    "只返回 JSON 对象：{\"answers\":[{\"question_id\":\"q1\","
                    "\"answer\":\"简短回答\",\"source_ids\":[\"s1\"]}]}。"
                )},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ], tools=[], max_tokens=2200,
        )
        raw = final_text(response).strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", raw)
            if not match:
                return {}
            data = json.loads(match.group(0))
        answer_rows = data.get("answers", []) if isinstance(data, dict) else []
        if isinstance(answer_rows, dict):
            answer_rows = [dict(value, question_id=key) for key, value in answer_rows.items() if isinstance(value, dict)]
        answers: dict[str, tuple[str, list[str]]] = {}
        question_ids = {question.id for question in state.subquestions}
        for item in answer_rows:
            if not isinstance(item, dict):
                continue
            question_id = str(item.get("question_id", ""))
            if question_id not in question_ids:
                question_id = next(
                    (candidate for candidate in question_ids if candidate.lower() == question_id.lower()),
                    question_id,
                )
            answer = " ".join(str(item.get("answer", item.get("summary", ""))).split())
            source_ids = item.get("source_ids", [])
            if isinstance(source_ids, str):
                source_ids = [source_ids]
            source_ids = [str(source_id).lower() for source_id in source_ids] if isinstance(source_ids, list) else source_ids
            if (question_id not in question_ids or not answer
                or not isinstance(source_ids, list) or not source_ids
                or any(source_id not in source_by_id for source_id in source_ids)):
                continue
            valid_ids = {
                entry["id"] for entry in evidence if entry["question_id"] == question_id
            }
            if any(source_id not in valid_ids for source_id in source_ids):
                continue
            answers[question_id] = (
                answer, list(dict.fromkeys(source_by_id[source_id] for source_id in source_ids))
            )
        return answers
    except Exception:
        return {}


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
            if (finding.subquestion_id == question.id
                or finding.task_id == question.question
                or finding.query == question.question)
            and finding.source_type != "subagent_error"
            and bool(finding.content.strip())
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
        normalized_report = " ".join(report_text.split())
        supported = any(
            " ".join(finding.content.split())[:120] in normalized_report
            for finding in source_findings if finding.content.strip()
        )
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


