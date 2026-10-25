from pathlib import Path
import json
import tempfile
import unittest
from guarded_agent import Executor, Journal
from guarded_agent.model import Receipt
from guarded_agent.ollama_agent import OllamaAgent, OllamaClient, CalculatorSimulation, calculator_profile, parse_profile
from guarded_agent.simulation import FakeClock


class FakePlanner:
    def __init__(self, decide=None):
        self.calls = 0
        self.callback = decide

    def decide(self, task, observation, catalog, history, timeout=None):
        self.calls += 1
        if self.callback:
            return self.callback(observation)
        return {"decision": "act", "action_id": "press_1" if observation["display"] == "0" else "press_2", "reason": "next digit"}


class OllamaAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.temp.name) / "journal.sqlite3")
        self.clock = FakeClock()
        self.backend = CalculatorSimulation(self.clock)
        self.executor = Executor(self.backend, self.journal, clock=self.clock, sleep=self.clock.sleep)

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def run_agent(self, client=None, **kwargs):
        return OllamaAgent(client or FakePlanner(), self.executor, **kwargs).run("enter 12", *calculator_profile(), "test-run")

    def test_two_steps_and_independent_goal_verification(self):
        result = self.run_agent()
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(self.backend.display, "12")
        self.assertEqual(self.backend.dispatch_count, 2)

    def test_model_done_is_not_accepted_as_success(self):
        client = FakePlanner(lambda _: {"decision": "done", "action_id": "", "reason": "looks finished"})
        result = self.run_agent(client)
        self.assertEqual((result["outcome"], result["reason"]), ("unknown", "model_done_without_verified_goal"))
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_goal_already_satisfied_does_not_call_model(self):
        self.backend.display = "12"
        client = FakePlanner()
        self.assertEqual(self.run_agent(client)["outcome"], "success")
        self.assertEqual(client.calls, 0)
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_unknown_action_stops_loop(self):
        self.backend.dispatch = lambda *args, **kwargs: Receipt("uncertain", "partial input")
        client = FakePlanner()
        result = self.run_agent(client)
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(client.calls, 1)

    def test_ui_changes_during_inference_prevent_action(self):
        def change(_):
            self.backend.display = "9"
            return {"decision": "act", "action_id": "press_1", "reason": "stale plan"}
        result = self.run_agent(FakePlanner(change))
        self.assertEqual(result["reason"], "ui_changed_while_model_was_planning")
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_repeated_no_progress_action_stops(self):
        client = FakePlanner(lambda _: {"decision": "act", "action_id": "press_1", "reason": "repeat"})
        result = self.run_agent(client)
        self.assertEqual(result["reason"], "repeated_action_without_state_progress")
        self.assertEqual(self.backend.dispatch_count, 1)

    def test_step_limit_is_enforced(self):
        result = self.run_agent(max_steps=1)
        self.assertEqual(result["reason"], "agent_step_budget_exhausted")
        self.assertEqual(self.backend.dispatch_count, 1)

    def test_default_desktop_planning_can_exceed_ten_steps(self):
        from dataclasses import replace
        from guarded_agent.model import Condition
        window, original, probe = calculator_profile()
        catalog = {f'step_{n}': replace(original['press_1'],
            condition=Condition(probe.condition.selector, 'name', '1' * n)) for n in range(1, 13)}
        probe = replace(probe, condition=Condition(probe.condition.selector, 'name', '1' * 12))
        client = FakePlanner(lambda observation: {'decision': 'act',
            'action_id': f"step_{1 if observation['display'] == '0' else len(observation['display']) + 1}", 'reason': 'next digit'})
        agent = OllamaAgent(client, self.executor, clock=self.clock)
        self.assertIsNone(agent.max_steps)
        result = agent.run('enter twelve ones', window, catalog, probe, 'long-run')
        self.assertEqual(result['outcome'], 'success')
        self.assertEqual(self.backend.dispatch_count, 12)

    def test_existing_unknown_blocks_model_and_input(self):
        a = calculator_profile()[1]["press_1"]
        self.journal.begin(a)
        self.journal.reserve(a)
        client = FakePlanner()
        result = self.run_agent(client)
        self.assertIn("unresolved_effect", result["reason"])
        self.assertEqual(client.calls, 0)
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_verify_method_has_no_mutation(self):
        probe = calculator_profile()[2]
        check = self.executor.verify(probe)
        self.assertEqual(check.reason, "goal_not_yet_satisfied")
        self.assertEqual((self.backend.prepare_count, self.backend.dispatch_count), (0, 0))

    def test_profile_roundtrip_uses_host_window_binding(self):
        from dataclasses import asdict
        window, catalog, probe = calculator_profile()
        data = {"window": asdict(window), "goal": asdict(probe.condition),
                "actions": {key: a.to_dict() for key, a in catalog.items()}}
        actual_window, actual_catalog, actual_probe = parse_profile(data)
        self.assertEqual(actual_window, window)
        self.assertEqual(actual_probe.condition, probe.condition)
        self.assertEqual(set(actual_catalog), set(catalog))

    def test_cancel_before_activation_sends_nothing(self):
        self.backend.activate_window = lambda *a, **k: self.fail('No activation after cancellation')
        result = self.run_agent(cancelled=lambda: True)
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_cancel_during_planning_sends_nothing(self):
        stopped = [False]
        def decide(_):
            stopped[0] = True
            return {'decision': 'act', 'action_id': 'press_1', 'reason': 'late'}
        result = self.run_agent(FakePlanner(decide), cancelled=lambda: stopped[0])
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_stop_after_action_retains_validation_and_skips_next(self):
        stopped, events = [False], []
        def event(value):
            events.append(value)
            if value['kind'] == 'action_result':
                stopped[0] = True
        result = self.run_agent(cancelled=lambda: stopped[0], on_event=event)
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(self.backend.dispatch_count, 1)
        self.assertEqual(result['steps'][0]['result']['outcome'], 'success')

    def test_unknown_result_is_not_hidden_by_cancellation(self):
        stopped = [False]
        self.backend.dispatch = lambda *a, **k: Receipt('uncertain', 'partial')
        def event(value):
            if value['kind'] == 'action_result':
                stopped[0] = True
        result = self.run_agent(cancelled=lambda: stopped[0], on_event=event)
        self.assertEqual(result['outcome'], 'unknown')

    def test_display_callback_failure_cannot_change_execution(self):
        def broken(_):
            raise RuntimeError('display failed')
        self.assertEqual(self.run_agent(on_event=broken)['outcome'], 'success')


