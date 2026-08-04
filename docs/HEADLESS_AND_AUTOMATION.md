# DeepCode Headless and Automation

<p align="center">
  Run the same DeepCode Agent and Session runtime from scripts, CI, and scheduled workflows.
</p>

<p align="center">
  <a href="../README.md">← Project README</a> ·
  <a href="#english">English</a> · <a href="#中文">中文</a>
</p>

---

## English

DeepCode is designed to be used through Desktop or the interactive CLI. The
commands in this guide are advanced integration surfaces for environments where
opening an interface is inconvenient: CI jobs, shell scripts, scheduled work,
and administration.

They do not start a second, simplified Agent. Headless work uses the same
Projects, canonical Sessions, models, Skills, permissions, Goals, tools,
recovery rules, and evidence as Desktop and CLI.

### Before you begin

Install and initialize DeepCode, configure a model connection, and open the
target repository in DeepCode at least once:

```console
uv tool install deepcode-hku
deepcode init
cd <your-project>
deepcode
```

The first Agent run requires explicit Project trust. Trust confirms which
workspace DeepCode may operate in; it does not grant unrestricted tool access.

Use `deepcode <command> --help` whenever you need the complete option list.

### Run one Turn without an interface

`deepcode exec` submits one durable coding Turn and streams its progress to the
terminal:

```console
deepcode exec "Fix the failing tests and explain the root cause"
```

The current directory becomes the workspace for a new Session. To continue an
existing canonical Session:

```console
deepcode exec "Now add a regression test" --resume <session-id>
```

Useful integration options:

```console
deepcode exec "Review this change" \
  --connection <connection-id> \
  --model <model-id> \
  --effort high \
  --skill security-review \
  --access read-only \
  --json
```

- `--json` emits newline-delimited JSON events for another process to consume.
- `--transcript normal|verbose|summary` controls human-readable output only.
- `--skill` is repeatable and selects Skills for this Turn.
- `--resume` keeps the original Session and stored workspace unless an explicit
  `--workspace` override is supplied for this process.
- `--max-iterations` is a diagnostic sampling limit; it is unlimited by
  default.

In non-interactive **Ask** mode, an approval that cannot be answered is denied.
Use `--access read-only` for inspection. Use `--access full-access` only in a
workspace where unrestricted local execution is intentionally acceptable.

### Run a durable Goal headlessly

Desktop and the interactive CLI are the normal places to create and steer a
Goal. `deepcode loop` is the compatibility entry point for scripts that need a
durable Goal without keeping an interface open:

```console
deepcode loop "Implement the requested feature and verify it"
```

An optional evidence command tells the Agent what it must run and inspect
before deciding whether the Goal is complete:

```console
deepcode loop "Implement the requested feature" \
  --test-cmd "python -m pytest -q"
```

Resume the same Goal and canonical Session after the process exits:

```console
deepcode loop --resume <session-id>
```

Connection, model, and Thinking overrides affect only the next Turn started by
the command. `--token-budget` is optional; omit it for an unbudgeted Goal.
`--max-iterations` remains an optional diagnostic limit rather than a product
default.

### Automation management

The Desktop Automation workspace is the recommended interface for creating and
reviewing Automations. The commands below expose the same service to scripts.
The Project must already be trusted.

Create a manual Automation:

```console
deepcode automation create "Security review" \
  --workspace . \
  --prompt "Review the current repository for security regressions." \
  --schedule manual
```

Create an interval Automation:

```console
deepcode automation create "Repository caretaker" \
  --workspace . \
  --prompt "Repair failing tests and verify the result." \
  --schedule interval \
  --interval-seconds 3600
```

Manage definitions and Run history:

```console
deepcode automation list --workspace .
deepcode automation update <automation-id> --prompt "Updated instruction"
deepcode automation enable <automation-id>
deepcode automation disable <automation-id>
deepcode automation run <automation-id>
deepcode automation runs <automation-id> --limit 100 --offset 0
deepcode automation delete <automation-id>
```

`automation run` accepts `--request-id <key>` as an idempotency key when a
caller may retry the same request. Deleting an Automation retires its
definition while retaining its durable Run history. Interval schedules execute
while a scheduler-enabled Desktop or App Server runtime is active; disabling an
interval does not remove the **Run now** path.

Add `--json` to Automation commands for machine-readable output. List and Run
history responses are paged and expose the next offset when more results exist.

### Connection and model management

Desktop users normally manage connections under **Settings → AI providers**.
The equivalent administrative commands are:

```console
deepcode provider list
deepcode provider set <connection-id> --template openrouter --api-key
deepcode provider models <connection-id> --refresh
deepcode provider test <connection-id>
deepcode provider test <connection-id> --model <model-id>
deepcode provider remove <connection-id>
```

