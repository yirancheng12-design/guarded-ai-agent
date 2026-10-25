"""Bound every native call, including hung UIA providers, with process isolation."""
import multiprocessing as mp
from .model import BackendTimeout


def _worker(pipe, factory, kwargs):
    try:
        backend = factory(**kwargs)
        pipe.send((True, "ready"))
        while True:
            request = pipe.recv()
            if request is None:
                break
            name, args, options = request
            try:
                pipe.send((True, getattr(backend, name)(*args, **options)))
            except Exception as exc:
                pipe.send((False, type(exc).__name__ + ":" + str(exc)))
    except EOFError:
        pass
    except Exception as exc:
        try:
            pipe.send((False, type(exc).__name__ + ":" + str(exc)))
        except (OSError, EOFError):
            pass
    finally:
        pipe.close()


class ProcessBackend:
    def __init__(self, factory=None, startup_timeout=15, **kwargs):
        if factory is None:
            from .windows import WindowsBackend
            factory = WindowsBackend
        context = mp.get_context("spawn")
        self.pipe, child = context.Pipe()
        self.process = context.Process(target=_worker, args=(child, factory, kwargs), daemon=True)
        self.closed = False
        self.process.start()
        child.close()
        try:
            self._receive(startup_timeout)
        except Exception:
            self.close()
            raise

    def _receive(self, timeout):
        if not self.pipe.poll(timeout):
            self.close()
            raise BackendTimeout("native_worker_timeout;no_automatic_restart")
        try:
            ok, value = self.pipe.recv()
        except (OSError, EOFError) as exc:
            self.close()
            raise BackendTimeout("native_worker_disconnected") from exc
        if not ok:
            raise RuntimeError(value)
        return value

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        def call(*args, timeout=2, **kwargs):
            if self.closed:
                raise BackendTimeout("native_worker_closed")
            self.pipe.send((name, args, {**kwargs, "timeout": timeout}))
            return self._receive(timeout)
        return call

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=0.5)
        self.pipe.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
