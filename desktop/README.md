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

### Requirements

| Requirement | Version | Used for |
|-------------|---------|----------|
| Python | 3.12+ | App Server and Agent runtime |
| uv | Current | Python environment management |
| Node.js | 22+ | React frontend and build scripts |
| Rust | Stable | Tauri application shell |

Install the platform dependencies from the
[Tauri 2 prerequisite guide](https://v2.tauri.app/start/prerequisites/) before
preparing the repository.

### Windows PowerShell

#### 1. Install the Windows toolchains

Tauri development on Windows requires Microsoft Edge WebView2 and the Visual
Studio 2022 Build Tools workload **Desktop development with C++**. Rust must use
the MSVC host toolchain; Rustup alone does not install Microsoft's `link.exe`.

Run the following commands in PowerShell. Accept the UAC prompt raised by the
Build Tools installer:

```powershell
winget install --id astral-sh.uv --exact
winget install --id OpenJS.NodeJS.LTS --exact
winget install --id Rustlang.Rustup --exact
winget install --id Microsoft.VisualStudio.2022.BuildTools --exact `
  --override "--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

WebView2 is normally already installed on supported Windows 10 and Windows 11
systems. If it is missing, install the Evergreen Runtime from the Microsoft
download page linked by the Tauri prerequisite guide.

#### 2. Restart PowerShell and verify the tools

Close all existing PowerShell windows after the installers finish. Open a new
PowerShell window so the updated user `PATH` is loaded, then run:

```powershell
uv --version
node --version
rustup default stable-msvc
rustc --version
cargo --version
```

#### 3. Prepare the repository

Run these commands from the repository root:

```powershell
uv venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -e .
Set-Location desktop
npm ci
$env:DEEPCODE_PYTHON = (Resolve-Path ..\.venv\Scripts\python.exe)
npm run setup:sidecar
npm run build:sidecar
```

The explicit interpreter path prevents an active Conda or other environment
from receiving the editable DeepCode installation.

#### 4. Start DeepCode Desktop

From the same `desktop` directory and PowerShell window:

```powershell
npm run tauri -- dev
```

Keep the terminal open while Desktop is running. Press `Ctrl+C` in that terminal
to stop the development application. On later launches, set `DEEPCODE_PYTHON`
again if using a new PowerShell window:

```powershell
Set-Location desktop
$env:DEEPCODE_PYTHON = (Resolve-Path ..\.venv\Scripts\python.exe)
npm run tauri -- dev
```

### macOS and Linux

After installing the platform dependencies from the Tauri prerequisite guide,
run the following commands from the repository root:

```bash
uv venv --python 3.12
uv pip install --python .venv/bin/python -e .
cd desktop
npm ci
npm run setup:sidecar
npm run build:sidecar
cd ..
./scripts/deepcode-desktop
```

To use `deepcode-desktop` from any working directory, link the launcher into a
directory on `PATH` once:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/scripts/deepcode-desktop" ~/.local/bin/deepcode-desktop
```

### Development sidecar

The first `build:sidecar` creates the resource directory declared in
`tauri.conf.json`, which Tauri validates during development as well as release
builds. Debug runtime launch still prefers the repository `.venv`, so ordinary
Python and React edits do not require rebuilding the PyInstaller sidecar.
Rebuild it only after removing `desktop/build/sidecar` or changing packaged
runtime dependencies.

### Windows troubleshooting

| Symptom | Likely cause | Resolution |
|---------|--------------|------------|
| `rustup`, `cargo`, or `npm` is not recognized | The terminal has the old `PATH` | Close all PowerShell windows and open a new one. |
| `linker 'link.exe' not found` | The Visual C++ workload is missing | Open Visual Studio Installer, modify Build Tools 2022, and select **Desktop development with C++**. |
| `uv pip` reports a Conda path instead of `.venv` | Another Python environment is active | Keep the explicit `--python .venv\Scripts\python.exe` argument. |
| `build/sidecar/dist/deepcode-app-server` does not exist | The first sidecar build has not run | From `desktop`, run `npm run setup:sidecar` and then `npm run build:sidecar`. |

### Configure the first LLM connection

1. Open **Settings → AI providers** and select **Add provider**.
2. Choose a provider. Its normal endpoint and protocol are filled in for you.
3. Paste an API key, or choose an environment variable. Custom gateways and
   local servers without a universal endpoint also ask for their base URL;
   optional endpoint overrides remain under **Advanced connection settings**.
4. Select **Save and check**. DeepCode checks credential availability and model
   discovery without sending repository or Session content.
5. Under **Agent model**, choose an exact model ID and select **Save and verify
   model**. This final step sends one tiny `reply OK` inference request so a
   public model catalog cannot be mistaken for usable model access.
6. Open a Session to inherit the default, or use its composer picker to switch
   the connection/model for future Turns.

API keys are written to `~/.deepcode/credentials.json` in user-private storage.
Desktop receives only configured/missing status and never reads a stored key
back. The same named connections and defaults are used by CLI and Desktop.

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
work. Paper2Code captures both its planning and implementation profiles at
admission; an explicit Session connection/model applies to both phases, while
Sessions without an override inherit the corresponding advanced phase default.

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
deepcode provider models personal-openrouter --refresh
deepcode provider test personal-openrouter --model <model-id>
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