`--api-key` prompts without echoing the secret. Use an environment variable
instead of the credential store when a CI platform manages secrets:

```console
deepcode provider set work-openrouter \
  --template openrouter \
  --api-key-env OPENROUTER_API_KEY
```

Connect an OpenAI-compatible gateway:

```console
deepcode provider set company-gateway \
  --template custom \
  --adapter openai_compat \
  --api-base https://llm.example.com/v1 \
  --catalog openai \
  --api-key
```

Catalog checks do not send repository content. Supplying `--model` to
`provider test` adds a minimal real inference check. Add `--json` for
machine-readable results.

### Skill management

DeepCode writes new Skills to the canonical Agent Skills locations and keeps
legacy directories as read-only compatibility inputs:

```text
.agents/skills/          project Skills (canonical)
~/.agents/skills/        user Skills (canonical)
.deepcode/skills/        legacy DeepCode compatibility
.claude/skills/          Claude compatibility
~/.deepcode/skills/      legacy user compatibility
~/.claude/skills/        Claude user compatibility
```

Inspect and manage the catalog:

```console
deepcode skill list
deepcode skill show <id-or-name>
deepcode skill import ./my-skill --scope project
deepcode skill enable <skill-id> --scope project
deepcode skill disable <skill-id> --scope project
deepcode skill remove <skill-id>
deepcode skill reload
```

Use `--workspace <path>` before the Skill subcommand when its project-level
catalog should be resolved from a directory other than the current one. A Skill
can guide an Agent Turn, but it cannot grant permissions or bypass trust,
approvals, or tool policy.

### Session administration

Archive and delete Sessions from Desktop whenever possible. For administrative
scripts, permanent deletion requires an exact Session ID:

```console
deepcode session delete <session-id>
```

Add `--yes` to skip the interactive confirmation or `--json` for a structured
result. DeepCode refuses deletion while a Session has active work, is open in
another CLI, owns a managed worktree, or belongs to an Automation. Repository
files are never deleted by Session deletion.

### Scripting contract

- `deepcode exec` exits successfully only when its Turn settles successfully.
- `deepcode loop` exits successfully only when the Goal is complete.
- JSON output is intended for machines; human transcripts are presentation,
  not a stable parsing format.
- Project trust and access presets are separate. `--trust` never implies
  `--access full-access`.
- CLI, Desktop, and headless commands write to the same canonical Session
  history under `~/.deepcode/sessions/`.

---

## 中文

DeepCode 的主要使用方式是 Desktop 或交互式 CLI。本指南中的命令面向不方便
打开界面的环境，例如 CI、Shell 脚本、定时任务和管理工具。

这些命令不会启动另一套简化 Agent。无界面执行仍使用与 Desktop、CLI 相同的
Project、规范 Session、模型、Skills、权限、Goal、工具、恢复规则与验证证据。

### 开始之前

先安装并初始化 DeepCode，配置模型连接，并至少在 DeepCode 中打开一次目标
仓库：

```console
uv tool install deepcode-hku
deepcode init
cd <你的项目>
deepcode
```

Agent 首次运行需要明确的 Project trust。Trust 只确认 DeepCode 可以在哪个
工作区执行，不代表授予无限制工具权限。

需要查看完整参数时，运行 `deepcode <命令> --help`。

### 无界面运行一个 Turn

`deepcode exec` 会提交一个持久 Coding Turn，并把进度输出到终端：

```console
deepcode exec "修复失败的测试，并解释根本原因"
```

当前目录会成为新 Session 的 workspace。继续已有规范 Session：

```console
deepcode exec "现在补充一个回归测试" --resume <session-id>
```

脚本集成常用参数：

```console
deepcode exec "检查本次修改" \
  --connection <连接ID> \
  --model <模型ID> \
  --effort high \
  --skill security-review \
  --access read-only \
  --json
```

- `--json` 输出供其他程序消费的 NDJSON 事件。
- `--transcript normal|verbose|summary` 只控制人类可读展示。
- `--skill` 可以重复传入，为本 Turn 选择多个 Skills。
- `--resume` 保留原 Session 与记录的 workspace；只有显式传入
  `--workspace` 才会为本进程临时覆盖执行目录。
- `--max-iterations` 是可选诊断限制，默认不限制。

无交互的 **Ask** 模式无法回答审批时会拒绝该工具调用。只读检查请使用
`--access read-only`。只有明确接受本地无限制执行风险时才使用
`--access full-access`。

### 无界面运行持久 Goal

Desktop 和交互式 CLI 是创建、调整 Goal 的主要入口。`deepcode loop` 是为
脚本保留的无界面兼容入口：

```console
deepcode loop "实现指定功能并完成验证"
```

可选的证据命令会要求 Agent 在判断完成之前运行并检查该命令：

