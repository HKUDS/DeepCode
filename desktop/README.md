# DeepCode Desktop

Tauri 2 desktop execution workbench for DeepCode. The desktop is a client of
the same Python application layer and Agent kernel used by the CLI; it does not
contain a second implementation of Agent or Workflow business rules.

P3 provides the packaged sidecar, lifecycle supervision and durable execution
workspace. P4 adds the code workbench: structured Git review and safe discard,
bounded file editing, owned worktrees, PTY terminal and durable test results.
P5 adds durable Paper2Code Threads with plan review, checkpoint retry, truthful
test-gated completion, and bounded Artifact inspection.
See the [P3 architecture](../docs/P3_DESKTOP_RUNTIME_ARCHITECTURE.md),
[P4 architecture](../docs/P4_CODE_WORKBENCH_ARCHITECTURE.md), and
[P5 architecture](../docs/P5_PAPER2CODE_ARCHITECTURE.md), and the
[desktop rebuild plan](../docs/TAURI_DESKTOP_REBUILD_PLAN.md).

## Development

Prerequisites: Python 3.12, Node.js 22, Rust stable, and the platform
dependencies listed by Tauri. Desktop packaging uses an isolated Python 3.12
environment installed from `sidecar-requirements.lock`; it does not depend on
whatever optional packages happen to be installed in the repository virtualenv.

```bash
npm ci
npm run setup:sidecar
npm run build:sidecar
npm run audit:licenses
npm run lint
npm run test
npm run check:protocol
npm run build
cd src-tauri
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --all-targets
```

`setup:sidecar` is idempotent. It creates `build/sidecar/.venv` and installs the
locked runtime plus PyInstaller. Regenerate the lock from
`sidecar-requirements.in` only when packaged runtime dependencies intentionally
change.

The baseline sidecar supports PDF, Markdown, text, HTML, and DOCX without
Docling or a system Office installation. CLI users who want Docling's advanced
layout/image conversion can install `deepcode-hku[advanced-documents]`; it is
deliberately excluded from the Desktop bundle.

Start from source. Debug builds prefer the repository `.venv`, preserving the
normal edit/run loop without rebuilding PyInstaller output:

```bash
npm run tauri -- dev
```

Regenerate desktop protocol types only after changing the canonical schema:

```bash
npm run generate:protocol
```

Build the release application bundle:

```bash
npm run tauri:build
```

Before bundling, Tauri builds a PyInstaller `onedir` App Server, verifies every
lazy runtime import, and performs an isolated `initialize → shutdown` RPC
smoke. The complete directory is embedded under the app resource directory as
`app-server/`; release builds never fall back to source paths or a system
Python.

Useful runtime overrides for tests and diagnostics:

- `DEEPCODE_APP_SERVER_PATH`: explicit sidecar executable.
- `DEEPCODE_DATABASE_PATH`: explicit SQLite path passed to the App Server.
- `DEEPCODE_SIDECAR_PYTHON`: Python interpreter used by the sidecar build.
- `DEEPCODE_SIDECAR_BOOTSTRAP_PYTHON`: Python 3.12 used to create the isolated
  packaging environment.
- `DEEPCODE_TARGET_TRIPLE`: target name used by the sidecar build.

Local macOS bundles use ad-hoc signing so nested Python resources and the app
resource seal can be verified with `codesign --verify --deep --strict`.
`release-desktop.yml` provides architecture-native macOS arm64/x64, Windows
x64, and Linux x64 release jobs, signed updater artifacts, macOS notarization,
Windows Authenticode, Linux AppImage signing, and draft release uploads. It
fails closed until the protected release environment contains every required
credential. See the
[release runbook](../docs/DESKTOP_RELEASE_RUNBOOK.md) and
[privacy/diagnostics contract](../docs/PRIVACY_AND_DIAGNOSTICS.md).

The first visual pass now covers the real empty, populated Session, Inspector,
Settings, system dark-mode, and narrow-window states. It is deliberately
isolated to the React/Tauri client: future visual refinement and the temporary
native application icon can evolve without changing CLI, Agent, or canonical
Session behavior.
