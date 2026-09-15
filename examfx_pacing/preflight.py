"""Prove the credentials work before a scheduled run depends on them.

``python -m examfx_pacing --check-auth`` answers the two questions that
actually block an unattended run: does the Windsor key work, and can the
service account read the tracker and write to it. Each check reports the
specific fix rather than a stack trace.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .config import PacingConfig
from .sheets import SheetsError
from .spend import WindsorError, WindsorSpendSource

log = logging.getLogger(__name__)

__all__ = ["CheckResult", "check_windsor", "check_sheets", "run_preflight"]


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one preflight check."""

    name: str
    ok: bool
    detail: str
    #: What to do about it, when it failed.
    fix: str = ""

    @property
    def mark(self) -> str:
        return "PASS" if self.ok else "FAIL"


def _service_account_email(credentials_file: str | None) -> str | None:
    """The service account's address -- the one the sheet must be shared with."""
    if not credentials_file:
        return None
    try:
        with open(credentials_file, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("client_email")


def _status_of(exc) -> int | None:
    """HTTP status from a googleapiclient HttpError, if that is what this is."""
    response = getattr(exc, "resp", None)
    status = getattr(response, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _open_failure(exc) -> str:
    """A one-line reason, instead of Google's paragraph-long error body."""
    status = _status_of(exc)
    text = str(exc)
    if status == 403 and "has not been used in project" in text:
        return "the Google Sheets API is not enabled for this project"
    if status == 403:
        return "the service account was refused access (403)"
    if status == 404:
        return "the spreadsheet was not found (404)"
    return f"could not open the spreadsheet: {exc}"


def _open_fix(exc, share_fix: str) -> str:
    """The actual remedy. A disabled API is not a sharing problem."""
    status = _status_of(exc)
    text = str(exc)
    if status == 403 and "has not been used in project" in text:
        url = ""
        match = re.search(r"https://console\.developers\.google\.com/\S*?sheets\S*?(?=[\s\"'])", text)
        if match:
            url = f" Enable it here: {match.group(0)}"
        return (
            "Enable the Google Sheets API for the service account's project, then "
            "wait a minute for it to propagate." + url
        )
    if status == 404:
        # Sheets reports an unshared spreadsheet as missing, not as forbidden.
        return (
            "Either the spreadsheet ID is wrong, or it is not shared at all. "
            + share_fix
        )
    return share_fix


def check_windsor(config: PacingConfig, probe_day: date | None = None) -> list[CheckResult]:
    """One cheap request per channel, to prove the key and account IDs work."""
    if not config.windsor_api_key:
        return [
            CheckResult(
                "Windsor API key",
                False,
                "WINDSOR_API_KEY is not set",
                "Copy your key from windsor.ai (Account -> API key) and set it as "
                "the WINDSOR_API_KEY repository secret.",
            )
        ]

    # Yesterday, so a run early in the day still has settled data to find.
    probe_day = probe_day or (date.today() - timedelta(days=1))

    try:
        source = WindsorSpendSource(
            config.windsor_api_key, config.windsor_base_url, max_attempts=1
        )
    except WindsorError as exc:
        return [CheckResult("Windsor API key", False, str(exc), "Set WINDSOR_API_KEY.")]

    results = []
    for channel in config.channels:
        ok, detail = source.probe(channel, probe_day)
        results.append(
            CheckResult(
                f"Windsor / {channel.channel}",
                ok,
                detail,
                ""
                if ok
                else f"Check the key, and that account {channel.account} is "
                f"connected to the {channel.connector} connector in Windsor.",
            )
        )
    return results


def check_sheets(config: PacingConfig, sheets) -> list[CheckResult]:
    """Prove the service account can reach the tracker, and can write to it."""
    results: list[CheckResult] = []
    email = _service_account_email(config.google_credentials_file)

    share_fix = (
        f"Share the tracker with {email} as an Editor."
        if email
        else "Share the tracker with the service account's client_email as an Editor."
    )

    # Building the client is what surfaces a missing or malformed key file.
    try:
        metadata = (
            sheets.service.spreadsheets()
            .get(
                spreadsheetId=config.spreadsheet_id,
                fields="properties.title,sheets.properties.title",
            )
            .execute()
        )
        titles = {
            sheet["properties"]["title"] for sheet in metadata.get("sheets", [])
        }
        document_title = metadata.get("properties", {}).get("title")
    except SheetsError as exc:
        return [
            CheckResult(
                "Google credentials",
                False,
                str(exc),
                "Install the requirements, and point GOOGLE_APPLICATION_CREDENTIALS "
                "at the service account JSON key.",
            )
        ]
    except Exception as exc:  # the Google client raises its own error types
        return [
            CheckResult(
                "Spreadsheet access", False, _open_failure(exc), _open_fix(exc, share_fix)
            )
        ]

    results.append(
        CheckResult(
            "Spreadsheet access",
            True,
            f"opened the tracker as {email or 'the configured account'}",
        )
    )

    for tab, label in (
        (config.tracker_tab, "budgets are read from here"),
        (config.pacing_tab, "the pacing table is written here"),
    ):
        present = tab in titles
        results.append(
            CheckResult(
                f"Tab {tab!r}",
                present,
                label if present else "not found in the spreadsheet",
                ""
                if present
                else f"Rename the tab to {tab!r}, or set the matching "
                f"EXAMFX_* environment variable to its real name.",
            )
        )

    # Proving Editor access needs a request the API will actually accept: an
    # empty `requests` list is rejected with 400 regardless of permission, so
    # it can never distinguish Viewer from Editor. Setting the title to the
    # title it already has is the smallest request that needs the write scope
    # and leaves every value, tab and cell exactly as it was.
    if document_title is None:
        results.append(
            CheckResult(
                "Write access",
                False,
                "could not read the spreadsheet title, so write access was not tested",
                share_fix,
            )
        )
        return results

    try:
        sheets.service.spreadsheets().batchUpdate(
            spreadsheetId=config.spreadsheet_id,
            body={
                "requests": [
                    {
                        "updateSpreadsheetProperties": {
                            "properties": {"title": document_title},
                            "fields": "title",
                        }
                    }
                ]
            },
        ).execute()
    except Exception as exc:
        results.append(
            CheckResult(
                "Write access", False, f"write was refused: {_open_failure(exc)}", share_fix
            )
        )
    else:
        results.append(
            CheckResult("Write access", True, "the service account can edit the tracker")
        )

    return results


def run_preflight(config: PacingConfig, sheets=None) -> list[CheckResult]:
    """Every check, Windsor first since it needs no Google setup."""
    results = check_windsor(config)
    if sheets is not None:
        results.extend(check_sheets(config, sheets))
    else:
        results.append(
            CheckResult(
                "Google credentials",
                False,
                "GOOGLE_APPLICATION_CREDENTIALS is not set",
                "Point it at the service account JSON key, or paste the key into "
                "the GOOGLE_SERVICE_ACCOUNT_JSON repository secret.",
            )
        )
    return results


def render_preflight(results: list[CheckResult]) -> str:
    """A readable pass/fail list, with fixes for whatever failed."""
    width = max((len(r.name) for r in results), default=0)
    lines = ["Credential preflight", "=" * 20, ""]
    for result in results:
        lines.append(f"  [{result.mark}] {result.name:<{width}}  {result.detail}")

    failures = [r for r in results if not r.ok]
    if not failures:
        lines += ["", "All checks passed - a scheduled run has everything it needs."]
        return "\n".join(lines)

    lines += ["", f"{len(failures)} check(s) failed. To fix:", ""]
    for result in failures:
        if result.fix:
            lines.append(f"  - {result.name}: {result.fix}")
    return "\n".join(lines)
