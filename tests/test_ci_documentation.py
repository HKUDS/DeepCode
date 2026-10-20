"""The docs/CI.md check table must keep pace with the workflow job inventory."""

from pathlib import Path

import yaml

# Jobs that never gate a pull request stay out of the check table.
RELEASE_ONLY_JOBS = {
    "deploy-website.yml": {"build", "deploy"},
    "pypi-publish.yml": {"release-build", "pypi-publish"},
    "release-desktop.yml": {"verify-tag", "publish"},
}

# Every other job must be recognisable under these phrases in docs/CI.md.
# When a job is added or renamed, document it in the table and map it here, or
# list it above when pull requests genuinely do not run it.
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
    ci_doc = (_root() / "docs" / "CI.md").read_text(encoding="utf-8").lower()
    for job_ids in CHECK_TABLE.values():
        for job_id, phrase in job_ids.items():
            assert phrase.lower() in ci_doc, (
                f"{job_id}: docs/CI.md no longer contains {phrase!r}"
            )
