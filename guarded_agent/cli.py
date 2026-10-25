from dataclasses import asdict
import argparse
import json
from pathlib import Path
import sys
import tempfile
from .engine import Executor
from .journal import Journal
from .model import Action, Condition, Policy, Selector, WindowIdentity


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Guarded Windows actions; use demo for a desktop-free run")
    sub = parser.add_subparsers(dest="command", required=True)
    web = sub.add_parser("web", help="Open the local Ollama chat and task interface")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--data-dir", type=Path)
    web.add_argument("--open", action="store_true", dest="open_browser")
    sub.add_parser("demo", help="Run simulated success and blocked-repeat scenarios; no desktop access")
    sub.add_parser("probe", help="Read-only PMv2/UIA/physical-coordinate diagnostic")
    sub.add_parser("windows", help="List visible windows and their process creation identities")
    inspect = sub.add_parser("inspect", help="Read selectors within one explicit HWND")
    inspect.add_argument("hwnd", type=lambda v: int(v, 0))
    run = sub.add_parser("run", help="Execute one explicitly configured action")
    run.add_argument("plan", type=Path)
    run.add_argument("--journal", type=Path, required=True)
    run.add_argument("--evidence-dir", type=Path)
    resolve = sub.add_parser("resolve", help="Record an independently verified resolution; sends no input")
    resolve.add_argument("operation_id")
    resolve.add_argument("--journal", type=Path, required=True)
    resolve.add_argument("--resolution", choices=["completed", "definitely_not_executed"], required=True)
    resolve.add_argument("--evidence", required=True)
    models = sub.add_parser("ollama-models", help="List models from the local Ollama service")
    models.add_argument("--base-url", default="http://127.0.0.1:11434")
    ollama_demo = sub.add_parser("ollama-demo", help="Real Ollama planning with a simulated calculator, no desktop input")
    ollama_demo.add_argument("--model", required=True)
    ollama_demo.add_argument("--base-url", default="http://127.0.0.1:11434")
    agent = sub.add_parser("agent", help="Run Ollama within an explicit Windows task profile")
    agent.add_argument("profile", type=Path)
    agent.add_argument("--task", required=True)
    agent.add_argument("--model", required=True)
    agent.add_argument("--run-id", required=True)
    agent.add_argument("--journal", type=Path, required=True)
    agent.add_argument("--base-url", default="http://127.0.0.1:11434")
    agent.add_argument("--max-steps", type=int, default=None, help="Optional finite action budget; default: unlimited")
    for command in (run, agent):
        command.add_argument("--samples", type=int, default=Policy().consecutive)
        command.add_argument("--interval", type=float, default=Policy().interval)
        command.add_argument("--timeout", type=float, default=Policy().observation_timeout)
        command.add_argument("--call-timeout", type=float, default=Policy().call_timeout)
    args = parser.parse_args(argv)
    try:
        if args.command == "web":
            from .web import serve
            if not 1 <= args.port <= 65535:
                raise ValueError("Port must be between 1 and 65535")
            serve(args.port, args.data_dir, args.open_browser)
            return 0
        if args.command in {"ollama-models", "ollama-demo", "agent"}:
            from .ollama_agent import OllamaClient, OllamaAgent, CalculatorSimulation, calculator_profile, parse_profile
            if args.command == "ollama-models":
                emit(OllamaClient("", args.base_url).models())
                return 0
            client = OllamaClient(args.model, args.base_url)
            if args.command == "ollama-demo":
                from .simulation import FakeClock
                with tempfile.TemporaryDirectory(prefix="guarded-ollama-") as scratch:
                    journal = Journal(Path(scratch) / "events.sqlite3")
                    try:
                        clock = FakeClock()
                        backend = CalculatorSimulation(clock)
                        executor = Executor(backend, journal, clock=clock, sleep=clock.sleep)
                        result = OllamaAgent(client, executor).run(
                            "Starting from display 0, enter 12: press 1 first, then press 2. Stop when display is 12.",
                            *calculator_profile(), run_id="ollama-calculator-demo")
                        result["model"] = args.model
                        result["simulated_display"] = backend.display
                        result["simulated_dispatches"] = backend.dispatch_count
                        emit(result)
                        return 0 if result["outcome"] == "success" else 2
                    finally:
                        journal.close()
            from .isolation import ProcessBackend
            window, catalog, probe = parse_profile(json.loads(args.profile.read_text(encoding="utf-8-sig")))
            args.journal.parent.mkdir(parents=True, exist_ok=True)
            journal = Journal(args.journal)
            try:
                with ProcessBackend() as backend:
                    policy = Policy(consecutive=args.samples, interval=args.interval,
                                    observation_timeout=args.timeout, call_timeout=args.call_timeout)
                    result = OllamaAgent(client, Executor(backend, journal, policy), max_steps=args.max_steps).run(
                        args.task, window, catalog, probe, args.run_id)
                    emit(result)
                    return 0 if result["outcome"] == "success" else 2
            finally:
                journal.close()
        if args.command == "demo":
            from .simulation import FakeClock, SimulatedBackend
            with tempfile.TemporaryDirectory(prefix="guarded-agent-") as scratch:
                journal = Journal(Path(scratch) / "events.sqlite3")
                try:
                    clock = FakeClock()
                    backend = SimulatedBackend(clock)
                    action = Action("demo-success", WindowIdentity(1, 2, 3), Selector(name="Digit 1"), "click",
                                    Condition(Selector(name="Display"), "name", "1"))
                    runner = Executor(backend, journal, clock=clock, sleep=clock.sleep)
                    success = runner.run(action)
                    backend = SimulatedBackend(clock, values=["0"])
                    runner = Executor(backend, journal, clock=clock, sleep=clock.sleep)
                    plan = action.to_dict()
                    plan["operation_id"] = "demo-timeout"
                    unknown = runner.run(Action.from_dict(plan))
                    plan["operation_id"] = "demo-new-id-blocked"
                    blocked = runner.run(Action.from_dict(plan))
                    emit({"success": success.to_dict(), "timeout": unknown.to_dict(),
                          "repeat": blocked.to_dict(), "timeout_backend_dispatches": backend.dispatch_count})
                finally:
                    journal.close()
            return 0
        if args.command == "resolve":
            journal = Journal(args.journal)
            try:
                journal.resolve(args.operation_id, args.resolution, args.evidence)
                emit({"resolved": args.operation_id, "resolution": args.resolution})
            finally:
                journal.close()
            return 0
        from .isolation import ProcessBackend
        if args.command == "run":
            action = Action.from_dict(json.loads(args.plan.read_text(encoding="utf-8-sig")))
            policy = Policy(consecutive=args.samples, interval=args.interval,
                            observation_timeout=args.timeout, call_timeout=args.call_timeout)
            args.journal.parent.mkdir(parents=True, exist_ok=True)
            journal = Journal(args.journal)
            try:
                with ProcessBackend(evidence_dir=str(args.evidence_dir) if args.evidence_dir else None) as backend:
                    result = Executor(backend, journal, policy).run(action)
                    emit(result.to_dict())
                    return 0 if result.outcome == "success" else 2
            finally:
                journal.close()
        with ProcessBackend() as backend:
            if args.command == "probe":
                emit(backend.probe(timeout=5))
            elif args.command == "windows":
                emit(backend.list_windows(timeout=5))
            else:
                emit(backend.inspect(args.hwnd, timeout=5))
        return 0
    except Exception as exc:
        emit({"error": type(exc).__name__, "detail": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
