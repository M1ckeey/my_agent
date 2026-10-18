"""Seven-metric scorer does not turn missing evidence into a success rate."""

import json
from pathlib import Path
import tempfile
import unittest

from eval.score import _seven_for_question


class SevenMetricScorerTests(unittest.TestCase):
    def test_reviewed_evidence_and_critic_rounds(self):
        row = {
            "id": "q001",
            "annotation_status": "calibrated",
            "expected_subquestions": ["a", "b"],
        }
        raw = {
            "status": "done", "error": None,
            "report": "# Topic\n## Answer\n" + "evidence " * 40,
            "token_used": 100, "max_tokens": 1000,
            "depth": 1, "max_depth": 20,
            "critic_signal": "stop", "reflection_log": [{}, {}],
        }
        citations = [{"source": "https://example.org/source"}]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            review = Path(temp) / "review"
            review.mkdir()
            (review / "q001.json").write_text(json.dumps({
                "reviewer": "human", "coverage_checks": [True, False],
                "citation_checks": [{"exists": True, "faithful": True}],
            }), encoding="utf-8")
            metrics = _seven_for_question(row, raw, citations, Path(temp))
        self.assertTrue(metrics["completion_rate"]["value"])
        self.assertEqual(metrics["citation_accuracy"]["value"], 1.0)
        self.assertEqual(metrics["information_coverage"]["value"], 0.5)
        self.assertEqual(metrics["average_steps"]["value"], 2)
        self.assertEqual(metrics["reflection_effectiveness"]["value"], "critic_stop")

    def test_tool_name_is_not_a_citation(self):
        row = {"id": "q001", "annotation_status": "topic_approved", "expected_subquestions": ["a"]}
        raw = {"status": "done", "report": "# T\n## A\n" + "x" * 300,
               "token_used": 1200, "max_tokens": 1000, "depth": 1, "max_depth": 20,
               "critic_signal": "stop"}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            metrics = _seven_for_question(row, raw, [{"source": "web_search"}], Path(temp))
        self.assertEqual(metrics["citation_accuracy"]["status"], "unavailable")
        self.assertEqual(metrics["information_coverage"]["status"], "pending_review")
        self.assertEqual(metrics["reflection_effectiveness"]["value"], "hard_stop")
        self.assertEqual(metrics["average_steps"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
