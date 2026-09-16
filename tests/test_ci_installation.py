"""A failed Windows dependency install cannot be hidden by a later command."""

from pathlib import Path
import subprocess

import yaml


def test_windows_installation_stops_on_the_first_failed_command(tmp_path):
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/python-ci.yml").read_text())
    steps = workflow["jobs"]["windows-lifecycle"]["steps"]
    step = next(s for s in steps if s["name"] == "Install package and test tools")
    assert step["shell"] == "bash"
    assert "python -m pip check" in step["run"]
    # Use the same fail-fast shell options as GitHub's built-in bash runner.
    # A successful second invocation would leave a marker and hide the failure.
    script = """
python() {
    if [[ "$*" == *requirements.lock* ]]; then
        return 23
    fi
    touch continued-after-failure
}
""" + step["run"]
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=tmp_path,
        capture_output=True,
    )
    assert result.returncode == 23
    assert not (tmp_path / "continued-after-failure").exists()
