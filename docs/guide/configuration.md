# Configuration

Most settings can be changed in **Settings** in Desktop and Web, with `/model`,
`/permissions` and other commands in the TUI, or with `deepcode provider` on
the command line. This page lists the settings that live only in the
configuration file or in environment variables.

## Where settings live

DeepCode reads two JSON files and merges them:

| Layer | File | Notes |
|---|---|---|
| User | `~/.deepcode/deepcode_config.json` | Created by `deepcode init`. Set `DEEPCODE_HOME` to move the whole directory. |
| Project | `deepcode_config.json` in the project, or the nearest parent folder that has one | Overrides the user file key by key. |

A project file cannot change what belongs to you: it cannot set provider
endpoints, headers or named connections, the `dictation` endpoint, Full
access, or turn the command sandbox off. Those keys are ignored when they
appear in a project file.

`deepcode init` writes a user file that sets the default access to **Ask**:

```json
{
  "security": {
    "accessPreset": "ask"
  }
}
```

Model connections and their keys are managed with `deepcode provider` or in
**Settings → AI providers**; see [Models and providers](models.md). Keys are
kept in `~/.deepcode/credentials.json`, not in `deepcode_config.json`.

## Settings reference

| Key | Values (default) | What it does |
|---|---|---|
| `security.accessPreset` | `ask`, `read_only`, `full_access` | Tool access for new turns. See [Security and data](security.md#access-presets). |
| `security.permissions` | object (`{}`) | Per-tool rules, see below. |
| `agents.defaults.connection` / `agents.defaults.model` | connection id / model id | Default connection and model for new sessions. |
| `agents.defaults.reasoningEffort` | a level the model supports | Default reasoning effort. |
| `agents.defaults.defaultPreset` | preset id | Agent preset for new sessions. |
| `agents.defaults.microcompact` | `true` / `false` (`false`) | When a long session nears its context limit, replace stale tool results with a short placeholder (keeping paths to saved output) before asking the model for a summary. |
| `goal.completionVerifier` | `"off"` / `"rule"` (`"off"`) | With `"rule"`, a claim that a Goal is complete is turned back while the latest check failed or no work was done. |
| `goal.maxRejections` | integer ≥ 1 (`3`) | After this many rejected completion claims, the Goal is marked blocked. |
| `providers.egress` | object | Limits which hosts receive model requests; see below. |
| `mcpServers` | object | MCP servers for the agent. See the [MCP guide](../integrations/MCP.md). |
| `skills.disabled` | list of Skill ids | Skills that are not offered to the agent. |
| `dictation` | object (absent = off) | Voice input. See [Voice dictation](dictation.md). |

### Permission rules

`security.permissions` maps a tool name to rules of the form
`pattern: action`, where the action is `allow`, `ask`, or `deny`. Tool names
and patterns use shell-style wildcards; the pattern is matched against the
command line or the target path. A plain string applies to every call of that
tool.

```json
{
  "security": {
    "permissions": {
      "bash": { "git push*": "deny", "npm test*": "allow" },
      "web_fetch": "ask"
    }
  }
}
```

A `deny` rule applies under every access preset, including Full access.

### Model egress

`providers.egress` restricts the hosts that receive prompts and tool results:

```json
{
  "providers": {
    "egress": {
      "allowedDomains": ["api.deepseek.com"],
      "blockedDomains": [],
      "mode": "enforce"
    }
  }
}
```

An empty `allowedDomains` list allows every host that is not blocked. A blocked
domain always wins. `mode` is `enforce` (default) or `warn`, which only logs.
The environment variables `DEEPCODE_EGRESS_ALLOW_DOMAINS`,
`DEEPCODE_EGRESS_BLOCK_DOMAINS` (comma-separated) and `DEEPCODE_EGRESS_MODE`
add to these settings. This setting is ignored in a project file.

## Agent presets

An agent preset is a Markdown file with YAML front matter. The body is added
to the system prompt; the file name is the preset id. DeepCode looks for
presets in these folders, nearest first:

1. `<project>/.agents/presets/` and `<project>/.claude/agents/`
2. `~/.deepcode/agent-presets/`, `~/.agents/presets/` and `~/.claude/agents/`
3. The presets that ship with DeepCode (`minimal`, `code-reader`)

```markdown
---
name: Reviewer
description: Reads and comments; never edits.
disallowed-tools: write, edit, multi_edit
allow-spawn: false
---
Review the change for correctness and explain each finding.
```

| Key | Meaning |
|---|---|
| `name`, `description` | Shown in the preset picker. |
| `tools` | Allow list of tools. Absent or `*` means all tools. |
| `disallowed-tools` | Tools to remove. Also accepted as `disallowedTools`. Removing `write`, `edit` or `multi_edit` also removes `apply_patch`. `*` and rules such as `Bash(rm:*)` are rejected. |
| `prompt-mode` | `append` (default) or `replace` the base system prompt. |
| `allow-spawn` | Whether the session may start sub-agents. |
| `model` | Suggested model. |
| `order` | Position in the picker. |

Pick a preset with `/preset` in the TUI or the preset picker in Desktop and
Web. A preset is fixed when the session is created; editing the file later
does not change existing sessions. Presets do not change the access preset or
the model route.

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `DEEPCODE_HOME` | `~/.deepcode` | Where configuration, credentials, sessions, state and logs live. |
| `DEEPCODE_SESSIONS_DIR` | `$DEEPCODE_HOME/sessions` | Where session files are stored. |
| `DEEPCODE_RISK_EVALUATOR` | off | `1` screens a tool call that would run without asking; a risky call asks for approval instead. Not used under Full access. |
| `DEEPCODE_COMMAND_SCREEN` | not set | `strict` adds screens for piping downloads into a shell and for package installs from unusual indexes or with look-alike names. The destructive-command screen is always on. |
| `DEEPCODE_ALLOW_REMOTE_SCRIPT` | off | `1` lets one deliberate run pass the download-into-shell screen. |
| `DEEPCODE_BASH_SCRUB_ENV` | off | `1` removes credential-like variables (names containing KEY, TOKEN, SECRET, PASSWORD, CREDENTIAL or AUTH) from the environment of shell commands, hooks and code mode. |
| `DEEPCODE_BASH_FULL_ENV` | off | `1` gives shell commands the full environment, including the model keys DeepCode itself uses, which are otherwise removed. |
| `DEEPCODE_KEYRING` | off | `1` also reads connection keys from the OS keychain (service name `deepcode`) when `credentials.json` has none. Needs the `keyring` Python package; DeepCode still writes keys only to `credentials.json`. |
| `DEEPCODE_EGRESS_ALLOW_DOMAINS`, `DEEPCODE_EGRESS_BLOCK_DOMAINS`, `DEEPCODE_EGRESS_MODE` | not set | See [Model egress](#model-egress). |
| `DEEPCODE_INSTRUCTION_EXCLUDES` | not set | Comma-separated globs of instruction files not to load, for example `code/CLAUDE.md,**/vendor/**`. |
| `DEEPCODE_COMPACTION_MEMORY` | on | `0` stops writing compaction summaries into the project memory note `compactions.md`. |
| `DEEPCODE_LOG_LEVEL` | `INFO` (TUI), `WARNING` (`deepcode mcp serve`) | Log level for those two commands. |

Provider keys can also come from the provider's own variable, such as
`OPENROUTER_API_KEY` or `DEEPSEEK_API_KEY`; `deepcode provider list` shows which
connections are ready. A background service started at login does not read
your shell profile, so set keys with `deepcode provider set` rather than only
in the shell.
