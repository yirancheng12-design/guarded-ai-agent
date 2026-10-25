# Try Guarded in five minutes

[Home](../README.md) · [Installation requirements](../INSTALL.md)

Start with one small file task, inspect the saved file and command output, and tell us where the experience breaks. You do not need to star the project to try it or report a problem.

## Watch an actual run

[Short MP4](https://github.com/yirancheng12-design/guarded-ai-agent/releases/download/v0.4.0/guarded-demo.mp4) · [Full task capture](https://github.com/yirancheng12-design/guarded-ai-agent/releases/download/v0.4.0/guarded-demo-full.webm) · [Verification JSON](../assets/demo-verification.json)

![Actual local Guarded task](../assets/demo-poster.png)

This is an actual Windows run using the already-installed `gemma4:26b` model through local Ollama. The model created `hello.py`, requested approval for `python hello.py`, received exit code 0 and stdout `Hello GitHub`, and read the file back. A separate process then ran the saved file again and checked the output and SHA-256.

The short clip is approximately 28 seconds: the task capture is played at 1.5× and followed by a separate post-run file-preview view. The full task recording is supplied separately. The recorded task duration was 29.156 seconds; this is one warm local run, not a speed benchmark. The Chinese UI is the current shipped interface. The recording is not evidence that every model/task will work or that a 26B model fits your machine.

## First trial

1. Install Python 3.11+, then download and extract the complete [release ZIP](https://github.com/yirancheng12-design/guarded-ai-agent/releases/latest).
2. Double-click `Install.cmd` for core setup. Use `Install-Desktop.cmd` only if you need Windows desktop/browser/document tools. Wait for the success message.
3. Install/start Ollama and select a tool-capable model that fits your hardware; see [setup](setup.md). No model is bundled.
4. Open `Start-Guarded.cmd`, choose an empty work folder, and ask: `Create hello.py containing print("Hello GitHub"). Run it and read the file back to verify the result.`
5. Review each command request before allowing it. Check the output and saved file yourself. If a result is unknown, inspect it instead of repeating the action.

No model available yet? Run the offline `doctor` and `demo` described in the README. Report it as a setup/simulation trial, not a completed real-model task.

## Useful feedback

Post in the repository's public-trial issue or open a specific bug. Include OS/Python version, selected installation profile, model name, expected/actual result, the failing stage and a redacted log excerpt. Do not upload tokens, private chats, account details or unredacted screenshots. Share what failed as well as what worked. State which parts you did not try.

中文：欢迎先试一个小任务，再报告安装、模型连接、命令核对或文件验证的问题。无需先点 Star。请附系统、Python、模型版本及脱敏错误信息；未测试的部分请直接说明。
