"""Turn pacing variance into concrete budget actions.

For each budgeted category/channel the run projects month-end spend from the
current run rate, compares it to the monthly budget, and works out the daily
budget that would land the line on plan. Where a line needs attention, the
campaigns driving it are named so the change can actually be applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from .categories import CategoryMapper
from .pacing import PacingReport, PacingRow
from .spend import CampaignSpend

__all__ = [
    "Action",
    "Recommendation",
    "CampaignDriver",
    "RecommendationSettings",
    "build_recommendations",
]


class Action:
    """The advice attached to a line."""

    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    HOLD = "HOLD"
    #: Spending with nothing allocated -- needs a budget or needs pausing.
    ALLOCATE_OR_PAUSE = "ALLOCATE OR PAUSE"
    #: Budgeted but not delivering at all.
    NOT_DELIVERING = "NOT DELIVERING"
    #: The monthly budget is already spent -- no daily budget can fix it.
    OVER_BUDGET = "OVER BUDGET"


@dataclass(frozen=True)
class RecommendationSettings:
    """Thresholds controlling when a line is called out."""

    #: Projected month-end variance (as a share of budget) that triggers advice.
    tolerance: float = 0.05
    #: Above this projected variance a line is flagged as high priority.
    urgent_tolerance: float = 0.15
    #: Ignore lines with less than this monthly budget to avoid noise.
    min_budget: float = 100.0
    #: Unbudgeted spend below this is noted in warnings but not escalated.
    min_unbudgeted_spend: float = 100.0
    #: Days left in the month below which a change is unlikely to be worth it.
    min_days_remaining: int = 2


@dataclass(frozen=True)
class CampaignDriver:
    """A campaign contributing to a line's over- or under-spend."""

    campaign: str
    spend: float
    share_of_line: float
    #: Recent daily rate versus the line's average daily rate. >1 = accelerating.
    recent_rate_index: float | None = None


@dataclass
class Recommendation:
    """A budget action for one category/channel line."""

    category: str
    channel: str
    action: str
    monthly_budget: float
    spend_to_date: float
    days_elapsed: int
    days_remaining: int
    current_daily: float
    projected_month_end: float
    #: Daily budget that would land the line exactly on budget.
    suggested_daily: float
    urgent: bool = False
    drivers: list[CampaignDriver] = field(default_factory=list)

    @property
    def projected_variance(self) -> float:
        return round(self.projected_month_end - self.monthly_budget, 2)

    @property
    def projected_variance_pct(self) -> float:
        if not self.monthly_budget:
            return 0.0
        return self.projected_variance / self.monthly_budget

    @property
    def daily_change_pct(self) -> float | None:
        """How much the daily budget needs to move, as a share of today's rate."""
        if not self.current_daily:
            return None
        return (self.suggested_daily - self.current_daily) / self.current_daily

    @property
    def remaining_budget(self) -> float:
        return round(self.monthly_budget - self.spend_to_date, 2)

    @property
    def headline(self) -> str:
        line = f"{self.category} / {self.channel}"
        if self.action == Action.HOLD:
            return f"{line}: on pace - hold"
        if self.action == Action.OVER_BUDGET:
            return (
                f"{line}: already ${abs(self.remaining_budget):,.0f} over a "
                f"${self.monthly_budget:,.0f} budget with {self.days_remaining} days "
                f"left - pause, or accept the overage"
            )
        if self.action == Action.ALLOCATE_OR_PAUSE:
            return (
                f"{line}: ${self.spend_to_date:,.0f} spent with no budget allocated "
                f"- allocate budget or pause"
            )
        if self.action == Action.NOT_DELIVERING:
            return (
                f"{line}: ${self.monthly_budget:,.0f} budgeted but nothing spent "
                f"- check the campaigns are live"
            )

        direction = "cut" if self.action == Action.DECREASE else "raise"
        change = self.daily_change_pct
        change_text = f" ({change:+.0%})" if change is not None else ""
        return (
            f"{line}: {direction} daily budget from ${self.current_daily:,.0f} to "
            f"${self.suggested_daily:,.0f}{change_text} - projecting "
            f"${self.projected_month_end:,.0f} vs ${self.monthly_budget:,.0f} budget "
            f"({self.projected_variance_pct:+.0%})"
        )


