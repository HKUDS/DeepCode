# DeepCode Tauri Desktop 重构实施计划

状态：P6 Session 对齐与 CLI 边界校正已完成，下一步进入后续 Desktop 产品能力
基线日期：2026-07-15
适用仓库：DeepCode

P0 完成记录（2026-07-15）：

- 已删除旧 `new_ui/`、`run.sh`、`run.bat`、旧 Web 截图和三个 Web 专用测试文件。
- 已清理 `deepcode --local`、FastAPI/Uvicorn/WebSocket 专用依赖和当前使用文档。
- Python 最低版本根据实际语法修正为 3.12，并新增 Python CI；394 个保留测试全部通过，sdist/wheel 构建通过。
- 已安装 Rust stable 与本地 PyInstaller 构建工具，新增 Tauri 2 + React/TypeScript 工程、最小 capability、CSP、设计 token 和 Desktop CI。
- ESLint、Vitest、Vite build、Rust fmt/clippy/check、pre-commit 和 macOS `.app` bundle 全部通过；桌面二进制已实际启动验证。
- 旧 Web 测试所覆盖的 workspace fence、Thread 恢复和 Workflow 恢复行为，将在 Phase 1 以新 application/domain contract 测试重新建立，不保留旧 HTTP API 测试。

P1 完成记录（2026-07-15）：

- 已建立无 UI/数据库依赖的 Project、Thread、Turn、Item、Approval、WorkflowRun、Artifact 与 DomainEvent 模型及状态不变量。
- 已建立 SQLite schema v1、显式 migration、WAL、外键、短事务、跨 Thread 关联约束，以及 Project/Thread/执行记录/Workflow/Artifact/Event repositories。
- 已建立 `DeepCodeApplication`、ProjectService、ThreadService、持久 event log、单 Thread sequence、replay 和有界 live event broker。
- 已建立 newline-framed stdio JSON-RPC App Server，支持握手、关闭、Project 生命周期、Thread 创建/读取/列表/重命名/归档/分叉和事件 replay。
- `protocol/app-server.schema.json` 为唯一桌面协议源，TypeScript 类型自动生成并在构建时检查是否过期。
- 已建立 SQLite Thread 投影和外部 SessionStore 兼容导入器；P6 已进一步修正为中央 JSONL Session 直接作为 canonical record，不再把内部既有 Session 当作 legacy 数据复制。
- P1 新增 32 项 domain/application/persistence/protocol 测试；全仓 426 项 Python 测试、前端 lint/test/typecheck/build 全部通过。

P2 完成记录（2026-07-15）：

- 已以 `AgentSessionFactory` 为单一适配边界，将 `build_agent_session()` 接入 `TurnService`；应用层测试使用脚本化 Session，不依赖网络或真实模型。
- 已将 SQ/EQ 的 assistant delta、assistant final、tool start/complete、error 与 task complete 投影为持久 Item；写文件、命令和普通工具分别形成 FileChange、CommandExecution 和 ToolCall Item。
- 已实现独立的 `ExecutionRegistry` asyncio runtime、默认并发上限 2、同 Thread 单活动 Turn、排队取消、运行中取消和应用关闭排空。
- bash、code mode 和外部 hook 命令现在使用独立进程组；超时或取消会先 TERM、再 KILL 整棵子进程树。
- 已实现持久 `ApprovalService`，支持 allow once、allow session、deny、取消；审批前后 Turn/Thread/Item/Approval 在同一事务内切换状态。
- 已修复 SQ/EQ submission 关联和忙碌/异常/中断终态保证；应用启动会把遗留 queued/running/waiting Turn、Item 和 Approval 恢复为明确终态。
- 已增加 `turn/start`、`turn/read`、`turn/interrupt`、`approval/respond` JSON-RPC 方法及生成类型；完成“Thread → Turn → 审批 → 文件变更 → 完成 → replay”的 stdio 端到端测试。
- permission mode 由客户端 adapter 选择默认值：未显式配置时 Desktop/App Server 使用 `default`，旧 CLI/批处理继续使用 P0 的 `full_auto`；显式配置和环境变量对两端生效。Project 未被信任时后端拒绝启动 Turn，执行前再次校验 workspace fence。
- 全仓 437 项 Python 测试通过；桌面 schema check、lint、Vitest、TypeScript typecheck 和 Vite production build 通过。

P3 完成记录（2026-07-16）：

- Rust host 已实现 packaged/source sidecar 解析、握手、并发 RPC、请求超时、崩溃与 stderr 事件、restart 和退出清理。
- React Command Center 已实现 Project/Thread 恢复、Execution Spine、Composer、Approval、interrupt、原生目录选择、trust 和 live/replay sequence guard。
- macOS arm64 release `.app` 已构建并实际启动；host 与 PyInstaller sidecar 能正常退出且无孤儿进程。详见 `docs/P3_DESKTOP_RUNTIME_ARCHITECTURE.md`。

P4 完成记录（2026-07-16）：

- 已实现共享应用层 File、Git、Worktree、Terminal 与 Test 服务；路径 fence、输出上限、进程归属和 durable TestResult 均由后端执行。
- Inspector 已接入结构化 Diff、revision-safe 逐文件 discard、Monaco 小文件编辑、xterm PTY、测试运行和 worktree keep/clean。
- Thread fork 会建立 owned worktree；清理同时校验确定性路径、专属分支、数据库归属与 ownership manifest，脏状态和活动进程 fail closed。
- 51 项相关 Python 测试、5 项桌面测试、TypeScript/ESLint/Vite、Rust fmt/test/clippy 和 macOS `.app` bundle 全部通过；bundle 内 sidecar 已实测文件、Diff、PTY 和优雅关闭。详见 `docs/P4_CODE_WORKBENCH_ARCHITECTURE.md`。

P5 完成记录（2026-07-16）：

