"""End-to-end check against the real "WoW Pacing" tab for August 2026.

Fixtures under ``tests/fixtures`` are genuine month-to-date campaign spend
pulled from Windsor.ai at each week boundary. ``SHEET_ACTUALS`` are the
numbers a human typed into the tracker for the same weeks. Reproducing them
proves the campaign-to-category mapping and the pacing math match how the
sheet has actually been maintained.
"""

from __future__ import annotations

from datetime import date

import pytest

from examfx_pacing.categories import CategoryMapper
from examfx_pacing.config import ChannelSource, PacingConfig
from examfx_pacing.pacing import aggregate_spend, build_report
from examfx_pacing.spend import CsvSpendSource

FIXTURES = "tests/fixtures/2026-08_week{week}_mtd.csv"

CHANNELS = (
    ChannelSource("Google", "google_ads", "997-052-9086"),
    ChannelSource("Bing", "bing", "180013684"),
    ChannelSource("Meta", "facebook", "253084931845072"),
)

# August 2026 budgets, from '2026 Monthly Tracker' column Y.
AUGUST_BUDGETS = {
    ("Brand", "Google"): 0.0,
    ("Brand", "Bing"): 0.0,
    ("Insurance", "Google"): 40000.0,
    ("Insurance", "Bing"): 6500.0,
    ("Insurance", "Meta"): 4000.0,
    ("Insurance", "LinkedIn"): 0.0,
    ("Insurance", "Programmatic"): 2500.0,
    ("Securities", "Google"): 4000.0,
    ("Securities", "Bing"): 800.0,
    ("Securities", "Meta"): 0.0,
    ("Securities", "LinkedIn"): 0.0,
    ("Adjusters", "Google"): 3000.0,
    ("Adjusters", "Bing"): 1000.0,
    ("Adjusters", "Meta"): 0.0,
    ("Adjusters", "LinkedIn"): 0.0,
}

# "Actual Spend (To-Date)" exactly as it appears in the tracker.
SHEET_ACTUALS: dict[int, dict[tuple[str, str], float]] = {
    1: {
        ("Insurance", "Google"): 2459.56,
        ("Insurance", "Bing"): 135.23,
        ("Insurance", "Meta"): 336.69,
        ("Securities", "Google"): 66.69,
        ("Securities", "Bing"): 171.44,
        ("Adjusters", "Google"): 216.72,
        ("Adjusters", "Bing"): 90.00,
    },
    2: {
        ("Insurance", "Google"): 14510.11,
        ("Insurance", "Bing"): 2432.52,
        ("Insurance", "Meta"): 1206.34,
        ("Securities", "Google"): 1176.10,
        ("Securities", "Bing"): 414.41,
        ("Adjusters", "Google"): 1212.74,
        ("Adjusters", "Bing"): 1008.00,
    },
    3: {
        ("Insurance", "Google"): 25491.70,
        ("Insurance", "Bing"): 4465.13,
        ("Insurance", "Meta"): 2036.18,
        ("Securities", "Google"): 1769.22,
        ("Securities", "Bing"): 447.02,
        ("Adjusters", "Google"): 2158.32,
        ("Adjusters", "Bing"): 1777.52,
    },
    4: {
        ("Insurance", "Google"): 35331.48,
        ("Insurance", "Bing"): 5356.53,
        ("Insurance", "Meta"): 2721.03,
        ("Securities", "Google"): 2444.42,
        ("Securities", "Bing"): 461.91,
        ("Adjusters", "Google"): 3093.76,
        ("Adjusters", "Bing"): 1840.38,
    },
}

# "Pacing Goal (To-Date)" from the sheet, for the same rows.
SHEET_GOALS: dict[int, dict[tuple[str, str], float]] = {
    1: {
        ("Insurance", "Google"): 2580.00,
        ("Insurance", "Bing"): 419.25,
        ("Insurance", "Meta"): 258.00,
        ("Securities", "Google"): 258.00,
        ("Securities", "Bing"): 51.60,
        ("Adjusters", "Google"): 193.50,
        ("Adjusters", "Bing"): 64.50,
    },
    4: {
        ("Insurance", "Google"): 29676.00,
        ("Insurance", "Bing"): 4822.35,
        ("Insurance", "Meta"): 2967.60,
        ("Securities", "Google"): 2967.60,
        ("Securities", "Bing"): 593.52,
        ("Adjusters", "Google"): 2225.70,
        ("Adjusters", "Bing"): 741.90,
    },
}

# Insurance/Meta weeks 1-3: the manual pull folded the Securities Meta
# retargeting campaign into the Insurance row (there is no Securities/Meta
# row in the sheet because that line has no budget). Week 4 was entered
# correctly. The tool splits it properly, so these three are expected to
# differ -- see test_manual_meta_misallocation_is_corrected.
KNOWN_MANUAL_ERRORS = {
    (1, "Insurance", "Meta"),
    (2, "Insurance", "Meta"),
    (3, "Insurance", "Meta"),
}

# Reported ad-platform spend drifts by cents after the fact (late conversions,
# currency rounding). Anything under this is refresh noise, not a mapping bug.
TOLERANCE = 1.50


def goal_tolerance(budget: float) -> float:
    """Allowance for the sheet rounding its pacing % to four decimals.

    The tracker's helper table stores e.g. 0.7419 rather than 23/31, then
    multiplies. The tool keeps full precision, so goals differ by up to
    half a unit in the fourth decimal place times the monthly budget.
    """
    return budget * 0.00005 + 0.01

