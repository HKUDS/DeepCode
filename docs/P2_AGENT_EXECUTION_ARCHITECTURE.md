# P2 Agent Execution Architecture

P2 closes the App Server/Desktop execution slice between the application model
and the existing Agent kernel. CLI and Desktop share the Agent assembly, policy
engine, and canonical `SessionStore`, while retaining independent client and
lifecycle adapters.

## Dependency and ownership model

```text
Desktop JSON-RPC adapter
            │
            ▼
       TurnService ───────────────▶ ApprovalService
            │                            │
            ▼                            ▼
 SessionRuntimeRegistry             SQLite projection
            │
            ▼
  AgentSessionFactory
            │
            ▼
 build_agent_session() ──▶ AgentSession ──▶ AgentRunner / tools / provider
            │
            └──────────────────────────────▶ canonical SessionStore JSONL

CLI/TUI/MCP ──▶ build_agent_session() + SessionStore (no TurnService dependency)
```

- `TurnService` owns durable Turn orchestration and terminal-state guarantees.
- `ExecutionRegistry` owns one lazy background asyncio loop and a bounded
  semaphore. The stdio request thread never runs provider or tool work.
- `SessionRuntimeRegistry` retains one `AgentSession` for each loaded Desktop
  Thread. Turns reuse that Session until eviction or application shutdown.
- `AgentSessionFactory` is the only application-to-kernel adapter. Production
  uses `build_agent_session()`; tests inject deterministic sessions.
- `TurnEventProjector` translates SQ/EQ events into Items. It knows no JSON-RPC
  or Tauri types.
- `ApprovalService` owns both the durable approval record and the in-memory
  future that resumes the exact suspended tool call.
- `SessionStore` is canonical for Session identity and visible conversation.
  SQLite Items and the event log are the rebuildable Desktop execution
  projection. Live delivery may overflow; clients recover through
  `event/replay` and `turn/read`.

## Turn lifecycle

`turn/start` performs one short `BEGIN IMMEDIATE` transaction:

1. Read Thread and Project.
2. Require a trusted Project and re-resolve the workspace inside the Project
   boundary.
3. Reject an archived Thread or another active Turn in the same Thread.
4. Insert a queued Turn and completed UserMessage Item.
5. Move the Thread to running and append durable projection events.
6. Commit, publish the events, then enqueue the background execution.

The registry defaults to two concurrent Turns across different Threads. A Turn
waiting for the semaphore remains `queued`. Cancelling such a Turn invokes a
pre-start cancellation callback so it cannot remain queued forever.

When execution begins, TurnService marks the Turn running, acquires the Thread's
long-lived AgentSession, and appends the user prompt to canonical JSONL before
running it. A newly loaded runtime takes history from the same SessionStore;
when another DeepCode process has appended visible messages, the runtime reloads
that history before the next Turn. The projector maps SQ/EQ events as follows:

| SQ/EQ event | Durable projection |
|---|---|
| `agent_message_delta` | Coalesced in-progress AssistantMessage Item |
| `agent_message` | Completed AssistantMessage projection |
| file write/edit tool | FileChange Item |
| shell command | CommandExecution Item |
| other tool | ToolCall Item |
| `error` | failed Error Item |
| `task_complete` | Completion Item and terminal Turn decision |

Delta writes are coalesced by time/size and the final message always overwrites
the draft, avoiding one SQLite transaction per token. A terminal assistant
message is also appended once to canonical JSONL.

Every path ends in exactly one of `completed`, `failed`, or `interrupted`.
Missing `task_complete`, factory/provider exceptions, projection failures,
explicit interrupt, queued cancellation, and application shutdown are all
converted to a durable terminal state. `_finish()` is idempotent, so races
between a user interrupt and normal completion cannot create two terminal
transitions.

## Approval state machine

