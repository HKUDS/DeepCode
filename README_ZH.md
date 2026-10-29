<div align="center">

<img src="https://raw.githubusercontent.com/HKUDS/DeepCode/main/assets/logo.png" alt="DeepCode logo" width="120" />

# DeepCode

**为长时间任务打造的开源 coding agent。**<br/>
Goal 可以跨多个回合持续推进，关掉窗口后本地后台服务继续工作，终端、桌面、
浏览器和无界面客户端共用同一批会话。

<p>
  <a href="https://pypi.org/project/deepcode-hku/"><img src="https://img.shields.io/pypi/v/deepcode-hku?label=PyPI" alt="PyPI version" /></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-blue" alt="Python 3.12+" />
  <a href="https://github.com/HKUDS/DeepCode/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green" alt="MIT License" /></a>
  <a href="https://arxiv.org/abs/2512.07921"><img src="https://img.shields.io/badge/arXiv-2512.07921-b31b1b" alt="arXiv paper" /></a>
</p>

[English](https://github.com/HKUDS/DeepCode/blob/main/README.md) · 中文 ·
[网站](https://hkuds.github.io/DeepCode/)

</div>

## 为什么选 DeepCode

- **不止回答一次。** 给 Agent 一个 Goal，它会跨多个回合持续工作并检查结果，
  直到报告完成或受阻。运行过程中可以补充要求、修改 Goal、暂停或停止。
- **关掉客户端也不停。** 回合在本地后台服务里运行，而不是在启动它的窗口里。
  关掉 TUI，在浏览器里打开同一个会话，在那里处理待批准的操作。
- **一个会话，四种客户端。** TUI、Desktop、Web 和 `deepcode exec` 读写同一批
  项目、会话、模型、Skills 和权限设置。
- **能做什么由你决定。** 文件夹要先信任，Agent 才会在里面工作。每个会话使用
  Ask、Read only 或 Full access 之一；Ask 下 shell 命令在系统沙箱里运行；仓库
  里的 hook 要经过你批准。可以用 14 个内置服务商、你自己的 OpenAI 或
  Anthropic 兼容端点，或本地模型。

<p align="center">
  <img src="https://raw.githubusercontent.com/HKUDS/DeepCode/main/assets/readme/web-ui.png" alt="DeepCode Web 客户端：一个修复失败测试的会话，对话里是 Agent 的工具调用，右侧 Review 面板是改动的 diff" width="100%" />
</p>

<p align="center"><sub>一次真实任务后的 Web 客户端：Agent 跑了测试、改了一个文件、
再跑测试，改动在右侧等待审阅。</sub></p>

## 快速开始

**1. 安装。** 使用 [uv](https://docs.astral.sh/uv/)：

```console
uv tool install --python 3.12 deepcode-hku
deepcode init
```

`deepcode init` 会创建 `~/.deepcode`，并把默认访问权限设为 **Ask**。如果终端
找不到 `deepcode`，运行 `uv tool update-shell` 后重新打开终端。Windows 上可以用
`winget install --id astral-sh.uv --exact` 安装 uv。

**2. 添加模型。** 下面以 OpenRouter 为例，其他[支持的服务商](#模型与服务商)
用法相同。输入密钥时不会回显，密钥保存在 `~/.deepcode/credentials.json`。

```console
deepcode provider set my-openrouter --template openrouter --api-key
deepcode provider models my-openrouter --refresh
deepcode provider test my-openrouter --model MODEL_ID
```

把 `MODEL_ID` 换成列表里的模型。在 Desktop 或 Web 中，也可以在
**设置 → AI 提供方** 里完成同样的操作。

**3. 在项目里运行。**

```console
cd /path/to/your-project
deepcode --trust --connection my-openrouter --model MODEL_ID
```

每个项目只需要一次 `--trust`。之后在该目录运行 `deepcode`，用 `/model` 切换
模型。可以先试这样一个任务：

```text
Explain how this project is organized and how to run its tests.
Do not change any files yet.
```

完整的分步教程见 [Your first coding task](docs/guide/getting-started.md)。

## 选择客户端

所有客户端都连接同一个本地服务，在一个客户端里开始的会话可以在任何其他客户端
里继续。

| 客户端 | 启动命令 | 适合 |
|---|---|---|
| **TUI** | `deepcode` | 在终端里工作。见 [The terminal UI](docs/guide/the-tui.md)。 |
| **Web** | `deepcode web` | 在浏览器里使用完整工作台（diff、文件、终端、Goal），同一次安装即可。见 [Web client](docs/WEB_CLIENT.md)。 |
| **Desktop** | `deepcode desktop` | 同样的工作台，原生应用。目前需从源码运行，见[下文](#desktop)。 |
| **无界面** | `deepcode exec "<task>"` | 在脚本或 CI 中运行一个回合；`--json` 以 NDJSON 输出事件。`deepcode loop "<goal>"` 运行 Goal。见 [Headless and automation](docs/HEADLESS_AND_AUTOMATION.md)。 |

`deepcode web --no-open` 只打印登录链接，不打开浏览器。链接只能用一次，60 秒
后失效。

### Desktop

Releases 页面目前没有 Desktop 安装包。Desktop 需要从源码运行，并且需要
Node.js 22+、Rust（stable）以及对应平台的
[Tauri 2 前置依赖](https://v2.tauri.app/start/prerequisites/)：

```console
git clone https://github.com/HKUDS/DeepCode.git
cd DeepCode
npm --prefix desktop ci
npm --prefix desktop run build:web
uv tool install --python 3.12 --force .
deepcode init
deepcode desktop
```

首次启动会构建应用所需资源，需要几分钟；Desktop 运行期间请保持终端开启。
Windows 工具链和排错见 [Desktop 指南](desktop/README.md)。

### 后台服务

启动任何客户端时，如果服务没在运行就会自动启动。关闭客户端后，已接受的工作
继续运行；待批准的操作会等你处理，电脑需要保持唤醒。

| 要做的事 | 命令 |
|---|---|
| 查看是否在运行 | `deepcode service status` |
| 查看最近日志 | `deepcode service logs --lines 100` |
| 等当前工作完成后停止 | `deepcode service stop --drain --timeout 60` |
| 登录系统时自动启动 | `deepcode service install --at-login` |

升级与备份：[Upgrading DeepCode](docs/UPGRADE_AND_RESTORE.md)。
遇到问题：[Troubleshooting](docs/guide/troubleshooting.md)。

## 运行要求与支持平台

| | |
|---|---|
| Python | 3.12 或更高（CI 测试 3.12、3.13、3.14） |
| 操作系统 | macOS、Linux、Windows |
| 工具 | Git，用于审阅改动和 worktree。Linux 上的命令沙箱需要 bubblewrap（`bwrap`）。 |
| 模型 | 一个支持的服务商的 API key，或本地 Ollama、vLLM 服务。模型需要支持工具调用，可用 `deepcode provider test <id> --agent` 检查。 |
| Web 客户端 | 同一台电脑上的新版浏览器 |
| Desktop | Node.js 22+、Rust 和 Tauri 前置依赖（源码构建） |

## 核心功能

- **在仓库里干活。** 读取、搜索、编辑文件，应用补丁，运行命令和测试，每处改动
  都以 diff 呈现。[Getting started](docs/guide/getting-started.md)
- **Goal。** 在 TUI 中用 `/goal <objective>`，在 Desktop 和 Web 中用 Goal 面板。
  Goal 跨回合继续，重启后也能恢复；可选的完成检查会在最近一次检查失败时驳回
  "已完成"。[Goals and automation](docs/guide/goals-and-headless.md)
- **会话与上下文。** 每个会话连同工具调用、批准记录和 Goal 状态一起保存。
  `/resume`、`/compact` 和会话级上下文上限让长任务能继续下去。
  [Sessions](docs/guide/sessions.md)
- **项目说明与记忆。** 读取 `AGENTS.md` 和 `CLAUDE.md`，项目笔记保存在
  `.deepcode/memory/`。[Skills and memory](docs/guide/skills-and-memory.md)
- **模型与服务商。** 命名连接、按会话选择模型和推理强度，使用前可检查连接。
  [Models and providers](docs/guide/models.md)
- **Skills 与插件。** 从 `.agents/skills` 和 `~/.agents/skills` 加载可复用的
  说明，内置 8 个 Skills 和一个 Skill 创建器。插件可以打包 Skills 和 MCP 服务器。
  [Skills](docs/guide/skills-and-memory.md) · [Local plugins](docs/LOCAL_PLUGINS.md)
- **MCP。** 支持 stdio、SSE 和 Streamable HTTP 服务器，内置模板目录和 OAuth
  登录。`deepcode mcp serve` 把 DeepCode 本身作为 MCP 服务器提供。
  [MCP guide](docs/integrations/MCP.md)
- **Agent 预设与子代理。** 预设设定角色并收窄可用工具。子代理可以在独立的 Git
  worktree 里工作。[Configuration](docs/guide/configuration.md#agent-presets)
- **自动化。** 按计划运行保存好的指令，使用同样的 Agent、权限和历史记录。
  [Automations](docs/guide/goals-and-headless.md#persistent-interval-automations)
- **语音输入。** Desktop 和 Web 的输入框可选开启听写。
  [Voice dictation](docs/guide/dictation.md)
- **Paper2Code。** 把论文或技术文档变成代码库。[Paper2Code](docs/paper2code.zh-CN.md)

### 模型与服务商

内置模板：OpenRouter、Requesty、Opper、Forge、Amazon Bedrock、Anthropic、
OpenAI、DeepSeek、Gemini、智谱 AI（Zhipu AI）、DashScope、MiniMax、Ollama 和
vLLM。**Custom** 连接可以指向任何支持 OpenAI Chat Completions、OpenAI Responses
或 Anthropic Messages API 的端点。`deepcode provider list` 会列出它们以及哪些
已经可用。

## 安全与数据

- **数据留在你的电脑上。** 设置、会话、状态和日志都在 `~/.deepcode`（或
  `$DEEPCODE_HOME`）下，项目笔记在 `<project>/.deepcode/memory/`。DeepCode
  没有账号系统，也不发送任何统计数据。
- **本地服务。** 后台服务只监听 `127.0.0.1`（默认端口 3081，被占用时换一个空闲
  端口）。原生客户端用私有令牌认证，Web 客户端用一次性链接登录。
- **项目信任。** Agent 不会在未信任的文件夹里运行。项目自己的配置不能选择 Full
  access、关闭沙箱或改变模型请求的去向。
- **访问预设与批准。** **Ask** 在编辑和执行命令前询问，并在系统沙箱里运行 shell
  命令；**Read only** 拒绝一切改动；**Full access** 去掉批准和沙箱。你设置的
  `deny` 规则在所有预设下都生效。
- **Hooks。** 仓库声明的 hook 要经你批准才会运行；改动过的 hook 要等
  `deepcode hooks approve`。
- **凭据。** API key 保存在 `~/.deepcode/credentials.json`，只有当前用户可读，
  不会返回给任何客户端。shell 命令也看不到这些密钥。
- **模型服务商会收到什么。** 系统提示词、项目说明、工具定义、对话内容和工具结果
  （文件内容、命令输出）会发送到你所选连接的端点。`providers.egress` 可以限制
  允许的主机。使用本地模型时，所有内容都留在本机。

详情见 [Security and data](docs/guide/security.md)。报告安全漏洞请看
[SECURITY.md](SECURITY.md)。

## 文档

| 主题 | 指南 |
|---|---|
| 第一个任务，分步说明 | [Getting started](docs/guide/getting-started.md) |
| 终端界面的按键与命令 | [The terminal UI](docs/guide/the-tui.md) |
| 恢复、压缩和管理会话 | [Sessions](docs/guide/sessions.md) |
| 服务商、本地模型、推理强度 | [Models and providers](docs/guide/models.md) |
| 项目说明、记忆、Skills | [Skills and memory](docs/guide/skills-and-memory.md) |
| Goal、自动化、无界面运行 | [Goals and automation](docs/guide/goals-and-headless.md) |
| 配置文件与环境变量 | [Configuration](docs/guide/configuration.md) |
| 数据、沙箱、批准 | [Security and data](docs/guide/security.md) |
| 启动和连接问题 | [Troubleshooting](docs/guide/troubleshooting.md) |
| 脚本、CI 与管理命令 | [Headless and automation](docs/HEADLESS_AND_AUTOMATION.md) |
| MCP 服务器 | [MCP guide](docs/integrations/MCP.md) |
| 后台服务 | [Local background service](docs/SERVICE_RUNTIME.md) |
| 升级与备份 | [Upgrading DeepCode](docs/UPGRADE_AND_RESTORE.md) |
| 从源码运行 Desktop | [Desktop 指南](desktop/README.md) |
| Paper2Code | [Paper2Code](docs/paper2code.zh-CN.md) |

## 最近更新

- **2026-10-29** — 可选的 microcompact：总结之前先清掉旧的工具输出
  （`agents.defaults.microcompact`）；Goal 完成前的可选检查
  （`goal.completionVerifier`）；Web 客户端可以浏览文件夹；可选的风险检查
  （`DEEPCODE_RISK_EVALUATOR=1`）；项目 hook 需要批准
  （`deepcode hooks status` / `approve`）。
- **2026-10-28** — Agent 预设可以用 `disallowed-tools` 去掉工具；文件工具写入
  hook 文件前会先询问；`deepcode mcp list` 会标出没有固定版本的 MCP 包。
- **2026-10-21** — Windows 后台服务不再弹出终端窗口；Desktop 和 Web 可以给代码
  单独设置字体。

全部更新见 [CHANGELOG_ZH.md](CHANGELOG_ZH.md)。

## 研究起源

DeepCode 起源于 Paper2Code：一个把研究论文变成可运行代码的多智能体系统。在
[PaperBench](https://openai.com/index/paperbench/) 的人类专家子集上，它取得
75.9%，高于论文报告的最佳人类基线（72.4%）。方法见论文
[DeepCode: Open Agentic Coding](https://arxiv.org/abs/2512.07921)。Paper2Code
仍可在 Desktop 和 Web 中使用，见 [Paper2Code](docs/paper2code.zh-CN.md)。

## 参与贡献

开发环境、检查项和 PR 规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。所有提交过
PR 的贡献者都列在 [CONTRIBUTORS.md](CONTRIBUTORS.md) 中。

<a href="https://github.com/HKUDS/DeepCode/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=HKUDS/DeepCode&max=999" alt="DeepCode contributors" />
</a>

## 支持

- 问题反馈与功能建议：[GitHub Issues](https://github.com/HKUDS/DeepCode/issues)
- 安全漏洞：[SECURITY.md](SECURITY.md)
- 社区：飞书群和微信群见
  [HKUDS 主页](https://github.com/HKUDS/.github/blob/main/profile/README.md)

## 引用

如果 DeepCode 对你的研究有帮助，请引用：

```bibtex
@misc{li2025deepcodeopenagenticcoding,
  title         = {DeepCode: Open Agentic Coding},
  author        = {Zongwei Li and Zhonghang Li and Zirui Guo and Xubin Ren and Chao Huang},
  year          = {2025},
  eprint        = {2512.07921},
  archivePrefix = {arXiv},
  primaryClass  = {cs.SE},
  url           = {https://arxiv.org/abs/2512.07921}
}
```

## 许可证

DeepCode 以 [MIT License](LICENSE) 发布。
Copyright © 2025 Data Intelligence Lab at The University of Hong Kong.
