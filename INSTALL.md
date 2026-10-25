# Installation and recovery / 安装与恢复

[English home](README.md) · [中文首页](README.zh-CN.md)

## Requirements checklist / 安装前提

| Requirement | What you need |
| --- | --- |
| Python | Python 3.11+ with the standard `venv` module; enable `py` or `python` on PATH. Python is not automatically installed |
| Files and permissions | Extract/clone the complete repository into a writable folder; run outside the ZIP. Administrator access is not required |
| Platform | `.cmd` entry points require Windows. Linux/macOS users run `python3 tools/install.py` for the core tools |
| Network and storage | Core setup downloads the package build dependencies from your configured pip index. Desktop setup also downloads optional packages and Chromium; allow sufficient disk space and network access |
| Local chat | Separately install and start Ollama, then download a model declaring tool support. Choose it to fit your RAM/VRAM; no universal memory minimum or GPU requirement is claimed |
| Desktop and GitHub tasks | Desktop control needs the desktop profile and an available Windows desktop session. GitHub tasks also need Git and suitable host-account permissions; these are not configured by the installer |

Successful setup ends with `Installation and verification passed` and `.setup-state.json` containing `ok: true`. If it fails, read `.setup.log`, fix the cause and rerun the installer; do not treat a created `.venv` alone as success.

| 项目 | 具体要求 |
| --- | --- |
| Python | 预先安装 Python 3.11+，包含标准 `venv` 模块，并让 `py` 或 `python` 命令可用；安装入口不会自动安装 Python |
| 文件与权限 | 完整解压或克隆到可写目录，在 ZIP 外运行；不要求管理员权限 |
| 系统 | 双击 `.cmd` 适用于 Windows；Linux/macOS 使用 `python3 tools/install.py` 安装核心工具 |
| 网络与磁盘 | 核心安装需要从配置的 pip 软件源下载构建依赖；桌面完整版还下载可选依赖和 Chromium，请预留相应空间并确保网络可用 |
| 本地聊天 | 另行安装并启动 Ollama，下载声明支持工具调用的模型；按内存/显存选择，没有统一的最低内存或显卡要求承诺 |
| 桌面与 GitHub 任务 | 桌面控制需要完整版依赖和可用的 Windows 桌面会话；GitHub 任务另需 Git 与宿主账号权限，安装器不会代为登录 |

只有显示 `Installation and verification passed` 且 `.setup-state.json` 中 `ok: true` 才表示安装检查通过。失败请查看 `.setup.log`，修复后重试；不能仅凭 `.venv` 已创建就认为成功。

Core setup prepares the program, not a running model. After installation, start Ollama and follow [model setup](docs/setup.md), then open `Start-Guarded.cmd` and select an installed model. Use `.venv\Scripts\python.exe -m guarded_agent doctor --check-ollama` to check the local Ollama connection. The desktop installer adds packages and Chromium but does not install Ollama, download model weights or configure GitHub credentials.

核心安装完成不代表模型已运行。安装后先启动 Ollama，按[模型配置指南](docs/setup.md)下载适合本机的模型，再双击 `Start-Guarded.cmd` 并在界面选择模型。可执行 `.venv\Scripts\python.exe -m guarded_agent doctor --check-ollama` 检查 Ollama 连接。桌面完整版安装依赖与 Chromium，不会安装 Ollama、下载模型权重或配置 GitHub 凭据。

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
