# Validation scope

[Home](../README.md) · [Contributing](../CONTRIBUTING.md)

This setup improvement is validated against the repository source, not the original developer's installed copy. The former nested test module failed on import because it appended a machine-specific D-drive path and imported a package module as a top-level file. It also expected an unverified model `done` result to count as success. It is replaced by portable tests that exercise actual executor behavior.

## Local results — October 25, 2026

| Check | Result / scope |
| --- | --- |
| Portable regression suite | 94 tests passed on Windows with Python 3.14 in a new virtual environment |
| Fresh package installation | Wheel built and installed without optional runtime dependencies |
| Outside-source check | Imported the installed `site-packages` copy; offline doctor and simulated success/unknown/blocked-repeat demo passed |
| Wheel resources | `doctor.py` and all three required UI assets present |
| Dependency consistency | `pip check` passed |
| Opt-in local Ollama query | Local API/model-list check passed; no inference, download, or new model-quality claim |

The workflow additionally runs on Windows/Linux with Python 3.11/3.14. Its live results belong to each specific commit; they are not inferred from the local run above.

## Checks to reproduce

```console
python -m guarded_agent doctor
python -m guarded_agent demo
python -m unittest discover -s tests -v
python -m pip wheel --no-deps --wheel-dir dist .
```

The suite covers executor postconditions and unknown-effect protection, model decision validation, cancellation and timeout boundaries, empty-response recovery, temporary Git repositories and mocked PR publication, offline setup checks, portable launcher paths, browser-path preservation, empty repositories, and maintained Markdown links.

The packaging check also installs the wheel and runs the offline doctor/demo from outside the source tree. That catches missing bundled UI files and accidental reliance on a developer's installed package. CI repeats the outside-source check after replacing its editable install with the built wheel; repeat a separate clean-environment install when changing packaging.

## Boundaries

- Core tests and the demo are offline: no model inference, desktop input, remote push, or real PR creation is required.
- An opt-in `doctor --check-ollama --model NAME` inspects a local API/model declaration only; it does not establish useful inference quality.
- Optional feature packages and browser binaries are not installed by diagnostics. Their real behavior needs separate feature testing.
- Linux CI checks the portable core, not Windows desktop compatibility.
- Old `CAPABILITIES.md`, `VERIFICATION.md`, and `chat-verification/` files retain their historical scope. Local paths cited there are not new evidence or shipped prerequisites.
