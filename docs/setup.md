# Setup guide

[Home](../README.md) · [中文入门](../README.zh-CN.md) · [Troubleshooting](troubleshooting.md)

## Choose the smallest working setup

| Goal | Requirements | Install |
| --- | --- | --- |
| Try the executor simulation | Python 3.11+ | Core package only; no Ollama, GPU, or desktop input |
| Local chat | Core package + running local Ollama + downloaded model | Core package; choose a model that fits your machine |
| File/code work and GitHub tasks | Tool-capable model; PowerShell for command execution; Git and host login for GitHub writes | Core package; GitHub permissions are separate from local model use |
| Native Windows controls/screenshots | Windows and optional automation libraries | `.[windows]` |
| Browser and Office/PDF tools | Optional libraries; Chromium for browser tools | `.[assistant]`, then install Chromium |

The optional dependency groups do not make native Windows controls available on Linux or macOS. Core simulation is portable; the full desktop workflow targets Windows. This repository does not bundle Python, Ollama, model weights, or Git credentials.

## Windows PowerShell

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m guarded_agent doctor
.\.venv\Scripts\python.exe -m guarded_agent demo
```

No activation or PowerShell execution-policy change is needed; invoke the environment's Python directly.

For Windows UI and browser/document features:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[windows,assistant]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

The launcher preserves an existing `PLAYWRIGHT_BROWSERS_PATH`. Otherwise it uses an existing repository-local `browsers/` folder if present, or Playwright's normal browser cache. Do not create an empty `browsers/` folder expecting that to install Chromium.

## Connect a local model

Install and start [Ollama](https://docs.ollama.com/quickstart). Download a model with declared tool support. One example is [qwen3:4b](https://ollama.com/library/qwen3:4b); its presence in this guide is not a quality or hardware-fit guarantee.

```console
ollama pull qwen3:4b
ollama list
```

In PowerShell, from the repository root:

```powershell
.\.venv\Scripts\python.exe -m guarded_agent doctor --check-ollama --model qwen3:4b
.\.venv\Scripts\python.exe -m guarded_agent web --data-dir data --open
```

Select the installed model in the UI. A declared `tools` capability is necessary for the work loop, but not proof that a model will reliably complete every task. Start with a small file-writing task and inspect the saved result. Larger models need more memory; do not assume the original developer's 26B/31B models fit your machine.

For later Windows starts, double-click `Start-Guarded.cmd`. It uses `.venv\Scripts\pythonw.exe` and `launch.pyw`; the portable launcher's data, workspace, logs, and caches stay under this checkout. The CLI command above also pins its data directory to the checkout. Without `--data-dir`, the CLI uses its platform-specific user-data location.

## macOS/Linux core simulation

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m guarded_agent doctor
.venv/bin/python -m guarded_agent demo
.venv/bin/python -m unittest discover -s tests -v
```

This exercises the portable execution core, not native Windows UI control. PowerShell-dependent commands require an installed `pwsh`. No Windows automation is claimed on these platforms.

## Understand the cost boundary

Guarded's local Ollama path does not require a paid model API key or a Copilot subscription. You still provide hardware, storage, electricity, model downloads, and any separately licensed software. Model licenses and GitHub account permissions remain separate. The adapter uses a loopback HTTP Ollama endpoint, not a cloud-provider credential.

## Data and actions

The local UI binds to `127.0.0.1`, normally port 8765. Chat/configuration goes in the chosen data directory; the workspace is selected in the UI. File tools check scope and versions; shell commands run with your operating-system user's actual permissions, so the workspace is **not** a shell sandbox.

Review concrete commands and publication requests in the UI. Repository files, webpages, and screenshots are data rather than authorization. `unknown` means an effect was not confirmed; do not retry it just because the model asks. See the [architecture and behavior guide](architecture.md).

Sources: [Python virtual environments](https://packaging.python.org/en/latest/guides/installing-using-pip-and-virtual-environments/), [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling), and [Playwright installation](https://playwright.dev/python/docs/intro). Package availability checks do not prove that a browser binary or a desktop application is working.
