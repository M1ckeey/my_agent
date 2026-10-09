import unittest

from agent.graph import ResearchGraph
from agent.research_state import ResearchState


class BudgetTests(unittest.TestCase):
    def test_token_budget_marks_state_exhausted(self):
        state = ResearchState(topic="topic", token_used=100, max_tokens=100)

        self.assertTrue(state.budget_exhausted)

    def test_critic_continue_is_stopped_when_token_budget_is_exhausted(self):
        graph = ResearchGraph(verbose=False)
        state = ResearchState(
            topic="topic",
            critic_signal="continue",
            frontier=[{"query": "next"}],
            token_used=100,
            max_tokens=100,
        )

        self.assertEqual(graph._route_critic(state), "stop")


if __name__ == "__main__":
    unittest.main()
