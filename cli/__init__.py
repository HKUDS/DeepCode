"""DeepCode CLI entries (P2, L5).

Two frontends, both pure consumers of the SQ/EQ event stream — neither
touches the kernel directly (DEEPCODE_V2_MASTER_PLAN.md §3 event-sourcing
first):

- ``python -m cli.tui`` — the interactive terminal UI: free-form multi-turn
  conversation, streaming output, tool progress, slash commands, session
  resume. The Claude Code / Codex CLI analogue.
- ``python -m cli.exec_cli`` — headless one-shot: run a task, stream NDJSON
  events, exit. The CI / harness / scripting entry.

CLI agent assembly lives in :mod:`core.agent_setup`; the TUI persists through
the canonical :mod:`core.sessions` JSONL store. Desktop uses the same agent
kernel and SessionStore through its own stdio App Server adapter, without
routing CLI command lifecycles through Desktop application services.
"""
