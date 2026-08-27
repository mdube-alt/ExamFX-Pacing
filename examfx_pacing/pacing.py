"""Build the weekly pacing table from budgets and campaign spend."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from .categories import UNMAPPED, CategoryMapper
from .config import ChannelSource, PacingConfig
from .spend import CampaignSpend
from .weeks import Week, build_weeks, format_range

__all__ = [
    "cumulative_spend_by_week",
    "PacingRow",
    "PacingReport",
    "BudgetKey",
    "aggregate_spend",
    "build_report",
    "HEADERS",
]

#: Column order written to the sheet, matching the existing "WoW Pacing" tab.
HEADERS = [
    "Week",
    "Dates",
    "Category",
    "Channel",
    "Start",
    "End",
    "% of Month (Cumulative)",
    "Pacing Goal (To-Date)",
    "Actual Spend\n(To-Date)",
    "Actual Pacing % to Budget",
    "Variance",
    "Status",
    "Notes",
]

BudgetKey = tuple[str, str]


@dataclass
class PacingRow:
    """One category/channel line for one week."""

    week: int
    week_label: str
    dates: str
    category: str
    channel: str
    start: date
    end: date
    cumulative_pct: float
    monthly_budget: float
    pacing_goal: float
    actual_spend: float
    notes: str = ""
    #: True when the week is still in progress as of the run date.
    in_progress: bool = False
    #: True when spend exists on a category/channel with no budget allocated.
    unbudgeted: bool = False
    #: True when the channel has no Windsor connector (spend is not automated).
    manual: bool = False

    @property
    def actual_pct_to_budget(self) -> float:
        """Spend to date as a share of the *monthly* budget, as the sheet does."""
        if not self.monthly_budget:
            return 0.0
        return self.actual_spend / self.monthly_budget

    @property
    def variance(self) -> float:
        """Actual minus goal. Positive = overspending against pace."""
        return round(self.actual_spend - self.pacing_goal, 2)

    @property
    def status(self) -> str:
        """Matches the sheet's rule: at-or-above goal reads as "Over"."""
        return "Over" if self.actual_spend >= self.pacing_goal else "Under"

    def as_sheet_row(self) -> list:
        return [
            self.week_label,
            self.dates,
            self.category,
            self.channel,
            self.start.isoformat(),
            self.end.isoformat(),
            round(self.cumulative_pct, 4),
            round(self.pacing_goal, 2),
            round(self.actual_spend, 2),
            round(self.actual_pct_to_budget, 6),
            self.variance,
            self.status,
            self.notes,
        ]


@dataclass
class PacingReport:
    """The full result of a pacing run."""

    month_start: date
    as_of: date
    rows: list[PacingRow]
    weeks: list[Week]
    #: Campaigns no rule matched, with their spend, so naming drift is visible.
    unmapped: dict[tuple[str, str], float]
    #: Budgeted (category, channel) lines with no automated data feed.
    manual_budgets: dict[BudgetKey, float] = field(default_factory=dict)

    @property
    def current_week(self) -> int | None:
        weeks = [row.week for row in self.rows if row.in_progress]
        return max(weeks) if weeks else None

    def rows_for_week(self, week: int) -> list[PacingRow]:
        return [row for row in self.rows if row.week == week]

    @property
    def warnings(self) -> list[str]:
        """Things a human should look at after a run."""
        messages: list[str] = []
        for (channel, campaign), spend in sorted(self.unmapped.items()):
            messages.append(
                f"Unmapped campaign on {channel}: {campaign!r} (${spend:,.2f}) "
                f"- add a rule or it will be excluded from every category"
            )
        for (category, channel), budget in sorted(self.manual_budgets.items()):
            messages.append(
                f"{category} / {channel}: ${budget:,.2f} budgeted but there is no "
                f"automated data source - this line still needs a manual update"
            )
        latest = self.current_week or (max((r.week for r in self.rows), default=None))
        for row in (r for r in self.rows if r.week == latest and r.unbudgeted):
            messages.append(
                f"{row.category} / {row.channel}: ${row.actual_spend:,.2f} spent "
                f"with no budget allocated for the month"
            )
        return messages


