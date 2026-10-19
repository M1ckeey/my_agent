"""Evidence extraction and report citation checks."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.hooks import _record_finding
from agent.langgraph.lg_nodes import _default_validate, _default_write
from agent.langgraph.lg_states import ResearchState, ResearchSubQuestion
from agent.state import AgentState, ResearchFinding


def call(name: str):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments='{"query":"topic"}'))


class ProvenanceTests(unittest.TestCase):
    def test_web_and_arxiv_results_keep_each_url(self):
        state = AgentState(task="topic")
        _record_finding(
            state, call("web_search"),
            "摘要：combined answer\n\n1. First\n   https://example.org/a\n   First fact"
            "\n\n2. Second\n   https://example.org/b\n   Second fact",
        )
        _record_finding(
            state, call("arxiv_search"),
            "arXiv 命中 1 篇，以下是前 1 篇：\n\n"
            "1. Paper\n   https://arxiv.org/abs/1234.5678\n   Author | 2025\n   Abstract",
        )
        self.assertEqual(
            [finding.source for finding in state.findings],
            ["https://example.org/a", "https://example.org/b", "https://arxiv.org/abs/1234.5678"],
        )
        self.assertNotIn("combined answer", state.findings[0].content)

    def test_read_file_has_line_address_and_errors_are_not_evidence(self):
        state = AgentState(task="topic")
        _record_finding(state, call("read_file"), "docs/step8.md 第 4-5 行\n     4\tcheckpoint")
        _record_finding(state, call("web_search"), "错误：缺少 API key")
        self.assertEqual(len(state.findings), 1)
        self.assertEqual(state.findings[0].source, "file:docs/step8.md#L4-L5")

    def test_report_uses_specific_sources_and_marks_gaps(self):
        state = ResearchState(
            topic="topic",
            subquestions=[
                ResearchSubQuestion(id="q1", question="first question"),
                ResearchSubQuestion(id="q2", question="second question"),
            ],
            findings=[
                ResearchFinding(
                    content="A supported fact", source="https://example.org/a",
                    source_type="web_search", subquestion_id="q1",
                ),
                ResearchFinding(
                    content="子任务未完成：timeout", source="subagent",
                    source_type="subagent_error", subquestion_id="q2",
                ),
            ],
        )
        with patch("agent.langgraph.lg_nodes._synthesize_answers", return_value={
            "q1": ("A short answer", ["https://example.org/a"]),
        }):
            state.report = _default_write(state)
        citations = _default_validate(state)
        self.assertIn("https://example.org/a", state.report)
        self.assertIn("A short answer", state.report)
        self.assertNotIn("来源：subagent", state.report)
        self.assertIn("信息不足", state.report)
        self.assertTrue(next(item for item in citations if item["type"] == "citation")["supported"])
        self.assertFalse(next(item for item in citations if item["subquestion_id"] == "q2")["exists"])


if __name__ == "__main__":
    unittest.main()