- SQLite schema v2 和 `WorkflowService` 已实现 durable input/result、stage、interaction、checkpoint、retry ancestry、Artifact、取消与重启恢复。
- Paper、URL、repository、requirement 统一走 `workflow/*`；Paper Thread 桌面界面支持原生文件选择、进度、计划审阅、修改反馈、中断、重试和 Artifact Inspector。
- Workflow kernel 改为显式 workspace root；Desktop adapter 显式启用严格结果模式，禁用 free-form plan 假成功路径，并要求发现且通过实际测试后才能完成；旧 CLI/直接 Python 调用保留 P0 兼容语义。
- URL 下载已加入逐跳 SSRF/DNS rebinding 防护、100 MiB 上限和原子临时文件；显式安装 Docling 时仍优先使用，Desktop 则使用轻量内置 TXT/HTML/DOCX 转换和持续维护的 `pypdf` PDF fallback。
- App Server 已隔离协议 stdout；P5 状态机、恢复、安全、stdio 和桌面 replay 均有自动测试。详见 `docs/P5_PAPER2CODE_ARCHITECTURE.md`。

P6 边界校正完成记录（2026-07-16）：

- 已撤销 P6 对 `exec`、`loop`、`schedule`、MCP 和 TUI 的 Application 接线；这些 CLI 入口保持原有直接组装 AgentSession 的客户端边界。
- CLI 与 Desktop 共享 Agent/Provider/Tools/Harness 内核和中央 `SessionStore`，但不共享命令路由、进程生命周期或 UI adapter。
- JSONL Session 继续拥有 canonical Session ID、标题、workspace origin、模型、可见消息和 task links；Desktop Thread ID 与 Session ID 完全相同。
- SQLite 改为可重建的 Desktop 投影及 Desktop-only runtime/checkpoint 数据；删除投影数据库后可从 JSONL 恢复可见 Thread/Turn/Item 历史。
- App Server 支持跨目录 Session 发现、exact-cwd/all 列表与显式 cross-cwd resume；显式目录覆盖只在当前 App Server 进程有效，不改写 Session origin。
- Desktop 每个已加载 Thread 复用一个长生命周期 AgentSession；approval context 按 Turn 更新，AgentControl、tools 和 hooks 在 Session eviction 或应用退出时统一关闭。
- 最终回归（包含 P7 发布与文档转换用例）为全仓 534 项 Python 测试、
  39 项 Desktop 测试和 4 项 Rust 测试；Ruff、sdist/wheel、Desktop
  protocol/lint/typecheck/Vite build、Rust fmt/check/clippy、CLI help 和
  App Server stdio smoke 全部通过。完整 Session 边界证据见
  `docs/P6_SESSION_ALIGNMENT_REVIEW.md`。

Phase 7 实现与本地验收完成记录（2026-07-16）：

- sidecar 已改为锁定依赖的独立 Python 3.12 环境和 PyInstaller `onedir`
  资源包；不再使用每次启动重新解压、在 macOS 上反复触发校验的
  `onefile`。
- 每次打包都会执行完整 lazy runtime import probe，以及隔离
  `DEEPCODE_HOME`/SQLite 的 `initialize → shutdown` smoke；缺少 Provider、
  MCP、PDF 或 Workflow 运行依赖时直接中止构建。
- release host 只接受显式诊断覆盖或 bundle 内 `app-server/` 签名资源；
  source virtualenv、source bundle 和旧 external binary 只在 debug 构建中
  可用。
- macOS 本地/CI `.app` 使用 ad-hoc 签名，app、sidecar、嵌入 Python
  动态库与 sealed resources 已通过 `codesign --verify --deep --strict`。
  Developer ID、hardened runtime、notarization 和正式发布矩阵已经配置，
  但真实凭据与干净机器发布验收仍待执行。
- `SessionStore` 的默认目录改为实例化时读取 `DEEPCODE_HOME`，默认值仍为
  `~/.deepcode/sessions`；因此发布态隔离 smoke 不再误读真实用户 Session，
  CLI 的中央 Session 设计与任意目录启动语义不变。
- 桌面端已加入手动签名更新检查、下载进度、验签安装和重启；开发构建不
  配置更新通道，自动降级保持禁用。
- CI 已建立 macOS arm64/x64、Windows x64、Linux x64 的原生架构打包矩阵，
  避免 PyInstaller sidecar 与 Tauri host 架构错配；正式发布缺少 updater、
  Apple、Windows 或 Linux 签名凭据时直接失败。
- Paper2Code 发布 sidecar 现以标准库转换 TXT/HTML/DOCX，以 `pypdf`
  转换 PDF，并强制 Phase 2 必须产出 Markdown；Docling 改为 CLI 可选
  extra，不再让界面宣称支持但发布包实际失败。
- 已加入 npm/pip/cargo 漏洞审计、三生态许可证报告、CycloneDX SBOM、
  Git 历史 secret scan、隐私/诊断说明和发布回滚手册。
- React 工作台已经完成第一轮真实 Tauri 视觉验收：空项目、跨目录历史
  Session、Markdown/代码块、Workflow completion、Inspector、Settings、
  系统深色模式和 860×620 最小窗口均使用隔离的真实 App Server/SessionStore
  数据检查，而不是截图专用假状态。UI 仍可独立迭代，不需要改变 CLI、
  Agent 或 canonical Session 语义。
- 本地 macOS arm64 已完成 release `.app`/DMG 构建、严格 codesign 校验、
  DMG 挂载资源校验、隔离 `DEEPCODE_HOME`/SQLite 的 LaunchServices 冷启动
  和 sidecar 无孤儿退出检查。
- 仍需在受保护 GitHub environment 配置真实签名凭据，并以旧签名版本执行
  一次端到端升级后，才能把首个 release draft 发布为正式版本。

## 1. 执行结论

本次重构采用以下不可逆的主线决策：

