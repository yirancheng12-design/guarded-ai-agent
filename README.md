# Guarded Agent：本地 Ollama 综合助手

## GitHub 仓库助手

综合助手已接入 GitHub 仓库任务流程；模型仍使用本机 Ollama，并不冒充或替换 GitHub 官方 Copilot 服务。

可以输入：`帮我检查 https://github.com/owner/repo，先阅读项目说明，在独立分支修复指定问题，运行相关测试，核对差异后创建 PR。`

- `github_prepare(repository, path)`：将指定仓库克隆到新的工作区相对目录，创建 `guarded/` 分支，不覆盖已有目录。
- `github_status(path)`：读取实际仓库、分支、差异、文件快照和发布记录。文件编辑继续使用工作区相对路径，例如 `repo/src/main.py`。
- `github_run_tests(path, command)`：在仓库目录运行实际测试，记录退出码及文件快照；测试后文件变化需要重新测试。
- `github_publish(path, title, body, files)`：核对明确文件列表和差异、经界面批准后提交分支并创建 PR，随后读回远端提交。不会合并主分支、强推或删除分支。

GitHub 身份由本机 Git Credential Manager 提供，凭据不交给模型、不写入仓库。未登录时公开仓库可匿名读取，但推送/创建 PR 需要相应权限。网络异常导致发布状态未知时，宿主保留持久记录并阻止重发；需要先在 GitHub 核对实际分支/PR。已有 PR 的自动追加修改与自动合并不在这版流程中，新任务使用新分支。

## 从聊天界面开始

双击桌面的 **Guarded 聊天助手**，或本目录的 **启动聊天界面.cmd**。默认地址是 `http://127.0.0.1:8765`，只接受本机连接。服务在后台运行，再次启动会打开已有服务；关闭网页不会自动终止正在执行的任务。

界面默认使用“综合助手”，支持这些真实工具：

- 在选定工作文件夹中列出、读取、创建、修改和移动文本/代码文件；覆盖前检查内容哈希，保存旧版本备份，写入后回读校验。
- 让 Ollama 编写代码并提出 PowerShell 命令；界面展示完整命令和工作目录，你允许后才执行，返回退出码和输出。
- 搜索公开网页、读取网页正文与链接；过程显示查询词和目标网址，回复保留来源链接。搜索每轮最多两次，避免反复改写查询。
- 明确输入“打开微信”“打开 WeGame”“打开记事本”“打开计算器”，从本机安装信息找到程序并验证窗口。
- 列出 Windows 窗口、读取 UIA 控件，并提出带后置条件的单步桌面动作；每次应用内操作先展示具体内容，允许后再执行并独立验证。

例如输入：`在工作文件夹创建 hello.py，打印 Hello，然后运行它并检查输出。`

点击侧栏 **工作文件夹与设置** 可以设置工作位置、打开文件夹，或导入已有的桌面任务配置。文件工具只访问选定目录；命令使用当前 Windows 用户权限，**工作目录不是命令的系统沙箱**，请核对命令卡片。密码、登录、UAC 等系统确认由用户处理。WeGame 的安装版本若要求提升权限，会使用正常的 Windows 启动入口，是否放行由系统确认决定。

0.4 新增项目库与对话绑定：输入框上方显示当前项目，切换项目时会新建对话，继续旧对话始终回到它的原文件夹。点击 **查看修改** 可查看本对话文件工具留下的文本差异，预览并确认恢复旧版本；如果文件在预览后又变化，将拒绝覆盖。恢复操作也会保留备份。命令、外部软件和旧版本产生的改动不在此列表。已保存文件支持直接 **查看内容**。

普通公开网页查询可直接进行；对话一旦读取过本地文件、命令输出或窗口内容，后续联网会先展示查询词/网址供你核对，避免把本地资料带出去。

“纯聊天”只与 Ollama 对话，不调用工具。“计算器演示”只演示输入 12，不操作桌面；输入明确的打开应用请求时，界面会提示并切换到综合助手。“预设桌面任务”保留旧的动作配置工作流。

