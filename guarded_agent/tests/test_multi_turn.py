import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add workspace to path
sys.path.append(r"D:\ai数据\app\guarded_agent")

# Use absolute import or ensure the parent package is handled
try:
    from ollama_agent import OllamaAgent
except ImportError:
    # Fallback for different execution environments
    import ollama_agent
    OllamaAgent = ollama_agent.OllamaAgent

class TestOllamaAgentMultiTurn(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_executor = MagicMock()
        self.mock_executor.backend = MagicMock()
        self.mock_policy = MagicMock()
        self.mock_policy.call_timeout = 5
        
        # Mocking the agent initialization
        with patch('ollama_agent.OllamaAgent.__init__', return_value=None):
            self.agent = OllamaAgent()
            self.agent.client = self.mock_client
            self.agent.executor = self.mock_executor
            self.agent.policy = self.mock_policy
            self.agent.cancelled = MagicMock(return_value=False)
            self.agent.emit = MagicMock()

    def test_multi_turn_on_non_fatal_error(self):
        """
        Test that a non-fatal error (e.g., 'warning' or 'unknown' outcome) 
        allows the loop to continue instead of exiting immediately.
        """
        # Setup mock data
        task = "test task"
        run_id = "run_123"
        window = MagicMock()
        catalog = {"action_1": MagicMock()}
        probe = MagicMock()
        probe.window = window
        for a in catalog.values(): a.window = window

        # Mock decision: 'act'
        self.mock_client.decide.return_value = {"decision": "act", "action_id": "action_1", "reason": "testing"}
        
        # Mock backend.inspect to return some state
        self.mock_executor.backend.inspect.return_value = {"content": "state"}
        
        # Mock executor to return a non-fatal error (warning)
        mock_result = MagicMock()
        mock_result.outcome = "warning"
        mock_result.reason = "non-fatal"
        mock_result.to_dict.return_value = {"outcome": "warning", "reason": "non-fatal"}
        self.mock_executor.run.return_value = mock_result

        # We want to see if it continues. Since we can't easily run the full loop 
        # without a real model/executor setup, we'll check if the loop logic 
        # (which we assume is implemented) would call decide again.
        
        # Let's try to run the actual agent.run.
        # But agent.run is complex. Let's try to run it with a very small max_steps.
        self.agent.max_steps = 2
        self.agent.max_seconds = 10
        self.agent.clock = lambda: 0
        
        # We'll mock the loop to run twice.
        # First call: decision 'act' -> executor returns 'warning'
        # Second call: decision 'done' -> exit
        self.mock_client.decide.side_effect = [
            {"decision": "act", "action_id": "action_1", "reason": "first"},
            {"decision": "done", "action_id": "", "reason": "second"}
        ]

        result = self.agent.run(task, window, catalog, probe, run_id)
        
        # If it continued, it should have called decide twice.
        self.assertEqual(self.mock_client.decide.call_count, 2)
        self.assertEqual(result["outcome"], "done")

    def test_fatal_error_stops_execution(self):
        """
        Test that a fatal error ('error' or 'fatal') stops the execution immediately.
        """
        task = "test task"
        run_id = "run_123"
        window = MagicMock()
        catalog = {"action_1": MagicMock()}
        probe = MagicMock()
        probe.window = window
        for a in catalog.values(): a.window = window

        self.mock_client.decide.return_value = {"decision": "act", "action_id": "action_1", "reason": "testing"}
        
        # Mock executor to return a fatal error
        mock_result = MagicMock()
        mock_result.outcome = "error"
        mock_result.reason = "fatal error"
        mock_result.to_dict.return_value = {"outcome": "error", "reason": "fatal error"}
        self.mock_executor.run.return_value = mock_result

        self.agent.max_steps = 5
        self.agent.max_seconds = 10
        self.agent.clock = lambda: 0

        result = self.agent.run(task, window, catalog, probe, run_id)
        
        # If it stopped, it should have called decide only once.
        self.assertEqual(self.mock_client.decide.call_count, 1)
        self.assertEqual(result["outcome"], "error")

if __name__ == "__main__":
    unittest.main()
