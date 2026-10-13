import unittest

from agent.graph import ResearchGraph
from agent.research_state import ResearchState, ResearchSubQuestion
from agent.state import ResearchFinding


class ResearchGraphTests(unittest.TestCase):
    def test_revise_runs_new_query_before_writing(self):
        batches = []
        critic_calls = []

        def researcher(tasks):
            batches.append(tasks)
            return [
                ResearchFinding(
                    content=f"evidence for {task}",
                    source="test",
                    source_type="test",
                    query=task,
                )
                for task in tasks
            ]

        def critic(state):
            critic_calls.append(list(state.frontier))
            if len(critic_calls) == 1:
                return "revise", [{"subquestion_id": "q1", "query": "follow-up"}]
            return "stop"

        graph = ResearchGraph(
            planner=lambda _: [ResearchSubQuestion(id="q1", question="initial")],
            researcher=researcher,
            critic=critic,
            verbose=False,
        )

        result = graph.run("topic")

        self.assertEqual(batches, [["initial"], ["follow-up"]])
        self.assertEqual(critic_calls, [[], []])
        self.assertEqual(result.depth, 2)
        self.assertEqual(result.status, "done")

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
            critic=lambda state: "stop",
            verbose=False,
        )

        result = graph.run("topic")

        self.assertEqual(result.report, "q1")
        self.assertEqual(result.findings[0].subquestion_id, "q1")


if __name__ == "__main__":
    unittest.main()
