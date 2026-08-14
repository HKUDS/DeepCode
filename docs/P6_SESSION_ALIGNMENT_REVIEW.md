# P6 Session Alignment Review

日期：2026-07-16
状态：已完成并通过全量质量门槛

## 1. 不可违反的边界

P6 的正确目标不是让 CLI 调用 Desktop 的 `core/application`。正确边界是：

```text
                         shared kernel
              Agent / Provider / Tools / Harness
                              │
                 canonical core/sessions JSONL
                       ╱                    ╲
CLI / TUI / MCP direct adapter       App Server/Desktop adapter
原命令与进程生命周期                 core/application + SQLite projection
```

- CLI 与 Desktop 共用 Agent 内核、权限规则、配置语义和中央 SessionStore。
- CLI 与 Desktop 不共用命令路由、transport、UI 或进程生命周期。
- Desktop Thread ID 必须等于 canonical Session ID；不能复制出第二套
  `thr_*` 用户身份。
- JSONL 拥有 Session identity、workspace origin、标题、模型、可见 transcript
  和 task links。SQLite 是 Desktop 可重建投影及 Desktop-only runtime 状态。

## 2. Codex 对齐依据

本地 Codex 源码体现的是“同一 thread record，多 client adapter”，而不是
“CLI adapter 必须调用 Desktop adapter”：

- `codex-rs/state/src/model/thread_metadata.rs` 明确把 SQLite thread metadata
  定义为由 rollout 文件派生的 canonical metadata。
- `codex-rs/tui/src/cli.rs` 的 resume/fork picker 同时支持 cwd 过滤与
  `show_all`，说明跨目录历史仍属于同一套 thread store。
- `codex-rs/app-server/src/request_processors/thread_processor.rs` 恢复 Thread
  时先读取历史记录的 session cwd，再应用显式 cwd override；override 是本次
  runtime 配置，不需要改写历史记录来源。
- `codex-rs/app-server/tests/suite/v2/thread_list.rs` 和
  `request_processors/thread_processor_tests.rs` 对 cwd filter 有独立契约测试。
- Codex App Server 暴露 thread/history/turn 能力给 Desktop；CLI 与 App
  Server 仍然是同一 core 之上的不同入口。

DeepCode 不需要复制 Codex 的存储格式，但应复制这条所有权原则：文件化
会话记录是跨客户端共享历史，SQLite 可用于索引/投影，客户端边界保持独立。

## 3. CLI 回归审查

- [x] `cli/exec_cli.py` 与 P6 前 Git 基线逐字一致。
- [x] `cli/loop_cli.py` 与 P6 前 Git 基线逐字一致。
- [x] `cli/schedule_cli.py` 与 P6 前 Git 基线逐字一致。
- [x] `cli/mcp_server.py` 与 P6 前 Git 基线逐字一致。
- [x] `cli/tui/app.py` 与 P6 前 Git 基线逐字一致。
- [x] `cli/tui/session_bridge.py` 已恢复且与 Git 基线逐字一致。
- [x] CLI 代码中不存在 `CliApplication`、`application_adapter` 或
  `application_runners` 接线残留。
- [x] `exec` 保持一次性 AgentSession/headless NDJSON 语义。
- [x] `loop` 保持每轮独立执行与原中止语义。
- [x] `schedule` 保持原调度入口，不依赖 Desktop database。
- [x] MCP 同一会话继续复用其原 AgentSession，不转发到 App Server。
- [x] TUI 启动一个长期 AgentSession，并通过原 `SessionBridge` 写入中央
  SessionStore。
- [x] TUI `/clear` 只清空当前 Agent 对话上下文，不创建新 Session ID；后续
  消息仍写入同一个 Session record。

对应自动测试：`tests/test_exec_cli.py`、`tests/test_loop_cli.py`、
`tests/test_mcp_server.py`、`tests/test_tui.py`、`tests/test_session_index.py`。

## 4. Canonical Session 与跨目录恢复

- [x] 内部中央 Session 不再经过 legacy importer 复制。
- [x] `Thread.id == Session.session_id`，兼容既有八位 CLI ID。
- [x] App Server 启动/列表会扫描中央 SessionStore 并修复投影。
- [x] 没有 `projectId` 和 `cwd` 时可列出所有目录的 Sessions。
- [x] 指定 `cwd` 时执行 exact-cwd 过滤。
- [x] 默认 resume 使用 Session 记录的 workspace origin。
- [x] 只有显式 `workspacePath` 才允许 cross-directory resume。
- [x] 显式 workspace override 在当前 App Server 生命周期内稳定，不会被
  后续 list/read reconciliation 静默改回。
- [x] workspace override 不写回 JSONL，App Server 重启后恢复记录的 origin。
- [x] 自动发现的 Project 使用稳定 path hash ID，默认 untrusted。
- [x] 已存在的手动 Project ancestor 优先于自动发现 Project。
- [x] workspace 缺失时保留 Session 可发现性，但执行仍由 Project trust 和
  workspace existence 检查 fail closed。
- [x] 长生命周期 Desktop reader 能看到其他 CLI 进程追加的消息和 task。
- [x] Session mutation 使用跨进程文件锁；JSONL 行格式没有改变。
- [x] Session ID 拒绝路径穿越、分隔符、NUL 和超长值。

对应自动测试：`tests/application/test_session_alignment.py`、
`tests/app_server/test_server.py`、`tests/test_session_index.py`。

## 5. Session、Hook 与 Agent 生命周期

