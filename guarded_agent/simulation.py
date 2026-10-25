"""Deterministic fault injection; never reads or changes the user's desktop."""
from collections import deque
from .model import Context, Evidence, Observation, Receipt


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class SimulatedBackend:
    def __init__(self, clock, values=("0", "1", "1", "1"), receipt=Receipt("sent")):
        self.clock = clock
        self.values = deque(values)
        self.last = "0"
        self.send_receipt = receipt
        self.dispatch_count = 0
        self.prepare_count = 0
        self.sequence = 0
        self.token = ("window", 120, (0, 0, 800, 600))
        self.guard_reason = ""
        self.fail_after_dispatch = False
        self.guard_calls = 0
        self.guard_hook = None
        self.sample_hook = None
        self.dispatch_error = None

    def prepare(self, action, timeout=2):
        self.prepare_count += 1

    def guard(self, action, observation=False, timeout=2):
        self.guard_calls += 1
        if self.guard_hook:
            result = self.guard_hook(self, observation)
            if result is not None:
                return result
        reason = self.guard_reason or ("foreground_lost" if self.fail_after_dispatch and self.dispatch_count else "")
        return Context(not reason, self.token, reason)

    def sample(self, action, timeout=2):
        self.sequence += 1
        if self.values:
            self.last = self.values.popleft()
        value = self.last
        evidence = value if isinstance(value, Evidence) else Evidence("valid", value)
        frame = Observation(self.sequence, self.clock(), self.clock(), evidence, region_hash="same_hash_is_allowed")
        return self.sample_hook(self, frame) if self.sample_hook else frame

    def dispatch(self, action, token, timeout=2):
        self.dispatch_count += 1
        if self.dispatch_error:
            raise self.dispatch_error
        return self.send_receipt
