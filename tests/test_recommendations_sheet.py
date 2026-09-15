"""Writing budget advice back to its own tab."""

from __future__ import annotations

from datetime import date

import pytest

from examfx_pacing.recommendations import (
    REC_HEADERS,
    Action,
    CampaignDriver,
    Recommendation,
)
from examfx_pacing.sheets import SheetsClient


class _Values:
    def __init__(self, log):
        self._log = log

    def clear(self, **kwargs):
        self._log.append(("clear", kwargs))
        return self

    def update(self, **kwargs):
        self._log.append(("update", kwargs))
        return self

    def execute(self):
        return {}


class _Spreadsheets:
    def __init__(self, log, titles):
        self._log = log
        self._titles = titles
        self._values = _Values(log)

    def values(self):
        return self._values

    def get(self, **kwargs):
        self._log.append(("get", kwargs))
        return self

    def batchUpdate(self, **kwargs):  # noqa: N802 - Google's own spelling
        self._log.append(("batchUpdate", kwargs))
        title = kwargs["body"]["requests"][0]["addSheet"]["properties"]["title"]
        self._titles.append(title)
        return self

    def execute(self):
        # Only the get() call reads a body; the rest ignore the return value.
        return {"sheets": [{"properties": {"title": t}} for t in self._titles]}


class _Service:
    def __init__(self, titles):
        self.log = []
        self._spreadsheets = _Spreadsheets(self.log, list(titles))

    def spreadsheets(self):
        return self._spreadsheets


def _client(titles=("WoW Pacing",)):
    client = SheetsClient("sheet-id")
    client._service = _Service(titles)
    return client


def _rec(**overrides):
    defaults = dict(
        category="Securities",
        channel="Bing",
        action=Action.INCREASE,
        monthly_budget=800.0,
        spend_to_date=461.91,
        days_elapsed=27,
        days_remaining=4,
        current_daily=17.11,
        projected_month_end=530.41,
        suggested_daily=84.52,
        urgent=True,
        drivers=[
            CampaignDriver("B2C - Securities - Non-Brand - PPC", 430.94, 0.9329, 1.4),
            CampaignDriver("B2C - Securities - Brand - PPC", 30.97, 0.0671, 0.5),
        ],
    )
    defaults.update(overrides)
    return Recommendation(**defaults)


def test_the_tab_is_created_when_missing():
    client = _client(titles=("WoW Pacing",))
    client.write_recommendations("Budget Recommendations", [_rec()], date(2026, 8, 27))

    calls = [name for name, _ in client._service.log]
    assert "batchUpdate" in calls, "a missing tab should be created"
    added = [
        kwargs["body"]["requests"][0]["addSheet"]["properties"]["title"]
        for name, kwargs in client._service.log
        if name == "batchUpdate"
    ]
    assert added == ["Budget Recommendations"]


def test_an_existing_tab_is_not_recreated():
    client = _client(titles=("WoW Pacing", "Budget Recommendations"))
    client.write_recommendations("Budget Recommendations", [_rec()], date(2026, 8, 27))

    calls = [name for name, _ in client._service.log]
    assert "batchUpdate" not in calls


def test_the_tab_is_cleared_before_writing():
    """A quieter week must not leave last week's advice sitting below."""
    client = _client(titles=("WoW Pacing", "Budget Recommendations"))
    client.write_recommendations("Budget Recommendations", [_rec()], date(2026, 8, 27))

    calls = [name for name, _ in client._service.log]
    assert calls.index("clear") < calls.index("update")


def test_the_written_row_matches_the_headers():
    client = _client(titles=("WoW Pacing", "Budget Recommendations"))
    rows = client.write_recommendations(
        "Budget Recommendations", [_rec()], date(2026, 8, 27)
    )
    assert rows == 1

    payload = next(
        kwargs["body"]["values"]
        for name, kwargs in client._service.log
        if name == "update"
    )
    assert payload[0] == REC_HEADERS
    assert len(payload[1]) == len(REC_HEADERS)

    row = dict(zip(REC_HEADERS, payload[1]))
    assert row["As Of"] == "2026-08-27"
    assert row["Category"] == "Securities"
    assert row["Channel"] == "Bing"
    assert row["Action"] == Action.INCREASE
    assert row["Priority"] == "Urgent"
    assert row["Monthly Budget"] == 800.0
    assert row["Spend to Date"] == 461.91
    assert row["Remaining"] == pytest.approx(338.09)
    assert row["Days Left"] == 4
    assert row["Suggested Daily"] == 84.52
    # Percentages go out as fractions so the sheet can format them as percents.
    assert row["Daily Change"] == pytest.approx((84.52 - 17.11) / 17.11, rel=1e-3)
    assert row["Projected Variance %"] == pytest.approx((530.41 - 800) / 800, rel=1e-3)