def _drivers_for_line(
    row: PacingRow,
    daily_spend: Iterable[CampaignSpend],
    mapper: CategoryMapper,
    as_of: date,
    month_start: date,
    limit: int = 3,
) -> list[CampaignDriver]:
    """Rank the campaigns behind a line by spend, noting which are accelerating."""
    relevant = [
        entry
        for entry in daily_spend
        if entry.channel == row.channel
        and mapper.category_for(entry.campaign) == row.category
        and (entry.day is None or month_start <= entry.day <= as_of)
    ]
    if not relevant:
        return []

    totals: dict[str, float] = {}
    for entry in relevant:
        totals[entry.campaign] = totals.get(entry.campaign, 0.0) + entry.spend

    line_total = sum(totals.values())
    if line_total <= 0:
        return []

    # Compare the last 7 days' daily rate against the month's average rate.
    days_elapsed = max((as_of - month_start).days + 1, 1)
    window_start = max(month_start, date.fromordinal(as_of.toordinal() - 6))
    window_days = (as_of - window_start).days + 1
    recent: dict[str, float] = {}
    has_dates = any(entry.day is not None for entry in relevant)
    for entry in relevant:
        if entry.day is not None and entry.day >= window_start:
            recent[entry.campaign] = recent.get(entry.campaign, 0.0) + entry.spend

    drivers = []
    for campaign, spend in sorted(totals.items(), key=lambda item: -item[1])[:limit]:
        rate_index = None
        if has_dates and spend > 0:
            average_daily = spend / days_elapsed
            recent_daily = recent.get(campaign, 0.0) / window_days
            if average_daily > 0:
                rate_index = round(recent_daily / average_daily, 2)
        drivers.append(
            CampaignDriver(
                campaign=campaign,
                spend=round(spend, 2),
                share_of_line=round(spend / line_total, 4),
                recent_rate_index=rate_index,
            )
        )
    return drivers


def build_recommendations(
    report: PacingReport,
    daily_spend: Iterable[CampaignSpend] | None = None,
    mapper: CategoryMapper | None = None,
    settings: RecommendationSettings | None = None,
) -> list[Recommendation]:
    """Produce budget advice from the latest week in a pacing report.

    Lines are ordered by how much money is at stake so the biggest levers
    come first.
    """
    settings = settings or RecommendationSettings()
    mapper = mapper or CategoryMapper()
    daily_spend = list(daily_spend or [])

    if not report.rows:
        return []

    latest_week = max(row.week for row in report.rows)
    rows = report.rows_for_week(latest_week)
    if not rows:
        return []

    month_start = report.month_start
    as_of = min(report.as_of, max(row.end for row in rows))
    days_in_month = report.weeks[-1].days_in_month if report.weeks else 30
    days_elapsed = max((as_of - month_start).days + 1, 1)
    days_remaining = max(days_in_month - days_elapsed, 0)

    recommendations: list[Recommendation] = []

    for row in rows:
        budget = row.monthly_budget
        spend = row.actual_spend
        current_daily = spend / days_elapsed

        if budget <= 0:
            if spend >= settings.min_unbudgeted_spend:
                recommendations.append(
                    Recommendation(
                        category=row.category,
                        channel=row.channel,
                        action=Action.ALLOCATE_OR_PAUSE,
                        monthly_budget=0.0,
                        spend_to_date=round(spend, 2),
                        days_elapsed=days_elapsed,
                        days_remaining=days_remaining,
                        current_daily=round(current_daily, 2),
                        projected_month_end=round(current_daily * days_in_month, 2),
                        suggested_daily=0.0,
                        urgent=True,
                        drivers=_drivers_for_line(
                            row, daily_spend, mapper, as_of, month_start
                        ),
                    )
                )
            continue

        if budget < settings.min_budget:
            continue

        projected = current_daily * days_in_month
        remaining_budget = budget - spend
        suggested_daily = (
            remaining_budget / days_remaining if days_remaining > 0 else 0.0
        )
        variance_pct = (projected - budget) / budget

        if spend <= 0:
            action = Action.NOT_DELIVERING
            urgent = True
        elif remaining_budget <= 0:
            action = Action.OVER_BUDGET
            urgent = True
        elif days_remaining < settings.min_days_remaining:
            # Too late in the month for a daily-budget change to matter.
            action = Action.HOLD
            urgent = False
        elif variance_pct > settings.tolerance:
            action = Action.DECREASE
            urgent = variance_pct > settings.urgent_tolerance
        elif variance_pct < -settings.tolerance:
            action = Action.INCREASE
            urgent = variance_pct < -settings.urgent_tolerance
        else:
            action = Action.HOLD
            urgent = False

        recommendations.append(
            Recommendation(
                category=row.category,
                channel=row.channel,
                action=action,
                monthly_budget=budget,
                spend_to_date=round(spend, 2),
                days_elapsed=days_elapsed,
                days_remaining=days_remaining,
                current_daily=round(current_daily, 2),
                projected_month_end=round(projected, 2),
                suggested_daily=round(max(suggested_daily, 0.0), 2),
                urgent=urgent,
                drivers=(
                    _drivers_for_line(row, daily_spend, mapper, as_of, month_start)
                    if action not in (Action.HOLD,)
                    else []
                ),
            )
        )

    # Biggest dollar impact first; HOLD lines sink to the bottom.
    recommendations.sort(
        key=lambda rec: (rec.action == Action.HOLD, -abs(rec.projected_variance))
    )
    return recommendations
