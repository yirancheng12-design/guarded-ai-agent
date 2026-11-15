import time
import unittest
from guarded_agent.isolation import ProcessBackend
from guarded_agent.model import BackendTimeout


class SlowBackend:
    def ping(self, timeout=2):
        return "ok"

    def blocked(self, timeout=2):
        time.sleep(10)


class BrokenBackend:
    def __init__(self):
        raise RuntimeError("initialization_failure")


class IsolationTests(unittest.TestCase):
    def test_worker_returns_results(self):
        with ProcessBackend(factory=SlowBackend) as worker:
            self.assertEqual(worker.ping(timeout=1), "ok")

    def test_hung_native_call_is_bounded_and_worker_not_restarted(self):
        with ProcessBackend(factory=SlowBackend) as worker:
            start = time.monotonic()
            with self.assertRaises(BackendTimeout):
                worker.blocked(timeout=0.1)
            self.assertLess(time.monotonic() - start, 2)
            self.assertFalse(worker.process.is_alive())
            with self.assertRaises(BackendTimeout):
                worker.ping(timeout=1)

    def test_initialization_failure_is_reported(self):
        with self.assertRaisesRegex(RuntimeError, "initialization_failure"):
            ProcessBackend(factory=BrokenBackend)
