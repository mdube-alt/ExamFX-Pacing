"""Guards on the scheduled workflow itself."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(".github/workflows/weekly-pacing.yml")


@pytest.fixture(scope="module")
def steps():
    parsed = yaml.safe_load(WORKFLOW.read_text())
    return parsed["jobs"]["pace"]["steps"]


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
    assert names.index("Verify credentials") < names.index("Update the pacing tab")


def test_a_missing_service_account_does_not_kill_the_job_early(steps):
    """The preflight must get to run so it can name the missing secret."""
    run = _named(steps, "Write service account credentials")["run"]
    assert "exit 0" in run
    assert "exit 1" not in run


def test_check_auth_only_skips_the_write(steps):
    assert _named(steps, "Update the pacing tab")["if"] == "${{ !inputs.check_auth_only }}"


def test_credentials_are_always_removed(steps):
    step = _named(steps, "Remove credentials")
    assert step["if"] == "always()"
