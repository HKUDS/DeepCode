# DeepCode Guides

Task-oriented, teaching-style documentation. Every command, flag, and output
shown here is taken from the current source tree, not from memory — each page
names the module that makes it true.

| Guide | You will learn |
|---|---|
| [Getting started](getting-started.md) | Install, connect a model, trust a folder, run your first session — and read what the agent shows you |
| [The terminal UI](the-tui.md) | Every slash command, pickers, `@file` and `$skill` references, transcript detail, interrupting, approvals |
| [Sessions](sessions.md) | Resume, work across two windows, compact a long conversation, what lives on disk |
| [Models and providers](models.md) | Connections, declared models, switching mid-conversation, reasoning effort |
| [Skills and memory](skills-and-memory.md) | Reusable playbooks, project instructions, durable notes, plugins |
| [Goals, automation, and headless runs](goals-and-headless.md) | Durable goals, the verify-loop, `deepcode exec --json`, schedules, MCP serving |

Two other surfaces share everything these guides describe:

- **Desktop** — a Tauri app over the same runtime. A Desktop *thread* and a CLI
  *session* are the same record with the same id; start in one, continue in
  the other.
- **Headless** — `deepcode exec` runs one task and exits; `--json` streams
  structured events for scripts and CI.

Reference documents (architecture, contracts, deep dives) remain in
[`docs/`](../). The Paper2Code research pipeline keeps its own documentation:
see the [README's Paper2Code section](../../README.md#paper2code).
