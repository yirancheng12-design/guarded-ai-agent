# Guarded — a local AI work agent

[![Portable checks](https://github.com/yirancheng12-design/how-free-ai-agent-set-up/actions/workflows/verify.yml/badge.svg?branch=main)](https://github.com/yirancheng12-design/how-free-ai-agent-set-up/actions/workflows/verify.yml)

[English](README.md) · [简体中文](README.zh-CN.md)

**Run a local model, work on files and code, and verify what actually happened.** Guarded combines an Ollama planner with host-controlled tools, local history, and a Windows desktop workflow. No paid model API key is required for the local path.

Start with a simulation that needs no model or GPU, then add local chat, tool use, browser/document features, or GitHub repository tasks. Hardware, electricity, storage, model downloads, and separately licensed services are still your responsibility.

## One-click installation (Windows)

With Python 3.11+ installed, extract the whole repository and double-click [Install.cmd](Install.cmd) for the core app, or [Install-Desktop.cmd](Install-Desktop.cmd) for Windows desktop, browser and document dependencies plus Chromium. After verification, double-click `Start-Guarded.cmd`. Ollama and a suitable model are separate prerequisites for chat. See [installation details and recovery](INSTALL.md).


## Choose a starting point

| I want to… | Start here |
| --- | --- |
| See the execution behavior without installing a model | [Offline quick start](docs/setup.md) and `python -m guarded_agent demo` |
| Chat with a model on my machine | [Ollama setup](docs/setup.md) |
| Edit files, run tests, and create a GitHub PR | [Repository workflow](docs/github-workflow.md) |
| Operate Windows apps or a real browser | [Optional feature requirements](docs/setup.md) |
| Diagnose an installation problem | `python -m guarded_agent doctor` and [troubleshooting](docs/troubleshooting.md) |

## Try the core first — Windows PowerShell

Clone or download this repository and open its root directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m guarded_agent doctor
.\.venv\Scripts\python.exe -m guarded_agent demo
```

The offline doctor checks required files and optional feature dependencies. The demo exercises simulated success, an unknown outcome, and a blocked repeat. It does not open applications, send desktop input, call a model, or publish anything.

For macOS/Linux simulation commands and feature-specific installation, see [Setup](docs/setup.md). Native desktop control targets Windows; a portable simulation is not a cross-platform desktop-automation claim.

## Add a local model and open the UI

Install and start [Ollama](https://docs.ollama.com/quickstart). Choose a model with tools support that fits your machine. [qwen3:4b](https://ollama.com/library/qwen3:4b) is an example to evaluate, not a guarantee of task quality or hardware fit.

```powershell
ollama pull qwen3:4b
.\.venv\Scripts\python.exe -m guarded_agent doctor --check-ollama --model qwen3:4b
.\.venv\Scripts\python.exe -m guarded_agent web --data-dir data --open
```

Select the installed model in the local UI. Its current labels are in Chinese. Try a small task such as:

> 在工作文件夹创建 hello.py，打印 Hello，运行它并检查输出。

For later Windows launches, double-click `Start-Guarded.cmd` after creating `.venv`. The portable launcher keeps data/workspace/logs/cache inside the checkout and preserves the configured Playwright browser location. The UI binds to loopback, normally `http://127.0.0.1:8765`.

## Capabilities and boundaries

| Capability | Behavior |
| --- | --- |
| Files and code | Read/search/edit with relative paths and file-version checks; retain backups |
| Commands and tests | Run real commands with output/exit status; shell access uses the current OS user's permissions |
| GitHub tasks | Clone a new work directory, use a dedicated branch, validate tests, review publication, then verify the PR |
| Windows apps | Inspect observed windows/controls and verify postconditions; optional Windows dependencies required |
| Browser/documents | Owned Chromium state and Office/PDF readers/writers; optional extras and browser installation required |
| History and cancellation | Local task records; stop later actions on cancellation or an unknown effect |

The model proposes; the host executes and verifies. A workspace is not a shell sandbox. Repository text and webpages do not grant new permission. GitHub credentials remain with the host credential helper. Unknown publication/input effects must not be retried automatically. This is not GitHub's hosted Copilot service or a guarantee of arbitrary app compatibility.

## Verify and contribute

```console
python -m unittest discover -s tests -v
```

The suite uses temporary repositories, simulated observations, and mocked model/network responses. It does not require GitHub credentials, paid APIs, model downloads, or a live desktop. CI runs core checks on Windows/Linux with Python 3.11 and 3.14 and builds a wheel.

See [architecture](docs/architecture.md), [contributing](CONTRIBUTING.md), and [current validation scope](docs/validation.md). Report a reproducible problem through the issue form. If the project is useful, a Star helps you find it again; specific bug reports and tested improvements are welcome too.

## Historical material

The previous machine-specific README is retained as [local installation notes](docs/legacy/local-installation-notes.zh-CN.md). [CAPABILITIES.md](CAPABILITIES.md), [VERIFICATION.md](VERIFICATION.md), and `chat-verification/` describe past local sessions. Paths and external evidence files mentioned there are not prerequisites or proof that a new installation works.
