"""The credential preflight: what it passes, fails and advises."""

from __future__ import annotations

import json
from datetime import date

import pytest

from examfx_pacing.config import ChannelSource, load_config
from examfx_pacing.preflight import (
    check_sheets,
    check_windsor,
    render_preflight,
    run_preflight,
)

CHANNELS = (
    ChannelSource("Google", "google_ads", "997-052-9086"),
    ChannelSource("LinkedIn", "linkedin", "518468129"),
)


def _config(**overrides):
    return load_config(channels=CHANNELS, **overrides)


# --- Windsor -----------------------------------------------------------------


def test_a_missing_key_is_the_only_windsor_failure_reported():
    """No point probing four connectors when there is no key to probe with."""
    results = check_windsor(_config(windsor_api_key=None))
    assert len(results) == 1
    assert not results[0].ok
    assert "WINDSOR_API_KEY" in results[0].detail
    assert "windsor.ai" in results[0].fix


def test_each_channel_is_probed(monkeypatch):
    seen = []

    def fake_probe(self, source, day):
        seen.append((source.connector, day))
        return True, "reachable, 3 row(s) for the probe day"

    monkeypatch.setattr(
        "examfx_pacing.spend.WindsorSpendSource.probe", fake_probe, raising=True
    )
    results = check_windsor(_config(windsor_api_key="k"), probe_day=date(2026, 8, 26))

    assert [c for c, _ in seen] == ["google_ads", "linkedin"]
    assert all(day == date(2026, 8, 26) for _, day in seen)
    assert all(r.ok for r in results)


def test_a_channel_with_no_spend_still_passes(monkeypatch):
    """LinkedIn reports nothing most days; that is not a credential problem."""
    monkeypatch.setattr(
        "examfx_pacing.spend.WindsorSpendSource.probe",
        lambda self, source, day: (True, "reachable, no spend reported for the probe day"),
    )
    results = check_windsor(_config(windsor_api_key="k"))
    assert all(r.ok for r in results)


def test_a_rejected_key_names_the_account_to_check(monkeypatch):
    monkeypatch.setattr(
        "examfx_pacing.spend.WindsorSpendSource.probe",
        lambda self, source, day: (False, "HTTP 401 - the API key was rejected"),
    )
    results = check_windsor(_config(windsor_api_key="bad"))

    assert not any(r.ok for r in results)
    assert "997-052-9086" in results[0].fix
    assert "google_ads" in results[0].fix


# --- Sheets ------------------------------------------------------------------


class _Resp:
    def __init__(self, status):
        self.status = status


class _HttpError(Exception):
    """Shaped like googleapiclient's HttpError: a resp with a status."""

    def __init__(self, status, message):
        super().__init__(message)
        self.resp = _Resp(status)


class _FakeSheets:
    """Enough of SheetsClient for the preflight to exercise every branch."""

    def __init__(self, titles, *, get_error=None, write_error=None, title="Tracker"):
        self._titles = titles
        self._get_error = get_error
        self._write_error = write_error
        self._title = title
        self.batch_bodies = []
        self.service = self

    def spreadsheets(self):
        return self

    def get(self, **kwargs):
        self._pending = ("get", kwargs)
        return self

    def batchUpdate(self, **kwargs):  # noqa: N802 - Google's own spelling
        self._pending = ("batchUpdate", kwargs)
        self.batch_bodies.append(kwargs["body"])
        return self

    def execute(self):
        kind, _ = self._pending
        if kind == "get":
            if self._get_error:
                raise self._get_error
            return {
                "properties": {"title": self._title},
                "sheets": [{"properties": {"title": t}} for t in self._titles],
            }
        if self._write_error:
            raise self._write_error
        # The real API rejects an empty request list with 400, whatever the
        # caller's permissions. The fake must too, or it hides that bug.
        body = self.batch_bodies[-1]
        if not body.get("requests"):
            raise _HttpError(400, "Must specify at least one request.")
        return {}


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "sa.json"
    path.write_text(json.dumps({"client_email": "pacing@proj.iam.gserviceaccount.com"}))
    return str(path)


def _by_name(results):
    return {r.name: r for r in results}


def test_a_healthy_spreadsheet_passes_every_check(key_file):
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets([config.tracker_tab, config.pacing_tab])

    results = _by_name(check_sheets(config, sheets))
    assert all(r.ok for r in results.values())
    assert "pacing@proj.iam.gserviceaccount.com" in results["Spreadsheet access"].detail


def test_the_write_check_leaves_every_value_alone(key_file):
    """The probe rewrites the title with the title it already has.

    An empty request list is rejected with 400 whatever the permissions, so
    it could never tell Viewer from Editor. This is the smallest request that
    needs the write scope and changes nothing.
    """
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets([config.tracker_tab, config.pacing_tab], title="ExamFX Tracker")

    results = _by_name(check_sheets(config, sheets))
    assert results["Write access"].ok

    assert len(sheets.batch_bodies) == 1
    requests = sheets.batch_bodies[0]["requests"]
    assert len(requests) == 1
    update = requests[0]["updateSpreadsheetProperties"]
    assert update["fields"] == "title", "only the title may be in the field mask"
    assert update["properties"] == {"title": "ExamFX Tracker"}, "same title back"


