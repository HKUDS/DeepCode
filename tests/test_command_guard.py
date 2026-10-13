"""Tests for the destructive-command screen (core.harness.command_guard).

The screen is cheap defense-in-depth in front of the real sandbox boundary
(see tests/test_harness_sandbox.py). These tests pin down two things:

* it catches the destructive commands the old substring blocklist *claimed* to
  catch but didn't (whitespace / split flags / flag spelling / numeric forms);
* it does not fire on benign commands that merely *contain* a scary substring.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.command_guard import screen_command


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /tmp/x",
        "rm  -rf /tmp/x",  # extra whitespace — old substring bypass
        "RM -rf /tmp/x",  # uppercase command — old screen was case-insensitive
        "rm -r -f /tmp/x",  # split flags — old substring bypass
        "rm -fr /tmp/x",  # reversed flag order
        "rm -Rf /tmp/x",  # capital R
        "rm --recursive --force /tmp/x",  # long flags — old substring bypass
        "chmod 777 file",
        "chmod 0777 file",  # leading zero — old substring bypass
        "chmod a+rwx file",  # symbolic form — old substring bypass
        "sudo rm file",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs /dev/sda1",
        "mkfs.ext4 /dev/sda1",  # mkfs variant — old substring bypass
        "reboot",
        "shutdown -h now",
        "echo hi && rm -rf /",  # destructive stage inside a sequence
        "ls | rm -rf /",  # destructive stage inside a pipeline
        "cd /tmp; rm -rf build; ls",  # destructive stage in the middle
    ],
)
def test_blocks_destructive_commands(command):
    assert screen_command(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "rm file.txt",  # plain remove is allowed
        "rm -r builddir",  # recursive but not forced
        "rm -f stale.lock",  # forced but not recursive
        "chmod 755 script.sh",
        "chmod +x script.sh",
        "touch rm-rf-notes.txt",  # scary substring, harmless command
        'echo "run without sudo"',  # 'sudo' only inside a string literal
        "dd if=input.bin count=1",  # dd reading only, no of=
        "python train.py",
        'git commit -m "drop the -rf flag from docs"',
        "ls && echo done",  # benign sequence
        "make && make install",  # benign sequence
        "echo a | grep b | wc -l",  # benign pipeline
    ],
)
def test_allows_benign_commands(command):
    assert screen_command(command) is None


def test_empty_command_is_allowed():
    assert screen_command("") is None
    assert screen_command("   ") is None


def test_unparseable_command_defers_to_sandbox():
    # Unbalanced quotes can't be tokenised; the screen must not raise and must
    # pass the command through (None) so the sandbox stays the boundary.
    assert screen_command('echo "unterminated') is None


@pytest.mark.parametrize(
    "command",
    [
        "/bin/rm -rf /",
        "/usr/bin/rm -rf /tmp/x",
        "/bin/rm -r -f /tmp/x",  # split flags behind a path
        "./rm --recursive --force /tmp/x",
        "/usr/bin/sudo reboot",
        "/bin/dd if=/dev/zero of=/dev/sda",
        "/sbin/mkfs.ext4 /dev/sda1",
        "/bin/chmod 777 /etc",
        "/usr/bin/chmod a+rwx /etc",
        "C:/Windows/System32/shutdown.exe /s",  # Windows extension stripping
        "/usr/bin/rm.exe -rf C:/temp",
        "echo hi && /bin/rm -rf /",  # path-qualified stage in a sequence
        "ls | /usr/bin/rm -rf /",  # path-qualified stage in a pipeline
    ],
)
def test_blocks_path_qualified_destructive_commands(command):
    """An absolute or relative path to the program must not defeat the screen.

    ``screen_egress`` / ``screen_install`` resolve the program through
    ``_program()``, which drops the directory and any Windows extension before
    matching. ``screen_command`` compared ``tokens[0].lower()`` directly, so the
    very same command was classified when typed bare and passed when typed with
    its path — ``/bin/rm -rf /`` slipped past the check that ``rm -rf /``
    trips.
    """

    assert screen_command(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "/bin/ls -la",  # path-qualified but benign
        "/usr/bin/rm notes.txt",  # plain remove stays allowed
        "/usr/local/bin/python train.py",
        "/bin/echo done && /bin/ls -la",  # benign path-qualified sequence
    ],
)
def test_allows_path_qualified_benign_commands(command):
    """Normalising the program must not turn every absolute path into a hit."""

    assert screen_command(command) is None


@pytest.mark.parametrize(
    "command",
    [
        "bin/shutdown.bat",  # Tomcat's own stop script
        "./reboot.cmd",
        "scripts/shutdown.ps1 -Force",
        "cd server && bin/shutdown.bat",
    ],
)
def test_allows_project_scripts_named_like_system_programs(command):
    """A Windows script is the project's own, never the system program: the
    system ``shutdown`` is ``shutdown.exe``."""

    assert screen_command(command) is None


@pytest.mark.parametrize(
    "command",
    [
        "env FOO=1 rm -rf /",
        "env -i PATH=/bin rm -rf /",
        "env -u HOME -- rm -rf /",
        "/usr/bin/env rm -rf /",
        "FOO=1 rm -rf /",
        "nohup rm -rf / &",
        "timeout 5 rm -rf /",
        "timeout -s KILL 5 rm -rf /",
        "timeout --signal=KILL -k 1 5 rm -rf /",
        "nice rm -rf /",
        "nice -n 10 rm -rf /",
        "nice -10 rm -rf /",
        "command rm -rf /",
        "exec rm -rf /",
        "exec -a name rm -rf /",
        "nohup nice -n 5 timeout 60 rm -rf /",  # stacked wrappers
        "env FOO=1 shutdown -h now",
        "nohup sudo reboot",
        "ls && env CI=1 chmod 777 /etc",
    ],
)
def test_blocks_destructive_commands_behind_wrappers(command):
    assert screen_command(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "env",
        "env FOO=1 python train.py",
        "timeout 5 pytest -q",
        "nohup python server.py &",
        "nice -n 10 make -j8",
        "command -v rm",
        "FOO=1 BAR=2 make",
        "timeout 5",
    ],
)
def test_allows_benign_commands_behind_wrappers(command):
    assert screen_command(command) is None