1. 删除旧 `new_ui/` 的 React、FastAPI、WebSocket 和启动脚本，不做兼容维护。
2. 保留 DeepCode Python Agent、Provider、Tools、Harness、Sessions、Workflow 等核心能力。
3. 新增前端无关的领域层、应用层和持久化层，作为 App Server/Desktop 的业务边界；CLI 保持独立 adapter，并与 Desktop 共享 Agent 内核和 canonical SessionStore。
4. 新增独立的 Python `app_server`，仅负责 stdio JSON-RPC 协议和进程级生命周期。
5. 新增 Tauri 2 桌面端。Rust 只负责原生窗口、sidecar、系统对话框、通知、更新和安全能力；业务逻辑继续留在 Python。
6. 桌面端第一版不开放 HTTP 端口，不使用 FastAPI，不使用 WebSocket。
7. Paper2Code 不再是独立页面或独立产品，改为 Thread 的一种运行模式。
8. 先完成“打开项目到任务恢复”的纵向闭环，再扩展 Git、终端、工作树、Paper2Code、Skills、MCP 和自动化。

目标结构：

```text
Tauri React/TypeScript UI
        │
        │ typed commands/events
        ▼
Tauri Rust host / sidecar supervisor
        │
        │ stdio JSON-RPC 2.0, newline framed
        ▼
Python app_server transport
        │
        ▼
core/application
        │
        ├── core/domain
        ├── core/persistence ── SQLite Desktop projection/runtime
        ├── core/sessions ───── canonical JSONL Session records
        └── existing Agent / Provider / Tools / Workflow kernel

CLI / TUI / MCP
        │
        └── existing Agent kernel + core/sessions
            （独立 client/lifecycle boundary，不经过 app_server）
```

## 2. 当前基线与删除边界

### 2.1 当前仓库事实

- 旧 Web 位于 `new_ui/`，包含 99 个被 Git 跟踪的文件。
- 旧 Web 后端直接依赖 FastAPI 和多套 WebSocket。
- `deepcode.py --local` 和 `run.sh` 会同时启动 Uvicorn 与 Vite。
- 旧 `AgentChatService` 已经复用 `build_agent_session()` 和 `core.sessions`，证明核心可复用，但会话编排仍然属于 Web 进程内服务。
- 旧 `WorkflowService` 保存大量内存任务状态，并在任务执行时调用全局 `os.chdir()`。
- 当前 `AgentSession` 已具备基础 SQ/EQ 事件接口，但缺少桌面产品所需的持久事件、完整关联标识、可靠重放和多 Thread 运行管理。
- 当前 SessionStore 使用 JSONL 加可丢弃的 SQLite 索引，是 CLI 与 Desktop 共用的 canonical Session 记录；Desktop-only Turn/Item/event/workbench/workflow runtime 另存于应用数据库。
- 当前机器已有 Node 22.14、npm 10.9 和 Xcode Command Line Tools。
- 当前机器尚未安装 Rust/Cargo、Tauri CLI 和 PyInstaller。

### 2.2 第一批删除内容

完整删除：

```text
new_ui/
run.sh
run.bat
```

同步删除或修改：

- 删除 `deepcode.py` 中的 `--local` 分支、端口清理、Uvicorn/Vite 进程管理、前端依赖安装和 Web 依赖检测。
- 从 CLI 帮助中删除 `deepcode --local`，桌面端稳定后新增 `deepcode desktop` 或独立应用启动方式。
- 从 `requirements.txt` 删除仅旧 Web 使用的 `fastapi`、`uvicorn`、`python-multipart`、`websockets`。
- 保留 `pydantic-settings`、`aiofiles` 和 `httpx`，因为核心代码仍在使用。
- 单独审计 `streamlit`；若确认无入口和导入，则一并删除。
- 更新 `README.md`、`README_ZH.md` 和 `deepcode_config.json.example` 中的 WebSocket、端口、Node 前端和 `--local` 说明。
- 更新 `MANIFEST.in`，删除无效的旧 UI 打包项，加入后续新增的协议 schema 和必要资源。

旧测试迁移：

| 旧测试 | 处理方式 |
|---|---|
| `tests/test_agent_chat_service.py` | 重写为 `tests/application/test_thread_service.py` |
| `tests/test_agent_fs.py` | 删除 FastAPI 路由测试，替换为 Tauri 目录选择和后端 workspace fence 测试 |
| `tests/ui_session_resume_test.py` | 重写为 `tests/application/test_workflow_recovery.py` |

删除原则：不复制旧前端组件，不保留旧 HTTP API 兼容层。仍有价值的行为以新领域测试重新表达，必要时可从 Git 历史查询旧实现。

### 2.3 删除提交的验收条件

- `rg "new_ui|deepcode --local|uvicorn|FastAPI"` 不再命中有效产品入口或文档。
- Python 测试完成收敛，不能因为删除旧 Web 留下 import error。
- `deepcode` TUI、`cli.exec_cli`、`cli.loop_cli`、`deepcode mcp` 仍可启动。
- Python 包可以构建，安装后帮助信息与实际入口一致。

## 3. 目标源码结构

