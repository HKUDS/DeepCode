# Concurrent turn submit crashes on a dangling worker reference

Status: **diagnosed, not fixed.** Pre-existing — reproduced identically on
`e01fa5c`, before any of the runtime work. Reproduction:
`docs/investigations/concurrent_turn_submit_repro.py`.

## Symptom

Two DeepCode processes driving the same Session: roughly **half of runs kill
one of them**.

```
core/application/turn_service.py:639 in _submit → turns.add(turn)
core/persistence/execution_repository.py:122 in add → INSERT INTO turns …
sqlite3.IntegrityError: FOREIGN KEY constraint failed
```

A second flavour surfaces in the coordinator's heartbeat query as
`sqlite3.OperationalError: disk I/O error`.

## What the failure actually is

`turns` carries three foreign keys:

```
execution_owner_id → runtime_workers(id)   ON DELETE RESTRICT
home_worker_id     → runtime_workers(id)   ON DELETE RESTRICT
thread_id          → threads(id)           ON DELETE CASCADE
```

Instrumenting `TurnRepository.add` to report the database's contents at the
moment of failure:

```
PROBE home_worker= worker_a60f4d6d98504703af10ea6b915b35d6   owner= None
PROBE thread_rows  = 1     ← the thread exists; not the failing key
PROBE same_ordinal = 0     ← not a UNIQUE(thread_id, ordinal) collision
PROBE worker_rows  = 0     ← the referenced worker row does not exist
PROBE fresh_connection_sees_worker = 0 of 5
PROBE existing_worker ('worker_8476…', 10830, 'cli-trust', stopped)
PROBE existing_worker ('worker_42b2…', 10830, 'cli',       stopped)
PROBE existing_worker ('worker_6847…', 10846, 'cli-trust', stopped)
PROBE existing_worker ('worker_5300…', 10846, 'cli',       running)
PROBE my_pid 10845
```

So: **`home_worker_id` points at a worker row that was never written.** A
second, independent connection agrees it is absent, so this is not snapshot
visibility. The failing process (pid 10845) has *no* worker rows at all,
while the other concurrent process (10846) has both of its.

## What was ruled out

- **Nothing deletes `runtime_workers`.** Workers are only marked `stopped_at`;
  there is no `DELETE FROM runtime_workers` anywhere in the tree, and
  `recover_dead_worker` releases claims without removing the row.
- **Not the stale-projection drop.** `_drop_stale_projection` logs a warning
  when it removes a thread; that warning never appears, and `thread_rows = 1`.
- **Not a racing migration.** `Database.initialize` holds an exclusive file
  lock, and the migration backup only copies out.
- **Not snapshot isolation.** `Database.transaction()` opens a fresh
  connection with `BEGIN IMMEDIATE`, and a fresh reader also fails to see the
  row.
- **Not an unregistered coordinator handing out an id.**
  `ExecutionCoordinator.start()` commits `register_worker` before assigning
  `self._worker`, and `_worker` is written in exactly two places — there and
  `close()`.

Which leaves: the registration transaction ran and its write did not land in
the file the rest of the process reads. That is inside execution
coordination — locks, worker liveness, turn ownership — the subsystem the
runtime plan fences off, and the reason this is written up rather than
patched.

## What is NOT affected

The canonical session record. Across every reproduction the JSONL stayed
intact, parsed cleanly, and rebuilt into a legal message sequence — including
the runs where a process died mid-turn. On `e01fa5c` the same runs left a
4-message record; with the current record it is 6–8 messages, complete.

## Suggested next step

Instrument `ExecutionCoordinator.start()` to read its own row back on the
same `Database` immediately after `register_worker` commits, and fail loudly
there. That converts a confusing mid-session crash into a startup error that
names the real problem, and it will show whether the write is lost or the
process is reading a different file than it wrote.

Two mitigations were considered and rejected for now: submitting with
`home_worker_id=None` when the row is missing (silently loses the
attribution `interrupt_unclaimed_queued_for_worker` needs to recover queued
work after a restart), and retrying the insert (the row never appears, so the
retry only delays the same failure).
