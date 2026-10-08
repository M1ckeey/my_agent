import tempfile
import unittest
from pathlib import Path

from agent.compactor import ConversationCompactor


class ConversationCompactorTests(unittest.TestCase):
    def make_compactor(self, root, **kwargs):
        return ConversationCompactor(
            output_dir=Path(root) / "tool-results",
            transcript_dir=Path(root) / "transcripts",
            **kwargs,
        )

    def test_tool_result_budget_persists_large_latest_result(self):
        with tempfile.TemporaryDirectory() as root:
            compactor = self.make_compactor(
                root,
                context_char_limit=100,
                large_result_char_limit=10,
                run_id="run-a",
            )
            messages = [
                {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
                {"role": "tool", "tool_call_id": "call-1", "content": "x" * 200},
            ]

            result = compactor.tool_result_budget(messages)

            saved = Path(root) / "tool-results" / "run-a" / "001_call-1.txt"
            self.assertTrue(saved.exists())
            self.assertIn(str(saved), result[1]["content"])
            self.assertEqual(saved.read_text(encoding="utf-8"), "x" * 200)

    def test_snip_compact_keeps_tool_call_with_its_results(self):
        with tempfile.TemporaryDirectory() as root:
            compactor = self.make_compactor(root, max_messages=6)
            messages = [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "request"},
                {"role": "assistant", "content": "old"},
                {"role": "assistant", "content": "older"},
                {"role": "user", "content": "old follow-up"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
                {"role": "tool", "tool_call_id": "call-1", "content": "result"},
                {"role": "user", "content": "follow-up"},
            ]

            result = compactor.snip_compact(messages)

            tool_index = next(i for i, item in enumerate(result) if item.get("role") == "tool")
            self.assertEqual(result[tool_index - 1]["role"], "assistant")
            self.assertTrue(result[tool_index - 1].get("tool_calls"))
            self.assertLessEqual(len(result), 7)

    def test_compact_history_preserves_active_request_and_transcript(self):
        with tempfile.TemporaryDirectory() as root:
            compactor = self.make_compactor(root, context_char_limit=10)
            result = compactor.compact_history(
                [{"role": "user", "content": "old context"}],
                "current request",
            )

            self.assertIn("Current user request:\ncurrent request", result[0]["content"])
            self.assertIn("Conversation summary:", result[0]["content"])
            transcript_dir = Path(root) / "transcripts" / compactor.run_id
            self.assertEqual(len(list(transcript_dir.glob("compact_*.json"))), 1)

    def test_transcripts_and_results_get_unique_sequence_names(self):
        with tempfile.TemporaryDirectory() as root:
            compactor = self.make_compactor(root, run_id="run-a")
            first = compactor.write_transcript([{"role": "user", "content": "one"}])
            second = compactor.write_transcript([{"role": "user", "content": "two"}])

            self.assertNotEqual(first, second)
            self.assertTrue(first.endswith("compact_001.json"))
            self.assertTrue(second.endswith("compact_002.json"))

    def test_summary_failure_falls_back_and_records_compaction(self):
        with tempfile.TemporaryDirectory() as root:
            def failing_summary(messages, active_request):
                raise RuntimeError("summary unavailable")

            compactor = self.make_compactor(root, summary_builder=failing_summary)
            result = compactor.compact_history(
                [{"role": "user", "content": "old context"}],
                "current request",
            )

            self.assertIn("user: old context", result[0]["content"])
            self.assertEqual(compactor.stats.history_compactions, 1)

    def test_prepare_records_size_metrics(self):
        compactor = ConversationCompactor(context_char_limit=100)
        messages = [{"role": "user", "content": "request"}]

        result = compactor.prepare(messages, "request")

        self.assertEqual(result, messages)
        self.assertEqual(compactor.stats.prepare_calls, 1)
        self.assertGreater(compactor.stats.chars_before, 0)
        self.assertGreater(compactor.stats.chars_after, 0)

    def test_reactive_compact_keeps_recent_messages(self):
        with tempfile.TemporaryDirectory() as root:
            compactor = self.make_compactor(root)
            messages = [
                {"role": "user", "content": "old"},
                {"role": "assistant", "content": "old answer"},
                {"role": "user", "content": "older context"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "call-2"}]},
                {"role": "tool", "tool_call_id": "call-2", "content": "recent result"},
                {"role": "assistant", "content": "recent answer"},
            ]

            result = compactor.reactive_compact(messages, "current")

            self.assertIn("Reactive compact", result[0]["content"])
            self.assertEqual(result[-1]["content"], "recent answer")
            self.assertEqual(result[-2]["role"], "tool")
            self.assertEqual(result[-3].get("tool_calls")[0]["id"], "call-2")


if __name__ == "__main__":
    unittest.main()
