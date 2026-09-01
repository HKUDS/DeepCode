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
