# DeepCode Desktop

DeepCode Desktop is the Tauri 2 workbench for DeepCode. It opens the same local
Projects, canonical Sessions, Agent runtime, models, Skills, Goals,
Automations, permissions, and evidence as the CLI. Desktop is a visual client,
not a second implementation of Agent behavior.

The workbench combines conversation history with structured tool progress,
approvals, Git review, files, terminals, tests, Artifacts, and provider
settings. Work started in one interface can be resumed in the other without
converting or copying its Session.

## Run from source

Prerequisites:

| Requirement | Version | Used for |
|-------------|---------|----------|
| Python | 3.12+ | App Server and Agent runtime |
| Node.js | 22+ | React frontend and build scripts |
| Rust | stable | Tauri application shell |

Install the platform toolchain required by
[Tauri 2](https://v2.tauri.app/start/prerequisites/), then prepare the Python
runtime and frontend dependencies from the repository root:

```bash
uv venv --python 3.12
uv pip install -e .
cd desktop && npm ci && cd ..
```

On macOS or Linux, start the development application with the repository
launcher:

```bash
./scripts/deepcode-desktop
```

To use `deepcode-desktop` from any working directory, link the launcher into a
directory on `PATH` once:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/scripts/deepcode-desktop" ~/.local/bin/deepcode-desktop
```

On Windows, run the same Tauri development command from PowerShell after the
setup above:

```powershell
cd desktop
$env:DEEPCODE_PYTHON = (Resolve-Path ..\.venv\Scripts\python.exe)
npm run tauri -- dev
```

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

API keys are written to `~/.deepcode/credentials.json` in user-private storage.
Desktop receives only configured/missing status and never reads a stored key
back.

### Use models inside a Session

The composer model picker changes the connection, model, and model-advertised
Thinking effort for future Turns in the current Session. Existing history stays
attached to the same canonical Session under `~/.deepcode/sessions/`. A switch
made while work is active leaves the current Turn's immutable execution profile
unchanged and applies to the next Turn. Raw chain-of-thought is never shown as
assistant text. Provider-designated summaries and reasoning details are kept in
a separate typed timeline item, while opaque continuation state remains
private.

The transcript picker beside the composer controls presentation independently
of model effort:

- **Normal** shows a compact completed reasoning preview and keeps provider
  details behind a disclosure.
- **Verbose** expands returned reasoning details and ordinary execution
  activity.
- **Summary** keeps the final answer and important outcomes while hiding
  reasoning and routine tool activity.

`Ctrl+O` cycles the same three modes. The preference is local UI state; changing
it never changes the Session, model request, Goal, tools, or stored evidence.

Every accepted Turn stores an immutable, secret-free execution profile. Later
changes to defaults or credentials cannot silently change queued or historical
work.

### Trust and tool access

The first time a Project is used for Agent execution, review and trust its
canonical folder. Trust remembers which workspace DeepCode may execute in; it
does not grant unrestricted tool access.

The composer access picker controls future Turns in the current Session:

- **Ask** keeps workspace protections and requests approval for sensitive
  actions.
- **Read only** allows inspection while denying mutating tools.
- **Full access** removes approvals and filesystem sandbox boundaries after an
  explicit warning. Use it only for a workspace you are prepared to expose to
  unrestricted local execution.

An active or queued Turn keeps the access profile captured when it was
accepted. Changing the picker applies to later submissions and is immediately
visible to the CLI because both clients edit the same Session setting.

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
deepcode -c personal-openrouter -m <model-id> --effort auto
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

### Run an Automation

Open **Automations** after selecting a trusted Project. A definition may be
manual or interval-based and owns one canonical Goal Thread. **Run now**
creates an idempotent Run; **Runs** shows its durable history; **Open Thread**
opens the same conversation, tools, approvals, evidence, and continuation
controls used by interactive work.

Pause/resume controls only an interval schedule. Manual definitions are always
enabled, and **Run now** remains available while an interval is paused.

Desktop loads one bounded page of definitions and one bounded page of expanded
Run history at a time. **Load more automations** and **Load more runs** append
the next explicit page with stable ID deduplication. Live notifications refresh
the first page; an overflow warning also resets the visible pages safely rather
than presenting a partial cache as complete.

Interval schedules execute while a scheduler-enabled Desktop or App Server is
active. If several compatible processes share the database, one holds the
scheduler leader lease and the others remain available for takeover. Agent and
Workflow Turns still use the shared cross-process capacity and workspace
fences, so another Session cannot mutate the same canonical checkout at the
same time.

Automation instructions never grant trust or elevated permissions. Each Turn
captures the workspace's explicit permission setting or the safe default, and
an approval can be answered from another connected DeepCode client. The same
definitions and Run history are available through `deepcode automation`.
See the
[Automation architecture](../docs/AUTOMATION_ARCHITECTURE.md) for lifecycle,
idempotency, and recovery details.

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