```text
DeepCode/
├── app_server/
│   ├── __init__.py
│   ├── __main__.py
│   ├── server.py
│   ├── dispatcher.py
│   ├── connection.py
│   ├── lifecycle.py
│   ├── errors.py
│   └── protocol/
│       ├── methods.py
│       ├── notifications.py
│       ├── models.py
│       ├── codec.py
│       └── schema.py
├── core/
│   ├── domain/
│   │   ├── project.py
│   │   ├── thread.py
│   │   ├── turn.py
│   │   ├── item.py
│   │   ├── approval.py
│   │   ├── workflow.py
│   │   └── artifact.py
│   ├── application/
│   │   ├── application.py
│   │   ├── project_service.py
│   │   ├── thread_service.py
│   │   ├── turn_service.py
│   │   ├── execution_registry.py
│   │   ├── approval_service.py
│   │   ├── workflow_service.py
│   │   └── event_service.py
│   ├── persistence/
│   │   ├── database.py
│   │   ├── migrations.py
│   │   ├── project_repository.py
│   │   ├── thread_repository.py
│   │   ├── event_repository.py
│   │   └── legacy_session_importer.py
│   └── ...existing kernel...
├── desktop/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── app/
│   │   ├── features/
│   │   ├── components/
│   │   ├── rpc/
│   │   ├── generated/
│   │   ├── styles/
│   │   └── tests/
│   └── src-tauri/
│       ├── Cargo.toml
│       ├── tauri.conf.json
│       ├── capabilities/
│       ├── icons/
│       └── src/
│           ├── main.rs
│           ├── sidecar.rs
│           ├── rpc_bridge.rs
│           ├── commands.rs
│           └── security.rs
├── protocol/
│   ├── app-server.schema.json
│   └── generated/
└── tests/
    ├── application/
    ├── app_server/
    ├── contract/
    ├── persistence/
    └── security/
```

分层约束：

- `core/domain` 不导入 Provider、Tauri、FastAPI 或 JSON-RPC。
- `core/application` 可以调用现有 Agent 和 Workflow 内核，但不能依赖具体 UI。
- `app_server` 只做参数校验、方法分派、连接生命周期和协议错误转换。
- `desktop/src` 不直接访问 shell、任意文件系统或数据库。
- `desktop/src-tauri` 不实现 Agent 或 Workflow 业务规则。
- CLI/TUI/MCP 保留直接组装 AgentSession 的客户端边界，不依赖
  `core/application`、SQLite Desktop projection 或 app_server。
- CLI 与 Desktop 必须共用 Agent/Provider/Tools/Harness 规则和同一中央
  `SessionStore`；任何共享语义应下沉到这些公共模块，而不是让一个
  client adapter 调用另一个 client adapter。

## 4. 领域模型

### 4.1 Project

关键字段：

```text
id
canonical_path
display_name
trust_state: untrusted | trusted
created_at
updated_at
last_opened_at
settings_json
```

规则：

- canonical path 必须解析软链接后保存。
- Project 是权限边界，不只是侧边栏标签。
- 用户第一次打开目录时明确确认信任。
- 同一个 canonical path 不重复创建 Project。

### 4.2 Thread

关键字段：

```text
id
project_id
parent_thread_id
title
mode: code | paper | brief | review | goal
status: idle | running | waiting | failed | archived
model
workspace_path
worktree_path
created_at
updated_at
archived_at
```

对齐规则：`core.sessions.Session` 在产品概念上对应 Thread，二者使用同一
ID。标题、workspace origin、模型、可见消息和 task 引用继续由原 JSONL
Session 保存；SQLite 只投影 Desktop UI 所需的 Turn、Item、event 和运行态。
显式选择外部 SessionStore 时才使用兼容 importer，并且不修改外部源。

### 4.3 Turn

```text
id
thread_id
ordinal
prompt
status: queued | running | waiting_approval | completed | failed | interrupted
stop_reason
error_code
error_message
started_at
completed_at
```

强制规则：每个 Turn 必须且只能进入一个终态：`completed`、`failed` 或 `interrupted`。异常、拒绝、进程崩溃和取消都不能让消费者永久等待。

### 4.4 Item

Item 是 UI 时间线的最小稳定单元：

```text
user_message
assistant_message
reasoning_summary
plan
tool_call
command_execution
file_change
diff
test_result
approval_request
workflow_stage
artifact
error
completion
```

关键字段：

```text
id
thread_id
turn_id
ordinal
kind
status: pending | in_progress | completed | failed | declined
summary
payload_json
created_at
updated_at
```

工具原始输出较大时写入 Artifact 或日志文件，Item 只保存摘要、状态和引用，避免通过 RPC 发送无限大的 payload。

### 4.5 Approval

```text
id
thread_id
turn_id
item_id
category: command | file_write | network | external_tool | destructive
status: pending | approved_once | approved_session | denied | cancelled | expired
request_json
decision_json
requested_at
resolved_at
```

审批必须在后端执行前完成。前端弹窗只是用户交互，不是安全边界。

### 4.6 WorkflowRun 与 Artifact

WorkflowRun 用于 Paper2Code、Build from Brief 和长任务：

```text
id
thread_id
turn_id
kind
status
current_stage
progress_current
progress_total
checkpoint_json
created_at
updated_at
completed_at
```

Artifact 保存报告、论文解析结果、日志、补丁、测试报告和导出包的元数据及安全路径引用。

## 5. 持久化与恢复

### 5.1 Canonical Session 与 Desktop 数据库

中央 Session 目录默认位于：

```text
~/.deepcode/sessions/
```

可用 `DEEPCODE_SESSIONS_DIR` 覆盖。这里的 JSONL 是 CLI 与 Desktop 共用
的 canonical Session record，保存 Session ID、标题、workspace origin、
模型、可见 user/assistant 消息和 task links；目录内 `index.db` 只是可丢弃
索引。跨进程写入使用 Session 级文件锁，长生命周期读取方根据文件签名
失效缓存。

Desktop 另使用 SQLite 数据库：

```text
~/.deepcode/state/deepcode.sqlite3
```

至少包含：

```text
schema_migrations
projects
threads
turns
items
approvals
workflow_runs
artifacts
event_log
```

要求：

- 打开 WAL 模式。
- 所有写入使用事务。
- schema migration 可重复执行、可回滚或可安全前滚。
- 数据库损坏不能导致原项目文件被改动。
- 删除或重建 Desktop SQLite 后，Session ID 和可见会话历史必须能从
  canonical JSONL 恢复。
- SQLite 可以保存 Desktop-only approval、worktree、terminal、artifact、
  checkpoint 和 replay 状态，但不能另造一套用户可见 Session identity。
- 只有显式外部 SessionStore 导入保持源只读；内部中央 Session 直接读取，
  不称为 legacy migration。

