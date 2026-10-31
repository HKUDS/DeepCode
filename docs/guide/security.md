# Security and data

DeepCode runs on your computer and works in folders you choose. This page
describes where its data lives, what runs in the background, how tool access is
controlled, and what is sent to model providers. To report a vulnerability, see
[SECURITY.md](../../SECURITY.md).

## Where your data lives

Everything DeepCode stores is under `~/.deepcode` (or `$DEEPCODE_HOME`),
except notes the agent keeps inside a project:

| Path | Contents |
|---|---|
| `deepcode_config.json` | Your settings. See [Configuration](configuration.md). |
| `credentials.json` | Model connection keys. Written atomically with owner-only permissions (`0600`; an owner-only ACL on Windows). Clients never receive a saved key back. |
| `auth/mcp.json` | Sign-in tokens for MCP servers that use OAuth. |
| `sessions/<id>/session.jsonl` | The record of each Session: messages, tool calls and tool results. |
| `state/deepcode.sqlite3` | Projects, trust decisions, Turns, approvals, Goals and automations. |
| `state/deepcode.sqlite3.service/` | The background service's address, private access token and `service.log`. |
| `logs/llm.jsonl` | One line per model call: provider, model, timing, token counts, and the first 2,000 characters of the request and response. |
| `plugins/`, `agent-presets/`, `hooks.json` | Your plugins, presets and hooks, if any. |
| `<project>/.deepcode/memory/` | Notes the agent keeps for that project (`MEMORY.md` and topic files). |

DeepCode has no account system, no product analytics and no crash reporter. It
sends nothing to its developers. Deleting a Session removes its records but
never your project files. To back up or move this data, see
[Upgrading DeepCode](../UPGRADE_AND_RESTORE.md).

## The background service

All clients (TUI, Desktop, Web and `deepcode exec`) talk to one local service.
It starts when a client needs it and keeps running after the client closes, so
accepted work continues. Stop it with `deepcode service stop`.

- It listens on `127.0.0.1` only, port `3081` by default (another free port if
  3081 is taken). `deepcode service status` shows the address.
- Native clients authenticate with a private token stored in the service
  folder. They first check, with a challenge, that the server knows the token
  before sending it.
- The Web client gets a one-time link from `deepcode web`. The link expires
  after 60 seconds; the browser session it creates lasts 12 hours and ends when
  the service restarts. The session is not stored in a cookie, and pages are
  served with a strict Content Security Policy.
- Requests with an unexpected `Host` header are refused, and management routes
  reject browser requests.
- It is not a remote or multi-user server. Every client acts as the local user.

The service runs only while you are logged in. `deepcode service install
--at-login` starts it at login (launchd on macOS, a systemd user unit on Linux,
Task Scheduler on Windows); `deepcode service uninstall` removes that. Details
are in [Local background service](../SERVICE_RUNTIME.md).

## Project trust

The agent does not run in a folder until you trust it: confirm the prompt in
Desktop or Web, or start the TUI or `deepcode exec` with `--trust`. Trust is
remembered per project. It lets the agent work there; it does not grant Full
access.

A project's own `deepcode_config.json` cannot point model requests at another
host, change the dictation endpoint, select Full access, or turn the sandbox
off.

## Access presets

Each Turn runs with one of three presets, chosen per Session in the composer,
with `/permissions` in the TUI, or with `--access` on the command line.
`deepcode init` makes **Ask** the default.

| Preset | What the agent may do |
|---|---|
| **Ask** | Read freely. Asks before edits, shell commands and other actions with side effects. Shell commands run in the OS sandbox, and credential files stay out of reach. |
| **Read only** | Read and search only. Edits, shell commands and other tools with side effects are denied. |
| **Full access** | Everything, without approvals, outside the sandbox. Use it only for work you would run yourself. |

- An approval resumes the exact tool call that was waiting. A Session's access
  is frozen into each Turn when it is accepted; changing it affects later Turns.
- Your own rules in `security.permissions` can `allow`, `ask` or `deny` a tool
  or a command pattern. A `deny` rule applies under every preset.
- Under Ask and Read only, file tools cannot read or write credential
  locations such as `~/.ssh` private keys, `~/.aws`, `~/.config/gcloud`,
  `.env` files, `*.pem` and `*.key` files, `deepcode_config.json` and
  DeepCode's own `credentials.json`. Sandboxed shell commands are kept out of
  the fixed locations in your home folder, such as `~/.ssh`, `~/.aws` and
  `~/.netrc`.
- A screen for destructive commands (for example `rm -rf /`) is always on.
  `DEEPCODE_COMMAND_SCREEN=strict` adds screens for piping downloads into a
  shell and for unusual package installs. `DEEPCODE_RISK_EVALUATOR=1` turns
  calls that would run without asking, but look risky, into approval requests.
- Stopping a Turn ends the processes it started. After a crash, interrupted
  Turns are settled without re-running their side effects.

### The command sandbox

Under Ask, shell commands run in an OS sandbox that limits writes to the
project (and temporary files):

| Platform | Mechanism |
|---|---|
| macOS | `sandbox-exec`, deny by default |
| Linux | bubblewrap (`bwrap`); install it from your distribution's packages |
| Windows | A Job Object: process-tree cleanup, but no write restriction; writes rely on approvals |

Network access stays available inside the sandbox, because builds and package
installs need it. Shell commands do not see the model keys DeepCode uses
(`DEEPCODE_BASH_FULL_ENV=1` restores them; `DEEPCODE_BASH_SCRUB_ENV=1` removes
all credential-like variables).

## Hooks

Hooks are shell commands that run on agent events. Your own hooks in
`~/.deepcode/hooks.json` always run. Hooks a repository declares, in
`.deepcode/hooks.json` or `.claude/settings.json`, run only after you approve
them:

- Trusting a project approves the hooks it declares at that moment.
- If the declarations change later, they stop running until you run
  `deepcode hooks approve` in the project.
- `deepcode hooks status` lists the hooks and whether they are approved.

File tools ask before writing those hook files, except under Full access.

## MCP servers and plugins

MCP servers run with your user's rights, outside the command sandbox. Adding a
server from the bundled catalog copies a disabled entry. An enabled server
starts inside an agent session, or when you test the connection.
`deepcode mcp list` flags `npx` and `uvx` servers whose package version is not
fixed. Plugin manifests that contain secret-like literal values produce a
warning. See the [MCP guide](../integrations/MCP.md) and
[Local plugins](../LOCAL_PLUGINS.md).

## What is sent to model providers

Each model request goes to the endpoint of the connection you selected, and
contains:

- DeepCode's system prompt and the Session's access mode;
- project instructions (`AGENTS.md`, `CLAUDE.md` and similar files) and the
  project memory index;
- the list of available Skills and the tool definitions, including MCP tools;
- the conversation so far, including tool results: file contents the agent
  read, command output, fetched web pages and attachments.

Your API key is sent only to the endpoint of the connection it belongs to.
`providers.egress` can restrict which hosts receive requests
([Model egress](configuration.md#model-egress)). The provider's own data
policy applies to what it receives. With a local model (Ollama, vLLM), requests
stay on your machine.

Other network traffic happens only when you or the agent ask for it: fetching
a model list, `web_fetch`, MCP servers, and commands the agent runs.
