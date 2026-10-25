# Troubleshooting

[Home](../README.md) · [Setup](setup.md)

## Start with a read-only check

```console
python -m guarded_agent doctor
python -m guarded_agent doctor --check-ollama
```

Use your virtual environment's Python if it is not activated. The default check is offline. `--check-ollama` queries the local model list; `--model NAME` also inspects that model's declared tool capability, without generating text or downloading weights.

Exit code **0** means the checks marked `required` passed. Exit code **2** means at least one required check failed. Missing optional packages do not fail an offline core check; consult their individual results before using that feature.

| Symptom | Check / next step |
| --- | --- |
| `No module named guarded_agent` | Run the editable-install command with the same Python used to launch the app; start in the repository root |
| Ollama connection fails | Start Ollama, check `ollama list`, and rerun `doctor --check-ollama`; the default endpoint is loopback port 11434 |
| No installed models | Pull a suitable model explicitly; diagnostics do not download one |
| Model cannot call tools | Check `doctor --model NAME`; choose a model declaring tool support, then validate a small real task |
| Command execution unavailable | Install PowerShell or `pwsh`; `doctor` reports presence, not whether every command will succeed |
| Native desktop tools fail | They require Windows, the optional Windows libraries, and a usable local desktop session; passing simulation is not desktop certification |
| Browser executable missing | Install the assistant extras and run `python -m playwright install chromium` with the same environment/browser-path configuration |
| `Start-Guarded.cmd` asks for setup | It intentionally requires `.venv`; create it at the repository root instead of relying on a different system Python |
| Port 8765 is occupied | Guarded may reuse its existing service. To run an independent instance, use `web --port 8766 --data-dir data-8766`; do not stop an unidentified process |
| Data appears in another location | The portable launcher uses checkout-local folders. CLI `web` without `--data-dir` uses a user-data default; pass the directory explicitly |
| GitHub clone or publish fails | Check Git installation, repository permissions, and Git Credential Manager login. Do not paste credentials into prompts, source files, or URLs |
| PR publication returned `unknown` | Inspect the actual remote branch/PR and the local publication record. Do not repeat the submission; the tool intentionally blocks a replay |
| A test changes source/generated files | Publication validation becomes stale. Inspect those changes, run a non-mutating validation on the intended final tree, and inspect the result |

For a bug report, include the failing command, Python/OS versions, relevant dependency check results, and a sanitized error excerpt. Never attach chat databases, credentials, or unrelated local files just to prove an installation issue.