### 5.2 事件日志

每条对 UI 可见的状态变化先持久化，再发送通知：

```json
{
  "eventId": "evt_...",
  "sequence": 42,
  "type": "item.updated",
  "threadId": "<canonical-session-id>",
  "turnId": "turn_...",
  "itemId": "item_...",
  "timestamp": "2026-07-15T10:30:00Z",
  "payload": {}
}
```

约束：

- `sequence` 在一个 Thread 内单调递增。
- UI 保存最后确认的 cursor，重连后调用 replay。
- 文本 delta 可以合并或丢弃，但最终 Item projection 和 canonical assistant
  message 必须收敛；投影冲突必须产生可诊断事件，不能静默覆盖 JSONL。
- 队列必须有界；慢消费者不能无限增加 Python 内存。
- App Server 崩溃后，将数据库中遗留的 `running` Turn 标记为 `interrupted`，并写入恢复事件。

## 6. App Server 协议

### 6.1 传输

- JSON-RPC 2.0。
- 每行一个完整 JSON 对象。
- stdin 接收 request/notification，stdout 只输出协议消息。
- 所有日志写 stderr，不允许污染 stdout。
- 桌面端启动 sidecar 后必须先发送 `initialize`。
- 协议包含 `protocolVersion`、`serverVersion`、capabilities 和兼容性检查。
- 单条消息有大小上限；大文件、完整日志和 Artifact 使用分页或句柄读取。

### 6.2 第一版方法

```text
initialize
shutdown

project/list
project/add
project/read
project/update
project/remove

thread/start
thread/list
thread/read
thread/rename
thread/archive
thread/delete
thread/fork

turn/start
turn/read
turn/steer
turn/interrupt

approval/respond

event/replay

file/read
file/list
diff/read

workflow/start
workflow/read
workflow/cancel
workflow/retry

config/read
config/update
model/list
```

第二批方法：

```text
terminal/create
terminal/write
terminal/resize
terminal/close

git/status
git/diff
git/worktree/create
git/worktree/remove

skill/list
mcp/list
mcp/status
artifact/list
artifact/open
```

### 6.3 通知

```text
thread.updated
turn.started
turn.updated
turn.completed
item.created
item.updated
approval.requested
approval.resolved
workflow.updated
artifact.created
server.warning
server.fatal
```

### 6.4 错误模型

所有错误返回稳定 code，不让 UI 匹配错误字符串：

```text
INVALID_REQUEST
PROTOCOL_MISMATCH
NOT_INITIALIZED
PROJECT_NOT_FOUND
THREAD_NOT_FOUND
TURN_ALREADY_RUNNING
PERMISSION_DENIED
WORKSPACE_OUT_OF_SCOPE
APPROVAL_EXPIRED
MODEL_UNAVAILABLE
PROVIDER_AUTH_FAILED
SIDE_EFFECT_FAILED
INTERRUPTED
INTERNAL_ERROR
```

错误 payload 可包含 `retryable`、`userMessage`、`details` 和 `correlationId`，但不得包含凭据、完整环境变量或内部链式思维。

## 7. 执行、取消与并发模型

### 7.1 ExecutionRegistry

每个活动 Turn 对应一个运行记录：

```text
turn_id
asyncio.Task
cancel_token
child_processes
pending_approval
event_sink
started_at
```

第一版限制：同一 Thread 同时只运行一个 Turn，不同 Thread 可以并行。并发上限由配置控制，默认保守值为 2。

### 7.2 真取消

`turn/interrupt` 必须：

1. 设置取消令牌。
2. 取消 Agent asyncio task。
3. 终止该 Turn 创建的子进程树。
4. 取消未完成审批。
5. 将 Turn 和活动 Item 写为 `interrupted`。
6. 写入唯一的终止事件。
7. 在限定时间内完成；超时后强制清理并记录 warning。

需要同步修复 `AgentSession.run_stream()`：忙碌拒绝、异常、hook block、取消和正常结束都必须返回可关联的终止事件；Event 必须关联 submission/turn，而不能只使用 session 内自增 id。

### 7.3 Workspace 上下文

- 禁止业务代码调用全局 `os.chdir()`。
- 所有文件工具显式接收 workspace root。
- 所有命令使用子进程 `cwd=`。
- workspace 路径做 canonicalize、软链接逃逸和越界检查。
- 多 Thread 隔离优先使用 Git worktree；非 Git 项目使用独立工作目录策略。

## 8. 安全计划

桌面端默认权限策略从“自治优先”调整为“用户可见、后端强制”：

- 默认 permission mode 使用 `default`，不是 `full_auto`。
- 未知 permission mode 不能回退到 `full_auto`，应 fail closed 或回退到 `default`。
- 文件写入、删除、危险命令、项目外访问和网络访问进入统一审批路径。
- command、native command、code mode、MCP tool 使用同一个权限决策入口。
- shell sandbox 默认限制写入范围；网络按任务或命令显式授权。
- URL 下载阻止 loopback、link-local、私网、云 metadata 地址和 DNS rebinding，并限制重定向、大小与超时。
- Tauri capabilities 按窗口最小授权；React 不获得通用 shell plugin 权限。
- 启用严格 CSP，仅加载本地打包资源；禁止任意远程脚本。
- sidecar 只接受父进程 stdio；不监听 `0.0.0.0` 或 localhost 端口。
- 日志统一脱敏 API key、Authorization header、cookie、环境变量和凭据路径内容。
- Project trust 与 Approval 决策可审计，不静默扩大权限。

## 9. Tauri 桌面端产品与设计计划

### 9.1 产品对象

桌面端面向需要长时间运行编码、论文复现和代码审查任务的开发者/研究者。主页面的唯一工作不是“与模型聊天”，而是让用户发起任务并持续看清执行、变更、审批和验证结果。

### 9.2 一级信息架构

