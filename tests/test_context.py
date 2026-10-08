import unittest

from agent.context import ContextManager
from agent.state import ResearchFinding


def finding(content: str, *, source: str = "source-a", query: str = "query"):
    return ResearchFinding(
        task_id="task-1",
        source=source,
        source_type="web",
        query=query,
        content=content,
        confidence=0.8,
    )


class ContextManagerTests(unittest.TestCase):
    def test_compact_deduplicates_limits_and_truncates_findings(self):
        manager = ContextManager(max_findings=2, max_chars_per_finding=5)
        first = finding("123456789")

        result = manager.compact([first, first, finding("abc", query="other")])

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].content, "12345…（内容已截断）")
        self.assertEqual(result[1].content, "abc")

    def test_compress_uses_injected_summarizer_for_same_source_and_task(self):
        manager = ContextManager(max_findings=1, max_chars_per_finding=100)
        calls = []

        def summarizer(topic, source, group):
            calls.append((topic, source, [item.content for item in group]))
            return "summary"

        result = manager.compress(
            [finding("first"), finding("second")],
            topic="topic-a",
            summarizer=summarizer,
        )

        self.assertEqual(result[0].content, "summary")
        self.assertEqual(result[0].query, "query")
        self.assertEqual(calls, [("topic-a", "source-a", ["first", "second"])])

    def test_compress_falls_back_to_compact_when_summarizer_fails(self):
        manager = ContextManager(max_findings=1, max_chars_per_finding=5)

        def failing_summarizer(topic, source, group):
            raise RuntimeError("summary failed")

        result = manager.compress(
            [finding("123456789"), finding("abcdef")],
            summarizer=failing_summarizer,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].content, "12345…（内容已截断）")


if __name__ == "__main__":
    unittest.main()
