"""Human-readable and CSV renderings of a pacing run."""

from __future__ import annotations

import csv
import io

from .pacing import HEADERS, PacingReport
from .recommendations import Action, Recommendation

__all__ = ["render_text", "render_csv", "render_recommendations"]

_ACTION_MARK = {
    Action.INCREASE: "^",
    Action.DECREASE: "v",
    Action.HOLD: "=",
    Action.ALLOCATE_OR_PAUSE: "!",
    Action.NOT_DELIVERING: "!",
    Action.OVER_BUDGET: "!",
}


def _money(value: float) -> str:
    """Format currency with the sign outside the symbol: ``-$913.34``."""
    return f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}"


def render_text(report: PacingReport, week: int | None = None) -> str:
    """A terminal summary of one week's pacing (defaults to the latest)."""
    if not report.rows:
        return "No pacing rows: the month has not started yet."

    week = week or max(row.week for row in report.rows)
    rows = report.rows_for_week(week)
    if not rows:
        return f"No rows for week {week}."

    first = rows[0]
    progress = " (in progress)" if first.in_progress else ""
    lines = [
        f"ExamFX pacing - {report.month_start:%B %Y}, {first.week_label}{progress}",
        f"Through {first.end:%b} {first.end.day} - "
        f"{first.cumulative_pct:.1%} of the month elapsed",
        "",
    ]

    widths = (12, 10, 13, 13, 13, 7)
    header = (
        f"{'Category':<{widths[0]}} {'Channel':<{widths[1]}} "
        f"{'Goal':>{widths[2]}} {'Actual':>{widths[3]}} "
        f"{'Variance':>{widths[4]}} {'Status':>{widths[5]}}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for row in sorted(rows, key=lambda r: (r.category, r.channel)):
        lines.append(
            f"{row.category:<{widths[0]}} {row.channel:<{widths[1]}} "
            f"{_money(row.pacing_goal):>{widths[2]}} {_money(row.actual_spend):>{widths[3]}} "
            f"{_money(row.variance):>{widths[4]}} {row.status:>{widths[5]}}"
        )

    total_goal = sum(row.pacing_goal for row in rows)
    total_actual = sum(row.actual_spend for row in rows)
    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<{widths[0]}} {'':<{widths[1]}} "
        f"{_money(total_goal):>{widths[2]}} {_money(total_actual):>{widths[3]}} "
        f"{_money(total_actual - total_goal):>{widths[4]}} "
        f"{('Over' if total_actual >= total_goal else 'Under'):>{widths[5]}}"
    )

    if report.warnings:
        lines.append("")
        lines.append("Needs attention:")
        lines.extend(f"  - {warning}" for warning in report.warnings)

    return "\n".join(lines)


def render_recommendations(recommendations: list[Recommendation]) -> str:
    """Budget actions, biggest lever first."""
    if not recommendations:
        return "No budget recommendations."

    lines = ["Budget recommendations", "=" * 22, ""]

    actionable = [r for r in recommendations if r.action != Action.HOLD]
    holds = [r for r in recommendations if r.action == Action.HOLD]

    for rec in actionable:
        mark = _ACTION_MARK.get(rec.action, "-")
        urgency = "  [URGENT]" if rec.urgent else ""
        lines.append(f"{mark} {rec.headline}{urgency}")
        remaining = (
            f"{_money(rec.remaining_budget)} remaining"
            if rec.remaining_budget >= 0
            else f"{_money(abs(rec.remaining_budget))} over"
        )
        lines.append(
            f"    spent {_money(rec.spend_to_date)} of {_money(rec.monthly_budget)} "
            f"in {rec.days_elapsed} days - {rec.days_remaining} days left, {remaining}"
        )
        for driver in rec.drivers:
            pace = ""
            if driver.recent_rate_index is not None:
                if driver.recent_rate_index >= 1.15:
                    pace = f", accelerating ({driver.recent_rate_index:.2f}x avg)"
                elif driver.recent_rate_index <= 0.85:
                    pace = f", slowing ({driver.recent_rate_index:.2f}x avg)"
            lines.append(
                f"      - {driver.campaign}: {_money(driver.spend)} "
                f"({driver.share_of_line:.0%} of the line){pace}"
            )
        lines.append("")

    if holds:
        lines.append("On pace - no change needed:")
        for rec in holds:
            lines.append(
                f"  = {rec.category} / {rec.channel}: projecting "
                f"{_money(rec.projected_month_end)} vs {_money(rec.monthly_budget)} "
                f"({rec.projected_variance_pct:+.0%})"
            )

    return "\n".join(lines).rstrip()


def render_csv(report: PacingReport) -> str:
    """The full pacing table as CSV, matching the sheet's columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for row in report.rows:
        writer.writerow(row.as_sheet_row())
    return buffer.getvalue()
