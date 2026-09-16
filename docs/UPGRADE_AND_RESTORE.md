# Controlled upgrades and state recovery

Desktop, Web, TUI and headless task entrypoints use the shared background
service by default. An incompatible native client does not kill an existing
service or silently open another application/database. Complete the platform
and stability checks before publishing an installation.

## Prepare an upgrade

```sh
deepcode service prepare-upgrade --output /safe/path/deepcode-before-upgrade
# Update the Python installation or Desktop through its normal installer.
# If a login item was installed, refresh its executable/environment definition:
deepcode service install --at-login
deepcode service start
```

`prepare-upgrade` drains and stops the selected service, then creates an offline
snapshot. `--database` chooses a non-default database. `--timeout` bounds drain;
`--cancel-running` is an explicit alternative. A failed drain preserves service
admission. A failure to snapshot after stopping leaves the service stopped and
reports the failure. It does not claim the upgrade is prepared.

`snapshot --output PATH` creates the same snapshot when hosts are already
stopped. The output path must be new and outside the Session/revision directories.
The service records its actual state layout, so a custom Session directory is
not guessed from a different CLI environment after shutdown. For older services
without layout metadata, use the original `DEEPCODE_HOME` and, if needed,
`--sessions PATH`.

Snapshots contain the SQLite database, canonical Sessions, home configuration,
Provider credentials, MCP credentials and private Provider revisions. Locks
exclude live database owners, active Session resources and concurrent canonical/
configuration/credential mutations. Pending Turns must be settled. SQLite's
backup API, per-file checksums and an atomic manifest protect the snapshot.
Symlinks are rejected. Snapshots retain private permissions and contain secrets;
they are local recovery artifacts, not diagnostic exports.

Session indexes and lock files are disposable or retain their existing inodes.
Project working trees, installed Plugins/tools, environment-variable values,
application installers and old executables are not copied into this snapshot.
Use the project's normal version control/backup for source-code recovery.

Frozen Desktop backends pin a complete bundle in a private versioned directory.
Client updater replacement does not delete that service bundle. Install the new
service definition from the updated executable (its `--service install
--at-login` entry is equivalent to the CLI command). An older bundle remains
available for controlled rollback; runtimes are not automatically deleted.

## Roll back

Prefer reverting the client installation when data remains compatible.
An older runtime reads the database schema first and refuses a newer schema
before changing its journal mode or starting migration. No schema downgrade is
assumed.

To restore an entire pre-upgrade state, stop every writer and explicitly run:

```sh
deepcode service restore --snapshot /safe/path/deepcode-before-upgrade --replace-data
```

This replaces runtime data written after the snapshot. It does not reverse
shell commands or file edits in project directories. All paths must match the
snapshot's original state locations. Checksums and quiescence are checked before
replacement; the current state is preserved in a separate `before-restore-*`
snapshot first.

An interrupted replacement leaves a durable journal. Application startup refuses
to use mixed data until the same restore command is resumed. Repeating that
command is idempotent. Do not remove the journal manually. Restored pending Provider login
flows are invalidated. On first startup, restored Goals and interval Automations
are paused before scheduling/recovery; review them and resume explicitly.
Historical events and canonical records are retained.

A before-restore forensic copy may include interrupted work from a failed newer
runtime. The automatic restore path rejects snapshots containing unsettled
Turns; inspect/recover that state before using it as a restore source.

## Stability acceptance

`scripts/foundation_soak.py` runs a controlled, no-key Agent fixture through real
service processes, HTTP/WebSocket authentication, cross-client approval, repeated
input IDs, incremental replay, PTYs and ordinary client disconnections. It
measures actual elapsed time and writes live status/samples under a new private
root. Run it from an isolated, fixed source checkout:

```sh
python scripts/foundation_soak.py --root /safe/path/deepcode-soak --seconds 86400
```

The default cadence is 20 seconds, with ten observer connections per cycle. It
checks a single tool effect per task, settled queues, zero peer/RPC residue at
shutdown, p95 attach ≤2 seconds, replay ≤3 seconds and local startup ≤10 seconds.
It records worker RSS, file descriptors and threads. Growth of more than 64 MiB
RSS or 16 descriptors/threads over the warm baseline fails the run for
investigation; raw trends remain available for review. These budgets are part
of this test, not claims about every machine or workload. Long runs also reject
a scheduling/sleep gap over 60 seconds (or three intervals), and sustained
growth over 16 MiB across four increasing RSS quarter medians.

A short `--seconds` run validates the harness only. It is never evidence for a
24-hour pass. This fixture does not replace real-model, native installer,
operating-system login, or Windows acceptance. Linux container tests require a
proper init process to reap child processes (`docker --init`).
