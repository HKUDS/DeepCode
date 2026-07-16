# P4 Code Workbench Architecture

状态：完成（2026-07-16）

P4 在 P2/P3 的 Thread、Turn、Item、Approval 与本地 stdio App Server
之上增加代码审查能力。所有业务规则仍位于 `core/application`；React
不直接访问文件系统、Git 或 shell，Rust 也不复制这些规则。

## 服务边界

- `WorkspaceService` 是所有路径操作的共同边界。它校验 Project/Thread、
  trust、canonical root、owned worktree 和 symlink escape。
- `FileService` 提供有界目录树、UTF-8 小文件读取和带 SHA-256 前置条件的
  原子小范围编辑。截断内容不返回可写 revision，活动 Turn 期间禁止编辑。
- `GitService` 只返回结构化 status/diff。每个 Diff 带 revision；逐文件
  discard 必须提交该 revision，并在后端重新计算后才允许执行。
- `WorktreeService` 只管理确定性 sibling 目录、专属 `deepcode/<threadId>`
  分支和匹配的 ownership manifest。脏 worktree 的清理必须显式 force，
  活动 Turn 或终端会阻止清理。
- `TerminalService` 管理 Thread-owned Unix PTY、窗口大小、进程组和退出通知。
  输出是有界增量通知，不写入 durable event log；应用退出会终止所有会话。
- `TestService` 只暴露探测到的 pytest/npm/cargo 命令。运行要求 trusted
  Project 和已有终态 Turn，结果及有界输出持久化为 `TestResult` Item。

## 协议与 UI

Canonical schema 新增：

```text
file/list              git/status             terminal/create
file/read              git/diff               terminal/write
file/write             git/discard            terminal/resize
git/worktree/create    test/discover           terminal/close
git/worktree/remove    test/run
```

Inspector 的 Changes、Files、Tests 和 Terminal 标签全部消费真实 RPC 数据。
Monaco 与 xterm 按需加载；常规首屏 bundle 不包含编辑器和终端运行时代码。
Thread fork 随后创建 owned worktree，使并行 Turn 使用隔离 workspace。

App Server 与 Rust bridge 的单条消息上限均为 1 MiB。文件读取/写入限制为
128 KiB，测试 stdout/stderr 各保留最后 64 KiB，结构化 Diff 同时限制文件数、
行数和文本字节。若任一结果仍超过协议上限，App Server 返回稳定的
`RESPONSE_TOO_LARGE` 错误并继续服务，不会让 sidecar 因超长行退出。

## 安全不变量

1. UI 提供的路径永远是 workspace-relative；canonicalization 只在后端执行。
2. symlink 不能把读取、写入或 discard 导向 workspace 之外。
3. 写文件和 discard 都使用 optimistic revision，旧视图不能覆盖新内容。
4. discard 仅处理明确选择的文件，并由 UI 二次确认；worktree force clean
   另有独立确认。
5. worktree 清理同时校验数据库归属、managed path、branch 和 manifest，
   不根据目录名称猜测所有权。
6. PTY 和 test subprocess 使用显式 `cwd`，不调用全局 `os.chdir()`。
7. Terminal input、Git output、文件内容、目录项和 test output 均有硬上限。

## 验证证据

- P4 application/App Server/contract/domain/persistence 组合：51 项通过。
- P4 边界覆盖 UTF-8 截断、symlink fence、stale SHA/Diff revision、unborn
  repository、rename/delete/untracked discard、manifest reclaim、脏 worktree、
  PTY ownership、test output truncation 和 oversized RPC response recovery。
- React TypeScript、ESLint、5 项 Vitest 和 production build 通过；慢旧 Thread
  请求不能覆盖当前 Inspector。
- Rust fmt、2 项单元测试和 clippy `-D warnings` 通过。
- PyInstaller sidecar 与 macOS arm64 `DeepCode.app` release bundle 构建通过；
  从 bundle 内 sidecar 实测 file/read、git/diff、PTY 和 graceful shutdown。
