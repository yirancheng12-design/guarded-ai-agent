from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from guarded_agent import Action, Condition, Executor, Journal, Policy, Selector, WindowIdentity, VisualSpec
from guarded_agent.model import Context, Evidence, Receipt
from guarded_agent.simulation import FakeClock, SimulatedBackend


def action(**changes):
    a = Action("operation-1", WindowIdentity(123, 456, 789), Selector(name="Digit 1"), "click",
               Condition(Selector(name="Display"), "name", "1"))
    return replace(a, **changes)


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "journal.sqlite3"
        self.journal = Journal(self.path)
        self.clock = FakeClock()
        self.backend = SimulatedBackend(self.clock)
        self.policy = Policy(consecutive=3, interval=0.1, observation_timeout=0.8)

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def run_action(self, a=None):
        return Executor(self.backend, self.journal, self.policy, self.clock, self.clock.sleep).run(a or action())

    def test_success_needs_three_fresh_matches(self):
        result = self.run_action()
        self.assertEqual((result.execution_status, result.outcome, result.samples), ("sent", "success", 4))
        self.assertEqual(self.backend.dispatch_count, 1)

    def slow_uia_sample(self, backend, frame):
        # Actual Win32 fixture reads took 1.40-1.45s per sample on this host.
        self.clock.sleep(1.45)
        return replace(frame, finished=self.clock())

    def test_default_budget_accepts_three_slow_fresh_uia_reads(self):
        self.policy = Policy()
        self.backend.sample_hook = self.slow_uia_sample
        result = self.run_action()
        self.assertEqual((result.execution_status, result.outcome, result.samples), ('sent', 'success', 4))
        self.assertEqual(self.backend.dispatch_count, 1)

    def test_short_budget_does_not_accept_late_third_uia_read(self):
        self.policy = Policy(observation_timeout=4)
        self.backend.sample_hook = self.slow_uia_sample
        result = self.run_action()
        self.assertEqual((result.execution_status, result.outcome), ('sent', 'unknown'))
        self.assertEqual(self.backend.dispatch_count, 1)

    def test_no_visual_change_does_not_prevent_semantic_success(self):
        self.backend.sample_hook = lambda _, f: replace(f, changed_ratio=0.0)
        self.assertEqual(self.run_action().outcome, "success")

    def test_same_hash_on_new_frames_is_fresh(self):
        self.assertEqual(self.run_action().outcome, "success")

    def test_already_satisfied_sends_nothing(self):
        self.backend = SimulatedBackend(self.clock, ["1"])
        result = self.run_action()
        self.assertEqual((result.execution_status, result.reason, result.samples), ("not_sent", "already_satisfied", 3))
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_command_cannot_reuse_an_old_result(self):
        self.backend = SimulatedBackend(self.clock, ["1"])
        result = self.run_action(action(intent="command"))
        self.assertEqual((result.execution_status, result.outcome), ("not_sent", "failed"))
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_command_accepts_new_stable_result(self):
        self.assertEqual(self.run_action(action(intent="command")).outcome, "success")

    def test_declared_terminal_failure_is_failed_and_still_blocks_replay(self):
        self.backend = SimulatedBackend(self.clock, ["0", "rejected"])
        condition = replace(action().condition, failure_values=("rejected",))
        result = self.run_action(action(condition=condition))
        self.assertEqual((result.execution_status, result.outcome), ("sent", "failed"))
        self.assertTrue(result.needs_review)
        blocked = self.run_action(action(operation_id="retry", condition=condition))
        self.assertEqual(blocked.execution_status, "not_sent")
        self.assertEqual(self.backend.dispatch_count, 1)

    def test_unknown_wrong_value_is_not_declared_failure(self):
        self.backend = SimulatedBackend(self.clock, ["0", "loading"])
        condition = replace(action().condition, failure_values=("rejected",))
        self.assertEqual(self.run_action(action(condition=condition)).outcome, "unknown")

    def test_timeout_preserves_sent(self):
        self.backend = SimulatedBackend(self.clock, ["0"])
        result = self.run_action()
        self.assertEqual((result.execution_status, result.outcome, result.needs_review), ("sent", "unknown", True))
        self.assertEqual(self.backend.dispatch_count, 1)

    def test_no_automatic_retry_on_partial_dispatch(self):
        self.backend.send_receipt = Receipt("uncertain", "partial", 2, 3)
        result = self.run_action()
        self.assertEqual((result.execution_status, result.outcome), ("uncertain", "unknown"))
        self.assertEqual(self.backend.dispatch_count, 1)

    def test_send_exception_is_uncertain(self):
        self.backend.dispatch_error = TimeoutError("provider_timeout")
        result = self.run_action()
        self.assertEqual((result.execution_status, result.outcome), ("uncertain", "unknown"))

    def test_rejected_send_is_not_sent(self):
        self.backend.send_receipt = Receipt("not_sent", "last_guard_failed", 0, 3)
        result = self.run_action()
        self.assertEqual((result.execution_status, result.outcome), ("not_sent", "failed"))

    def test_pre_guard_rejects_wrong_window(self):
        self.backend.guard_reason = "wrong_window_identity"
        result = self.run_action()
        self.assertEqual((result.execution_status, result.outcome), ("not_sent", "failed"))
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_focus_loss_after_send_is_unknown(self):
        self.backend.fail_after_dispatch = True
        result = self.run_action()
        self.assertEqual((result.execution_status, result.outcome), ("sent", "unknown"))
        self.assertEqual(self.backend.dispatch_count, 1)

    def test_dpi_or_geometry_change_before_send_rejects_coordinates(self):
        def hook(b, observation):
            if b.guard_calls == 4:
                b.token = ("window", 144, (0, 0, 1200, 900))
        self.backend.guard_hook = hook
        result = self.run_action()
        self.assertEqual((result.execution_status, result.outcome), ("not_sent", "failed"))
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_context_change_inside_capture_invalidates_sample(self):
        def hook(b, observation):
            if b.guard_calls == 3:
                b.token = ("different_geometry",)
        self.backend.guard_hook = hook
        self.assertEqual(self.run_action().outcome, "failed")
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_old_sample_timestamp_cannot_succeed(self):
        self.backend.sample_hook = lambda b, f: replace(f, started=99, finished=99) if b.dispatch_count else f
        result = self.run_action()
        self.assertEqual(result.outcome, "unknown")
        self.assertIn("stale_observation", result.reason)

    def test_duplicate_sequence_cannot_succeed(self):
        self.backend.sample_hook = lambda _, f: replace(f, sequence=1)
        self.assertEqual(self.run_action().outcome, "unknown")

    def test_late_frame_does_not_succeed_past_deadline(self):
        def slow(b, f):
            if b.dispatch_count:
                b.clock.sleep(1)
                return replace(f, finished=b.clock())
            return f
        self.backend.sample_hook = slow
        self.assertEqual(self.run_action().outcome, "unknown")

    def test_nonconsecutive_matches_do_not_accumulate(self):
        self.backend = SimulatedBackend(self.clock, ["0", "1", "1", "0", "1", "1", "0", "0"])
        self.assertEqual(self.run_action().outcome, "unknown")

    def test_missing_evidence_resets_stability_count(self):
        self.backend = SimulatedBackend(self.clock, ["0", "1", "1", Evidence("error"), "1", "1", "0", "0"])
        self.assertEqual(self.run_action().outcome, "unknown")

    def visual_action(self, mode):
        return action(condition=Condition(Selector(name="Display"), "name", "1", mode, VisualSpec("expected.png")))

    def test_uia_visual_conflict_is_unknown(self):
        self.backend.sample_hook = lambda b, f: replace(f, visual=Evidence("valid", False))
        result = self.run_action(self.visual_action("both"))
        self.assertEqual((result.execution_status, result.reason), ("sent", "evidence_conflict"))

    def test_supported_uia_and_visual_must_both_match(self):
        self.backend.sample_hook = lambda b, f: replace(f, visual=Evidence("valid", bool(b.dispatch_count)))
        self.assertEqual(self.run_action(self.visual_action("both")).outcome, "success")

    def test_explicit_unsupported_allows_predeclared_visual_fallback(self):
        self.backend.sample_hook = lambda b, f: replace(f, uia=Evidence("unsupported"), visual=Evidence("valid", bool(b.dispatch_count)))
        self.assertEqual(self.run_action(self.visual_action("uia_or_visual")).outcome, "success")

    def test_uia_error_never_enables_fallback(self):
        self.backend.sample_hook = lambda b, f: replace(f, uia=Evidence("error", detail="RPC timeout"), visual=Evidence("valid", True))
        result = self.run_action(self.visual_action("uia_or_visual"))
        self.assertEqual((result.execution_status, result.outcome), ("not_sent", "unknown"))
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_cannot_weaken_strategy_after_send(self):
        self.backend.sample_hook = lambda b, f: replace(f, uia=Evidence("unsupported") if b.dispatch_count else f.uia, visual=Evidence("valid", True))
        result = self.run_action(self.visual_action("uia_or_visual"))
        self.assertEqual((result.execution_status, result.outcome), ("sent", "unknown"))

    def test_same_operation_returns_record_without_input(self):
        first = self.run_action()
        second = self.run_action()
        self.assertEqual(first, second)
        self.assertEqual(self.backend.dispatch_count, 1)
        self.assertEqual(self.backend.prepare_count, 1)

    def test_operation_id_cannot_change_plan(self):
        self.run_action()
        with self.assertRaises(ValueError):
            self.run_action(action(text="changed"))

    def test_unknown_blocks_new_id_and_changed_selector_without_activation(self):
        self.backend = SimulatedBackend(self.clock, ["0"])
        self.run_action()
        blocked = self.run_action(action(operation_id="other", target=Selector(name="Other")))
        self.assertEqual((blocked.execution_status, blocked.outcome), ("not_sent", "unknown"))
        self.assertEqual(self.backend.dispatch_count, 1)
        self.assertEqual(self.backend.prepare_count, 1)

    def test_crash_reservation_blocks_after_restart(self):
        self.journal.begin(action())
        self.journal.reserve(action())
        self.journal.close()
        self.journal = Journal(self.path)
        same = self.run_action()
        other = self.run_action(action(operation_id="new"))
        self.assertEqual((same.execution_status, same.outcome), ("uncertain", "unknown"))
        self.assertEqual(other.outcome, "unknown")
        self.assertEqual(self.backend.dispatch_count, 0)

    def test_explicit_resolution_allows_a_new_operation(self):
        self.backend = SimulatedBackend(self.clock, ["0"])
        self.run_action()
        self.journal.resolve("operation-1", "definitely_not_executed", "Independent application transaction log checked")
        self.backend = SimulatedBackend(self.clock)
        self.assertEqual(self.run_action(action(operation_id="after-review")).outcome, "success")

    def test_resolution_requires_evidence(self):
        self.run_action()
        with self.assertRaises(ValueError):
            self.journal.resolve("operation-1", "completed", "")

    def test_success_releases_window_for_next_action(self):
        self.run_action()
        self.backend = SimulatedBackend(self.clock)
        self.assertEqual(self.run_action(action(operation_id="next")).outcome, "success")

    def test_action_json_round_trip(self):
        a = self.visual_action("both")
        self.assertEqual(Action.from_dict(a.to_dict()), a)

    def test_invalid_policy_rejected(self):
        with self.assertRaises(ValueError):
            Policy(interval=0)
        with self.assertRaises(ValueError):
            Policy(consecutive=0)
        with self.assertRaises(ValueError):
            Policy(observation_timeout=float("nan"))