停止按钮阻止后续调用；正在发送的原生动作会先验证，正在等待模型或网络的请求可能需要等待本次调用结束。命令最长运行 90 秒，停止或超时后返回 unknown，不自动重发相同命令。所有桌面对话共享防重日志。用户拒绝确认时，该轮任务立即停止，不换一种说法重新请求。

本机桌面入口把程序和数据统一保存在 **D:\ai数据**。聊天和配置在 `data`，工作文件和附件在 `workspace`，启动日志在 `logs`。修改过的旧文件备份在 `data/backups`，原路径保存在相邻 JSON 中；命令输出在 `data/commands`，桌面证据在 `data/evidence`。备份不会自动清空。完整目录说明见 [CAPABILITIES.md](CAPABILITIES.md)。

本机工作文件夹为 **D:\ai数据\workspace**，原桌面工作区中的文件已保留。完整验收记录见 [VERIFICATION.md](VERIFICATION.md)；新版扩展能力见 [CAPABILITIES.md](CAPABILITIES.md)。回归测试结果以 test-results.json 为准，另有实际 Ollama、文件/命令、网页、微信启动和原生 UIA 操作证据。WeGame 按用户要求暂时跳过。

开发者启动方式：

```powershell
& 'D:\ai数据\runtime\Scripts\python.exe' -m guarded_agent web --data-dir 'D:\ai数据\data' --open
& 'D:\ai数据\runtime\Scripts\python.exe' -m guarded_agent web --port 8766 --data-dir 'D:\ai数据\验收记录\qa-data'
```