```text
Command Center
├── Active work
├── Needs attention
├── Recent projects
└── Recent threads

Project Workspace
├── Project / Thread navigation
├── Execution Spine
├── Composer
└── Inspector
    ├── Changes
    ├── Files
    ├── Plan
    ├── Terminal
    ├── Artifacts
    ├── Paper
    └── References

Settings
├── Models and providers
├── Permissions and sandbox
├── MCP
├── Skills
├── Appearance
└── Updates and diagnostics
```

Thread 模式：

```text
Code Task
Reproduce Paper
Build from Brief
Review Repository
Long-running Goal
```

### 9.3 主工作区布局

```text
┌────────────────┬──────────────────────────────┬──────────────────────┐
│ Projects       │ Thread title / state         │ Inspector            │
│                │                              │                      │
│ Threads        │ Execution Spine              │ Changes              │
│                │ intent                       │ Files                │
│ + New Thread   │   plan                       │ Plan                 │
│                │   approval                   │ Terminal             │
│                │   tools                      │ Artifacts            │
│                │   file changes               │ Paper                │
│                │   tests                      │ References           │
│                │   verification               │                      │
│                │                              │                      │
│                │ Composer                     │                      │
└────────────────┴──────────────────────────────┴──────────────────────┘
```

响应策略：桌面窗口较窄时先折叠 Inspector，再收起 Project 栏；核心 Execution Spine 始终保留。第一版不以手机端为目标，但所有控件支持键盘、可见焦点和缩放。

### 9.4 视觉系统

视觉概念：研究实验台，而不是聊天网站或霓虹黑色开发者仪表盘。

```text
Lab Canvas      #ECEFF3
Panel White     #F8F9FB
Instrument Ink  #171A20
Blueprint Blue  #3659D9
Verified Teal   #278776
Review Copper   #B86A3D
```

字体角色：

- UI/正文：Instrument Sans 或 IBM Plex Sans。
- 命令、路径、日志、数据：IBM Plex Mono。
- 不使用大面积装饰性衬线标题，不堆叠玻璃拟态卡片。

唯一视觉签名是 Execution Spine：它真实表达任务从目标、计划、审批、执行、修改到验证的顺序。其余界面保持克制，不再添加与信息无关的装饰。

### 9.5 交互规则

- Assistant 文本只是 Item 之一，工具、Diff、测试和审批具有同等地位。
- 同类型连续文本 delta 合并，避免时间线抖动。
- Tool Item 默认显示状态和摘要，原始输出按需展开。
- Approval 固定出现在活动执行位置，并同时进入 Needs attention。
- Diff 可以逐文件查看、接受、还原或在外部编辑器打开；还原必须二次确认且只作用于明确选择。
- 中断按钮在 Turn 进入终态前始终可达。
- 空状态明确引导“打开项目”或“新建 Thread”，错误状态给出可执行恢复动作。
- UI 不展示隐藏 chain-of-thought，只展示模型或系统明确提供的 reasoning summary、plan 和 sources。

## 10. 分阶段实施路线

### Phase 0：移除旧 Web 与建立工程基线

任务：

- 创建重构分支和基线 tag。
- 删除第 2 节列出的旧 Web 文件、入口、依赖和文档。
- 将仍有价值的旧 Web 测试重写到应用层测试命名空间。
- 修复 Python 包元数据：最低 Python 版本明确为 3.12，sidecar 构建统一使用 Python 3.12。
- 新增正式测试 CI，而不只运行 pre-commit。
- 安装 Rust stable、Cargo、Tauri CLI；为 sidecar 构建安装 PyInstaller。

退出条件：旧 Web 完全不可达，CLI 回归通过，Python 包构建通过，空 Tauri 工程能在 macOS 启动。

### Phase 1：领域层、数据库与协议骨架

任务：

- 建立 Project、Thread、Turn、Item、Approval、WorkflowRun、Artifact 模型。
- 建立 SQLite migration、repositories 和事务边界。
- 建立 JSON-RPC codec、dispatcher、initialize/shutdown。
- 从 schema 生成 TypeScript 类型，禁止手写两套协议类型。
- 建立 event log、sequence、replay 和 bounded queue。
- 建立 Session projection/importer 骨架；P6 将内部中央 Session 修正为直接
  canonical 对齐，importer 仅保留给显式外部 SessionStore。

退出条件：协议契约测试通过；App Server 可启动、握手、创建 Project/Thread，并在重启后读取相同状态。

### Phase 2：Agent 纵向闭环

状态：已完成（2026-07-15）。实现细节见 `docs/P2_AGENT_EXECUTION_ARCHITECTURE.md`。

任务：

- 使用 `build_agent_session()` 接入 TurnService。
- 将 SQ/EQ event 映射为持久 Item。
- 修复事件关联和所有路径的终止保证。
- 实现 ExecutionRegistry、并发限制、真取消和子进程清理。
- 实现 ApprovalService，并将审批 callback 接入现有 permission engine。
- 实现 workspace fence 和 Project trust。

退出条件：无 UI 情况下，仅通过 JSON-RPC 完成“创建 Thread → 执行 Turn → 工具审批 → 文件变更 → 完成/失败/中断 → 重启恢复”。

### Phase 3：Tauri 最小桌面端

状态：已完成（2026-07-16）。实现细节见 `docs/P3_DESKTOP_RUNTIME_ARCHITECTURE.md`。

任务：

- Rust 启动并监管 Python sidecar。
- 完成 RPC bridge、请求超时、崩溃检测、stderr 日志和优雅退出。
- React 实现 Command Center、Project 栏、Thread 栏、Execution Spine、Composer 和基础 Inspector。
- 支持原生目录选择、Project trust、流式 Item、Approval、中断和历史恢复。
- 完成主题 token、键盘导航、焦点管理和 reduced motion。

退出条件：真实用户可以在桌面应用中完成 Phase 2 的完整闭环，关闭应用再打开仍能恢复 Thread 和最终 Item。

### Phase 4：代码工作台能力

