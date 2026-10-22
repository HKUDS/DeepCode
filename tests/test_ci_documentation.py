"""The docs/CI.md check table must keep pace with the workflow job inventory."""

from pathlib import Path

import yaml

# Release-only jobs stay out of the check table.
RELEASE_ONLY_JOBS = {
    "deploy-website.yml": {"build", "deploy"},
    "pypi-publish.yml": {"release-build", "pypi-publish"},
    "release-desktop.yml": {"verify-tag", "publish"},
}

# Every other job must be recognisable in the first column of the check table
# in docs/CI.md. When a job is added or renamed, document it in the table and
# map it here, or list it above when the workflow is release-only.
CHECK_TABLE = {
    "linting.yaml": {"lint-and-format": "formatting"},
    "python-ci.yml": {
        "test": "Python 3.13",
        "windows-lifecycle": "Windows lifecycle",
        "package": "Python package",
    },
    "security-ci.yml": {
        "secret-scan": "Secret scan",
        "dependency-audit": "Dependency audit",
        "dependency-review": "dependency review",
    },
    "web-ci.yml": {"browser": "Browser"},
    "desktop-ci.yml": {
        "quality": "Desktop quality",
        "bundle": "Four platform bundles",
    },
}


def _root():
    return Path(__file__).resolve().parents[1]


def _workflow_jobs():
    workflows = _root() / ".github" / "workflows"
    return {
        path.name: set(
            (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("jobs", {})
        )
        for path in sorted(workflows.iterdir())
        if path.suffix in {".yml", ".yaml"}
    }


def _check_table_first_columns():
    """First column of the "Check | What it verifies" table, one entry per row."""
    ci_doc = (_root() / "docs" / "CI.md").read_text(encoding="utf-8")
    first_columns = []
    in_table = False
    for line in ci_doc.splitlines():
        stripped = line.strip()
        if stripped.startswith("| Check |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped.startswith("|"):
            break
        first_cell = stripped.lstrip("|").split("|", 1)[0].strip()
        if first_cell and not set(first_cell) <= {"-"}:
            first_columns.append(first_cell)
    return first_columns


def test_every_workflow_job_is_classified():
    jobs = _workflow_jobs()
    assert set(jobs) == set(CHECK_TABLE) | set(RELEASE_ONLY_JOBS), (
        "classify every workflow file in CHECK_TABLE or RELEASE_ONLY_JOBS"
    )
    for filename, job_ids in jobs.items():
        documented = set(CHECK_TABLE.get(filename, ()))
        allowlisted = RELEASE_ONLY_JOBS.get(filename, set())
        assert job_ids == documented | allowlisted, (
            f"{filename}: classify each job in CHECK_TABLE or RELEASE_ONLY_JOBS"
        )


def test_check_table_phrases_still_appear_in_the_docs():
    first_columns = _check_table_first_columns()
    for job_ids in CHECK_TABLE.values():
        for job_id, phrase in job_ids.items():
            assert any(phrase.lower() in col.lower() for col in first_columns), (
                f"{job_id}: no check-table row in its first column mentions {phrase!r}"
            )
