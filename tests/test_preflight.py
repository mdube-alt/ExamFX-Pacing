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


class _FakeSheets:
    """Enough of SheetsClient for the preflight to exercise every branch."""

    def __init__(self, titles, *, get_error=None, write_error=None):
        self._titles = titles
        self._get_error = get_error
        self._write_error = write_error
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
                "sheets": [{"properties": {"title": t}} for t in self._titles]
            }
        if self._write_error:
            raise self._write_error
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


def test_the_write_check_changes_nothing(key_file):
    """Write access is proven with an empty batch, not by touching a cell."""
    config = _config(google_credentials_file=key_file)
    sheets = _FakeSheets([config.tracker_tab, config.pacing_tab])

    check_sheets(config, sheets)
    assert sheets.batch_bodies == [{"requests": []}]


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