Permission defaults belong to the client adapter rather than the shared
kernel. The App Server/Desktop adapter is approval-first when the user has not
configured a mode; the legacy CLI and batch adapters retain their P0
`full_auto` default. An explicit config value or
`DEEPCODE_PERMISSION_MODE` applies to both. The existing immutable
sensitive-path denylist remains in force in every mode.

When the permission engine returns `ask`, the AgentRunner awaits the callback
provided by TurnService. ApprovalService atomically:

- creates a pending ApprovalRequest Item and Approval record;
- changes the Turn to `waiting_approval` and Thread to `waiting`;
- appends `item.created`, `approval.requested`, `turn.updated`, and
  `thread.status_changed` events;
- suspends on an asyncio Future associated with that approval ID.

`approval/respond` accepts only `approved_once`, `approved_session`, or
`denied`. It updates Approval, Item, Turn, Thread, and events in one transaction,
publishes those events, then resolves the suspended Future on its owning loop.
Session grants are scoped to a Thread and tool name. Interrupting a waiting Turn
marks the Approval cancelled and the approval Item declined before the Turn
settles.

The frontend never authorizes a tool directly. It only submits a decision; the
Python backend remains the enforcement boundary.

## Cancellation and process cleanup

`turn/interrupt` cancels the registry future. Cancellation propagates through
the event consumer, AgentSession, AgentRunner, approval wait, and active tool.
AgentSession preserves the original submission correlation on the terminal
`task_complete(interrupted)` event.

Shell, code-mode, and hook commands start in a DeepCode-owned process group. On
timeout or cancellation the runtime sends group SIGTERM, waits briefly, then
sends SIGKILL to surviving descendants. Windows uses a new process group and
`taskkill /T /F`. ToolRegistry and delegated AgentControl resources remain
owned by the loaded AgentSession across Turns and are closed when that Session
is evicted or the application exits.

Application shutdown drains all tasks before stopping the background event
loop. This includes queued cancellation callbacks and running Turn finalizers.
After jobs settle, every live AgentSession is closed on that same loop so its
AgentControl, tools, hooks, and child resources receive one lifecycle end.

`SessionStart` therefore fires once per loaded AgentSession, not once per Turn;
`UserPromptSubmit` and approval context remain Turn-scoped. This matches the
established CLI session lifecycle without coupling CLI command routing to the
App Server.

## Crash recovery

At application open, before accepting requests, `recover_incomplete()` scans
queued, running, and waiting-approval Turns left by the previous process. In one
transaction per startup pass it:

- cancels pending approvals with reason `application_restarted`;
- marks pending/in-progress Items failed or declined;
- appends a failed Completion Item;
- marks each Turn interrupted and its Thread idle;
- appends item, approval, thread, and `turn.recovered` events.

Recovery never replays a side effect automatically. A future retry feature must
start a new Turn or resume from an explicit workflow checkpoint.
Recovery repairs only disposable Desktop runtime state; it does not rewrite or
replace canonical Session identity or existing JSONL messages.

## P2 JSON-RPC surface

P2 adds:

```text
turn/start
turn/read
turn/interrupt
approval/respond
```

`turn/start` and `turn/read` return a complete snapshot containing the Turn,
ordered Items, and Approvals. Event notifications use the same canonical Event
shape and can be replayed by per-Thread sequence. The schema remains the only
source for generated desktop TypeScript contracts.

## Verified behavior and P2 limits

Automated coverage includes successful projection, streaming coalescence,
approval/resume, approval cancellation, running and queued interruption,
bounded cross-Thread concurrency, process-tree cleanup, crash recovery, stable
SQ/EQ correlation, and the full stdio JSON-RPC approval/file-change/replay flow.

P2 intentionally does not include the Rust sidecar supervisor or production
React UI; those are P3. It also does not implement `turn/steer`, Git/diff/file
read APIs, workflow checkpoints, terminal PTYs, or Artifact payload storage.
Large raw tool output is still summarized by the existing Agent event adapter;
moving expandable full output into Artifact storage belongs to a later phase.
