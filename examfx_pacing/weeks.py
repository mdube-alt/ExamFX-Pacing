"""Month/week calendar math for pacing.

The tracker splits a month into Monday-through-Sunday weeks. Week 1 is the
partial week containing the 1st, and the final week is the partial week
containing the last day. Pacing is measured on *cumulative* days elapsed:

    cumulative % = (days from the 1st through the week end) / days in month

For August 2026 this reproduces the percentages used in the sheet:
Week 1 (8/1-8/2) = 2/31 = 6.45%, Week 2 (8/3-8/9) = 9/31 = 29.03%, etc.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

__all__ = ["Week", "month_bounds", "build_weeks", "format_day", "format_range"]


@dataclass(frozen=True)
class Week:
    """One pacing week within a month."""

    number: int
    start: date
    end: date
    month_start: date
    month_end: date

    @property
    def cumulative_days(self) -> int:
        """Days elapsed from the 1st of the month through this week's end."""
        return (self.end - self.month_start).days + 1

    @property
    def days_in_month(self) -> int:
        return (self.month_end - self.month_start).days + 1

    @property
    def cumulative_pct(self) -> float:
        """Share of the month elapsed through this week's end."""
        return self.cumulative_days / self.days_in_month

    @property
    def label(self) -> str:
        return f"Week {self.number}"

    @property
    def date_range(self) -> str:
        """Human date range, e.g. ``8/3 - 8/9``, matching the sheet."""
        return format_range(self.start, self.end)

    def truncated_to(self, as_of: date) -> "Week":
        """This week with its end clamped to ``as_of`` (for an in-progress week)."""
        if as_of >= self.end:
            return self
        return Week(self.number, self.start, as_of, self.month_start, self.month_end)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """First and last calendar day of a month."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def build_weeks(year: int, month: int) -> list[Week]:
    """Split a month into Monday-Sunday pacing weeks.

    The first and last weeks are truncated to stay inside the month.
    """
    month_start, month_end = month_bounds(year, month)

    weeks: list[Week] = []
    cursor = month_start
    number = 1
    while cursor <= month_end:
        # Sunday ends the week; weekday() is Mon=0 .. Sun=6.
        days_to_sunday = 6 - cursor.weekday()
        week_end = min(cursor + timedelta(days=days_to_sunday), month_end)
        weeks.append(Week(number, cursor, week_end, month_start, month_end))
        cursor = week_end + timedelta(days=1)
        number += 1
    return weeks


def format_day(day: date) -> str:
    """``date(2026, 8, 3)`` -> ``8/3`` (no zero padding, matching the sheet)."""
    return f"{day.month}/{day.day}"


def format_range(start: date, end: date) -> str:
    if start == end:
        return format_day(start)
    return f"{format_day(start)} - {format_day(end)}"