def test_an_empty_batch_would_be_rejected_by_the_api(key_file):
    """Guards the bug this replaced: the old probe always 400ed."""
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets([config.tracker_tab, config.pacing_tab])
    sheets.batch_bodies.append({"requests": []})
    sheets.batchUpdate(spreadsheetId="x", body={"requests": []})
    with pytest.raises(_HttpError):
        sheets.execute()


def test_an_unshared_sheet_advises_sharing_with_the_service_account(key_file):
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets([], get_error=PermissionError("caller lacks permission"))

    results = check_sheets(config, sheets)
    assert len(results) == 1
    assert not results[0].ok
    assert "pacing@proj.iam.gserviceaccount.com" in results[0].fix


def test_viewer_access_fails_the_write_check(key_file):
    """Read-only sharing is the failure mode that only bites on write day."""
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets(
        [config.tracker_tab, config.pacing_tab],
        write_error=PermissionError("request had insufficient authentication scopes"),
    )

    results = _by_name(check_sheets(config, sheets))
    assert results["Spreadsheet access"].ok, "reading still works"
    assert not results["Write access"].ok
    assert "as an Editor" in results["Write access"].fix


def test_a_renamed_tab_is_caught(key_file):
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets([config.tracker_tab, "Pacing v2"])

    results = _by_name(check_sheets(config, sheets))
    assert not results[f"Tab {config.pacing_tab!r}"].ok
    assert "EXAMFX_" in results[f"Tab {config.pacing_tab!r}"].fix
    assert results[f"Tab {config.tracker_tab!r}"].ok


def test_a_key_file_without_an_email_still_advises(tmp_path):
    path = tmp_path / "sa.json"
    path.write_text("{}")
    config = _config(google_credentials_file=str(path))
    sheets = _FakeSheets([], get_error=PermissionError("nope"))

    assert "client_email" in check_sheets(config, sheets)[0].fix


# --- Assembly ----------------------------------------------------------------


def test_no_google_credentials_is_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(
        "examfx_pacing.spend.WindsorSpendSource.probe",
        lambda self, source, day: (True, "reachable"),
    )
    results = run_preflight(_config(windsor_api_key="k"), sheets=None)
    names = [r.name for r in results]
    assert "Google credentials" in names
    assert not _by_name(results)["Google credentials"].ok


def test_the_rendered_report_lists_only_real_fixes(monkeypatch):
    monkeypatch.setattr(
        "examfx_pacing.spend.WindsorSpendSource.probe",
        lambda self, source, day: (True, "reachable"),
    )
    text = render_preflight(run_preflight(_config(windsor_api_key="k"), sheets=None))

    assert "[PASS] Windsor / Google" in text
    assert "1 check(s) failed" in text
    # Passing checks must not appear in the fix list.
    fixes = text.split("To fix:")[1]
    assert "Windsor / Google" not in fixes


def test_all_clear_says_so(key_file, monkeypatch):
    monkeypatch.setattr(
        "examfx_pacing.spend.WindsorSpendSource.probe",
        lambda self, source, day: (True, "reachable"),
    )
    config = _config(windsor_api_key="k", google_credentials_file=key_file)
    results = run_preflight(config, _FakeSheets([config.tracker_tab, config.pacing_tab]))

    assert all(r.ok for r in results)
    assert "All checks passed" in render_preflight(results)


# --- Diagnosing why the spreadsheet would not open -----------------------------


SERVICE_DISABLED = (
    'Google Sheets API has not been used in project 892115308099 before or it '
    'is disabled. Enable it by visiting '
    'https://console.developers.google.com/apis/api/sheets.googleapis.com/overview?project=892115308099 '
    'then retry.'
)


def test_a_disabled_api_is_not_reported_as_a_sharing_problem(key_file):
    """The first real setup returned this, and 'share as Editor' was wrong."""
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets([], get_error=_HttpError(403, SERVICE_DISABLED))

    result = check_sheets(config, sheets)[0]
    assert not result.ok
    assert result.detail == "the Google Sheets API is not enabled for this project"
    assert "Enable the Google Sheets API" in result.fix
    assert "as an Editor" not in result.fix
    assert "project=892115308099" in result.fix, "the activation link is quoted"


def test_a_plain_403_still_advises_sharing(key_file):
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets([], get_error=_HttpError(403, "The caller does not have permission"))

    result = check_sheets(config, sheets)[0]
    assert result.detail == "the service account was refused access (403)"
    assert "pacing@proj.iam.gserviceaccount.com" in result.fix


def test_a_404_covers_both_an_unshared_sheet_and_a_wrong_id(key_file):
    """Sheets reports an unshared spreadsheet as missing, not as forbidden."""
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets([], get_error=_HttpError(404, "Requested entity was not found."))

    result = check_sheets(config, sheets)[0]
    assert result.detail == "the spreadsheet was not found (404)"
    assert "spreadsheet ID is wrong" in result.fix
    assert "as an Editor" in result.fix


def test_an_unrecognised_error_is_passed_through(key_file):
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets([], get_error=RuntimeError("socket hung up"))

    result = check_sheets(config, sheets)[0]
    assert "socket hung up" in result.detail
