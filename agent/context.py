"""研究发现的整理与上下文预算控制。

这里先做确定性的整理，不调用 LLM。后续可以在 ``compact`` 的超限分支里加入
按来源摘要的压缩逻辑，同时保留 findings 的来源和任务信息。
"""

from __future__ import annotations

from dataclasses import replace

from .state import ResearchFinding


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