状态：已完成（2026-07-16）。实现细节见 `docs/P4_CODE_WORKBENCH_ARCHITECTURE.md`。

任务：

- Git status、结构化 Diff、文件树和安全文件读取。
- Monaco 只用于查看/小范围编辑，不复制完整 IDE。
- xterm.js + PTY，终端进程归属 Project/Thread 并受生命周期管理。
- Git worktree 创建、回收、冲突提示和磁盘清理。
- 测试结果结构化为 TestResult Item。
- Thread fork 和多 Thread 并行。

退出条件：用户能隔离执行任务、查看和审查修改、运行测试、打开终端，并明确处理或保留工作树。

### Phase 5：Paper2Code 迁移

状态：已完成（2026-07-16）。实现细节见 `docs/P5_PAPER2CODE_ARCHITECTURE.md`。

任务：

- 将旧 WorkflowService 中有价值的任务生命周期重新实现在 `core/application/workflow_service.py`，不复制 Web broadcast 代码。
- 移除全局 `os.chdir()`，所有 workflow 显式使用 workspace/task_dir。
- 统一 Paper、URL、repo、requirement 输入为 WorkflowRun。
- 结构化 stage、plan review、interaction、retry、checkpoint 和 Artifact。
- 修复失败写入仍报告成功、代码任务不测试、通用 planner fallback、URL 下载安全和 Docling 实际接入问题。

退出条件：Reproduce Paper 从输入到 Artifact 完整运行；任一阶段失败不会显示成功；应用重启后至少能恢复到明确的 interrupted/checkpoint 状态并重试。

### Phase 6：Session 对齐、CLI 边界与扩展能力

状态：已完成（2026-07-16）。完整审查见 `docs/P6_SESSION_ALIGNMENT_REVIEW.md`。

任务：

- 保持 CLI/TUI/MCP 原有直接 AgentSession adapter，不接入 Desktop
  `core/application`、SQLite projection 或 app_server 生命周期。
- CLI 与 Desktop 共用中央 `SessionStore`；Thread ID 必须等于 Session ID，
  不能为 Desktop 复制出第二个 `thr_*` identity。
- 支持 exact-cwd 与 all-directory Session 列表；默认从 Session 记录的
  workspace 恢复，只有显式请求才使用当前/其他目录，并且不改写 origin。
- Desktop 对每个 loaded Thread 复用一个 AgentSession；`SessionStart`、
  AgentControl、tools 和 hooks 属于 Session 生命周期，approval callback
  context 属于 Turn 生命周期。
- SQLite Thread/Turn/Item/event 是可重建投影；兼容吸收 P1-P6 期间产生的
  SQLite-only Thread，并把错误 P6 projection 的新增 transcript tail 合并回
  原 canonical Session ID。
- 保留 headless exec 的稳定 NDJSON 输出。
- 接入 Skills、MCP 管理、Goal、自动化和诊断页面。
- 评估远程/SSH、浏览器控制和 PR Review；不阻塞桌面 1.0。

退出条件：CLI 原入口行为回归测试无变化；CLI 与 Desktop 看到同一套
Session identity/history；投影可删除重建；跨目录恢复、Hook/Session
生命周期和并发写入均通过 Review List；共享内核规则无业务分叉，同时
两个 client adapter 的命令与进程边界保持独立。

### Phase 7：打包、更新与发布

状态：实现与本地平台验收已完成（2026-07-16）；Developer ID/
notarization、Windows Authenticode、Linux GPG 和“旧签名版本升级到候选版”
属于受保护发布环境验收，不能用本地 ad-hoc 签名替代。

任务：

- 使用锁定 Python 3.12 依赖和 PyInstaller `onedir` 将 App Server 构建为
  对应平台的自包含资源，并在打包前验证 lazy runtime 与 RPC 启停。
- Tauri bundle 只包含对应平台的签名 App Server 资源；release 不允许回退
  到 source 路径、系统 Python 或宿主旁未声明的可执行文件。
- 建立 macOS arm64/x64、Windows x64、Linux x64 构建矩阵；若资源有限，先交付 macOS arm64 内测，再扩展。
- 配置 macOS 签名/notarization、Windows code signing、Linux 包格式。
- 配置签名更新 manifest、回滚策略、数据库迁移备份和崩溃诊断导出。
- 许可证、第三方依赖、隐私说明和日志脱敏审计。

退出条件：干净机器可安装、首次启动、更新、卸载；卸载不误删用户项目；升级失败可恢复；sidecar 与桌面版本兼容。

## 11. 测试策略与质量门槛

### 11.1 Python

- Domain：状态机、非法转换、序列化。
- Application：Project/Thread/Turn/Approval/Workflow 服务。
- Persistence：migration、事务回滚、WAL、并发写、旧 Session 导入。
- Agent adapter：完成、错误、空响应、hook block、取消、审批、工具失败。
- Security：路径穿越、软链接逃逸、credential path、命令绕过、SSRF、超大下载。
- Recovery：App Server 被强杀、运行中重启、半写事务、损坏事件 cursor。

新模块目标覆盖率不低于 80%，状态机、权限和恢复路径要求分支覆盖。

### 11.2 Protocol

- JSON schema snapshot。
- Python 编码/解码 round trip。
- 生成的 TypeScript 类型编译测试。
- 未知方法、错误参数、协议版本不匹配、超大消息和 stdout 污染测试。
- 10,000 条事件 replay、慢消费者和 delta coalescing 测试。

### 11.3 React

- Vitest + Testing Library：store、Item renderer、审批、恢复、错误状态。
- 对 mock RPC 做页面级集成测试。
- 键盘导航、焦点、aria、颜色对比和 reduced motion 检查。
- Execution Spine 使用真实协议 fixture，不使用只为截图设计的假数据结构。

### 11.4 Rust/Tauri

