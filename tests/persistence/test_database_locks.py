"""WAL-mode lock hygiene for core.persistence.database.Database.

POSIX fcntl locks are per process: closing *any* descriptor on the database
file drops every lock the process holds on it. A WAL-mode connection keeps a
SHARED lock for its lifetime; losing it lets another process checkpoint and
unlink ``-wal``/``-shm`` under this process's open connections.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from core.persistence.database import Database
from core.private_storage import UnsafePrivateFileError

POSIX = os.name != "nt"

_GETLK = """
import fcntl, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDWR)
# SQLite SHARED range: PENDING_BYTE(0x40000000)+2, 510 bytes.
lk = struct.pack('qqihh', 0x40000002, 510, 0, fcntl.F_WRLCK, 0)
typ = struct.unpack('qqihh', fcntl.fcntl(fd, fcntl.F_GETLK, lk))[3]
print('HELD' if typ != fcntl.F_UNLCK else 'NONE')
"""

_WRITE_AND_CLOSE = """
import sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute('PRAGMA busy_timeout = 10000')
c.execute('BEGIN IMMEDIATE'); c.execute("INSERT INTO probe(x) VALUES (42)"); c.execute('COMMIT')
c.close()
"""


def _shared_lock_seen_by_other_process(path: Path) -> str:
    if sys.platform == "linux":
        code = (
            _GETLK.replace("'qqihh'", "'hhqqi'")
            .replace(
                "struct.pack('hhqqi', 0x40000002, 510, 0, fcntl.F_WRLCK, 0)",
                "struct.pack('hhqqi', fcntl.F_WRLCK, 0, 0x40000002, 510, 0)",
            )
            .replace("[3]", "[0]")
        )
    else:
        code = _GETLK
    out = subprocess.run(
        [sys.executable, "-c", code, str(path)], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "state" / "deepcode.sqlite3")
    db.initialize()
    with db.transaction() as connection:
        connection.execute("CREATE TABLE probe (x INTEGER)")
    yield db
    db.close()


@pytest.mark.skipif(not POSIX, reason="fcntl lock semantics are POSIX")
def test_connect_never_drops_the_process_lock_on_the_database_file(
    database: Database,
) -> None:
    with database.read() as outer:
        outer.execute("SELECT count(*) FROM probe").fetchone()
        assert _shared_lock_seen_by_other_process(database.path) == "HELD"
        # A second connect()/close() in this process (other thread, nested
        # repository call, ...) must not take the kernel lock away.
        with database.read() as inner:
            inner.execute("SELECT count(*) FROM probe").fetchone()
        assert _shared_lock_seen_by_other_process(database.path) == "HELD"


@pytest.mark.skipif(not POSIX, reason="/dev/fd and unlink semantics are POSIX")
def test_other_process_close_cannot_unlink_wal_under_an_open_connection(
    database: Database,
) -> None:
    wal = Path(f"{database.path}-wal")
    with database.read() as outer:
        outer.execute("SELECT count(*) FROM probe").fetchone()
        with database.read() as inner:
            inner.execute("SELECT count(*) FROM probe").fetchone()
        wal_inode_before = wal.stat().st_ino
        subprocess.run(
            [sys.executable, "-c", _WRITE_AND_CLOSE, str(database.path)],
            check=True,
        )
        assert wal.exists() and wal.stat().st_ino == wal_inode_before
        assert outer.execute("SELECT x FROM probe").fetchall()[0][0] == 42
    with database.read() as fresh:
        assert fresh.execute("SELECT x FROM probe").fetchall()[0][0] == 42


@pytest.mark.skipif(not POSIX, reason="the anchor connection is POSIX-only")
def test_anchor_keeps_wal_files_alive_between_operations(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("INSERT INTO probe(x) VALUES (1)")
    assert Path(f"{database.path}-wal").exists()
    assert Path(f"{database.path}-shm").exists()


def test_close_is_idempotent_and_reads_still_work(database: Database) -> None:
    database.close()
    database.close()
    with database.read() as connection:
        assert connection.execute("SELECT count(*) FROM probe").fetchone()[0] == 0


def test_every_operation_still_gets_its_own_connection(database: Database) -> None:
    with database.read() as first:
        with database.read() as second:
            assert first is not second


@pytest.mark.skipif(not POSIX, reason="symlink semantics")
def test_database_path_replaced_by_a_symlink_is_refused(database: Database) -> None:
    # Database.__init__ resolves the path, so a link must appear afterwards.
    real = database.path.with_name("elsewhere.sqlite3")
    database.path.rename(real)
    database.path.symlink_to(real)
    with pytest.raises(UnsafePrivateFileError):
        with database.read():
            pass


def test_database_file_and_wal_are_user_private(database: Database) -> None:
    if not POSIX:
        pytest.skip("POSIX mode bits")
    with database.transaction() as connection:
        connection.execute("INSERT INTO probe(x) VALUES (1)")
    for suffix in ("", "-wal", "-shm"):
        assert stat.S_IMODE(os.stat(f"{database.path}{suffix}").st_mode) == 0o600


@pytest.mark.skipif(POSIX, reason="Windows must not hold an anchor handle")
def test_no_anchor_on_windows(database: Database) -> None:
    with database.read() as connection:
        connection.execute("SELECT count(*) FROM probe").fetchone()
    assert database._anchor is None