- [x] Desktop 每个 loaded Thread 最多保留一个 AgentSession runtime。
- [x] 同一 Thread 的连续 Turn 复用该 AgentSession 与其 AgentControl/tools。
- [x] `SessionStart` 在一个 AgentSession 中只触发一次，不按 Turn 重触发。
- [x] `UserPromptSubmit` 保持每 Turn 触发。
- [x] approval router 对 AgentSession 稳定，实际 approval context 每 Turn 替换。
- [x] interrupt 结束当前 Turn，但不错误关闭整个 Session runtime。
- [x] model 或 workspace 显式改变时关闭旧 runtime，再按新配置创建。
- [x] 空闲 runtime 有上限并使用 LRU eviction。
- [x] application shutdown 先取消/收敛运行任务，再关闭全部 AgentSession，
  最后停止 asyncio loop。
- [x] AgentSession close 失败不会阻止其他 Session 释放资源。
- [x] 外部进程改变 canonical transcript 后，下一 Turn 会重新加载可见历史，
  不会在旧内存历史上静默分叉。

对应自动测试：`tests/application/test_turn_service.py`、
`tests/test_hooks.py`、`tests/test_agent_session.py`、`tests/test_spawn_agent.py`。

## 6. SQLite 投影与恢复

- [x] JSONL user/assistant 消息可投影为 completed Turn/Item/event。
- [x] 删除 Desktop SQLite、WAL 和 SHM 后可从 JSONL 重建 Thread 与可见历史。
- [x] event replay 包含重建的 `thread.projected`、`turn.projected` 和
  `item.projected`，Desktop 重启后仍能重放 transcript。
- [x] Session task links 可重建最小历史 WorkflowRun/Completion projection。
- [x] P1-P6 已存在的 SQLite-only Thread 会以原 ID 提升为 canonical JSONL，
  不丢失其可见 transcript。
- [x] 错误 P6 importer 产生的 `thr_*` duplicate 若对应原 Session，会把
  projection 中新增的 transcript tail 合并回原 Session ID；duplicate 不再
  出现在可见 Session 列表。
- [x] projection 与 canonical transcript 非前缀冲突时写一次
  `thread.projection_conflict`，不静默覆盖执行 artifact。
- [x] 正常 projection/reconcile 不修改 canonical source bytes。
- [x] 外部 SessionStore importer 仅作显式兼容路径，外部源保持只读。

对应自动测试：`tests/application/test_session_alignment.py`、
`tests/application/test_legacy_session_import.py`、
`tests/application/test_workflow_service.py`。

## 7. Protocol 与 Desktop controller

- [x] schema 增加 `thread/resume`。
- [x] `thread/list.projectId` 改为可选，并增加可选 exact `cwd`。
- [x] Thread 关联 ID schema 接受 canonical Session ID，不再强制 `thr_`。
- [x] Python protocol codec/dispatcher 与 schema 一致。
- [x] TypeScript protocol 类型由 schema 重新生成，没有手工双写。
- [x] Desktop startup/select Thread 时先调用 `thread/resume` 再 replay。
- [x] `event/replay` 使用 `nextAfter`/`hasMore` 游标，并按实际 JSON-RPC
  编码字节裁页，不会因旧 Session 的事件总量超过 1 MiB 而整体失败。
- [x] Desktop 对旧 App Server 的 `RESPONSE_TOO_LARGE` 会折半分页重试。
- [x] assistant streaming 使用 `item.delta`，event log 不再反复保存增长中的
  完整文本；最终 Item checkpoint 和重启恢复仍保留完整内容。
- [x] `project/list` 会先 reconcile，以显示 CLI 在其他目录创建的 Sessions。
- [x] React controller 的 RPC 顺序有 Vitest 回归测试。

对应自动测试：`tests/contract/`、`tests/app_server/`、
`desktop/src/App.test.tsx`。

## 8. Failure-mode review

- [x] corrupted/missing Session index 可由 JSONL scan 修复或退回纯扫描。
- [x] 并发 CLI/Desktop mutation 不共用固定 temp 文件名。
- [x] explicit Session ID collision 抛出错误，生成 ID 才允许 reroll。
- [x] 不存在或非目录的 cross-cwd override 被拒绝。
- [x] untrusted auto-discovered Project 不能启动 Agent Turn。
- [x] cross-directory resume 不会绕过执行时 workspace fence。
- [x] canonical Session 在 Turn 开始时消失会进入明确失败终态。
- [x] Workflow canonical task/prompt 持久化失败会把 Workflow/Turn 收敛为
  failed，而不是留下永久 running 状态。
- [x] App Server crash recovery 不自动重放副作用，也不改写已有 JSONL 历史。

## 9. 最终质量门槛

- [x] 定向 Session/CLI/Hook/Application 回归测试。
- [x] 定向 Ruff 检查。
- [x] Desktop protocol generation check。
- [x] Desktop TypeScript typecheck。
- [x] Desktop Vitest。
- [x] 当前最终全仓 Python pytest（包含后续 P7 测试）：541 passed。
- [x] 全仓 Ruff：`ruff check .` passed。
- [x] Python sdist/wheel build：两种 artifact 均成功生成。
- [x] Desktop ESLint：0 errors、0 warnings；production build passed。
- [x] Rust fmt/check/test/clippy：4 tests passed，clippy 使用 `-D warnings`。
- [x] CLI help smoke：原 TUI/exec/loop/schedule/MCP 入口说明正常。
- [x] App Server stdio smoke：`initialize → project/list → thread/list → shutdown`
  成功，capabilities 包含 `thread/resume`。
- [x] 六个 CLI 文件最终仍与 Git 基线无差异。

本节已全部通过，P6 Session 对齐可以标记完成。Skills、MCP 管理 UI、Goal、
自动化、诊断页面和远程能力属于后续 Desktop 产品工作，不应以再次改写
CLI Session 生命周期为前提。
