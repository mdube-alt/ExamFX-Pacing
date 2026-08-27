"""Read budgets/notes from and write the pacing table back to Google Sheets.

Auth uses a service account: share the tracker with the service account's
email (Editor) and point ``GOOGLE_APPLICATION_CREDENTIALS`` at its JSON key.
Google client libraries are imported lazily so the rest of the package -- and
``--dry-run`` -- work without them installed.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from .pacing import HEADERS, BudgetKey, PacingReport

log = logging.getLogger(__name__)

__all__ = ["SheetsClient", "parse_budgets", "parse_notes", "SheetsError"]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

#: Rows in the monthly tracker that are subtotals or labels, not real lines.
_NON_CHANNEL_LABELS = {"", "total", "paid search total", "paid social total"}


class SheetsError(RuntimeError):
    """Raised when the tracker does not look the way we expect."""


def _clean_number(value: Any) -> float:
    """Parse a sheet cell into a float, tolerating ``$``, commas and blanks."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text or text.startswith("#"):  # blank or a spreadsheet error like #DIV/0!
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        number = float(text)
    except ValueError:
        return 0.0
    return -number if negative else number


def _parse_month_cell(value: Any) -> date | None:
    """Recognise a month header cell such as ``2026-08-01`` or ``8/1/2026``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%b %Y", "%B %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _cell(row: list, index: int) -> Any:
    """Sheets omits trailing empty cells, so index defensively."""
    return row[index] if index < len(row) else ""


def parse_budgets(values: list[list], year: int, month: int) -> dict[BudgetKey, float]:
    """Extract ``(category, channel) -> monthly budget`` from the tracker tab.

    The tracker lays each month out as a Budget/Actual/Delta column trio under
    a date header, with categories in column B (spanning several rows) and
    channels in column C. This locates the requested month's Budget column
    rather than assuming a fixed letter, so it keeps working year to year.
    """
    month_row_index = budget_column = None

    for index, row in enumerate(values[:40]):
        next_row = values[index + 1] if index + 1 < len(values) else []
        if "Budget" not in [str(c).strip() for c in next_row]:
            continue
        for column, cell in enumerate(row):
            parsed = _parse_month_cell(cell)
            if parsed and parsed.year == year and parsed.month == month:
                # The month header sits directly above its Budget column.
                if str(_cell(next_row, column)).strip() == "Budget":
                    month_row_index, budget_column = index, column
                    break
        if budget_column is not None:
            break

    if budget_column is None:
        raise SheetsError(
            f"could not find a Budget column for {year}-{month:02d} in the tracker tab. "
            f"Check that the month header row still sits directly above the "
            f"Budget/Actual/Delta row."
        )

    budgets: dict[BudgetKey, float] = {}
    category = ""
    for row in values[month_row_index + 2 :]:
        label = str(_cell(row, 1)).strip()
        channel = str(_cell(row, 2)).strip()

        if label.lower() == "total":
            break
        if label:
            category = label
        if not category or channel.lower() in _NON_CHANNEL_LABELS:
            continue

        budgets[(category, channel)] = _clean_number(_cell(row, budget_column))

    if not budgets:
        raise SheetsError(f"found the {year}-{month:02d} Budget column but no category rows")
    return budgets


def parse_notes(values: list[list]) -> dict[tuple[int, str, str], str]:
    """Recover analyst notes from an existing pacing tab, keyed by week/line."""
    notes: dict[tuple[int, str, str], str] = {}
    if not values:
        return notes

    header = [str(c).strip() for c in values[0]]
    try:
        note_column = header.index("Notes")
    except ValueError:
        return notes

    for row in values[1:]:
        note = str(_cell(row, note_column)).strip()
        if not note:
            continue
        match = re.search(r"(\d+)", str(_cell(row, 0)))
        if not match:
            continue
        key = (int(match.group(1)), str(_cell(row, 2)).strip(), str(_cell(row, 3)).strip())
        notes[key] = note
    return notes


class SheetsClient:
    """Minimal Google Sheets reader/writer for the tracker."""

    def __init__(self, spreadsheet_id: str, credentials_file: str | None = None):
        self.spreadsheet_id = spreadsheet_id
        self.credentials_file = credentials_file
        self._service = None

    @property
    def service(self):
        if self._service is None:
            try:
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise SheetsError(
                    "Google Sheets access needs 'google-api-python-client' and "
                    "'google-auth'. Install them, or run with --dry-run."
                ) from exc

            if self.credentials_file:
                credentials = service_account.Credentials.from_service_account_file(
                    self.credentials_file, scopes=SCOPES
                )
            else:  # Application Default Credentials
                import google.auth

                credentials, _ = google.auth.default(scopes=SCOPES)
            self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return self._service

    def read(self, tab: str) -> list[list]:
        """Read a whole tab as raw values."""
        response = (
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=tab,
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING",
            )
            .execute()
        )
        return response.get("values", [])

    def write_pacing(self, tab: str, report: PacingReport) -> int:
        """Replace the pacing tab with the report. Returns rows written."""
        body_rows = [row.as_sheet_row() for row in report.rows]
        payload = [HEADERS] + body_rows

        # Clear first so a shorter month cannot leave stale rows behind.
        self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id, range=tab, body={}
        ).execute()

        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{tab}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": payload},
        ).execute()

        log.info("wrote %d pacing rows to %r", len(body_rows), tab)
        return len(body_rows)
