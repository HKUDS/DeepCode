<div align="center">

<img src="https://raw.githubusercontent.com/HKUDS/DeepCode/main/assets/logo.png" alt="DeepCode logo" width="120" />

# DeepCode

**An open-source coding agent for long-running work.**<br/>
Goals keep going across turns, a local background service keeps working when
you close the window, and the terminal, desktop, browser and headless clients
share the same sessions.

<p>
  <a href="https://pypi.org/project/deepcode-hku/"><img src="https://img.shields.io/pypi/v/deepcode-hku?label=PyPI" alt="PyPI version" /></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-blue" alt="Python 3.12+" />
  <a href="https://github.com/HKUDS/DeepCode/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green" alt="MIT License" /></a>
  <a href="https://arxiv.org/abs/2512.07921"><img src="https://img.shields.io/badge/arXiv-2512.07921-b31b1b" alt="arXiv paper" /></a>
</p>

English · [中文](https://github.com/HKUDS/DeepCode/blob/main/README_ZH.md) ·
[Website](https://hkuds.github.io/DeepCode/)

</div>

## Why DeepCode

- **Work that outlasts one answer.** Give the agent a Goal and it keeps
  working across turns, checking its results, until it reports the Goal
  complete or blocked. You can add requirements, edit the Goal, pause or stop
  it while it runs.
- **Keeps running when you close the client.** Turns run in a local
  background service, not in the window you started them from. Close the TUI,
  reopen the session in the browser, and answer the pending approval there.
- **One session, four clients.** The TUI, Desktop, Web and `deepcode exec`
  read and write the same projects, sessions, models, Skills and permissions.
- **You decide what it may do.** A folder must be trusted before the agent
  works in it. Each session runs under Ask, Read only or Full access, shell
  commands run in an OS sandbox under Ask, and repository hooks need your
  approval. Use any of 14 built-in providers, your own OpenAI- or
  Anthropic-compatible endpoint, or a local model.

<p align="center">
  <img src="https://raw.githubusercontent.com/HKUDS/DeepCode/main/assets/readme/web-ui.png" alt="The DeepCode Web client: a session that fixed a failing test, with the agent's tool calls in the conversation and the resulting diff in the Review panel" width="100%" />
</p>

<p align="center"><sub>The Web client after a real task: the agent ran the tests, edited
one file, re-ran the tests, and the change is open for review.</sub></p>

## Quick start

**1. Install.** With [uv](https://docs.astral.sh/uv/):

```console
uv tool install --python 3.12 deepcode-hku
deepcode init
```

`deepcode init` creates `~/.deepcode` and sets the default access to **Ask**.
If your shell cannot find `deepcode`, run `uv tool update-shell` and open a new
terminal. On Windows, install uv with
`winget install --id astral-sh.uv --exact`.

**2. Add a model.** This example uses OpenRouter; any
[supported provider](#models-and-providers) works the same way. The key is
read without echo and stored in `~/.deepcode/credentials.json`.

```console
deepcode provider set my-openrouter --template openrouter --api-key
deepcode provider models my-openrouter --refresh
deepcode provider test my-openrouter --model MODEL_ID
```

Replace `MODEL_ID` with a model from the list. In Desktop or Web you can do the
same under **Settings → AI providers**.

**3. Run it in a project.**

```console
cd /path/to/your-project
deepcode --trust --connection my-openrouter --model MODEL_ID
```

`--trust` is needed once per project. Later, run `deepcode` in that folder and
use `/model` to switch models. Try a first task:

```text
Explain how this project is organized and how to run its tests.
Do not change any files yet.
```

The step-by-step tutorial is [Your first coding task](docs/guide/getting-started.md).

## Choosing a client

All clients connect to the same local service, so a session started in one
can be continued in any other.

| Client | Start with | Use it for |
|---|---|---|
| **TUI** | `deepcode` | Working in the terminal. See [The terminal UI](docs/guide/the-tui.md). |
| **Web** | `deepcode web` | The full workbench (diffs, files, terminal, Goals) in your browser, from the same install. See [Web client](docs/WEB_CLIENT.md). |
| **Desktop** | `deepcode desktop` | The same workbench as a native app. Runs from a source checkout for now, see [below](#desktop). |
| **Headless** | `deepcode exec "<task>"` | One turn from a script or CI job; `--json` prints events as NDJSON. `deepcode loop "<goal>"` runs a Goal. See [Headless and automation](docs/HEADLESS_AND_AUTOMATION.md). |

`deepcode web --no-open` prints the sign-in link instead of opening a browser.
The link works once and expires after 60 seconds.

### Desktop

There are no Desktop installers on the Releases page yet. Desktop runs from a
source checkout and needs Node.js 22+, Rust (stable) and the
[Tauri 2 prerequisites](https://v2.tauri.app/start/prerequisites/) for your
platform:

```console
git clone https://github.com/HKUDS/DeepCode.git
cd DeepCode
npm --prefix desktop ci
npm --prefix desktop run build:web
uv tool install --python 3.12 --force .
deepcode init
deepcode desktop
```

The first launch builds the app's resources and takes a few minutes; keep the
terminal open while Desktop runs. Windows toolchains and troubleshooting are in
the [Desktop guide](desktop/README.md).

### The background service

Starting any client starts the service if it is not running. Closing a client
leaves accepted work running; pending approvals wait for you, and the computer
must stay awake.

| Task | Command |
|---|---|
| Check whether it is running | `deepcode service status` |
| Read recent logs | `deepcode service logs --lines 100` |
| Stop after current work finishes | `deepcode service stop --drain --timeout 60` |
| Start it when you log in | `deepcode service install --at-login` |

Upgrades and backups: [Upgrading DeepCode](docs/UPGRADE_AND_RESTORE.md).
Problems: [Troubleshooting](docs/guide/troubleshooting.md).

## Requirements and platforms

| | |
|---|---|
| Python | 3.12 or later (CI tests 3.12, 3.13 and 3.14) |
| Operating systems | macOS, Linux and Windows |
| Tools | Git, for change review and worktrees. On Linux, bubblewrap (`bwrap`) for the command sandbox. |
| Model | An API key for a supported provider, or a local Ollama or vLLM server. The model must support tool calling; `deepcode provider test <id> --agent` checks it. |
| Web client | A current browser on the same computer |
| Desktop | Node.js 22+, Rust and the Tauri prerequisites (source build) |

## Core features

- **Repository work.** Reads, searches and edits files, applies patches, runs
  commands and tests, and shows each change as a diff.
  [Getting started](docs/guide/getting-started.md)
- **Goals.** `/goal <objective>` in the TUI or the Goal panel in Desktop and
  Web. The Goal continues across turns and survives restarts; an optional
  check turns back a completion claim while the latest check failed.
  [Goals and automation](docs/guide/goals-and-headless.md)
- **Sessions and context.** Every session is saved with its tool calls,
  approvals and Goal state. `/resume`, `/compact` and per-session context caps
  keep long work going. [Sessions](docs/guide/sessions.md)
- **Project instructions and memory.** Reads `AGENTS.md` and `CLAUDE.md`, and
  keeps project notes in `.deepcode/memory/`.
  [Skills and memory](docs/guide/skills-and-memory.md)
- **Models and providers.** Named connections, per-session model and
  reasoning effort, and connection checks before use.
  [Models and providers](docs/guide/models.md)
- **Skills and plugins.** Reusable instructions from `.agents/skills` and
  `~/.agents/skills`, eight bundled Skills, and a Skill creator. Plugins
  package Skills and MCP servers. [Skills](docs/guide/skills-and-memory.md) ·
  [Local plugins](docs/LOCAL_PLUGINS.md)
- **MCP.** Connects to stdio, SSE and Streamable HTTP servers, with a bundled
  catalog and OAuth sign-in. `deepcode mcp serve` exposes DeepCode itself as an
  MCP server. [MCP guide](docs/integrations/MCP.md)
- **Agent presets and sub-agents.** Presets set a persona and narrow the tool
  set. Sub-agents can work in separate Git worktrees.
  [Configuration](docs/guide/configuration.md#agent-presets)
- **Automations.** Run a saved instruction on a schedule with the same
  agent, permissions and history.
  [Automations](docs/guide/goals-and-headless.md#persistent-interval-automations)
- **Voice input.** Optional dictation in the Desktop and Web composer.
  [Voice dictation](docs/guide/dictation.md)
- **Paper2Code.** Turn a paper or technical document into a code base.
  [Paper2Code](docs/paper2code.md)

### Models and providers

Built-in templates: OpenRouter, Requesty, Opper, Forge, Amazon Bedrock,
Anthropic, OpenAI, DeepSeek, Gemini, Zhipu AI, DashScope, MiniMax, Ollama and
vLLM. A **Custom** connection points at any endpoint that speaks the OpenAI
Chat Completions, OpenAI Responses or Anthropic Messages API.
`deepcode provider list` shows them and which ones are ready.

## Security and data

- **Data stays on your computer.** Settings, sessions, state and logs are under
  `~/.deepcode` (or `$DEEPCODE_HOME`). Project notes are in
  `<project>/.deepcode/memory/`. DeepCode has no account system and sends no
  analytics.
- **Local service.** The background service listens on `127.0.0.1` only (port
  3081, or a free port if that is taken). Native clients authenticate with a
  private token; the Web client signs in with a one-time link.
- **Project trust.** The agent does not run in a folder you have not trusted.
  A project's own config cannot select Full access, turn off the sandbox or
  redirect model requests.
- **Access presets and approvals.** **Ask** asks before edits and commands and
  runs shell commands in an OS sandbox; **Read only** denies changes; **Full
  access** removes approvals and the sandbox. Your `deny` rules apply under
  every preset.
- **Hooks.** Hooks a repository declares run only after you approve them;
  changed hooks wait for `deepcode hooks approve`.
- **Credentials.** API keys are stored in `~/.deepcode/credentials.json` with
  owner-only permissions and never returned to a client. Shell commands do not
  see them.
- **What model providers receive.** The system prompt, project instructions,
  tool definitions, the conversation and tool results (file contents, command
  output) go to the endpoint of the connection you chose. `providers.egress`
  can restrict which hosts are allowed. A local model keeps everything on your
  machine.

Details: [Security and data](docs/guide/security.md). To report a
vulnerability, see [SECURITY.md](SECURITY.md).

## Documentation

| Topic | Guide |
|---|---|
| First task, step by step | [Getting started](docs/guide/getting-started.md) |
| Terminal UI keys and commands | [The terminal UI](docs/guide/the-tui.md) |
| Resume, compact and manage sessions | [Sessions](docs/guide/sessions.md) |
| Providers, local models, reasoning | [Models and providers](docs/guide/models.md) |
| Project instructions, memory, Skills | [Skills and memory](docs/guide/skills-and-memory.md) |
| Goals, automations, headless runs | [Goals and automation](docs/guide/goals-and-headless.md) |
| Config file and environment variables | [Configuration](docs/guide/configuration.md) |
| Data, sandbox, approvals | [Security and data](docs/guide/security.md) |
| Startup and connection problems | [Troubleshooting](docs/guide/troubleshooting.md) |
| Scripts, CI and administration | [Headless and automation](docs/HEADLESS_AND_AUTOMATION.md) |
| MCP servers | [MCP guide](docs/integrations/MCP.md) |
| Background service | [Local background service](docs/SERVICE_RUNTIME.md) |
| Upgrades and backups | [Upgrading DeepCode](docs/UPGRADE_AND_RESTORE.md) |
| Desktop from source | [Desktop guide](desktop/README.md) |
| Paper2Code | [Paper2Code](docs/paper2code.md) |

## What's new

- **2026-10-29** — Optional microcompact that clears old tool output before
  summarizing (`agents.defaults.microcompact`), an optional check before a
  Goal completes (`goal.completionVerifier`), folder browsing in the Web
  client, an opt-in risk check (`DEEPCODE_RISK_EVALUATOR=1`), and approval for
  project hooks (`deepcode hooks status` / `approve`).
- **2026-10-28** — Agent presets can remove tools with `disallowed-tools`,
  file tools ask before writing hook files, and `deepcode mcp list` flags MCP
  packages without a fixed version.
- **2026-10-21** — The Windows background service no longer opens console
  windows, and Desktop and Web take a separate font for code.

All changes: [CHANGELOG.md](CHANGELOG.md).

## Research origin

DeepCode started as Paper2Code, a multi-agent system that turns research papers
into working code. On [PaperBench](https://openai.com/index/paperbench/) it
scored 75.9% on the human-expert subset, above the best reported human
baseline (72.4%). The method is described in
[DeepCode: Open Agentic Coding](https://arxiv.org/abs/2512.07921). Paper2Code
is still available in Desktop and Web; see [Paper2Code](docs/paper2code.md).

## Contributing

Development setup, checks and pull request guidelines are in
[CONTRIBUTING.md](CONTRIBUTING.md). Everyone who has opened a pull request is
listed in [CONTRIBUTORS.md](CONTRIBUTORS.md).

<a href="https://github.com/HKUDS/DeepCode/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=HKUDS/DeepCode&max=999" alt="DeepCode contributors" />
</a>

## Support

- Bugs and feature requests: [GitHub Issues](https://github.com/HKUDS/DeepCode/issues)
- Security reports: [SECURITY.md](SECURITY.md)
- Community: the Feishu and WeChat groups listed on the
  [HKUDS profile](https://github.com/HKUDS/.github/blob/main/profile/README.md)

## Citation

If DeepCode contributes to your research, please cite:

```bibtex
@misc{li2025deepcodeopenagenticcoding,
  title         = {DeepCode: Open Agentic Coding},
  author        = {Zongwei Li and Zhonghang Li and Zirui Guo and Xubin Ren and Chao Huang},
  year          = {2025},
  eprint        = {2512.07921},
  archivePrefix = {arXiv},
  primaryClass  = {cs.SE},
  url           = {https://arxiv.org/abs/2512.07921}
}
```

## License

DeepCode is released under the [MIT License](LICENSE).
Copyright © 2025 Data Intelligence Lab at The University of Hong Kong.
