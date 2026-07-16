# P3 Desktop Runtime Architecture

P3 closes the first production desktop slice without moving Agent rules into
Tauri or React. The desktop has three explicit process/layer boundaries:

```text
React Command Center
        │ typed Tauri invoke + event listeners
        ▼
Rust RPC bridge / sidecar supervisor
        │ newline JSON-RPC 2.0 over private stdio
        ▼
Python App Server → application services → Agent kernel
                         │                    │
                         ▼                    ▼
                SQLite projection      canonical Session JSONL
```

The CLI remains independent of Tauri and Node. It continues to assemble the
same Agent kernel directly. P3 changes neither CLI command routing nor provider
execution; it adds an App Server/Desktop adapter over the shared Agent kernel
and `SessionStore` without making CLI depend on `core/application`.

## Sidecar build and launch

Desktop packaging is reproducible and independent of the developer's general
Python environment:

```text
desktop/sidecar-requirements.in
        │ uv pip compile (intentional dependency update only)
        ▼
desktop/sidecar-requirements.lock
        │ npm run setup:sidecar
        ▼
desktop/build/sidecar/.venv              Python 3.12
        │ npm run build:sidecar
        ▼
desktop/build/sidecar/dist/
└── deepcode-app-server/
    ├── deepcode-app-server[.exe]
    └── _internal/...
```

`desktop/scripts/build-sidecar.py` runs PyInstaller in `onedir` mode with the
repository root as an analysis path. `onefile` was rejected because it extracts
to a fresh temporary directory on every launch; on macOS that repeatedly
validates embedded libraries and measured about 24 seconds before application
startup. The persistent `onedir` resource starts in about 0.7–1.2 seconds after
normal installation validation; the first fresh Python 3.12 build measured
about 3 seconds.

The build fails unless the packaged executable:

1. imports the Agent, Anthropic/OpenAI adapters, MCP clients, PDF and baseline
   document converters, and workflow kernel through
   `app_server --verify-runtime`;
2. completes an isolated `initialize → shutdown` exchange using temporary
   `DEEPCODE_HOME` and SQLite paths.

Tauri copies the whole directory into the platform resource directory as
`app-server/`. On macOS the executable is therefore at
`DeepCode.app/Contents/Resources/app-server/deepcode-app-server`, with its
private Python runtime in the same sealed resource tree.

Launch resolution is deterministic:

1. explicit `DEEPCODE_APP_SERVER_PATH`;
2. in debug builds only, repository `.venv` source runtime;
3. bundled `app-server/` resource;
4. in debug builds only, source `onedir` output and the legacy external-binary
   path.

An invalid explicit path fails closed instead of silently choosing another
binary. Release builds never inspect a compiled-in repository path, load an
executable merely placed beside the host, or fall back to a system Python.
`DEEPCODE_DATABASE_PATH` is forwarded as `--database`, allowing clean
acceptance tests without touching user state. `SessionStore()` resolves its
default root when instantiated, so the same acceptance run's
`DEEPCODE_HOME/sessions` is isolated as well.

## Rust ownership and failure model

`RpcBridge` owns exactly one child, its stdin writer, a pending-request map, and
the sidecar lifecycle state. A monotonic request ID correlates responses. A
separate launch generation prevents a late EOF from an old process from marking
a newly restarted process as crashed.

- stdout accepts newline-framed JSON up to the App Server's 1 MiB limit;
- responses resolve one pending channel; notifications are emitted to React;
- stderr is kept outside the protocol stream and emitted as diagnostic events;
- normal requests time out after 30 seconds; startup gets 45 seconds for
  first-install validation and imports;
- EOF rejects all pending requests and exposes a crashed state;
- restart performs protocol shutdown, bounded wait, then kill as a fallback;
- application exit sends `shutdown`, waits, and reaps the process, so no
  sidecar remains orphaned.

Rust does not parse domain payloads beyond JSON-RPC success/error correlation.
Stable application error codes and retryability are preserved for the UI.

## Desktop state and recovery

React receives only typed protocol results and canonical notifications. The
workspace controller restores the last selected Project and Thread IDs from
local preferences, calls `thread/resume`, then rebuilds the trace from
`event/replay` in 1,000-event pages. JSONL owns Session identity and visible
conversation; SQLite and its event log are the rebuildable Desktop projection.

Project discovery reconciles Sessions from the central store, including records
created by CLI processes in other directories. Listing can be scoped to an
exact cwd or span all directories. An explicit cross-directory resume changes
only the current App Server execution context and never rewrites the Session's
recorded workspace origin.

Live notification subscription starts before reads. Per-entity event sequence
tracking prevents either of these races:

- a replayed older Item overwriting a newer live delta/final Item;
- a stale `turn/start` or `turn/interrupt` response overwriting an event that
  arrived while the request was in flight.

Queue-overflow warnings trigger a durable replay. Project/Thread selection and
the final Item therefore recover after a desktop restart without replaying any
tool side effect.

## Interaction and security boundaries

- Project add uses the native directory picker and starts untrusted.
- Turn execution remains disabled until the Python Project record is trusted.
- Approval buttons submit a decision only; Python remains the enforcement
  boundary and resumes the exact suspended tool future.
- React has no arbitrary shell, filesystem, database, or child-process API.
- The main capability contains Tauri core defaults, native directory/message
  dialogs, safe external URLs, signed updater check/install, and restart only.
  It has no generic shell or filesystem permission. Destructive confirmations
  use the asynchronous native dialog adapter; browser preview uses
  `window.confirm`.
- Composer supports Command/Ctrl+Enter, interrupt remains reachable for every
  active Turn, focus styles are visible, and reduced-motion preferences disable
  nonessential animation.

The visual system is deliberately a research execution workbench: cold lab
gray, blueprint blue, verified teal, and review copper. The only signature
element is the real, data-driven Execution Spine; it does not invent decorative
workflow stages when no durable Items exist.

Manual update checks use Tauri's signed updater plugin. The frontend can check,
download, and install only through its narrow updater permissions; the plugin
verifies the release signature before installation and the process plugin is
exposed only for restart. Development builds have no configured update
endpoint, and downgrades remain disabled.

## Verification

P3 validation covers:

- locked Python 3.12 PyInstaller runtime import probe and
  `initialize → shutdown` smoke test;
- Rust response/error mapping tests, formatting, clippy, and all-target tests;
- React typecheck, lint, reducer race tests, event-replay restoration test, and
  production build;
- release Tauri bundle creation with the `onedir` resource present;
- macOS ad-hoc signing of the app plus nested Python executable/libraries and
  strict verification of the sealed resource tree;
- release desktop launch against an isolated SQLite database, successful schema
  and Session home, successful schema initialization/handshake, application
  quit, and confirmation that both host and sidecar exit without an orphan.

CI additionally builds architecture-native macOS arm64/x64, Windows x64, and
Linux x64 bundles. Formal release jobs fail closed on missing updater/platform
signing credentials and create only draft releases until clean-machine
acceptance is complete.

Ad-hoc signing is deliberately limited to local and CI verification. The
release workflow is configured for Developer ID signing, hardened runtime,
Apple notarization, Windows Authenticode, and Linux AppImage signing, but cannot
execute without protected credentials. Visual browser inspection is still
required whenever an interactive browser is available; it is not replaced by
snapshot assumptions. P4 extends Inspector with files/diffs/Git and adds a
lifecycle-owned PTY.
