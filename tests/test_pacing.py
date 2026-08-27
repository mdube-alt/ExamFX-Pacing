"""Report assembly rules that are independent of any particular month."""

from __future__ import annotations

from datetime import date

import pytest

from examfx_pacing.categories import CategoryMapper
from examfx_pacing.config import PacingConfig
from examfx_pacing.pacing import (
    HEADERS,
    aggregate_spend,
    build_report,
    cumulative_spend_by_week,
)
from examfx_pacing.spend import CampaignSpend
from examfx_pacing.weeks import build_weeks

MAPPER = CategoryMapper()


def test_spend_rolls_up_to_category_and_channel():
    rows = [
        CampaignSpend("Google", "B2C - Insurance - Brand - PPC", 10.0),
        CampaignSpend("Google", "B2C - Insurance - Non Brand - PPC", 5.5),
        CampaignSpend("Bing", "B2C - Insurance - Brand - PPC", 2.25),
    ]
    totals, unmapped = aggregate_spend(rows, MAPPER)
    assert totals == {("Insurance", "Google"): 15.5, ("Insurance", "Bing"): 2.25}
    assert unmapped == {}


def test_unmapped_campaigns_are_reported_not_dropped():
    rows = [CampaignSpend("Google", "Mystery Campaign", 99.0)]
    totals, unmapped = aggregate_spend(rows, MAPPER)
    assert totals == {}
    assert unmapped == {("Google", "Mystery Campaign"): 99.0}


def test_cumulative_totals_grow_week_by_week():
    daily = [
        CampaignSpend("Bing", "B2C - Insurance - Brand - PPC", 10.0, date(2026, 8, 1)),
        CampaignSpend("Bing", "B2C - Insurance - Brand - PPC", 5.0, date(2026, 8, 5)),
        CampaignSpend("Bing", "B2C - Securities - Brand - PPC", 3.0, date(2026, 8, 12)),
    ]
    by_week, _ = cumulative_spend_by_week(
        daily, build_weeks(2026, 8), date(2026, 8, 20), MAPPER
    )
    assert by_week[1] == {("Insurance", "Bing"): 10.0}
    assert by_week[2] == {("Insurance", "Bing"): 15.0}
    assert by_week[3][("Securities", "Bing")] == 3.0
    assert 5 not in by_week, "week 5 has not started by 8/20"


def test_undated_rows_are_treated_as_already_cumulative():
    daily = [CampaignSpend("Bing", "B2C - Insurance - Brand - PPC", 10.0)]
    by_week, _ = cumulative_spend_by_week(
        daily, build_weeks(2026, 8), date(2026, 8, 9), MAPPER
    )
    assert by_week[1] == by_week[2] == {("Insurance", "Bing"): 10.0}


def test_future_weeks_are_not_reported():
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 5),
        budgets={("Insurance", "Google"): 40000.0},
        spend_by_week={1: {("Insurance", "Google"): 100.0}, 2: {("Insurance", "Google"): 500.0}},
    )
    assert {row.week for row in report.rows} == {1, 2}


def test_lines_with_neither_budget_nor_spend_are_skipped():
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 2),
        budgets={("Insurance", "Google"): 40000.0, ("Securities", "LinkedIn"): 0.0},
        spend_by_week={1: {("Insurance", "Google"): 100.0}},
    )
    assert [(r.category, r.channel) for r in report.rows] == [("Insurance", "Google")]


def test_spend_with_no_budget_still_gets_a_row():
    """The manual process missed these entirely."""
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 2),
        budgets={("Securities", "Meta"): 0.0},
        spend_by_week={1: {("Securities", "Meta"): 148.32}},
    )
    row = report.rows[0]
    assert row.unbudgeted and row.actual_spend == 148.32
    assert row.pacing_goal == 0.0
    assert row.actual_pct_to_budget == 0.0, "no division by zero"


def test_manual_channels_are_never_written_as_zero():
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 2),
        budgets={("Insurance", "Programmatic"): 2500.0, ("Insurance", "Google"): 40000.0},
        spend_by_week={1: {("Insurance", "Google"): 100.0}},
        config=PacingConfig(),
    )
    assert not [r for r in report.rows if r.channel == "Programmatic"]
    assert report.manual_budgets == {("Insurance", "Programmatic"): 2500.0}
    assert any("Programmatic" in warning for warning in report.warnings)


def test_notes_are_carried_over_to_the_matching_row():
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 2),
        budgets={("Insurance", "Google"): 40000.0},
        spend_by_week={1: {("Insurance", "Google"): 100.0}},
        existing_notes={(1, "Insurance", "Google"): "Budget increased"},
    )
    assert report.rows[0].notes == "Budget increased"


def test_status_matches_the_sheets_rule_at_the_boundary():
    """The sheet reads exactly-on-goal as "Over"."""
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 2),
        budgets={("Insurance", "Google"): 31000.0},
        spend_by_week={1: {("Insurance", "Google"): 2000.0}},
    )
    row = report.rows[0]
    assert row.pacing_goal == 2000.0
    assert row.status == "Over" and row.variance == 0.0


def test_sheet_rows_line_up_with_the_headers():
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 2),
        budgets={("Insurance", "Google"): 40000.0},
        spend_by_week={1: {("Insurance", "Google"): 100.0}},
    )
    assert len(report.rows[0].as_sheet_row()) == len(HEADERS)


def test_month_that_has_not_started_produces_nothing():
    report = build_report(
        year=2026, month=9, as_of=date(2026, 8, 26),
        budgets={("Insurance", "Google"): 40000.0}, spend_by_week={},
    )
    assert report.rows == [] and report.weeks == []
