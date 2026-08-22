# The terminal UI

Everything in the TUI happens through plain conversation, a small set of slash
commands, and three keys. This page is the complete surface — the table below
is generated from the command registry in `cli/tui/commands.py`, so if it's
here, it works.

## The three keys

| Key | What it does |
|---|---|
| `Esc` | Interrupt the running turn. The turn stops; the conversation survives. |
| `Ctrl+O` | Cycle transcript detail: `normal → verbose → summary`. Presentation only — it never changes what the model receives. |
| `Ctrl+D` | Quit (same as `/exit`). |

## Slash commands

Type `/` and the completer lists everything. **Every command that takes an
argument opens a picker when you give none** — type `/model` alone and choose
from a list with arrow keys, type to filter, `Enter` to commit, `Esc` to
cancel (a first `Esc` clears your filter, a second closes).

| Command | Does |
|---|---|
| `/help` | This table, in the terminal |
| `/new [title]` | Start a fresh conversation |
| `/resume [id\|all]` | Pick a session from this directory — or `all` directories |
| `/rename <title>` | Rename this session |
| `/delete <id>` | Permanently delete a stored session |
| `/model [connection] [id]` | Show or switch connection/model — the picker shows every configured connection's full catalog |
| `/effort [auto\|off\|level]` | Reasoning effort for the *next* turns |
| `/permissions [preset]` | Tool access: `ask` · `read-only` · `full-access` · `inherit` |
| `/transcript [mode]` | Same three modes `Ctrl+O` cycles |
| `/preset [id\|clear]` | Agent presets — selectable only while the conversation is still blank |
| `/skills` | List every discovered skill |
| `/skill <id\|name>` | Arm a skill for the next turn |
| `/plugins` | List installed plugins |
| `/mcp [action]` | List, add, test, authorize MCP servers |
| `/goal …` | A durable goal for this session — see [Goals](goals-and-headless.md) |
| `/queue <instruction>` | Enqueue an instruction as the next turn |
| `/stop` | Interrupt the active turn (same as `Esc`) |
| `/retry` | Re-run the last finished turn with the current model |
| `/clear` | Clear the conversation context |
| `/compact` | Summarize older turns to free context — see [Sessions](sessions.md) |
| `/exit` | Quit |

## Inline references

**`@path`** — attach a file's content to your message. Tab-completion walks
the workspace; the file arrives as a fenced block, so the model reads it
without a tool round-trip:

```text
› the parser in @src/config/loader.py rejects valid YAML — why?
```

**`$skill-name`** — invoke a skill inline, with completion over active skills:

```text
› $code-review the changes on this branch
```

## Talking while it works

You don't have to wait for a turn to finish:

- **Type a message mid-turn** and it *steers* the running work — the agent
  sees it at the next boundary: `Steered the active turn.`
- **`/queue`** lines up a full instruction to run *after* the current turn.
- **`Esc`** stops the turn; what completed stays in history.

## Approvals

Under the default `ask` preset, sensitive tools pause for you:

```text
◆ approval needed bash
  rm -rf build/
  ⎿ reply y once · a session · n deny
```

`y`/`yes` allows this once. `a`/`always` allows that tool for the rest of the
session. `n`/`no` denies — the agent is told, and works around it.

Switching to `/permissions full-access` asks you to confirm explicitly before
it takes effect; `read-only` blocks writes and commands outright. These are
per-session — your other sessions keep their own settings.

## What the status line tells you

The bottom line is alive during work — a spinner plus what the agent is on:

```text
⠸ Run · 4s · pytest -q
⠼ Thinking · 8s · High · comparing the two configs
⠋ Working · 2s
```

`Working` means the provider hasn't produced its first token yet — the turn is
alive, not hung. When idle it shows the transcript mode and key hints.