def test_campaign_drivers_land_in_one_cell_with_their_trend():
    client = _client(titles=("WoW Pacing", "Budget Recommendations"))
    client.write_recommendations("Budget Recommendations", [_rec()], date(2026, 8, 27))

    payload = next(
        kwargs["body"]["values"]
        for name, kwargs in client._service.log
        if name == "update"
    )
    cell = dict(zip(REC_HEADERS, payload[1]))["Top Campaigns"]

    assert cell.count("\n") == 1, "one campaign per line"
    assert "B2C - Securities - Non-Brand - PPC: $430.94 (93% of line" in cell
    assert "accelerating 1.40x" in cell
    assert "slowing 0.50x" in cell


def test_unbudgeted_lines_get_no_target_daily():
    """There is no daily number that fixes a line with no budget."""
    client = _client(titles=("WoW Pacing", "Budget Recommendations"))
    rec = _rec(
        action=Action.ALLOCATE_OR_PAUSE,
        monthly_budget=0.0,
        spend_to_date=148.32,
        suggested_daily=0.0,
    )
    client.write_recommendations("Budget Recommendations", [rec], date(2026, 8, 27))

    payload = next(
        kwargs["body"]["values"]
        for name, kwargs in client._service.log
        if name == "update"
    )
    row = dict(zip(REC_HEADERS, payload[1]))
    assert row["Suggested Daily"] == ""
    assert row["Daily Change"] == ""
    assert row["Projected Variance %"] == ""
    assert "allocate budget or pause" in row["Recommendation"]


def test_engine_order_is_preserved():
    """The engine sorts by money at stake; the sheet must not reshuffle it."""
    client = _client(titles=("WoW Pacing", "Budget Recommendations"))
    first = _rec(category="Insurance", channel="Google")
    second = _rec(category="Securities", channel="Bing")
    client.write_recommendations(
        "Budget Recommendations", [first, second], date(2026, 8, 27)
    )

    payload = next(
        kwargs["body"]["values"]
        for name, kwargs in client._service.log
        if name == "update"
    )
    assert [row[1] for row in payload[1:]] == ["Insurance", "Securities"]


def test_no_recommendations_still_leaves_a_header():
    client = _client(titles=("WoW Pacing", "Budget Recommendations"))
    rows = client.write_recommendations("Budget Recommendations", [], date(2026, 8, 27))
    assert rows == 0

    payload = next(
        kwargs["body"]["values"]
        for name, kwargs in client._service.log
        if name == "update"
    )
    assert payload == [REC_HEADERS]


class _RecordingSheets:
    """Stands in for SheetsClient on the write path."""

    def __init__(self):
        self.pacing_calls = []
        self.recommendation_calls = []

    def write_pacing(self, tab, report):
        self.pacing_calls.append(tab)
        return len(report.rows)

    def write_recommendations(self, tab, recommendations, as_of):
        self.recommendation_calls.append((tab, len(recommendations), as_of))
        return len(recommendations)


def _run_with(sheets, **kwargs):
    from examfx_pacing.config import load_config
    from examfx_pacing.run import run_pacing
    from examfx_pacing.spend import CsvSpendSource

    return run_pacing(
        year=2026,
        month=8,
        as_of=date(2026, 8, 23),
        spend_source=CsvSpendSource("tests/fixtures/2026-08_week4_mtd.csv"),
        config=load_config(),
        sheets=sheets,
        budget_override="tests/fixtures/budgets_august_2026.json",
        write=True,
        **kwargs,
    )


def test_run_pacing_writes_both_tabs():
    sheets = _RecordingSheets()
    result = _run_with(sheets)

    assert sheets.pacing_calls == ["WoW Pacing"]
    assert len(sheets.recommendation_calls) == 1
    tab, count, as_of = sheets.recommendation_calls[0]
    assert tab == "Budget Recommendations"
    assert as_of == date(2026, 8, 23)
    assert count == len(result.recommendations)
    assert result.recommendation_rows_written == count


def test_recommendations_write_can_be_turned_off():
    sheets = _RecordingSheets()
    result = _run_with(sheets, write_recommendations=False)

    assert sheets.pacing_calls == ["WoW Pacing"], "the pacing tab is still written"
    assert sheets.recommendation_calls == []
    assert result.recommendation_rows_written == 0
