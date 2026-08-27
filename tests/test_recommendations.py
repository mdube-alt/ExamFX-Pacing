"""Budget advice: direction, sizing and escalation."""

from __future__ import annotations

from datetime import date

import pytest

from examfx_pacing.categories import CategoryMapper
from examfx_pacing.pacing import build_report
from examfx_pacing.recommendations import (
    Action,
    RecommendationSettings,
    build_recommendations,
)
from examfx_pacing.spend import CampaignSpend
from examfx_pacing.weeks import build_weeks


def _latest_week(as_of: date) -> int:
    """The week number containing ``as_of``, so spend lands on the right row."""
    return max(w.number for w in build_weeks(as_of.year, as_of.month) if w.start <= as_of)


def _spend_by_week(as_of: date, totals: dict) -> dict:
    """Put ``totals`` on the latest started week, with earlier weeks empty."""
    latest = _latest_week(as_of)
    return {week: ({} if week < latest else totals) for week in range(1, latest + 1)}


def _report(spend_to_date: float, budget: float = 3100.0, as_of=date(2026, 8, 10)):
    """A one-line report: a single Insurance/Google row at a chosen spend."""
    return build_report(
        year=2026,
        month=8,
        as_of=as_of,
        budgets={("Insurance", "Google"): budget},
        spend_by_week=_spend_by_week(as_of, {("Insurance", "Google"): spend_to_date}),
    )


def _only(report):
    recs = build_recommendations(report)
    assert len(recs) == 1
    return recs[0]


def test_on_pace_line_is_left_alone():
    # 10 of 31 days elapsed; 10/31 of a $3,100 budget is $1,000.
    rec = _only(_report(1000.0))
    assert rec.action == Action.HOLD
    assert rec.projected_variance == pytest.approx(0.0, abs=1.0)


def test_overspending_line_is_told_to_cut():
    rec = _only(_report(1500.0))
    assert rec.action == Action.DECREASE
    assert rec.projected_month_end == pytest.approx(4650.0, abs=1.0)
    assert rec.suggested_daily < rec.current_daily
    # $1,600 left over the 21 remaining days.
    assert rec.suggested_daily == pytest.approx(1600.0 / 21, abs=0.5)


def test_underspending_line_is_told_to_raise():
    rec = _only(_report(500.0))
    assert rec.action == Action.INCREASE
    assert rec.suggested_daily > rec.current_daily
    assert rec.daily_change_pct > 0


def test_urgency_tracks_the_size_of_the_miss():
    settings = RecommendationSettings(tolerance=0.05, urgent_tolerance=0.15)
    mild = build_recommendations(_report(1080.0), settings=settings)[0]
    severe = build_recommendations(_report(1500.0), settings=settings)[0]
    assert mild.action == Action.DECREASE and not mild.urgent
    assert severe.action == Action.DECREASE and severe.urgent


def test_line_already_past_budget_is_told_to_pause():
    rec = _only(_report(3500.0))
    assert rec.action == Action.OVER_BUDGET
    assert rec.urgent
    assert rec.remaining_budget < 0
    assert "pause" in rec.headline


def test_budgeted_line_with_no_spend_is_escalated():
    rec = _only(_report(0.0))
    assert rec.action == Action.NOT_DELIVERING
    assert rec.urgent
    assert "check the campaigns are live" in rec.headline


def test_no_advice_at_the_very_end_of_the_month():
    """With a day left, changing a daily budget cannot move the outcome."""
    rec = _only(_report(1500.0, as_of=date(2026, 8, 30)))
    assert rec.action == Action.HOLD


def test_small_lines_are_ignored():
    report = _report(60.0, budget=50.0)
    assert build_recommendations(report) == []


def test_trivial_unbudgeted_spend_is_not_escalated():
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 10),
        budgets={("Brand", "Google"): 0.0},
        spend_by_week=_spend_by_week(date(2026, 8, 10), {("Brand", "Google"): 54.52}),
    )
    assert build_recommendations(report) == []


def test_material_unbudgeted_spend_is_escalated():
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 10),
        budgets={("Securities", "Meta"): 0.0},
        spend_by_week=_spend_by_week(date(2026, 8, 10), {("Securities", "Meta"): 1480.0}),
    )
    rec = build_recommendations(report)[0]
    assert rec.action == Action.ALLOCATE_OR_PAUSE
    assert rec.urgent


def test_recommendations_are_ordered_by_money_at_stake():
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 10),
        budgets={
            ("Insurance", "Google"): 40000.0,
            ("Securities", "Bing"): 800.0,
            ("Adjusters", "Google"): 3000.0,
        },
        spend_by_week=_spend_by_week(
            date(2026, 8, 10),
            {
                ("Insurance", "Google"): 20000.0,   # hugely over
                ("Securities", "Bing"): 100.0,      # mildly under
                ("Adjusters", "Google"): 1500.0,    # over
            },
        ),
    )
    recs = build_recommendations(report)
    assert [r.category for r in recs] == ["Insurance", "Adjusters", "Securities"]
    assert abs(recs[0].projected_variance) > abs(recs[1].projected_variance)


def test_drivers_name_the_campaigns_behind_a_line():
    """Advice points at the campaign to actually change."""
    daily = [
        CampaignSpend("Google", "B2C - Insurance - Non Brand - PPC", 100.0, date(2026, 8, day))
        for day in range(1, 11)
    ] + [
        # Started only in the last few days -- should read as accelerating.
        CampaignSpend("Google", "B2C - Insurance - Performance Max", 200.0, date(2026, 8, day))
        for day in range(8, 11)
    ]
    report = build_report(
        year=2026, month=8, as_of=date(2026, 8, 10),
        budgets={("Insurance", "Google"): 3100.0},
        spend_by_week=_spend_by_week(date(2026, 8, 10), {("Insurance", "Google"): 1600.0}),
    )
    rec = build_recommendations(report, daily, CategoryMapper())[0]

    names = [d.campaign for d in rec.drivers]
    assert "B2C - Insurance - Non Brand - PPC" in names
    pmax = next(d for d in rec.drivers if d.campaign == "B2C - Insurance - Performance Max")
    assert pmax.recent_rate_index > 1.2, "a campaign that just launched is accelerating"
    assert sum(d.share_of_line for d in rec.drivers) == pytest.approx(1.0, abs=0.01)


def test_no_rows_means_no_advice():
    report = build_report(
        year=2026, month=8, as_of=date(2026, 7, 20),
        budgets={("Insurance", "Google"): 40000.0}, spend_by_week={},
    )
    assert build_recommendations(report) == []