本机 `gemma4:26b` 声明支持 tool calling；综合助手使用 Ollama 原生工具调用循环。[Ollama 工具调用协议](https://docs.ollama.com/capabilities/tool-calling)。它具备本地工作工具，但不等同于 Codex 的模型能力、已登录的连接器或任意软件兼容性。公开网页工具用于搜索和正文读取；另有独立 Chromium 浏览器支持页面读取、填写、点击与后置条件验证。文件工具支持 UTF-8 文本、代码搜索与局部修改，并可读取 PDF、DOCX、XLSX、PPTX，创建基础 Office 文档和分析图片。无法验证的桌面动作会停止，不承诺任意软件都能自动完成。

这是根据本次评审条款建立的独立 Python 实现，已接入本地 Ollama。原工作区没有现有 Agent 代码，因此本项目提供 Ollama 规划循环、状态驱动执行核心、真实 Windows 后端、命令行工具和故障注入测试。

## 以 Ollama 为基底运行

已在本机使用 `gemma4:26b` 完成真实 Windows 输入验收：Ollama 选择两次点击，原生测试应用显示 `12`，执行器逐次验证并独立确认最终目标。Unicode 输入、Toggle/Invoke、多帧 UIA 与截图、防重和失焦停机也已通过。见 `live-verification-final-03/result.json`。另保留模拟任务记录 `ollama-demo-result.json`。本机也检测到 `gemma4:31b`，但没有测试该模型。

在本目录执行：

```powershell
python -m guarded_agent ollama-models
python -m guarded_agent ollama-demo --model gemma4:26b
```

默认连接 `http://127.0.0.1:11434`。适配器使用 Ollama `/api/chat` 的 JSON Schema 结构化输出，并在本地再次验证响应；没有把模型返回内容当作 Python、Shell 或任意工具调用执行。[Ollama 结构化输出](https://ollama.com/blog/structured-outputs)

真实桌面使用 `examples/ollama-profile.example.json` 作为任务配置模板：填写实际 HWND/PID/创建时间、允许的动作、各动作后置条件和任务最终目标。然后运行：

```powershell
python -m guarded_agent agent my-profile.json --task "从清零状态输入12" --model gemma4:26b --run-id enter-12-001 --journal ../../work/runs/actions.sqlite3 --max-steps 5
```

这条命令会实际操作配置中的窗口。循环是：读取目标状态 → Ollama 选择一个已配置动作 → 再次检查界面是否变化 → 执行一次 → 独立验证 → 检查最终目标。失败或未知都会停止，不会请模型决定是否重发。模型说 done 也不能跳过目标验证。

这个 `agent` 命令使用**预设任务配置**：模型读取控件名称、类型、可用性、输入框值和开关状态，并选择预先配置的动作；实际文本和验证条件由配置固定。聊天界面的综合助手另支持从刚读取的 UIA 控件生成单步动作及文本，经过用户核对和最新状态校验后执行。两种方式都使用同一个状态驱动执行器。综合助手还可以经核对后截取指定前台窗口并交给本机视觉模型分析。操作仍由 UIA 或浏览器 DOM 控件定位，不开放任意全屏坐标输入；UIA 未暴露的原生控件可能无法操作。

使用 `agent` 命令执行其他任务时，应提供对应控件和动作配置。综合助手可现场读取控件，但仍需要对目标应用分别验收，不能把计算器测试视为任意软件都能自动操作的证明。

## 已实现的执行约束

- 每个动作必须绑定目标窗口、目标控件和后置条件。
- `execution_status` 只记录发送事实：`not_sent / sent / uncertain`；`outcome` 独立记录 `success / failed / unknown`。
- 每次执行最多发送一次动作。只重复观测，不自动重复点击、输入或提交。
- SQLite 在发送前提交持久化预留记录。未知结果锁住该绑定窗口的后续写操作；重启、换操作 ID、换目标选择器均不能绕过。
- SQLite 日志保留原计划、发送回执、采样时间、UIA/视觉证据和最终原因。结果未知时不自动重置环境。
- 进程身份通过 HWND、PID、进程创建时间绑定。窗口标题只供用户发现窗口，不用于唯一身份判断。
- 动作前尝试激活一次，然后校验前台、DPI、窗口/控件几何、可用性；文本输入还校验实际焦点。前台句柄按 GA_ROOT 归属到顶层窗口，允许本窗口内的子控件，不把独立 owned popup 合并成原窗口。
- 启动和每次上下文检查都读取 Windows 会话锁定状态及当前输入桌面。锁屏、会话断开、桌面切换或状态不可读会阻止输入；最小化窗口在激活前进行有时限的还原。
- 鼠标点击检查落点所在原生窗口和 UIA 元素。`IsOffscreen` 不作为无遮挡证据，也不使用不存在的标准 `IsObscured` 属性。
- 原生输入前再次检查。命名互斥量让本执行器的多个实例协作串行操作；它不能阻止用户或其他软件改变桌面。
- Windows 后端运行在独立、无窗口的 COM MTA 进程中，采用 Per-Monitor V2，避免修改调用方的 DPI 配置。原生调用超时会终止工作进程，不会自动重启后重发。
- 支持 `click`、字面文本 `type_text`、UIA `invoke` 和 `toggle`。`type_text` 不自动清空输入框，后置条件应包含预期的完整文本。

## 快速运行

Python 3.11+ 可运行模拟核心；真实 Windows 后端建议使用已验证的 Python 3.14、Windows 10/11。以下命令在本目录执行：

```powershell
python -m guarded_agent demo
python -m unittest discover -v
```

`demo` 完全使用模拟后端，展示成功、发送后超时和阻止重复执行；不会访问桌面。真实 Windows 后端依赖按本机验证版本固定：

```powershell
python -m pip install -e ".[windows,assistant]"
python -m guarded_agent probe
```

`probe` 是只读诊断：检查 PMv2、物理桌面坐标、物理鼠标坐标、真实 UIA COM 连接及 `session.interactive`。只有会话处于活动、解锁状态且输入桌面一致，`session.interactive` 才为 true。

## 绑定真实目标

```powershell
python -m guarded_agent windows
python -m guarded_agent inspect 0x123456
```

`windows` 返回 HWND、PID、进程创建时间及标题；`inspect` 返回指定窗口的 UIA 选择器。必须从实际结果选择唯一目标，不要猜测示例 HWND。查询控件时采用精确匹配，零个或多个动作目标都会拒绝执行。

复制 `examples/action.example.json`，填写实际窗口身份、动作目标、结果控件及精确期望值。模板中所有零值和 `REPLACE_...` 都是占位符。计算器结果控件的 `Name` 可能包含本地化前缀，不应假设一定是单个数字。

```powershell
python -m guarded_agent run my-action.json --journal ../../work/runs/actions.sqlite3 --evidence-dir ../../work/runs/evidence
```

`run` 会实际操作指定窗口。默认连续 3 次有效采样、间隔 200 ms、观测总时限 10 s、单次原生调用时限 5 s。`run` 和 `agent` 均可通过 `--samples`、`--interval`、`--timeout`、`--call-timeout` 调整。

`intent=ensure` 表示达到目标状态：若执行前已满足，连续验证后返回 `not_sent + success`。`intent=command` 表示必须产生新结果：若基线已经满足条件，则拒绝发送，避免旧弹窗冒充本次成功。对于真正的业务提交，应将新回执 ID、版本或其他本次操作标识编码到后置条件；通用 UI 状态跃迁不能证明服务器端事务的唯一因果关系。

## 后置条件和视觉模式

UIA `property` 支持：`value`（ValuePattern）、`text`（TextPattern）、`name`、`toggle`（0/1/2）、`exists`（布尔值）。不会把 Name 自动当成 Value，也不会把 Invoke 返回当成任务完成。

可在 condition 中声明 `failure_values`（如 `["交易被拒绝"]`），仅将这些已知终态作为明确失败。其他不符合预期的状态可能仍在加载，超时后报告 unknown。即使发送后观察到明确失败，也保留写锁等待核对，避免忽略部分副作用。

| mode | 规则 |
|---|---|
| `uia` | UIA 目标属性必须符合预期 |
| `visual` | 必须匹配预先给定的期望图像模板 |
| `both` | UIA 和模板都必须匹配；二者冲突返回 unknown |
| `uia_or_visual` | 只有基线明确返回“不支持该模式”时，才选定视觉验证；运行中不会降级 |

视觉配置示例（加入 condition）：

```json
{
  "mode": "both",
  "visual": {
    "template": "expected-result.png",
    "pixel_tolerance": 24,
    "max_changed_ratio": 0.01,
    "masks": [{"left": 98, "top": 4, "width": 2, "height": 20}]
  }
}
```

默认 ROI 是**结果控件**的物理矩形，而非被点击按钮的矩形。可显式给定物理 `roi`；固定 ROI 遇到窗口几何变化会失效。模板必须与 ROI 尺寸一致，不会自动缩放模板以掩盖坐标错误。`masks` 使用 ROI 内相对像素坐标，可屏蔽已知插入符区域；鼠标附近区域自动屏蔽。掩码覆盖整个区域会报告证据无效。

执行器的视觉后端实现期望图像匹配和相对于基线的像素变化率，不使用 OCR 或视觉模型来替代动作判定。综合助手另提供本机视觉模型图片分析工具。阈值应根据真实目标应用校准。小变化不是失败，大变化不是成功；最终判定依赖声明的后置条件。

每帧必须重新采集，序号递增且单调时钟时间落在本次采样区间内。相同哈希可以来自有效的新截图。证据缺失、旧帧或不满足条件会清零连续计数；冲突和失去上下文立即停止为 unknown（未发送前的确定守卫失败为 failed）。

## 坐标约定

UIA 矩形和 `GetPhysicalCursorPos` 已使用物理像素，不再乘除 DPI。`point` 是相对于当前动作目标矩形的归一化坐标，默认中心点。

`ImageMapping` 提供模型缩小图/带留白图到原始物理 ROI 的逆映射，带观测版本校验；不允许将整屏归一化坐标套入按钮矩形。Windows 输入适配层独立转换到 SendInput 的虚拟桌面绝对坐标域，保留负屏幕原点。不同 DPI/跨屏移动后必须重新观测。

## 未知结果的复核

不得删除或更换执行日志来规避未知结果。只有独立确认“已经完成”或“确定未执行”，才可以显式解除窗口写锁：

```powershell
python -m guarded_agent resolve operation-id --journal ../../work/runs/actions.sqlite3 --resolution definitely_not_executed --evidence "已核对应用交易记录，确认没有执行"
```

该命令不操作应用，也不会重写原始发送事实。若确实需要后续动作，使用新 operation_id；同一个 ID 始终返回其原始记录。应在执行器停止后进行人工复核，不要在原操作仍运行时解除预留。

## 接入与边界

本机 103 项回归测试和 10 项真实桌面验收全部通过。已修复重复 UIA 查询和观测预算不足的问题。真实验收运行于独立原生测试窗口；其他应用需各自配置选择器、动作和后置条件。详见 `VERIFICATION.md`。调用激活 API 不保证 Windows 允许取得前台；失败时程序停止，不发送输入。

可在可交互的 Windows 桌面上运行独立测试应用进行验收（会暂时使用鼠标、键盘；输出目录必须不存在）：

```powershell
python examples/verify_live.py --model gemma4:26b --output live-run-new --wait-for-unlock 900
```

脚本最多等待 15 分钟，确认桌面解锁后才创建测试窗口。出现 `session_locked` 时应解锁 Windows；出现 `session_not_active` 时应恢复对应桌面会话；出现 `input_desktop_changed` 或 `input_desktop_unavailable` 时应回到普通可交互桌面。如果解锁后仍被系统拒绝前台激活，可另加 `--wait-for-foreground`，在测试窗口出现后将它置前。

该脚本覆盖真实点击、Unicode 输入、Toggle/Invoke、多帧验证、失焦停机及重启后的未知操作锁；本机已完成这些真实输入用例，证据见 `live-verification-final-03/result.json`。测试只操作自行创建的窗口。每项结果即时写入输出目录的 `result.json`，`status=passed` 且 `success=true` 才表示全部通过；`waiting_for_unlock`、`running` 和空检查列表都不是通过。

核心接口是 `Executor(backend, journal, policy).run(action)`。自定义后端需要实现 `prepare / guard / sample / dispatch`；真实原生调用必须使用 `ProcessBackend`，不能仅依赖协作式 timeout 参数。执行器和 SQLite 连接按单控制线程使用。

观测期间默认要求原窗口保持前台。预期结果出现在同进程的 owned window 时，可显式启用 `allow_owned_result`，查询将覆盖这些窗口；任意同 PID 的其他窗口不会被自动接受。

这里没有宣称共享桌面的绝对原子性。最终检查与系统输入之间仍存在竞态；用户改动焦点、遮挡和 UIA 提供者错误可能使结果进入 unknown。UIA 读取与截图也不是原子快照，连续采样和前后上下文检查用于发现不一致。

## 代码定位

- `model.py`：动作、后置条件、证据、状态、策略。
- `engine.py`：守卫、一次发送、多次观测及判定。
- `journal.py`：SQLite 持久化预留、结果和人工核对记录。
- `geometry.py`、`vision.py`：坐标映射、模板匹配、噪声屏蔽。
- `winapi.py`、`windows.py`：Win32/UIA、输入和截图适配。
- `isolation.py`：原生调用的进程隔离和强制超时。
- `session.py`：Windows 会话锁定、连接和输入桌面诊断。
- `ollama_agent.py`：本地模型调用、动作范围校验、逐步规划和独立目标验证。
- `tests/`：正常流程、故障、重启、防重、坐标、像素及超时测试。

技术依据：[UIA 坐标](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-screenscaling)、[DPI 配置](https://learn.microsoft.com/en-us/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process)、[SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)、[按钮模式](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-supportbuttoncontroltype)。
