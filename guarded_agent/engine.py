from dataclasses import asdict
import time
from .journal import EffectBlocked
from .model import ContextLost, Policy, Result


class Executor:
    """One dispatch maximum; observations may repeat, inputs never do.

    Backends implement prepare, guard, sample and dispatch, each with a timeout
    argument. Use ProcessBackend for native COM to enforce wall-clock limits.
    """
    def __init__(self, backend, journal, policy=Policy(), clock=time.monotonic, sleep=time.sleep):
        self.backend, self.journal, self.policy = backend, journal, policy
        self.clock, self.sleep = clock, sleep

    def _timeout(self, deadline):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError("observation_deadline")
        return min(remaining, self.policy.call_timeout)

    def _sample(self, action, deadline):
        before = self.backend.guard(action, observation=True, timeout=self._timeout(deadline))
        if not before.ok:
            raise ContextLost(before.reason)
        requested_at = self.clock()
        frame = self.backend.sample(action, timeout=self._timeout(deadline))
        after = self.backend.guard(action, observation=True, timeout=self._timeout(deadline))
        if not after.ok or after.token != before.token:
            raise ContextLost(after.reason or "context_changed_during_sample")
        valid_time = requested_at <= frame.started <= frame.finished <= self.clock() <= deadline
        self.journal.record(action.operation_id, "observation", {**asdict(frame), "context": after.token, "fresh_time": valid_time})
        return frame, after.token, valid_time

    @staticmethod
    def _strategy(condition, frame):
        mode = condition.mode
        if mode == "uia_or_visual":
            if frame.uia.status == "valid":
                return "uia"
            if frame.uia.status == "unsupported" and frame.visual.status == "valid":
                return "visual"
            raise ValueError("fallback_requires_explicit_unsupported_capability")
        required = [frame.uia] if mode == "uia" else [frame.visual] if mode == "visual" else [frame.uia, frame.visual]
        if any(e.status != "valid" for e in required):
            raise ValueError("required_evidence_unavailable_before_dispatch")
        return mode

    @staticmethod
    def _matches(condition, frame, strategy):
        uia = frame.uia.value == condition.expected
        visual = frame.visual.value is True  # Explicit expected-template match.
        evidence = [frame.uia] if strategy == "uia" else [frame.visual] if strategy == "visual" else [frame.uia, frame.visual]
        if any(e.status != "valid" for e in evidence):
            return None, "evidence_unavailable"
        if strategy == "both" and uia != visual:
            return None, "evidence_conflict"
        if strategy != "visual" and frame.uia.value in condition.failure_values:
            return False, "declared_terminal_failure"
        return (uia if strategy == "uia" else visual if strategy == "visual" else uia and visual), ""

    def run(self, action):
        existing = self.journal.begin(action)
        if existing:
            return existing
        status, samples = "not_sent", 0

        def finish(outcome, reason):
            result = Result(action.operation_id, status, outcome, reason, samples,
                            outcome == "unknown" or (status != "not_sent" and outcome != "success"))
            try:
                return self.journal.finish(result)
            except Exception as exc:
                # An already committed reservation remains locked after failure.
                return Result(action.operation_id, status, "unknown", "journal_finish_failed:" + str(exc), samples, True)

        try:
            self.backend.prepare(action, timeout=self.policy.call_timeout)
            initial = self.backend.guard(action, observation=False, timeout=self.policy.call_timeout)
            if not initial.ok:
                return finish("failed", "pre_dispatch_guard:" + initial.reason)
            deadline = self.clock() + self.policy.observation_timeout
            first, token, fresh = self._sample(action, deadline)
            samples += 1
            if not fresh:
                return finish("unknown", "stale_baseline")
            strategy = self._strategy(action.condition, first)
            self.journal.record(action.operation_id, "strategy", {"mode": strategy})
            baseline, reason = self._matches(action.condition, first, strategy)
            if baseline is None:
                return finish("unknown", reason)
            if action.intent == "command" and baseline:
                return finish("failed", "command_requires_a_new_result")

            if baseline:
                # Ensure-state goal already holds; verify stability without input.
                count, last_sequence, last_started = 1, first.sequence, first.started
                if self.policy.consecutive == 1:
                    return finish("success", "already_satisfied")
            else:
                current = self.backend.guard(action, observation=False, timeout=self.policy.call_timeout)
                if not current.ok or current.token != initial.token:
                    return finish("failed", "context_changed_before_dispatch")
                try:
                    self.journal.reserve(action)
                except EffectBlocked as exc:
                    return finish("unknown", str(exc))
                status = "uncertain"  # Persisted before calling backend.
                receipt = self.backend.dispatch(action, current.token, timeout=self.policy.call_timeout)
                if receipt.status not in {"not_sent", "sent", "uncertain"}:
                    raise ValueError("Invalid backend dispatch receipt")
                status = receipt.status
                self.journal.receipt(action.operation_id, status)
                self.journal.record(action.operation_id, "dispatch", asdict(receipt))
                if status == "not_sent":
                    return finish("failed", receipt.detail or "dispatch_rejected")
                if status == "uncertain":
                    return finish("unknown", receipt.detail or "partial_or_unconfirmed_dispatch")
                deadline = self.clock() + self.policy.observation_timeout
                count, last_sequence, last_started = 0, first.sequence, first.started

            last_token, last_problem = token, "postcondition_not_observed"
            while self.clock() < deadline:
                self.sleep(min(self.policy.interval, max(0, deadline - self.clock())))
                if self.clock() >= deadline:
                    break
                frame, token, fresh = self._sample(action, deadline)
                samples += 1
                fresh = fresh and frame.sequence > last_sequence and frame.started > last_started
                last_sequence, last_started = max(last_sequence, frame.sequence), max(last_started, frame.started)
                matched, problem = self._matches(action.condition, frame, strategy)
                if not fresh:
                    count, last_problem = 0, "stale_observation"
                    continue
                if token != last_token:
                    count = 0
                last_token = token
                if problem == "evidence_conflict":
                    return finish("unknown", problem)
                if problem == "declared_terminal_failure":
                    return finish("failed", problem)
                if matched:
                    count += 1
                    if count >= self.policy.consecutive:
                        return finish("success", "already_satisfied" if baseline else "postcondition_stable")
                else:
                    count, last_problem = 0, problem or "postcondition_not_observed"
            return finish("unknown", last_problem + ":deadline")
        except ContextLost as exc:
            return finish("failed" if status == "not_sent" else "unknown", "context_lost:" + str(exc))
        except Exception as exc:
            return finish("unknown", type(exc).__name__ + ":" + str(exc))

    def verify(self, probe):
        """Read-only goal verification. False first reading returns immediately.

        A model's done decision cannot substitute for this verification. This
        method never calls prepare, reserve, dispatch or window activation.
        """
        deadline = self.clock() + self.policy.observation_timeout
        count, samples, last_sequence, last_started, previous_context = 0, 0, -1, -1, None
        strategy = None
        try:
            while self.clock() < deadline:
                frame, context, fresh = self._sample(probe, deadline)
                samples += 1
                if strategy is None:
                    strategy = self._strategy(probe.condition, frame)
                matched, reason = self._matches(probe.condition, frame, strategy)
                fresh = fresh and frame.sequence > last_sequence and frame.started > last_started
                if not fresh or reason:
                    return Result(probe.operation_id, "not_sent", "unknown", reason or "stale_goal_observation", samples, True)
                if not matched:
                    return Result(probe.operation_id, "not_sent", "unknown", "goal_not_yet_satisfied", samples)
                if previous_context is not None and context != previous_context:
                    count = 0
                count += 1
                previous_context, last_sequence, last_started = context, frame.sequence, frame.started
                if count >= self.policy.consecutive:
                    return Result(probe.operation_id, "not_sent", "success", "goal_verified", samples)
                self.sleep(min(self.policy.interval, max(0, deadline - self.clock())))
            return Result(probe.operation_id, "not_sent", "unknown", "goal_verification_timeout", samples, True)
        except Exception as exc:
            return Result(probe.operation_id, "not_sent", "unknown", type(exc).__name__ + ":" + str(exc), samples, True)
