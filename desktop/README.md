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
[P5 architecture](../docs/P5_PAPER2CODE_ARCHITECTURE.md).

## Run from source

Prerequisites:

| Requirement | Version | Used for |
|-------------|---------|----------|
| Python | 3.12+ | App Server and Agent runtime |
| Node.js | 22+ | React frontend and build scripts |
| Rust | stable | Tauri application shell |

Install dependencies once:

```bash
cd desktop
npm ci
```

Then start the development application from any directory with the repository
launcher:

```bash
deepcode-desktop
```

Install the launcher command once by linking `scripts/deepcode-desktop` into a
directory on `PATH`, for example `~/.local/bin`.

Debug builds prefer the repository `.venv`, so normal Python and React edits do
not require rebuilding the packaged PyInstaller sidecar.

### Configure the first LLM connection

1. Open **Settings → Connections**.
2. Select **Add connection**.
3. Choose a provider template, enter the endpoint when needed, and provide
   either an API key or an environment variable name.
4. Save the connection, then select **Test**.
5. Open a Session and choose the connection/model from the picker below the
   composer.

API keys are written to `~/.deepcode/credentials.json` with user-only
permissions. Desktop receives only configured/missing status and never reads a
stored key back.

### Use models inside a Session

The composer model picker changes the connection, model, and model-advertised
Thinking effort for future Turns in the current Session. Existing history stays
attached to the same canonical Session under `~/.deepcode/sessions/`. A switch
made while work is active leaves the current Turn's immutable execution profile
unchanged and applies to the next Turn. Raw chain-of-thought is never shown as
assistant text; only provider-designated summaries may be rendered, while
opaque continuation state remains private.

Every accepted Turn stores an immutable, secret-free execution profile. Later
changes to defaults or credentials cannot silently change queued or historical
work.

The Session menu separates **Archive** from **Delete permanently**. Archive
preserves the canonical transcript. Permanent deletion removes the transcript,
Goal ledger, and rebuildable application records but never deletes repository
files. The backend rejects deletion when another CLI/terminal still owns the
Session, work is active, a managed worktree is attached, or an Automation must
be removed first.

The equivalent CLI workflow uses the same connection and Session backend:

```bash
deepcode provider list
deepcode provider set personal-openrouter --template openrouter --api-key
deepcode provider test personal-openrouter
deepcode provider models personal-openrouter --refresh
deepcode -c personal-openrouter -m moonshotai/kimi-k3 --effort low
```

### Run a durable Goal

Use **Set a Goal** above the Session composer to define one natural-language
outcome and optional Skills. While it runs, the composer steers the current
Turn; if that Turn has already ended, the same submission starts the next Turn
with the same idempotency key. **Queue next** is the only way to request
next-Turn delivery, and Desktop reports whether an input was started, steered,
or queued. **Edit Goal** updates the same durable Goal identity and delivers the
new objective to its active Turn when possible. Completed Goals can be reopened
for new work without erasing the Session history.

The compact Goal rail shows the current objective, status, token usage, and
pause/resume controls. Tests, builds, diagnostics, diffs, and independent
review remain visible evidence for the working Agent's completion decision.
Goals are not a Desktop-only workflow: the same ledger and ordinary Turn
execution are available from the interactive CLI with `/goal`.

## Development and verification

Desktop packaging uses an isolated Python 3.12 environment installed from
`sidecar-requirements.lock`; it does not depend on optional packages installed
in the repository virtualenv.

Run the complete validation sequence before opening a pull request:

```bash
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
locked runtime plus PyInstaller. Regenerate `sidecar-requirements.lock` from
`sidecar-requirements.in` only when packaged runtime dependencies intentionally
change.

The packaged sidecar supports PDF, Markdown, text, HTML, and DOCX without
Docling or a system Office installation. CLI users who need Docling's advanced
layout/image conversion can install `deepcode-hku[advanced-documents]`; it is
deliberately excluded from the Desktop bundle.

Regenerate Desktop protocol types only after changing the canonical schema:

```bash
npm run generate:protocol
```

## Release build

Build the application bundle:

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
