# Installation and recovery / 安装与恢复

[English home](README.md) · [中文首页](README.zh-CN.md)

## Windows

1. Install Python 3.11+ from [python.org](https://www.python.org/downloads/), including the Python launcher or PATH entry. Python itself is a prerequisite, not bundled by this project.
2. Download/extract the complete repository or clone it. Do not run inside a ZIP. Keep this folder in a writable location; paths containing spaces are supported.
3. Double-click `Install.cmd`. It creates or reuses this checkout's `.venv`, runs the project's checks, and leaves the terminal open so the result is visible.
4. Use the project's launch command below after success.

No activation, administrator elevation or PowerShell execution-policy changes are needed. The wrapper tries `py -3`, then `python`, and requires Python 3.11+. For a custom interpreter, set `SETUP_PYTHON` to its executable path. Set `SETUP_NO_PAUSE=1` only for terminal/CI runs.

## What gets installed

`Install.cmd`: creates `.venv` with pip, installs this package in editable mode, then runs `pip check`, offline `doctor` and `demo`. Package/build dependencies can be downloaded from your configured pip index.

`Install-Desktop.cmd`: installs `.[windows,assistant]` and Playwright Chromium, then performs the same verification. This profile needs Windows and internet access. It preserves `PLAYWRIGHT_BROWSERS_PATH`; otherwise an existing checkout-local `browsers/` is used, matching the launcher. Desktop/Chromium installation uses more disk and bandwidth than core setup.

Start with `Start-Guarded.cmd`, or `.venv/bin/python -m guarded_agent web --data-dir data --open` on Linux/macOS for the core UI. Ollama, model weights, Git login and host tools remain separate; follow [model setup](docs/setup.md). Installation does not launch the assistant or act on your desktop. On Windows you can also use `python tools/install.py --profile desktop`.

## Terminal and other operating systems

From the repository root:

```console
python tools/install.py
python tools/install.py --dry-run
```

Use `python3` where needed. `--dry-run` prints the command plan as JSON without creating files or downloading anything. The real installer locates the checkout from its own file, so it also works when invoked from another directory. Core Python tools work on Windows/Linux/macOS; Windows desktop control is Windows-only.

## Results and retries

The installer saves stdout/stderr from verification/dependency steps to `.setup.log` and a machine-readable result to `.setup-state.json`. Both are local and ignored by Git. Exit 0 and `ok: true` mean every selected step passed; failure returns 1 and keeps `ok: false`. The last failed command/output is in the log. An old success is reset before a new attempt.

Rerunning reuses the environment and preserves project data. Guarded's package installation may refresh selected dependencies; archive projects require no pip/package download. A failed download can be retried after fixing the connection. No existing environment is deleted automatically. A folder named `.venv` that is not an environment, or a linked/redirected environment, is refused. If the checkout moved, create a fresh environment after preserving any data you put there; Python environments are not portable between locations.

Archive checks validate preserved bytes/references, not game compatibility or GPU stability. Guarded diagnostics validate setup, not model quality or arbitrary desktop actions. Test coverage includes repeat installation, failure-state handling, non-environment protection, paths with spaces, dry runs and Windows wrapper exit codes. CI additionally performs a real core installation in a fresh `.venv`; it does not download model weights or run game/controller scripts.

## 中文说明

首次需要 Python 3.11+，它不随仓库提供。解压完整仓库后双击 `Install.cmd`；程序会创建或复用仓库内的 `.venv` 并运行检查。失败请查看 `.setup.log`，修复后可重新运行，不会自动删除环境或用户数据。`.setup-state.json` 的 `ok` 只有在所有步骤通过后才为 `true`。模型、驱动、游戏 Mod 和账号凭据不会由核心安装自动配置。

Sources: [Python virtual environments](https://docs.python.org/3/library/venv.html), [installing packages in a virtual environment](https://packaging.python.org/en/latest/guides/installing-using-pip-and-virtual-environments/).
