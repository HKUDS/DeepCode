# The terminal UI

Run `deepcode` in your project folder to work from the terminal. Type a task in
plain language, then use the commands below to choose a model, attach files,
and control the conversation. New to DeepCode? Follow
[Your first coding task](getting-started.md) for setup.

## The three keys

| Key | What it does |
|---|---|
| `Esc` | Interrupt the running turn. The turn stops; the conversation survives. |
| `Ctrl+O` | Cycle transcript detail: `normal → verbose → summary`. Show more or less detail about the work. |
| `Ctrl+D` | Close the client (same as `/exit`); running work continues in the background. |

## Slash commands

Type `/` to list commands. `/model` and `/resume` open pickers when called
without arguments: use arrow keys, type to filter, and press `Enter` to choose.
Other commands may show their current setting or require an argument;
`/queue` and `/rename`, for example, require text.

| Command | Does |
|---|---|
| `/help` | This table, in the terminal |
| `/new [title]` | Start a fresh conversation |
| `/resume [id\|all]` | Pick a session from this directory — or `all` directories |
| `/rename <title>` | Rename this session |
| `/delete <id>` | Permanently delete a stored session |
| `/model [connection] [id]` | Show or switch connection/model — the picker shows every configured connection's full catalog |
| `/effort [auto\|off\|level]` | Reasoning effort for the *next* turns |
| `/context [auto\|tokens]` | Context-window cap for the *next* turns — `64k`, `1m`, or `auto` to follow the model's published window |
| `/permissions [preset]` | Tool access: `ask` · `read-only` · `full-access` · `inherit` |
| `/transcript [mode]` | Same three modes `Ctrl+O` cycles |
| `/preset [id\|clear]` | Agent presets — selectable only while the conversation is still blank |
| `/skills` | List every discovered skill |
| `/skill <id\|name>` | Choose a Skill for your next message |
| `/plugins` | List installed plugins |
| `/mcp [action]` | List, add, test, authorize MCP servers |
| `/goal …` | A durable goal for this session — see [Goals](goals-and-headless.md) |
| `/queue <instruction>` | Save an instruction to run after the current task |
| `/stop` | Interrupt the active turn (same as `Esc`) |
| `/retry` | Re-run the last finished turn with the current model |
| `/reconnect` | Reconnect and return to your current conversation |
| `/clear` | Clear the conversation context |
| `/compact` | Summarize older turns to free context — see [Sessions](sessions.md) |
| `/exit` | Quit |

## Inline references

**`@path`** — include a project file in your message. Type `@` and use
Tab-completion to find the file:

```text
› the parser in @src/config/loader.py rejects valid YAML — why?
```

**`$skill-name`** — invoke a skill inline, with completion over active skills:

```text
› $SKILL_NAME review the changes on this branch
```

Replace `SKILL_NAME` with a name shown by `/skills`.

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
it takes effect; `read-only` denies mutating actions under the tool policy. These are
per-session — your other sessions keep their own settings.

## What the status line tells you

The bottom line is alive during work — a spinner plus what the agent is on:

```text
⠸ Run · 4s · pytest -q
⠼ Thinking · 8s · High · comparing the two configs
⠋ Working · 2s
```

`Run` shows the current tool or command. `Thinking` appears when the provider
shares reasoning progress; `Working` means DeepCode is waiting for its response
to begin. If it stays there unexpectedly long, see [Troubleshooting](troubleshooting.md).
When idle, the line shows your display mode and keyboard hints.