def aggregate_spend(
    campaign_spend: Iterable[CampaignSpend], mapper: CategoryMapper
) -> tuple[dict[BudgetKey, float], dict[tuple[str, str], float]]:
    """Roll campaign spend up to (category, channel) totals.

    Returns the totals plus any campaigns that did not match a rule, keyed by
    ``(channel, campaign)`` so naming drift can be reported instead of
    silently vanishing from the numbers.
    """
    totals: dict[BudgetKey, float] = {}
    unmapped: dict[tuple[str, str], float] = {}

    for entry in campaign_spend:
        category = mapper.category_for(entry.campaign)
        if category == UNMAPPED:
            key = (entry.channel, entry.campaign)
            unmapped[key] = unmapped.get(key, 0.0) + entry.spend
            continue
        key = (category, entry.channel)
        totals[key] = totals.get(key, 0.0) + entry.spend

    return {key: round(value, 2) for key, value in totals.items()}, unmapped


def cumulative_spend_by_week(
    daily_spend: Iterable[CampaignSpend],
    weeks: Iterable[Week],
    as_of: date,
    mapper: CategoryMapper,
) -> tuple[dict[int, dict[BudgetKey, float]], dict[tuple[str, str], float]]:
    """Derive each week's month-to-date totals from one day-by-day pull.

    Rows without a date are treated as already-cumulative and attributed to
    every week, which lets a plain windowed pull still work.
    """
    rows = list(daily_spend)
    by_week: dict[int, dict[BudgetKey, float]] = {}
    unmapped: dict[tuple[str, str], float] = {}

    for week in weeks:
        if week.start > as_of:
            continue
        cutoff = min(week.end, as_of)
        through_cutoff = [
            row for row in rows if row.day is None or row.day <= cutoff
        ]
        totals, week_unmapped = aggregate_spend(through_cutoff, mapper)
        by_week[week.number] = totals
        # Keep the widest view of unmapped campaigns seen in any week.
        for key, value in week_unmapped.items():
            unmapped[key] = max(unmapped.get(key, 0.0), value)

    return by_week, unmapped


def _week_effective_end(week: Week, as_of: date) -> tuple[Week, bool]:
    """Clamp an in-progress week to the run date."""
    if as_of < week.end:
        return week.truncated_to(as_of), True
    return week, False


def build_report(
    *,
    year: int,
    month: int,
    as_of: date,
    budgets: dict[BudgetKey, float],
    spend_by_week: dict[int, dict[BudgetKey, float]],
    unmapped: dict[tuple[str, str], float] | None = None,
    existing_notes: dict[tuple[int, str, str], str] | None = None,
    config: PacingConfig | None = None,
) -> PacingReport:
    """Assemble pacing rows for every week that has started.

    ``spend_by_week`` maps a week number to month-to-date spend through that
    week's effective end date, keyed by ``(category, channel)``.
    """
    config = config or PacingConfig()
    existing_notes = existing_notes or {}
    epsilon = config.spend_epsilon

    manual_budgets = {
        key: value
        for key, value in budgets.items()
        if key[1] in config.manual_channels and abs(value) > epsilon
    }
    budgets = {key: value for key, value in budgets.items() if key[1] not in config.manual_channels}

    all_weeks = build_weeks(year, month)
    rows: list[PacingRow] = []

    for week in all_weeks:
        if week.start > as_of:
            break  # week hasn't started yet
        effective, in_progress = _week_effective_end(week, as_of)
        spend = spend_by_week.get(week.number, {})

        # Emit a row for every budgeted line, plus any line with spend but no
        # budget (which the manual process used to miss entirely).
        keys = set(budgets) | {k for k, v in spend.items() if abs(v) > epsilon}
        keys = {k for k in keys if k[1] not in config.manual_channels}

        for category, channel in sorted(keys):
            budget = budgets.get((category, channel), 0.0)
            actual = spend.get((category, channel), 0.0)
            if abs(budget) <= epsilon and abs(actual) <= epsilon:
                continue  # nothing budgeted and nothing spent

            rows.append(
                PacingRow(
                    week=week.number,
                    week_label=week.label,
                    dates=format_range(effective.start, effective.end)
                    if in_progress
                    else week.date_range,
                    category=category,
                    channel=channel,
                    start=week.month_start,
                    end=effective.end,
                    cumulative_pct=effective.cumulative_pct,
                    monthly_budget=budget,
                    pacing_goal=round(budget * effective.cumulative_pct, 2),
                    actual_spend=actual,
                    notes=existing_notes.get((week.number, category, channel), ""),
                    in_progress=in_progress,
                    unbudgeted=abs(budget) <= epsilon and abs(actual) > epsilon,
                    manual=channel in config.manual_channels,
                )
            )

    return PacingReport(
        month_start=date(year, month, 1),
        as_of=as_of,
        rows=rows,
        weeks=[w for w in all_weeks if w.start <= as_of],
        unmapped=unmapped or {},
        manual_budgets=manual_budgets,
    )
