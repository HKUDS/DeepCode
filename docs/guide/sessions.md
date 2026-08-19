# Sessions

A session is the durable record of one conversation — what was said *and what
the agent did*. It survives restarts, moves between the CLI and Desktop, and
can be resumed weeks later with the agent's own actions intact.

## Resume

```console
deepcode --resume 3f2a91c0     # from the shell
```

or inside the TUI, just:

```text
› /resume
```

`/resume` with no argument opens a picker of this directory's sessions —
titles first, ids on the dim detail line. `/resume all` lifts the directory
filter and shows where each session came from. On resume the tail of the
conversation replays dimmed, so you see where you left off.

**What makes resume trustworthy here:** the canonical record stores tool calls
and tool results, not only the chat text. A resumed agent can answer *"what
did you just run?"* because the record actually contains what it ran. A test
(`tests/test_model_visible_is_logged.py`) pins the rule: every request the
model ever saw must be rebuildable from the session file alone.

## Two windows, one session

Desktop and the CLI share sessions by id — start over lunch in the terminal,
continue in Desktop later. Both may hold the same session open; what's
serialized is *execution*:

```text
› quick question about the config
This Session is currently running a turn in desktop (pid 4821). Wait for it
to finish there, or continue in that window.
```

One live writer per session, enforced with an OS lock held only while a turn
runs. Alternate freely; collide mid-turn and you get that sentence — not a
crashed process. If the holding process dies, the lock dies with it.

## When the conversation gets long: `/compact`

```text
› /compact
Compacted: 9 → 5 messages (~704 chars of older turns replaced by a summary).
```

Compaction replaces the *older* range with a checkpoint and keeps the recent
tail verbatim — including the agent's recent tool results, so it doesn't
forget what it just did. The checkpoint speaks as the agent's own history
("this is your own earlier conversation, compacted"), and it persists: resume
after compacting and the compacted shape replays, not the original giant.

Compaction also fires automatically under context pressure, measured against
the prompt size the provider actually reports — not a character-count guess.

## Housekeeping

| | |
|---|---|
| `/rename Fix flaky auth tests` | Title the session something findable |
| `/delete 3f2a91c0` | Permanently delete a stored session |
| `deepcode session delete <id> --yes` | Same, from the shell |
| `/clear` | Keep the session, drop the conversation context |

## On disk, if you're curious

```text
~/.deepcode/sessions/<id>/session.jsonl   ← the canonical record (line-per-entry JSON)
~/.deepcode/state/deepcode.sqlite3        ← a rebuildable projection for Desktop views
<workspace>/.deepcode/tool-results/       ← oversized tool outputs, referenced from the record
```

`session.jsonl` is deliberately plain — `jq` and `tail -f` read it. Delete the
SQLite projection and it rebuilds from the JSONL; the JSONL is the truth.