WEEK_END = {1: date(2026, 8, 2), 2: date(2026, 8, 9), 3: date(2026, 8, 16), 4: date(2026, 8, 23)}


def _spend_for_week(week: int) -> dict[tuple[str, str], float]:
    source = CsvSpendSource(FIXTURES.format(week=week))
    rows = source.fetch(CHANNELS, date(2026, 8, 1), WEEK_END[week])
    totals, unmapped = aggregate_spend(rows, CategoryMapper())
    assert not unmapped, f"week {week} produced unmapped campaigns: {unmapped}"
    return totals


def _report(as_of: date):
    spend_by_week = {week: _spend_for_week(week) for week in SHEET_ACTUALS}
    return build_report(
        year=2026,
        month=8,
        as_of=as_of,
        budgets=AUGUST_BUDGETS,
        spend_by_week=spend_by_week,
        config=PacingConfig(),
    )


@pytest.mark.parametrize("week", sorted(SHEET_ACTUALS))
def test_actual_spend_matches_the_sheet(week):
    """Category rollups reproduce what a human entered, week by week."""
    report = _report(WEEK_END[4])
    computed = {(r.category, r.channel): r.actual_spend for r in report.rows_for_week(week)}

    for (category, channel), expected in SHEET_ACTUALS[week].items():
        if (week, category, channel) in KNOWN_MANUAL_ERRORS:
            continue
        assert (category, channel) in computed, f"week {week}: missing {category}/{channel}"
        actual = computed[(category, channel)]
        assert actual == pytest.approx(expected, abs=TOLERANCE), (
            f"week {week} {category}/{channel}: computed {actual:.2f}, sheet {expected:.2f}"
        )


@pytest.mark.parametrize("week", sorted(SHEET_GOALS))
def test_pacing_goals_match_the_sheet(week):
    """budget x cumulative-% reproduces the sheet's Pacing Goal column."""
    report = _report(WEEK_END[4])
    computed = {(r.category, r.channel): r.pacing_goal for r in report.rows_for_week(week)}

    for key, expected in SHEET_GOALS[week].items():
        allowed = goal_tolerance(AUGUST_BUDGETS[key])
        assert computed[key] == pytest.approx(expected, abs=allowed), (
            f"week {week} {key}: computed {computed[key]}, sheet {expected} "
            f"(allowed +/-{allowed:.2f})"
        )


def test_manual_meta_misallocation_is_corrected():
    """Securities Meta spend is its own line instead of inflating Insurance.

    In weeks 1-3 the manual entry added Securities Meta retargeting spend to
    the Insurance/Meta row. The difference between the sheet and the tool for
    those weeks is exactly the Securities Meta spend.
    """
    report = _report(WEEK_END[4])

    for week in (1, 2, 3):
        rows = {(r.category, r.channel): r for r in report.rows_for_week(week)}
        insurance_meta = rows[("Insurance", "Meta")].actual_spend
        securities_meta = rows[("Securities", "Meta")].actual_spend

        sheet_value = SHEET_ACTUALS[week][("Insurance", "Meta")]
        assert insurance_meta + securities_meta == pytest.approx(sheet_value, abs=TOLERANCE), (
            f"week {week}: the sheet's Insurance/Meta value should equal "
            f"Insurance + Securities Meta spend"
        )
        assert securities_meta > 0


def test_unbudgeted_spend_is_flagged():
    """Securities/Meta spends against a $0 budget -- the run must say so."""
    report = _report(WEEK_END[4])
    row = next(
        r for r in report.rows_for_week(4)
        if (r.category, r.channel) == ("Securities", "Meta")
    )
    assert row.unbudgeted
    assert row.monthly_budget == 0
    assert any("Securities / Meta" in w for w in report.warnings)


def test_programmatic_is_reported_not_zeroed():
    """A budgeted line with no data feed is surfaced, never written as $0."""
    report = _report(WEEK_END[4])
    assert not [r for r in report.rows if r.channel == "Programmatic"]
    assert any("Programmatic" in w and "manual update" in w for w in report.warnings)


def test_status_and_variance_match_the_sheet():
    """Week 4 Over/Under flags agree with the sheet's IF(goal<=actual) rule."""
    report = _report(WEEK_END[4])
    rows = {(r.category, r.channel): r for r in report.rows_for_week(4)}

    expected_status = {
        ("Insurance", "Google"): "Over",
        ("Insurance", "Bing"): "Over",
        ("Insurance", "Meta"): "Under",
        ("Securities", "Google"): "Under",
        ("Securities", "Bing"): "Under",
        ("Adjusters", "Google"): "Over",
        ("Adjusters", "Bing"): "Over",
    }
    for key, status in expected_status.items():
        assert rows[key].status == status, f"{key}: expected {status}"

    insurance_google = rows[("Insurance", "Google")]
    allowed = TOLERANCE + goal_tolerance(AUGUST_BUDGETS[("Insurance", "Google")])
    assert insurance_google.variance == pytest.approx(5655.48, abs=allowed)


def test_in_progress_week_is_clamped_to_run_date():
    """Mid-week runs compare like for like instead of pacing to a future date."""
    report = _report(date(2026, 8, 26))
    week5 = report.rows_for_week(5)
    assert week5, "week 5 has started and should produce rows"
    assert all(r.in_progress for r in week5)
    assert all(r.end == date(2026, 8, 26) for r in week5)
    # 26 of 31 days elapsed, not the 30 the full week would imply.
    assert week5[0].cumulative_pct == pytest.approx(26 / 31, abs=1e-6)