class OllamaProtocolTests(unittest.TestCase):
    def fake_client(self, content, tool_calls=None):
        client = OllamaClient("test-model")
        calls = []
        def request(path, data=None, timeout=None):
            calls.append((path, data))
            return {"message": {"content": content, "tool_calls": tool_calls}, "done": True}
        client.request = request
        return client, calls

    def ask(self, client):
        return client.decide("enter 12", {"display": "0"}, calculator_profile()[1], [])

    def test_requests_structured_output_from_local_api(self):
        client, calls = self.fake_client(json.dumps({"decision": "act", "action_id": "press_1", "reason": "next"}))
        self.assertEqual(self.ask(client)["action_id"], "press_1")
        self.assertEqual(calls[0][0], "/api/chat")
        self.assertFalse(calls[0][1]["stream"])
        self.assertFalse(calls[0][1]["format"]["additionalProperties"])

    def test_remote_endpoint_not_implicitly_used(self):
        for base in ("http://example.com:11434", "http://user:pass@localhost:11434", "file:///tmp/model"):
            with self.assertRaises(ValueError):
                OllamaClient("model", base)

    def test_model_cannot_add_arbitrary_commands(self):
        client, _ = self.fake_client(json.dumps({"decision": "act", "action_id": "press_1", "reason": "", "command": "do something else"}))
        with self.assertRaises(ValueError):
            self.ask(client)

    def test_model_cannot_select_an_unknown_action(self):
        client, _ = self.fake_client(json.dumps({"decision": "act", "action_id": "unknown", "reason": ""}))
        with self.assertRaises(ValueError):
            self.ask(client)

    def test_unexpected_tool_calls_are_never_executed(self):
        client, _ = self.fake_client("{}", [{"function": {"name": "shell", "arguments": {}}}])
        with self.assertRaises(ValueError):
            self.ask(client)

    def test_invalid_json_is_not_repaired_into_an_action(self):
        client, _ = self.fake_client("click the button")
        with self.assertRaises(ValueError):
            self.ask(client)
