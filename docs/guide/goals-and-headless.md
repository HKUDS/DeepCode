# Goals, automation, and headless runs

Everything past the interactive conversation: objectives that outlive a turn,
runs you script, and runs that happen on a schedule.

## Goals — an objective the session keeps

A goal is durable: it survives turns, interruptions, and resume, and the agent
reports progress against it instead of just answering the last message.

```text
› /goal migrate the config loader off pyyaml, keeping every test green
Goal active · goal_2afe77e3 · tokens 0 · time 0s
```

Manage it with subcommands — `show`, `edit <text>`, `pause`, `resume`,
`continue`, `wait`, `reopen`, `clear`. `wait` blocks until the goal settles;
`continue` nudges another working turn. The agent closes a goal itself through
its goal tools when it judges the work done — with a stated reason you'll see.

## The verify-loop: `deepcode loop`

Goals get teeth when there's a test command to satisfy:

```console
deepcode loop "keep calc.add working while refactoring" \
    -w ./myproj -t "python -m pytest -q" --token-budget 200000
```

The loop works the goal, runs *your* test command as the referee, and
continues until the goal completes, the budget runs out, or you stop it.
`--resume <session>` picks a loop back up. The test command is the honest
part: completion means your tests said so, not the model.

## One task, scripted: `deepcode exec`

```console
deepcode exec "add a --version flag to the CLI" -w ./myproj
deepcode exec "summarize failing tests" -w . --json | jq -r '.type'
```

`exec` runs a single headless task and exits. `--json` streams one structured
event per line — tool starts, results, text, completion — for scripts and CI.
Useful flags: `--resume`, `--model/-m`, `--connection/-c`, `--preset`,
`--skill` (repeatable), `--max-iterations`.

## On a schedule

Two shapes, one runtime:

```console
# a goal with a test command, repeated
deepcode schedule nightly-lint "fix anything ruff flags" \
    -w ./myproj -t "ruff check ." --every 86400 --max-runs 30   # seconds

# lighter: a prompt on an interval, managed as a set
deepcode automation create "triage new TODOs" --prompt "..." --interval-seconds 3600
deepcode automation list | run | runs | update | delete
```

Scheduled runs execute headless under the same permission engine as
interactive ones — trust and access presets apply identically.

## DeepCode as an MCP server

Give other agents (or editors) DeepCode as a tool:

```console
deepcode mcp serve
```

And the other direction — giving DeepCode more tools — is MCP too:

```console
deepcode mcp presets          # bundled catalog
deepcode mcp add context7     # copy a definition (disabled by default)
deepcode mcp test context7    # real handshake before you trust it
deepcode mcp login <name>     # browser OAuth where a server needs it
```

The TUI's `/mcp` offers the same actions. Servers are configured under
`mcpServers` in `deepcode_config.json`; stdio, SSE, and Streamable HTTP are
supported, tools appear as `mcp__server__tool`, and MCP tool policy can only
*narrow* your trust and sandbox decisions — never widen them.

## Hooks, if you need the seams

Lifecycle hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse`,
`PostToolUse`, `PermissionRequest`, `PreCompact`, `Stop`, `SubagentStart/Stop`)
can observe or block at each point — the substrate for audit pipelines and
policy beyond the built-in presets. See
[`docs/HEADLESS_AND_AUTOMATION.md`](../HEADLESS_AND_AUTOMATION.md).
