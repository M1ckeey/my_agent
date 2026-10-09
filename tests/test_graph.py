import unittest

from agent.graph import ResearchGraph
from agent.research_state import ResearchState, ResearchSubQuestion
from agent.state import ResearchFinding


class ResearchGraphTests(unittest.TestCase):
    def test_research_restores_subquestion_id_from_frontier(self):
        graph = ResearchGraph(
            researcher=lambda tasks: [
                ResearchFinding(
                    content="evidence",
                    source="test",
                    source_type="test",
                    query=tasks[0],
                )
            ],
            verbose=False,
        )
        state = ResearchState(topic="topic")
        state.frontier = [{"subquestion_id": "q1", "query": "question one"}]

        result = graph._research(state)

        self.assertEqual(result["findings"][0].subquestion_id, "q1")

    def test_run_preserves_subquestion_id_through_write(self):
        def planner(_topic):
            return [ResearchSubQuestion(id="q1", question="question one")]

        def researcher(tasks):
            return [
                ResearchFinding(
                    content="answer",
                    source="test",
                    source_type="test",
                    query=tasks[0],
                )
            ]

        graph = ResearchGraph(
            planner=planner,
            researcher=researcher,
            writer=lambda state: state.findings[0].subquestion_id,
            verbose=False,
        )

        result = graph.run("topic")

        self.assertEqual(result.report, "q1")
        self.assertEqual(result.findings[0].subquestion_id, "q1")


if __name__ == "__main__":
    unittest.main()
