import unittest
from unittest.mock import patch

from agent.subagent import SubagentResult, run_subagents


class SubagentRetryTests(unittest.TestCase):
    def test_failed_subagent_is_retried_and_success_is_returned(self):
        attempts = []

        def fake_run(task, *, model, max_steps):
            attempts.append(task)
            if len(attempts) == 1:
                return SubagentResult(task=task, error="temporary failure")
            return SubagentResult(task=task, answer="recovered", steps=2)

        with patch("agent.subagent.run_subagent", side_effect=fake_run):
            results = run_subagents(["task"], max_workers=1, max_retries=1)

        self.assertEqual(len(attempts), 2)
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].retry_count, 1)
        self.assertEqual(results[0].error_history, ["temporary failure"])

    def test_retry_exhaustion_keeps_all_failure_reasons(self):
        def fake_run(task, *, model, max_steps):
            return SubagentResult(task=task, error="still failing")

        with patch("agent.subagent.run_subagent", side_effect=fake_run):
            results = run_subagents(["task"], max_workers=1, max_retries=2)

        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].retry_count, 2)
        self.assertEqual(results[0].error_history, ["still failing"] * 3)


if __name__ == "__main__":
    unittest.main()
