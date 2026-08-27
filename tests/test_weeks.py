"""Calendar behaviour that the pacing percentages depend on."""

from __future__ import annotations

from datetime import date

import pytest

from examfx_pacing.weeks import build_weeks, format_range, month_bounds


def test_august_2026_matches_the_trackers_helper_table():
    """The sheet's own week table is the reference for this math."""
    expected = [
        ("Week 1", "8/1 - 8/2", 2, 0.0645),
        ("Week 2", "8/3 - 8/9", 9, 0.2903),
        ("Week 3", "8/10 - 8/16", 16, 0.5161),
        ("Week 4", "8/17 - 8/23", 23, 0.7419),
        ("Week 5", "8/24 - 8/30", 30, 0.9677),
        ("Week 6", "8/31", 31, 1.0),
    ]
    weeks = build_weeks(2026, 8)
    assert len(weeks) == len(expected)
    for week, (label, dates, days, pct) in zip(weeks, expected):
        assert week.label == label
        assert week.date_range == dates
        assert week.cumulative_days == days
        assert week.cumulative_pct == pytest.approx(pct, abs=5e-5)


@pytest.mark.parametrize("year,month", [(2026, m) for m in range(1, 13)] + [(2028, 2)])
def test_weeks_tile_the_month_exactly(year, month):
    """Every day belongs to exactly one week, with no gaps or overlaps."""
    start, end = month_bounds(year, month)
    weeks = build_weeks(year, month)

    assert weeks[0].start == start
    assert weeks[-1].end == end
    for earlier, later in zip(weeks, weeks[1:]):
        assert (later.start - earlier.end).days == 1
    assert weeks[-1].cumulative_pct == pytest.approx(1.0)


def test_month_starting_on_monday_has_no_stub_first_week():
    """June 2026 starts on a Monday, so week 1 is a full seven days."""
    weeks = build_weeks(2026, 6)
    assert weeks[0].start == date(2026, 6, 1)
    assert weeks[0].end == date(2026, 6, 7)
    assert weeks[0].cumulative_days == 7


def test_leap_day_is_included():
    weeks = build_weeks(2028, 2)
    assert weeks[-1].end == date(2028, 2, 29)
    assert weeks[-1].days_in_month == 29


def test_truncating_an_in_progress_week():
    week = build_weeks(2026, 8)[4]  # 8/24 - 8/30
    partial = week.truncated_to(date(2026, 8, 26))
    assert partial.end == date(2026, 8, 26)
    assert partial.cumulative_days == 26
    # Clamping to a date past the end leaves the week untouched.
    assert week.truncated_to(date(2026, 9, 5)) is week


def test_single_day_range_is_not_repeated():
    assert format_range(date(2026, 8, 31), date(2026, 8, 31)) == "8/31"
