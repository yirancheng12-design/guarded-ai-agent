# Contributing

Start with a small, reproducible problem. Describe expected/actual behavior, environment, and a sanitized error excerpt. Use the [setup guide](docs/setup.md) and [diagnostics](docs/troubleshooting.md) before assuming a model or desktop failure.

From the repository root:

```console
python -m pip install -e .
python -m unittest discover -s tests -v
python -m guarded_agent doctor
python -m guarded_agent demo
```

Tests must use temporary directories, simulated desktop evidence, and mocked remote publication. Do not require the original developer's disk layout, live credentials, paid APIs, a particular model, or a real desktop session for the portable suite. Never weaken an unknown-result stop merely to make a model continue.

For packaging changes, build and install a wheel in a clean environment, then run the offline doctor and demo outside the source directory. UI assets must be included in the wheel. Keep generated data, logs, caches, credentials, and local model files out of commits.

Historical notes under `docs/legacy/`, `CAPABILITIES.md`, `VERIFICATION.md`, and `chat-verification/` are historical material, not an up-to-date compatibility promise. New validation claims should identify exactly what was run. A green offline CI does not establish a working real Windows desktop or a model's tool-use quality.
