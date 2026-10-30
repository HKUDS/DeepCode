# The terminal UI

Run `deepcode` in your project folder to work from the terminal. Type a task in
plain language, then use the commands below to choose a model, attach files,
and control the conversation. New to DeepCode? Follow
[Your first coding task](getting-started.md) for setup.

## Keys

| Key | What it does |
|---|---|
| `Esc` | With an empty prompt, interrupt the running turn: the turn stops and the conversation survives. With a completion menu open, close the menu. While you are typing a message, `Esc` does nothing, so your draft is kept. |
| `Ctrl+C` | Clear the message you are typing. With an empty prompt, interrupt the running turn, like `Esc`. While a command such as `/compact` or `/goal wait` is running, stop waiting for that command. |
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
| `/raw [on\|off]` | Print answers exactly as the model wrote them, without Markdown styling or wrapping, for copying — see [How answers are shown](#how-answers-are-shown) |
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

## How answers are shown

Answers are rendered as Markdown while they stream. Each line appears once
its last word has arrived, and lines already printed never change:

- Headings, lists, quotes and paragraphs are styled as soon as their line is
  complete. Links show their address, as `text (https://…)`.
- Code blocks appear line by line with syntax highlighting. They have no
  indent and DeepCode never wraps them, so a copied block is exactly what the
  model wrote. Your terminal wraps lines that are too long for the window.
- A table appears once its last row has arrived, because a later row can
  widen the columns above it.
- A line that contains a URL is wrapped by your terminal instead of
  DeepCode, so the address is never split.

The line the model is still writing is shown under the transcript, above the
status line, until it is complete.

To copy an answer verbatim, run `/raw`: answers are then printed exactly as
the model wrote them, Markdown syntax included, and your terminal does all
the wrapping. `/raw` again (or `/raw off`) switches back. The setting lasts
until you close the TUI.

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
- **`Esc`** (from an empty prompt) stops the turn; what completed stays in
  history.

## Approvals

Under the default `ask` preset, sensitive tools pause for you. The card shows
the whole command and where it runs, or the change an edit would make:

```text
◆ Approval needed · Run a command
  Running a command needs your approval.
    $ rm -rf build && echo removed
    in ~/projects/app
  y allow once · a always allow `rm …` and `echo …` this session · n deny
```

While it waits, the status line stops and says `◆ Waiting for your approval`,
and the prompt reads `allow? y/a/n ›`. Anything else you type is kept in the
prompt, not sent, until you answer.

`y`/`yes` allows this once. `n`/`no` denies — the agent is told, and works
around it. `a`/`always` allows the same kind of action for the rest of the
session, and the card says which:

- **Commands** — the program and its subcommand: allowing `git status` allows
  `git status -s` but not `git push`; allowing `ls` does not allow `rm`. A
  command joined with `&&`, `;` or `|` runs without asking only when every
  part was allowed. A command that writes a file with `>`, runs through
  `sudo`, `env` or `xargs`, or runs inline code (`python -c`) is allowed only
  exactly as written.
- **File edits** — edits anywhere in this workspace. An edit outside it is
  allowed for that file only.
- **Other tools** — that tool.

Switching to `/permissions full-access` asks you to confirm explicitly before
it takes effect; `read-only` denies mutating actions under the tool policy. These are
per-session — your other sessions keep their own settings.

## What the status line tells you

The bottom line is alive during work — a spinner plus what the agent is on:

```text
⠸ Run · 4s · pytest -q
⠼ Thinking · 8s · High · comparing the two configs
⠋ Working · 2s
⠙ Queued · 6s · waiting for a free slot (2 running)
◆ Waiting for your approval · y allow once · a always allow `ls …` this session · n deny
```

`Run` shows the current tool or command. `Thinking` appears when the provider
shares reasoning progress; `Working` means DeepCode is waiting for its response
to begin; `Responding` means the answer is arriving, with its unfinished line
shown just above. `Queued` says what your message waits for: the current Turn
in this session, another session working in the same folder, or a free slot
(the service runs two Turns at once). A message queued behind a running Turn
shows as `1 queued next`. While an approval waits, nothing runs and the line
stands still. A tool call you denied shows as `Denied by you`, and one a
permission rule or a hook refused as `⊘ … declined` with its reason. If it
stays there unexpectedly
long, see [Troubleshooting](troubleshooting.md).
When idle, the line shows your display mode and keyboard hints.

Leaving with `Ctrl-D` while a task runs does not stop it: the service keeps
running it, and the TUI prints the command to come back to it
(`deepcode --resume <session id>`).