```console
deepcode loop "实现指定功能" \
  --test-cmd "python -m pytest -q"
```

进程退出后继续同一个 Goal 与规范 Session：

```console
deepcode loop --resume <session-id>
```

连接、模型和 Thinking 覆盖只影响该命令启动的下一个 Turn。
`--token-budget` 是可选项；不传入就是无预算 Goal。`--max-iterations` 同样只是
可选诊断限制，不是产品默认上限。

### Automation 管理

建议在 Desktop 的 Automation 工作区创建和检查 Automation。以下命令把相同
服务提供给脚本；目标 Project 必须已经完成 trust。

创建手动 Automation：

```console
deepcode automation create "安全审查" \
  --workspace . \
  --prompt "检查当前仓库是否存在安全回归。" \
  --schedule manual
```

创建定时 Automation：

```console
deepcode automation create "仓库维护" \
  --workspace . \
  --prompt "修复失败的测试并验证结果。" \
  --schedule interval \
  --interval-seconds 3600
```

管理定义与 Run 历史：

```console
deepcode automation list --workspace .
deepcode automation update <automation-id> --prompt "更新后的指令"
deepcode automation enable <automation-id>
deepcode automation disable <automation-id>
deepcode automation run <automation-id>
deepcode automation runs <automation-id> --limit 100 --offset 0
deepcode automation delete <automation-id>
```

调用方可能重试同一请求时，`automation run` 可以传入
`--request-id <幂等键>`。删除 Automation 只会退役定义，持久 Run 历史仍然
保留。定时任务需要启用了 scheduler 的 Desktop 或 App Server 保持运行；
暂停 interval 后仍可手动 **Run now**。

Automation 命令可以添加 `--json` 获取机器可读输出。定义和 Run 历史使用分页，
仍有结果时会返回下一页 offset。

### 连接与模型管理

Desktop 用户通常在 **Settings → AI providers** 中管理连接。对应的管理命令为：

```console
deepcode provider list
deepcode provider set <连接ID> --template openrouter --api-key
deepcode provider models <连接ID> --refresh
deepcode provider test <连接ID>
deepcode provider test <连接ID> --model <模型ID>
deepcode provider remove <连接ID>
```

`--api-key` 会以不回显方式输入密钥。由 CI 平台管理密钥时，可以改用环境变量：

```console
deepcode provider set work-openrouter \
  --template openrouter \
  --api-key-env OPENROUTER_API_KEY
```

接入 OpenAI-compatible 网关：

```console
deepcode provider set company-gateway \
  --template custom \
  --adapter openai_compat \
  --api-base https://llm.example.com/v1 \
  --catalog openai \
  --api-key
```

Catalog 检查不会发送仓库内容。为 `provider test` 指定 `--model` 后会增加一次
极小的真实推理检查。添加 `--json` 可以获得机器可读结果。

### Skills 管理

DeepCode 将新 Skill 写入标准 Agent Skills 目录，并把旧目录作为只读兼容来源：

```text
.agents/skills/          项目级 Skills（标准目录）
~/.agents/skills/        用户级 Skills（标准目录）
.deepcode/skills/        旧 DeepCode 兼容目录
.claude/skills/          Claude 兼容目录
~/.deepcode/skills/      旧用户级 DeepCode 兼容目录
~/.claude/skills/        用户级 Claude 兼容目录
```

检查和管理目录：

```console
deepcode skill list
deepcode skill show <ID或名称>
deepcode skill import ./my-skill --scope project
deepcode skill enable <skill-id> --scope project
deepcode skill disable <skill-id> --scope project
deepcode skill remove <skill-id>
deepcode skill reload
```

如果需要从当前目录以外的位置解析项目级 Skills，请把
`--workspace <路径>` 放在 Skill 子命令之前。Skill 可以指导 Agent，但不能授予
权限，也不能绕过 trust、审批或工具策略。

### Session 管理

优先在 Desktop 中 Archive 或删除 Session。管理脚本永久删除 Session 时必须
提供完整 ID：

```console
deepcode session delete <session-id>
```

添加 `--yes` 可以跳过交互确认，添加 `--json` 可以获得结构化结果。如果
Session 正在运行、被另一个 CLI 打开、拥有托管 worktree，或属于 Automation，
DeepCode 会拒绝删除。删除 Session 永远不会删除项目文件。

### 脚本契约

- `deepcode exec` 仅在 Turn 成功收敛时返回成功退出码。
- `deepcode loop` 仅在 Goal 完成时返回成功退出码。
- JSON 输出供程序解析；人类可读 transcript 不是稳定的机器协议。
- Project trust 与权限档位相互独立，`--trust` 不代表
  `--access full-access`。
- CLI、Desktop 和无界面命令都写入 `~/.deepcode/sessions/` 下的同一份规范
  Session 历史。
