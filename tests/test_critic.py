import unittest
from unittest.mock import patch

from agent.langgraph.lg_nodes import _llm_critic
from agent.llm import Message
from agent.research_state import ResearchState


class CriticTests(unittest.TestCase):
    def test_continue_signal_is_rejected(self):
        from agent.graph import ResearchGraph

        graph = ResearchGraph(critic=lambda state: "continue", verbose=False)

        with self.assertRaisesRegex(ValueError, "revise 或 stop"):
            graph._critic(ResearchState(topic="topic"))

    def test_revise_without_new_query_routes_to_write(self):
        from agent.graph import ResearchGraph

        graph = ResearchGraph(verbose=False)
        state = ResearchState(topic="topic", critic_signal="revise", next_queries=[])

        self.assertEqual(graph._route_critic(state), "stop")

    def test_llm_critic_parses_revise_queries(self):
        state = ResearchState(
            topic="topic",
            frontier=[],
        )
        response = Message(
            content='{"signal":"revise","next_queries":[{"subquestion_id":"q1","query":"补充查询"}]}',
            tool_calls=None,
        )

        with patch("agent.langgraph.lg_nodes.get_client"), patch(
            "agent.langgraph.lg_nodes.call_llm", return_value=response
        ):
            signal, queries = _llm_critic(state)

        self.assertEqual(signal, "revise")
        self.assertEqual(queries[0]["subquestion_id"], "q1")

    def test_budget_exhaustion_skips_injected_critic(self):
        called = []
        from agent.graph import ResearchGraph

        graph = ResearchGraph(critic=lambda state: called.append(state) or "revise", verbose=False)
        state = ResearchState(topic="topic", token_used=10, max_tokens=10)

        result = graph._critic(state)

        self.assertEqual(result["critic_signal"], "stop")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
