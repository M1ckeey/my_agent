import unittest

from agent.graph import _default_validate
from agent.research_state import ResearchState, ResearchSubQuestion
from agent.state import ResearchFinding


class ValidatorTests(unittest.TestCase):
    def test_validator_reports_coverage_and_supported_citation(self):
        state = ResearchState(
            topic="topic",
            subquestions=[ResearchSubQuestion(id="q1", question="question")],
            findings=[
                ResearchFinding(
                    content="evidence text",
                    source="source-a",
                    source_type="web",
                    query="question",
                    subquestion_id="q1",
                )
            ],
            report="# topic\n\n- evidence text（来源：source-a）",
        )

        citations = _default_validate(state)

        self.assertEqual(citations[0]["type"], "coverage")
        self.assertTrue(citations[0]["exists"])
        self.assertTrue(citations[1]["supported"])

    def test_validator_reports_missing_subquestion_material(self):
        state = ResearchState(
            topic="topic",
            subquestions=[ResearchSubQuestion(id="q1", question="question")],
            report="# topic\n\n信息不足。",
        )

        citations = _default_validate(state)

        self.assertFalse(citations[0]["exists"])
        self.assertIn("没有对应", citations[0]["note"])


if __name__ == "__main__":
    unittest.main()
