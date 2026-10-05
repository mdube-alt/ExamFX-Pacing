"""Guards on the scheduled workflow itself."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(".github/workflows/weekly-pacing.yml")


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def steps(workflow):
    return workflow["jobs"]["pace"]["steps"]


def _named(steps, name):
    return next(step for step in steps if step.get("name") == name)


def test_piped_steps_set_pipefail(steps):
    """`cmd | tee f` reports tee's status, so a failure would look green.

    GitHub's default shell is ``bash -e`` with no pipefail; ``shell: bash``
    adds it. Any step that pipes must opt in, or the job lies about failing.
    """
    # A real pipe, not the `||` of a deliberate fallback.
    pipe = re.compile(r"(?<!\|)\|(?!\|)")
    offenders = [
        step.get("name")
        for step in steps
        if pipe.search(str(step.get("run", ""))) and step.get("shell") != "bash"
    ]
    assert not offenders, f"piped steps missing 'shell: bash': {offenders}"


def test_the_preflight_runs_before_the_write(steps):
    names = [step.get("name") for step in steps]
    assert names.index("Verify credentials") < names.index("Build the dashboard")


def test_a_missing_service_account_does_not_kill_the_job_early(steps):
    """The preflight must get to run so it can name the missing secret."""
    run = _named(steps, "Write service account credentials")["run"]
    assert "exit 0" in run
    assert "exit 1" not in run


def test_check_auth_only_skips_the_run(steps):
    assert _named(steps, "Build the dashboard")["if"] == "${{ !inputs.check_auth_only }}"


def test_the_dashboard_is_only_published_after_a_successful_run(workflow):
    """A broken run must not replace a good dashboard with a broken one."""
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "pace"
    assert "check_auth_only" in publish["if"] and "dry_run" in publish["if"]


def test_a_dry_run_builds_but_does_not_publish(steps):
    package = _named(steps, "Package the dashboard for Pages")
    assert "!inputs.dry_run" in package["if"]
    # The build step itself has no dry-run guard: building is always safe.
    assert "dry_run" not in _named(steps, "Build the dashboard")["if"]


def test_pages_permissions_are_declared(workflow):
    perms = workflow["permissions"]
    assert perms["pages"] == "write"
    assert perms["id-token"] == "write"
    assert perms["contents"] == "read", "the job never needs to push"


def test_the_schedule_no_longer_writes_the_spreadsheet(steps):
    """The sheet write moved behind --write, which the schedule stopped using."""
    run = _named(steps, "Build the dashboard")["run"]
    assert "--dashboard" in run
    assert "--write" not in run


def test_credentials_are_always_removed(steps):
    step = _named(steps, "Remove credentials")
    assert step["if"] == "always()"
