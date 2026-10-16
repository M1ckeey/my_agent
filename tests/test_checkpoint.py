import unittest
import os
from uuid import uuid4
from unittest.mock import patch, MagicMock

from agent.graph import ResearchGraph
from agent.langgraph.checkpoint import memory_checkpointer, open_checkpointer
from agent.langgraph.lg_builder import graph as exported_graph
from agent.research_state import ResearchState, ResearchSubQuestion
from agent.state import ResearchFinding


def finding(task):
    return ResearchFinding(content=f"answer {task}", source="test", source_type="test", query=task)


class CheckpointTests(unittest.TestCase):
    def test_exported_graph_has_checkpointer(self):
        self.assertIsNotNone(exported_graph.checkpointer)

    def services(self, saver, *, planner=None, researcher=None, interrupt_after=None):
        return ResearchGraph(
            checkpointer=saver,
            interrupt_after=interrupt_after,
            planner=planner or (lambda topic: [ResearchSubQuestion(id="q1", question=topic)]),
            researcher=researcher or (lambda tasks: [finding(tasks[0])]),
            critic=lambda state: "stop",
            verbose=False,
        )

    def test_resume_after_plan_with_recreated_graph(self):
        saver = memory_checkpointer()
        first = self.services(saver, interrupt_after=["plan"])
        paused = first.run("topic", thread_id="thread-a")
        self.assertEqual(paused.status, "planned")
        self.assertEqual(first.get_state("thread-a")[1], ("research",))
        earliest = list(saver.list({"configurable": {"thread_id": "thread-a"}}))[-1]
        self.assertIsInstance(earliest.checkpoint["channel_values"]["__start__"], ResearchState)

        resumed = self.services(saver).resume("thread-a")
        self.assertEqual(resumed.status, "done")
        self.assertEqual(resumed.run_id, paused.run_id)
        self.assertEqual(resumed.thread_id, "thread-a")
        self.assertEqual(len(resumed.completed_queries), 1)
        self.assertEqual(self.services(saver).get_state("thread-a")[1], ())

    def test_failed_query_resumes_without_repeating_committed_query(self):
        saver = memory_checkpointer()
        calls = []

        def planner(_):
            return [
                ResearchSubQuestion(id="q1", question="first"),
                ResearchSubQuestion(id="q2", question="second"),
            ]

        def failing(tasks):
            calls.extend(tasks)
            if tasks[0] == "second":
                raise RuntimeError("interrupted")
            return [finding(tasks[0])]

        first = self.services(saver, planner=planner, researcher=failing)
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            first.run("topic", thread_id="thread-b")
        saved, pending = first.get_state("thread-b")
        self.assertEqual(saved.depth, 1)
        self.assertEqual(len(saved.completed_queries), 1)
        self.assertEqual(pending, ("research",))

        second = self.services(saver, planner=planner, researcher=lambda tasks: calls.extend(tasks) or [finding(tasks[0])])
        result = second.resume("thread-b")
        self.assertEqual(calls, ["first", "second", "second"])
        self.assertEqual(result.depth, 2)
        self.assertEqual(len(result.findings), 2)

    def test_same_thread_keeps_conversation_and_other_thread_is_isolated(self):
        saver = memory_checkpointer()
        requests = []

        def planner(request):
            requests.append(request)
            return [ResearchSubQuestion(id="q1", question=request.splitlines()[0])]

        graph = self.services(saver, planner=planner)
        first = graph.run("first", thread_id="same")
        second = graph.run("second", thread_id="same")
        other = graph.run("other", thread_id="other")

        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual([turn["topic"] for turn in second.conversation], ["first", "second"])
        self.assertEqual(second.depth, 1)
        self.assertEqual(len(second.findings), 1)
        self.assertEqual(second.findings[0].query, "second")
        self.assertIn("first", requests[1])
        self.assertEqual([turn["topic"] for turn in other.conversation], ["other"])
        self.assertEqual(graph.get_state("same")[0].topic, "second")
        self.assertEqual(graph.get_state("other")[0].topic, "other")

    def test_cannot_start_new_run_on_interrupted_thread(self):
        saver = memory_checkpointer()
        graph = self.services(saver, interrupt_after=["plan"])
        graph.run("first", thread_id="same")
        with self.assertRaisesRegex(ValueError, "未完成"):
            graph.run("second", thread_id="same")

    def test_default_depth_handles_twenty_checkpointed_queries(self):
        graph = self.services(
            memory_checkpointer(),
            planner=lambda _: [
                ResearchSubQuestion(id=f"q{i}", question=f"query {i}")
                for i in range(20)
            ],
        )
        result = graph.run("topic")
        self.assertEqual(result.depth, 20)
        self.assertEqual(len(result.completed_queries), 20)
        self.assertEqual(result.status, "done")

    def test_critic_repeating_completed_query_stops_without_looping(self):
        calls = []

        def researcher(tasks):
            calls.extend(tasks)
            return [finding(tasks[0])]

        graph = ResearchGraph(
            checkpointer=memory_checkpointer(),
            planner=lambda _: [ResearchSubQuestion(id="q1", question="same query")],
            researcher=researcher,
            critic=lambda _: ("revise", [{"subquestion_id": "q1", "query": "same query"}]),
            verbose=False,
        )
        result = graph.run("topic")
        self.assertEqual(result.status, "done")
        self.assertEqual(calls, ["same query"])
        self.assertEqual(result.depth, 1)

    def test_postgres_backend_opens_connection_and_runs_setup(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        saver = MagicMock()
        with patch.dict(os.environ, {"RESEARCH_DATABASE_URL": "postgresql://example/test"}), patch(
            "psycopg.Connection.connect", return_value=connection
        ) as connect, patch(
            "langgraph.checkpoint.postgres.PostgresSaver", return_value=saver
        ) as saver_class:
            with open_checkpointer("postgres") as opened:
                self.assertIs(opened, saver)
        connect.assert_called_once()
        self.assertTrue(connect.call_args.kwargs["autocommit"])
        saver_class.assert_called_once()
        saver.setup.assert_called_once()

    @unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要 TEST_POSTGRES_URL")
    def test_postgres_resume_after_reopening_connection(self):
        thread_id = f"checkpoint-test-{uuid4().hex}"
        with patch.dict(os.environ, {"RESEARCH_DATABASE_URL": os.environ["TEST_POSTGRES_URL"]}):
            with open_checkpointer("postgres") as saver:
                paused = self.services(saver, interrupt_after=["plan"]).run(
                    "topic", thread_id=thread_id
                )
                self.assertEqual(paused.status, "planned")
                earliest = list(saver.list({"configurable": {"thread_id": thread_id}}))[-1]
                self.assertIsInstance(earliest.checkpoint["channel_values"]["__start__"], ResearchState)
            with open_checkpointer("postgres") as saver:
                finished = self.services(saver).resume(thread_id)
                self.assertEqual(finished.status, "done")
                self.assertEqual(finished.run_id, paused.run_id)


if __name__ == "__main__":
    unittest.main()
