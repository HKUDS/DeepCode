# DeepCode Desktop Product UI Specification

Status: active implementation contract
Date: 2026-07-16

## Product thesis

DeepCode Desktop is a local command center for developers supervising long-running
agents across repositories. Its primary job is not to expose every backend event.
It must make three things immediately clear:

1. Which project and Session am I in?
2. What is the agent doing, and does it need me?
3. What changed, and how do I review or continue?

The Desktop and CLI share the Agent kernel and canonical SessionStore, while their
client lifecycles remain independent. UI work must never introduce a second
Desktop-only Session identity.

## Information architecture

```text
Desktop
├── Threads
│   ├── New thread
│   ├── Search all Sessions
│   └── Projects
│       └── Sessions grouped by recency
├── Automations
├── Skills
├── MCP
└── Settings

Thread workspace
├── Header: project / branch / trust / runtime
├── Conversation: user, assistant, plans, tools, approvals, completion
├── Composer: prompt, mode, model, permission, attachments
└── Review panel (closed by default)
    ├── Changes
    ├── Files
    ├── Terminal
    ├── Tests
    ├── Artifacts
    └── Details
```

## Visual direction

The visual language is “local execution instrument”: cool neutral surfaces,
strong readable type, and one live cobalt signal for activity. It should feel
closer to a focused coding/writing tool than an observability dashboard, and
must provide a complete system-dark variant rather than a light theme with
patched dark components.

### Color tokens

- `canvas` — `#f7f8fa` / dark `#17191e`: main reading surface.
- `sidebar` — `#eef0f3` / dark `#111318`: navigation and project context.
- `ink` — `#191b20` / dark `#f2f4f7`: primary text and decisive actions.
- `muted` — `#5c626d` / dark `#b0b6c0`: secondary context.
- `signal` — `#5267f5` / dark `#8291ff`: selection, activity, and focus.
- `success` — `#228064` / dark `#5fbc98`: completed and verified work.
- `attention` — `#a9602e` / dark `#e09a64`: approval and trust boundaries.
- `danger` — `#b54c51` / dark `#eb858b`: failure and destructive actions.

### Type

- Interface and conversation: system UI (`-apple-system`, `BlinkMacSystemFont`).
- Large empty-state/product headings: `Avenir Next` when available, then the
  platform display/system stack; never a downloaded web font.
- Code, paths, metadata: `SFMono-Regular` / `ui-monospace`.
- No decorative display font. Personality comes from proportion, rhythm, and
  the coding-specific information hierarchy.

### Layout

- Sidebar: 272 px on wide windows, 232/210 px at narrow breakpoints.
- Conversation: centered, maximum readable width 820 px.
- Inspector: closed unless requested; 380–430 px when docked on wide windows.
- Composer: visually attached to the conversation, not to the application edge.
- At narrow widths the Inspector becomes an overlay, never disappears without a
  reachable replacement.

### Signature element

Each Session has a small “thread beacon” in navigation and header. It communicates
idle, running, waiting, failed, or complete using shape and motion, not a wall of
status labels. This is the single expressive element; the rest of the UI stays
quiet.

## Interaction rules

- Selecting a Session from another project changes project context atomically.
- Search covers Sessions across all recorded workspace directories.
- The recorded workspace origin is always visible before cross-directory resume.
- Inspector opens from a concrete action: review changes, inspect a tool, open a
  file, run a test, or open the terminal.
- Tool calls are compact by default. Assistant answers and approvals receive the
  strongest hierarchy.
- Empty states provide one next action and do not show irrelevant panels.
- Runtime, trust, and recovery errors explain both the cause and the next action.
- Keyboard focus is visible. Reduced motion is respected.

## Code ownership

```text
App.tsx                       composition only
app/workspaceState.ts         durable protocol projection
app/useWorkspaceController.ts RPC orchestration
app/useDesktopUi.ts           local navigation/panel preferences
features/navigation/          projects, Session search, primary destinations
features/thread/              header, conversation, composer
features/inspector/           review/workbench surfaces
features/settings/            models, permissions, diagnostics
features/extensions/          Skills and MCP
features/automations/         schedules and review queue
styles/                       tokens and global reset only
```

Feature components own their styles through colocated CSS modules. New global
feature selectors must not be added to the legacy `styles/app.css`; that file is
removed as the workbench migration completes.

## Quality gates

- No component should combine protocol loading, navigation state, and rendering.
- No fake management data: Skills, MCP, Goals, and Automations appear only when a
  real backend contract exists.
- Desktop tests cover cross-project Session selection, search, replay, approval,
  Inspector accessibility, and error/recovery states.
- Visual checks cover empty, populated, running, waiting approval, failed, and
  narrow-window states.
- Dark-sensitive renderers (Prism and Monaco) follow
  `prefers-color-scheme`; their theme choice has an automated subscription
  regression test.
- Monaco and xterm load only when their surfaces open.
- CLI entry files and canonical Session semantics remain under the P6 review gate.
