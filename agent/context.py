"""研究发现的整理与上下文预算控制。

``compact`` 做确定性的整理，``compress`` 在超限时按来源调用 fast LLM 摘要。
两者都会保留 findings 的来源和任务信息。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace

from .state import ResearchFinding
from .llm import call_llm, final_text, get_client

Summarizer = Callable[[str, str, list[ResearchFinding]], str]


class ContextManager:
    """管理一轮运行产生的结构化研究发现。"""

    def __init__(
        self,
        *,
        max_findings: int = 30,
        max_chars_per_finding: int = 2000,
    ) -> None:
        if max_findings < 1:
            raise ValueError("max_findings 必须大于 0")
        if max_chars_per_finding < 1:
            raise ValueError("max_chars_per_finding 必须大于 0")
        self.max_findings = max_findings
        self.max_chars_per_finding = max_chars_per_finding

    def dedupe(self, findings: list[ResearchFinding]) -> list[ResearchFinding]:
        """按来源、查询和内容去重，保留第一次出现的顺序。"""
        result: list[ResearchFinding] = []
        seen: set[tuple[str, str, str]] = set()
        for finding in findings:
            key = (finding.source, finding.query, finding.content)
            if key in seen:
                continue
            seen.add(key)
            result.append(finding)
        return result

    def compact(self, findings: list[ResearchFinding]) -> list[ResearchFinding]:
        """去重、截断并限制总数量，保持输入顺序。"""
        compacted: list[ResearchFinding] = []
        for finding in self.dedupe(findings)[: self.max_findings]:
            if len(finding.content) <= self.max_chars_per_finding:
                compacted.append(finding)
                continue
            compacted.append(
                replace(
                    finding,
                    content=(
                        finding.content[: self.max_chars_per_finding]
                        + "…（内容已截断）"
                    ),
                )
            )
        return compacted

    def compress(
        self,
        findings: list[ResearchFinding],
        *,
        topic: str = "",
        state: object | None = None,
        summarizer: Summarizer | None = None,
        model: str | None = None,
    ) -> list[ResearchFinding]:
        """按来源和任务分组摘要；失败时回退到确定性整理。

        原始 ``findings`` 不会被修改。只有超过 ``max_findings`` 时才调用
        LLM，避免短任务平白增加一次模型请求。
        """
        deduped = self.dedupe(findings)
        if len(deduped) <= self.max_findings:
            return self.compact(deduped)

        groups: dict[tuple[str, str], list[ResearchFinding]] = {}
        for finding in deduped:
            groups.setdefault((finding.source, finding.task_id), []).append(finding)

        summarize = summarizer or self._llm_summarizer(
            topic=topic,
            state=state,
            model=model,
        )
        compressed: list[ResearchFinding] = []
        for group in groups.values():
            if len(group) == 1:
                compressed.append(group[0])
                continue
            try:
                summary = summarize(topic, group[0].source, group).strip()
                if not summary:
                    raise ValueError("摘要器返回了空内容")
                compressed.append(
                    replace(
                        group[0],
                        content=summary,
                        query="；".join(
                            dict.fromkeys(
                                finding.query for finding in group if finding.query
                            )
                        ),
                        confidence=max(finding.confidence for finding in group),
                    )
                )
            except Exception:
                compressed.extend(self.compact(group))

        return self.compact(compressed)

    def _llm_summarizer(
        self,
        *,
        topic: str,
        state: object | None,
        model: str | None,
    ) -> Summarizer:
        """创建默认的 fast LLM 摘要器。"""
        selected_model = model or os.getenv(
            "LLM_SUMMARY_MODEL",
            os.getenv("LLM_MODEL", "deepseek-v4-flash"),
        )

        def summarize(
            current_topic: str,
            source: str,
            group: list[ResearchFinding],
        ) -> str:
            content = "\n".join(
                f"- {finding.content}" for finding in group
            )
            message = call_llm(
                get_client(),
                selected_model,
                [
                    {
                        "role": "system",
                        "content": (
                            "你是研究材料压缩助手。只根据给定材料生成简洁摘要，"
                            "保留关键事实、数字和限制，不要补充外部知识。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"研究主题：{current_topic or topic}\n"
                            f"来源：{source}\n\n材料：\n{content}\n\n"
                            "请只输出摘要正文。"
                        ),
                    },
                ],
                tools=[],
                max_tokens=min(1200, max(256, self.max_chars_per_finding // 2)),
            )
            return final_text(message)

        return summarize