- sidecar 启动、握手、异常退出、超时、重启和优雅关闭。
- capabilities/CSP 快照检查。
- 原生目录选择结果必须再次经过 Python workspace fence。
- 每个平台至少执行安装与启动 smoke test。

### 11.5 合并门槛

每个阶段必须通过：

```text
Python unit/integration tests
Ruff/format/type checks
Protocol contract tests
TypeScript lint/typecheck/tests/build
Rust fmt/clippy/tests
Secret scan
Dependency/license audit
Platform smoke test（发布分支）
```

## 12. CI/CD 计划

新增工作流：

```text
python-ci.yml
protocol-ci.yml
desktop-ci.yml
security-ci.yml
release-desktop.yml
```

现有 GitHub Actions 需要更新到当前主版本，并固定关键第三方 action 的版本或 commit。PyPI 发布与桌面发布分离：Python CLI 可以独立发版，Desktop 使用独立 bundle/version，但共享协议兼容矩阵。

## 13. 可观测性与诊断

- stdout 永远是 RPC；stderr 和结构化文件保存 App Server 日志。
- 日志包含 correlationId、threadId、turnId、itemId，但不记录完整 prompt、凭据或敏感文件内容。
- Settings 提供“导出诊断包”，默认只包含版本、平台、脱敏配置、最近错误和 sidecar 生命周期。
- 对冷启动、握手、首个 Item、Turn 完成、事件 replay 和数据库写入建立耗时指标。
- 第一版性能预算：桌面 UI 启动后 5 秒内可操作；App Server 启动后 3 秒内完成握手；常规 Item 通知在本机 100ms 内进入 UI。若未达到，先测量导入和打包瓶颈，不通过隐藏加载状态掩盖。

## 14. 主要风险与控制措施

| 风险 | 控制措施 |
|---|---|
| 一次重写范围过大 | 按纵向闭环交付，每个 Phase 都有独立退出条件 |
| 删除旧 Web 丢失唯一 workflow 行为 | 删除前用新应用层测试表达必要行为，Git 历史保底 |
| CLI 与 Desktop 再次分叉 | Agent、Provider、Tools、Harness、permission policy 与 SessionStore 语义只能存在于共享内核；CLI 和 Desktop adapter 通过契约测试对齐，不互相依赖 |
| Python sidecar 启动慢、体积大 | 固定 Python 3.12 构建环境，分析 import，按平台打包，不在首屏导入所有 workflow |
| stdout 被第三方日志污染 | App Server 启动时重定向日志到 stderr，并用协议污染测试守门 |
| 取消后留下子进程 | ExecutionRegistry 记录进程树并在中断/退出/崩溃恢复时清理 |
| SQLite projection 与 canonical JSONL 不一致 | JSONL 固定为 Session identity/visible transcript 真相源；SQLite 可删除重建，冲突写诊断事件，跨进程 mutation 使用文件锁 |
| Tauri 权限过大 | capabilities 按窗口和命令白名单配置，React 无通用 shell 权限 |
| 工作树误删用户修改 | 只删除 DeepCode 创建且有 manifest 的 worktree；脏状态必须确认 |
| 自动更新破坏数据库 | 升级前备份、migration 事务、兼容性检查、保留最近可用版本 |

## 15. Desktop 1.0 完成定义

只有同时满足以下条件才称为 Desktop 1.0：

- 旧 Web 代码、入口、依赖和文档已经清理。
- CLI 与 Desktop 共用 Agent 内核、权限规则和 canonical SessionStore；各自
  client/application adapter 保持独立，不要求 CLI 经过 Desktop 服务层。
- 可管理多个 Project 和 Thread。
- 可流式执行任务并显示结构化 Item。
- 审批在后端强制执行。
- 中断能停止 Agent 和子进程，并产生确定终态。
- 可查看文件、Diff、测试和 Artifact。
- 支持 Thread 历史和应用重启恢复。
- Paper2Code 作为 Thread mode 完成至少一条可靠链路。
- 没有默认开放的 HTTP 端口。
- Tauri capabilities、CSP、日志脱敏和 workspace fence 通过安全测试。
- macOS 安装、签名、更新和卸载流程通过；其他承诺支持的平台同样通过 smoke test。
- README、开发文档、协议文档和故障排查与实际行为一致。

## 16. 推荐提交顺序

按以下小提交推进，避免一个无法审查的大型重写：

1. `docs: add desktop rebuild architecture and execution plan`
2. `chore: remove legacy web ui and launchers`
3. `chore: clean web dependencies docs and legacy tests`
4. `ci: add python test and package build gates`
5. `feat(domain): add project thread turn item models`
6. `feat(storage): add sqlite state and migrations`
7. `feat(protocol): add versioned json-rpc schema and codegen`
8. `feat(app-server): add stdio lifecycle and core methods`
9. `feat(application): add thread and turn services`
10. `fix(runtime): guarantee correlated terminal events and cancellation`
11. `feat(security): add project trust approval and workspace fence`
12. `feat(desktop): scaffold tauri host and sidecar bridge`
13. `feat(desktop): add command center and execution spine`
14. `feat(desktop): add approval diff and recovery flows`
15. `feat(workbench): add git terminal and worktrees`
16. `feat(workflow): migrate paper2code into thread mode`
17. `test(cli): lock adapter and canonical session compatibility`
18. `release: package sign update and desktop 1.0 checks`

## 17. 立即开工批次

第一批实施范围固定为：

```text
A. 删除旧 Web 与所有残留引用
B. 让 CLI/Python 测试和包构建重新全绿
C. 创建 core/domain、core/application、core/persistence、app_server 骨架
D. 落地 Project/Thread/Turn/Item 最小 schema
E. 跑通 initialize → project/add → thread/start → thread/read
```

这一批不做完整视觉页面、不迁移 Paper2Code、不做终端和 Git worktree。完成后再进入 Agent Turn 纵向闭环。这样旧 Web 可以立即退出，同时不会让新系统从一开始就背负过大的并行实现面。
