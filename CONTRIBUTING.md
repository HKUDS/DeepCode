# Contributing to DeepCode

Thank you for helping. Bug reports, fixes, documentation and new features are
all welcome. For a larger change, open an issue first so we can agree on the
approach before you spend time on it.

Security problems are the exception: report them privately as described in
[SECURITY.md](SECURITY.md).

## Set up a development checkout

You need Git, Python 3.12 or later, [uv](https://docs.astral.sh/uv/) (or pip),
and Node.js 22 or later.

```bash
git clone https://github.com/HKUDS/DeepCode.git
cd DeepCode

uv venv --python 3.12
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1

# Runtime and test dependencies, pinned the same way CI pins them
uv pip install -r scripts/ci/requirements.lock
uv pip install --no-deps -e '.[test]'

# The Web client is served from app_server/web_assets, which is built, not committed
npm --prefix desktop ci
npm --prefix desktop run build:web

deepcode --version
```

Without `npm run build:web`, `deepcode web` stops with "Web assets are missing
or incompatible". Rebuild after changing anything under `desktop/src`.

To keep your development data apart from your everyday DeepCode, point
`DEEPCODE_HOME` at a scratch folder while you test:

```bash
export DEEPCODE_HOME="$PWD/.dev-home"
deepcode init
```

### Desktop

Desktop also needs Rust (stable) and the
[Tauri 2 prerequisites](https://v2.tauri.app/start/prerequisites/) for your
platform. Then run:

```bash
deepcode desktop --source .
```

The first launch runs `npm ci`, builds the App Server sidecar and opens the app
with `tauri dev`; keep the terminal open. `deepcode desktop --setup` rebuilds
those resources. Platform notes, Windows toolchains and the release build are
in [desktop/README.md](desktop/README.md).

## Before you open a pull request

Run the checks CI runs:

```bash
# Formatting and lint (Ruff, whitespace, workflow lint)
pre-commit run --all-files

# Python tests
python -m pytest -q

# Desktop and Web client
cd desktop
npm run lint
npm test
npm run typecheck
npm run check:protocol
```

`npm run test:web` runs the browser tests against a real local service; see
[Working with CI](docs/CI.md) for that and for the Windows-only checks. A pull
request that changes only Markdown, `docs/`, `assets/` or `website/` skips the
runtime tests.

## Pull request guidelines

- Keep a pull request to one topic. Split unrelated fixes.
- Add or update tests for behavior you change.
- Update the documentation a user would read: the guides in `docs/guide/`,
  the README if the change is visible on the front page, and
  [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
- Use conventional commit subjects, for example `fix(tui): …` or
  `docs: …`.
- Describe how you tested the change in the pull request.

## Project layout

| Path | What it contains |
|---|---|
| `core/` | Agent runtime, tools, permissions and sandbox, providers, MCP, Skills, storage |
| `app_server/` | The local background service and its JSON-RPC protocol |
| `cli/` | The `deepcode` command and the TUI |
| `desktop/` | The React client shared by Desktop and Web, and the Tauri shell |
| `workflows/`, `tools/` | The Paper2Code workflow |
| `tests/` | Python tests |
| `docs/` | Guides and architecture notes |

Architecture notes for contributors:

| Topic | Document |
|---|---|
| Agent execution and approvals | [P2 Agent execution](docs/P2_AGENT_EXECUTION_ARCHITECTURE.md) |
| Desktop sidecar and lifecycle | [P3 Desktop runtime](docs/P3_DESKTOP_RUNTIME_ARCHITECTURE.md) |
| Git review, files, terminal and tests | [P4 Code workbench](docs/P4_CODE_WORKBENCH_ARCHITECTURE.md) |
| Paper2Code workflow | [P5 Paper2Code](docs/P5_PAPER2CODE_ARCHITECTURE.md) |
| Sessions and cross-directory resume | [P6 Session alignment](docs/P6_SESSION_ALIGNMENT_REVIEW.md) |
| Background service | [Local background service](docs/SERVICE_RUNTIME.md) |
| Skills | [Skills architecture](docs/SKILLS_PRODUCT_ARCHITECTURE.md) |
| MCP | [MCP client guide](docs/integrations/MCP.md) |
| Automations | [Automation architecture](docs/AUTOMATION_ARCHITECTURE.md) |
| Desktop interface | [Desktop UI specification](docs/DESKTOP_PRODUCT_UI_SPEC.md) |
| Privacy and diagnostics | [Privacy and diagnostics](docs/PRIVACY_AND_DIAGNOSTICS.md) |

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
