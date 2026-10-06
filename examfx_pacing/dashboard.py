"""Render the pacing run as a self-contained HTML dashboard.

One file, no external requests: the page is published to GitHub Pages by the
weekly job, so it has to work offline-ish and survive having no CDN. Charts are
inline SVG rather than a charting library for the same reason.

Colours come from the validated data-viz palette: series blue/orange for the
two cumulative lines, the fixed status palette for Over/Under and for the
recommendation actions. Status is never carried by colour alone -- every status
ships a label, and the recommendation cards carry an arrow glyph as well.
"""

from __future__ import annotations

import html
import json
from datetime import date

from .pacing import PacingReport, PacingRow
from .recommendations import Action, Recommendation

__all__ = ["render_dashboard"]

# --- Palette ---------------------------------------------------------------
# Validated with the data-viz validator in both modes (all checks pass).
_LIGHT = {
    "surface": "#fcfcfb",
    "plane": "#f9f9f7",
    "primary": "#0b0b0b",
    "secondary": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "series_goal": "#2a78d6",
    "series_actual": "#eb6834",
    "border": "rgba(11,11,11,0.10)",
}
_DARK = {
    "surface": "#1a1a19",
    "plane": "#0d0d0d",
    "primary": "#ffffff",
    "secondary": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "series_goal": "#3987e5",
    "series_actual": "#d95926",
    "border": "rgba(255,255,255,0.10)",
}
#: Fixed in both modes, never themed, never reused for a series.
_STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

#: Action -> (status role, glyph, label). The glyph means status never rests
#: on colour alone.
_ACTION_STYLE = {
    Action.INCREASE: ("warning", "&uarr;", "Raise"),
    Action.DECREASE: ("serious", "&darr;", "Cut"),
    Action.HOLD: ("good", "=", "Hold"),
    Action.OVER_BUDGET: ("critical", "!", "Over budget"),
    Action.ALLOCATE_OR_PAUSE: ("critical", "!", "No budget"),
    Action.NOT_DELIVERING: ("critical", "!", "Not delivering"),
}


def _long_date(day: date) -> str:
    """``Monday 5 October 2026``. Built by hand because %-d is glibc-only."""
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B %Y')}"


def _short_date(day: date) -> str:
    """``Oct 5``."""
    return f"{day.strftime('%b')} {day.day}"


def _money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _vars(palette: dict) -> str:
    return "\n".join(f"    --{k.replace('_', '-')}: {v};" for k, v in palette.items())


# --- Chart -----------------------------------------------------------------


def _cumulative_series(report: PacingReport) -> tuple[list[str], list[float], list[float]]:
    """Goal and actual totals per week, across every line."""
    labels: list[str] = []
    goals: list[float] = []
    actuals: list[float] = []
    for week in sorted({row.week for row in report.rows}):
        rows = report.rows_for_week(week)
        if not rows:
            continue
        labels.append(rows[0].dates)
        goals.append(round(sum(r.pacing_goal for r in rows), 2))
        actuals.append(round(sum(r.actual_spend for r in rows), 2))
    return labels, goals, actuals


def _line_chart(labels: list[str], goals: list[float], actuals: list[float]) -> str:
    """Cumulative goal vs actual. Two series, so it carries a legend.

    Change over time with a pace comparison is the one thing a table does
    badly, so this is the only chart on the page.
    """
    if len(labels) < 2:
        # Keep the heading: a bare sentence in an unlabelled panel reads like
        # something failed rather than like a chart waiting for a second week.
        return (
            '<figure class="chart">\n'
            "  <figcaption>\n    <h3>Cumulative pace</h3>\n"
            '    <p class="sub">Spend to date against the pacing goal, '
            "week by week.</p>\n  </figcaption>\n"
            '  <p class="empty">Only one complete week so far. The trend '
            "appears once the month has a second one.</p>\n</figure>"
        )

    width, height = 720, 260
    pad_l, pad_r, pad_t, pad_b = 64, 16, 16, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    top = max(max(goals), max(actuals)) or 1.0
    top *= 1.08

    def x(i: int) -> float:
        return pad_l + (plot_w * i / (len(labels) - 1))

    def y(value: float) -> float:
        return pad_t + plot_h - (plot_h * value / top)

    def path(values: list[float]) -> str:
        return " ".join(
            f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values)
        )

    ticks = 4
    gridlines = []
    for step in range(ticks + 1):
        value = top * step / ticks
        yy = y(value)
        gridlines.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" y2="{yy:.1f}" '
            f'class="grid"/>'
            f'<text x="{pad_l - 10}" y="{yy + 4:.1f}" class="tick tick-y">'
            f"{_money(value)}</text>"
        )

    xlabels = []
    for i, label in enumerate(labels):
        # Only the ends and every other point, so labels never collide.
        if i == 0 or i == len(labels) - 1 or len(labels) <= 5:
            xlabels.append(
                f'<text x="{x(i):.1f}" y="{height - pad_b + 20}" class="tick tick-x">'
                f"{_esc(label)}</text>"
            )

    points = []
    for i, label in enumerate(labels):
        points.append(
            f'<g class="hit" data-label="{_esc(label)}" '
            f'data-goal="{_money(goals[i])}" data-actual="{_money(actuals[i])}">'
            f'<rect x="{x(i) - 24:.1f}" y="{pad_t}" width="48" height="{plot_h}" '
            f'fill="transparent"/>'
            f'<circle cx="{x(i):.1f}" cy="{y(goals[i]):.1f}" r="4.5" class="dot dot-goal"/>'
            f'<circle cx="{x(i):.1f}" cy="{y(actuals[i]):.1f}" r="4.5" '
            f'class="dot dot-actual"/>'
            f"</g>"
        )

    return f"""
<figure class="chart">
  <figcaption>
    <h3>Cumulative pace</h3>
    <p class="sub">Spend to date against the pacing goal, week by week.</p>
  </figcaption>
  <div class="legend">
    <span class="key"><i class="swatch goal"></i>Pacing goal</span>
    <span class="key"><i class="swatch actual"></i>Actual spend</span>
  </div>
  <svg viewBox="0 0 {width} {height}" role="img"
       aria-label="Cumulative pacing goal against actual spend by week."
       preserveAspectRatio="xMidYMid meet">
    {''.join(gridlines)}
    <line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{width - pad_r}" y2="{pad_t + plot_h}"
          class="axis"/>
    <path d="{path(goals)}" class="line line-goal"/>
    <path d="{path(actuals)}" class="line line-actual"/>
    {''.join(xlabels)}
    {''.join(points)}
  </svg>
  <div class="tip" hidden></div>
</figure>
"""


# --- Sections --------------------------------------------------------------


def _latest_rows(report: PacingReport) -> list[PacingRow]:
    latest = report.current_week or max((r.week for r in report.rows), default=0)
    return sorted(report.rows_for_week(latest), key=lambda r: -r.pacing_goal)


def _tiles(report: PacingReport, rows: list[PacingRow]) -> str:
    goal = sum(r.pacing_goal for r in rows)
    actual = sum(r.actual_spend for r in rows)
    budget = sum(r.monthly_budget for r in rows)
    variance = actual - goal
    elapsed = rows[0].cumulative_pct if rows else 0.0
    over = variance >= 0
    role = "serious" if over else "warning"
    word = "over pace" if over else "under pace"

    return f"""
<section class="tiles">
  <div class="tile">
    <span class="label">Month elapsed</span>
    <span class="value">{elapsed:.0%}</span>
    <span class="foot">through {_esc(_short_date(report.as_of))}</span>
  </div>
  <div class="tile">
    <span class="label">Spend to date</span>
    <span class="value">{_money(actual)}</span>
    <span class="foot">of {_money(budget)} monthly budget</span>
  </div>
  <div class="tile">
    <span class="label">Pacing goal</span>
    <span class="value">{_money(goal)}</span>
    <span class="foot">where spend should be today</span>
  </div>
  <div class="tile">
    <span class="label">Variance</span>
    <span class="value status-{role}">{_money(variance)}</span>
    <span class="foot">{_esc(word)}</span>
  </div>
</section>
"""


def _pacing_table(rows: list[PacingRow]) -> str:
    body = []
    for row in rows:
        goal = row.pacing_goal
        actual = row.actual_spend
        scale = max(goal, actual) or 1.0
        status_role = "serious" if row.status == "Over" else "warning"
        flags = []
        if row.unbudgeted:
            flags.append('<span class="flag">no budget</span>')
        if row.manual:
            flags.append('<span class="flag">manual</span>')
        body.append(f"""
      <tr>
        <th scope="row">
          <span class="line">{_esc(row.category)} / {_esc(row.channel)}</span>
          {''.join(flags)}
        </th>
        <td class="num">{_money(row.monthly_budget)}</td>
        <td class="num">{_money(goal)}</td>
        <td class="num">{_money(actual)}</td>
        <td class="bar-cell">
          <span class="bar" title="Actual {_money(actual)} of goal {_money(goal)}">
            <i class="bar-goal" style="width:{100 * goal / scale:.1f}%"></i>
            <i class="bar-actual" style="width:{100 * actual / scale:.1f}%"></i>
          </span>
        </td>
        <td class="num status-{status_role}">{_money(row.variance)}</td>
        <td><span class="pill pill-{status_role}">{_esc(row.status)}</span></td>
      </tr>""")

    return f"""
<section class="panel">
  <h2>Pacing by line</h2>
  <p class="sub">Cumulative goal is the monthly budget times the share of the
     month elapsed, measured through the end of the last complete
     Monday-to-Sunday week, so goal and actual cover the same whole days.</p>
  <div class="scroll">
  <table>
    <thead>
      <tr>
        <th scope="col">Line</th>
        <th scope="col" class="num">Monthly budget</th>
        <th scope="col" class="num">Goal to date</th>
        <th scope="col" class="num">Actual</th>
        <th scope="col">Goal vs actual</th>
        <th scope="col" class="num">Variance</th>
        <th scope="col">Status</th>
      </tr>
    </thead>
    <tbody>{''.join(body)}
    </tbody>
  </table>
  </div>
</section>
"""


def _recommendation_cards(recommendations: list[Recommendation]) -> str:
    if not recommendations:
        return ""

    actionable = [r for r in recommendations if r.action != Action.HOLD]
    holds = [r for r in recommendations if r.action == Action.HOLD]

    cards = []
    for rec in actionable:
        role, glyph, verb = _ACTION_STYLE.get(rec.action, ("warning", "-", rec.action))
        urgent = '<span class="urgent">Urgent</span>' if rec.urgent else ""
        targeted = rec.action not in (Action.ALLOCATE_OR_PAUSE, Action.OVER_BUDGET)
        # The daily block and the facts row say the same thing as the sentence,
        # structurally. Keep the sentence only where there is no daily block.
        headline = "" if targeted else f'<p class="headline">{_esc(rec.headline)}</p>'
        daily = ""
        if targeted:
            change = rec.daily_change_pct
            delta = f" ({change:+.0%})" if change is not None else ""
            daily = f"""
      <div class="daily">
        <span class="from">{_money(rec.current_daily)}<small>/day now</small></span>
        <span class="arrow" aria-hidden="true">&rarr;</span>
        <span class="to">{_money(rec.suggested_daily)}<small>/day target</small></span>
        <span class="change">{_esc(delta.strip())}</span>
      </div>"""

        drivers = "".join(
            f"""
        <li>
          <span class="campaign">{_esc(d.campaign)}</span>
          <span class="share">{_money(d.spend)} &middot; {d.share_of_line:.0%} of line</span>
          {_trend(d.recent_rate_index)}
        </li>"""
            for d in rec.drivers
        )

        cards.append(f"""
    <article class="rec rec-{role}">
      <header>
        <span class="badge badge-{role}"><i aria-hidden="true">{glyph}</i>{_esc(verb)}</span>
        <h3>{_esc(rec.category)} / {_esc(rec.channel)}</h3>
        {urgent}
      </header>
      {headline}
      {daily}
      <dl class="facts">
        <div><dt>Spent</dt><dd>{_money(rec.spend_to_date)}</dd></div>
        <div><dt>Budget</dt><dd>{_money(rec.monthly_budget)}</dd></div>
        <div><dt>Projected</dt><dd>{_money(rec.projected_month_end)}</dd></div>
        <div><dt>Days left</dt><dd>{rec.days_remaining}</dd></div>
      </dl>
      <ul class="drivers">{drivers}</ul>
    </article>""")

    hold_list = ""
    if holds:
        items = "".join(
            f"<li><span class='line'>{_esc(r.category)} / {_esc(r.channel)}</span>"
            f"<span class='share'>projecting {_money(r.projected_month_end)} "
            f"vs {_money(r.monthly_budget)} ({r.projected_variance_pct:+.0%})</span></li>"
            for r in holds
        )
        hold_list = f"""
  <div class="holds">
    <h3>On pace, no change needed</h3>
    <ul>{items}</ul>
  </div>"""

    return f"""
<section class="panel">
  <h2>Budget recommendations</h2>
  <p class="sub">Month-end is projected from the current daily run rate. Lines
     are ordered by how much money is at stake.</p>
  <div class="recs">{''.join(cards)}</div>
  {hold_list}
</section>
"""


def _trend(index: float | None) -> str:
    """A driver's recent pace against its own month-to-date average."""
    if index is None:
        return ""
    if index >= 1.15:
        return f'<span class="trend up">accelerating {index:.2f}x</span>'
    if index <= 0.85:
        return f'<span class="trend down">slowing {index:.2f}x</span>'
    # Steady is the default; labelling it would put a chip on nearly every row.
    return ""


def _warnings(report: PacingReport) -> str:
    if not report.warnings:
        return ""
    items = "".join(f"<li>{_esc(w)}</li>" for w in report.warnings)
    return f"""
<section class="panel warn">
  <h2>Needs attention</h2>
  <ul class="warnings">{items}</ul>
</section>
"""


# --- Page ------------------------------------------------------------------


def render_dashboard(
    report: PacingReport,
    recommendations: list[Recommendation] | None = None,
    *,
    generated_at: date | None = None,
    source_note: str = "",
    standalone: bool = True,
) -> str:
    """Build the dashboard as one HTML string.

    ``standalone`` wraps the page in its own document skeleton, for a file you
    open directly. Published as an Artifact the platform supplies that
    skeleton, so pass ``standalone=False`` and emit just the title, styles and
    body -- a nested ``<html>`` would be dropped or duplicated.
    """
    recommendations = recommendations or []
    rows = _latest_rows(report)
    labels, goals, actuals = _cumulative_series(report)
    month = report.month_start.strftime("%B %Y")
    generated = generated_at or report.as_of

    table_rows = [
        {
            "line": f"{r.category} / {r.channel}",
            "budget": r.monthly_budget,
            "goal": round(r.pacing_goal, 2),
            "actual": round(r.actual_spend, 2),
            "variance": r.variance,
            "status": r.status,
        }
        for r in rows
    ]

    page = f"""<title>ExamFX Pacing</title>
<style>
:root {{
  color-scheme: light;
{_vars(_LIGHT)}
  --status-good: {_STATUS['good']};
  --status-warning: {_STATUS['warning']};
  --status-serious: {_STATUS['serious']};
  --status-critical: {_STATUS['critical']};
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
{_vars(_DARK)}
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
{_vars(_DARK)}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 0 16px 64px;
  background: var(--plane); color: var(--primary);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}}
.wrap {{ max-width: 1040px; margin: 0 auto; }}
header.page {{ padding: 32px 0 8px; }}
h1 {{ font-size: 1.6rem; margin: 0 0 4px; letter-spacing: -0.01em; }}
.meta {{ color: var(--secondary); font-size: 0.9rem; margin: 0; }}
.sub {{ color: var(--secondary); font-size: 0.875rem; margin: 2px 0 14px; }}
h2 {{ font-size: 1.05rem; margin: 0 0 2px; }}
h3 {{ font-size: 0.95rem; margin: 0; }}

.tiles {{ display: grid; gap: 12px; grid-template-columns: repeat(4, 1fr); margin: 20px 0; }}
.tile {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px; display: flex; flex-direction: column; gap: 2px;
}}
.tile .label {{ color: var(--secondary); font-size: 0.78rem; text-transform: uppercase;
  letter-spacing: 0.04em; }}
.tile .value {{ font-size: 1.7rem; font-weight: 600; letter-spacing: -0.02em; }}
.tile .foot {{ color: var(--muted); font-size: 0.78rem; }}

.panel {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px 20px; margin: 16px 0;
}}
.scroll {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--grid); }}
thead th {{ color: var(--secondary); font-weight: 600; font-size: 0.78rem;
  text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 1px solid var(--axis); }}
tbody tr:last-child th, tbody tr:last-child td {{ border-bottom: none; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.line {{ font-weight: 600; }}
.flag {{ display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 999px;
  font-size: 0.68rem; background: var(--grid); color: var(--secondary); }}

.bar-cell {{ width: 190px; min-width: 150px; }}
.bar {{ display: block; position: relative; height: 18px; }}
.bar i {{ position: absolute; left: 0; height: 7px; border-radius: 4px; }}
.bar-goal {{ top: 0; background: var(--series-goal); }}
.bar-actual {{ top: 11px; background: var(--series-actual); }}

.pill {{ padding: 2px 9px; border-radius: 999px; font-size: 0.76rem; font-weight: 600;
  border: 1px solid currentColor; }}
.pill-serious {{ color: var(--status-serious); }}
/* status-warning is sub-3:1 on the light surface, so light mode uses a darker
   step of the same hue. The pill's own label carries the meaning either way. */
.pill-warning {{ color: #8a6200; }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) .pill-warning {{ color: var(--status-warning); }}
  :root:not([data-theme="light"]) .badge-warning {{ color: var(--status-warning); }}
}}
:root[data-theme="dark"] .pill-warning {{ color: var(--status-warning); }}
:root[data-theme="dark"] .badge-warning {{ color: var(--status-warning); }}
.status-serious {{ color: var(--status-serious); }}
.status-warning {{ color: var(--secondary); }}
.status-critical {{ color: var(--status-critical); }}

.chart {{ margin: 0; }}
.chart svg {{ width: 100%; height: auto; overflow: visible; }}
.grid {{ stroke: var(--grid); stroke-width: 1; }}
.axis {{ stroke: var(--axis); stroke-width: 1; }}
.tick {{ fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }}
.tick-y {{ text-anchor: end; }}
.tick-x {{ text-anchor: middle; }}
.line {{ fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
.line-goal {{ stroke: var(--series-goal); }}
.line-actual {{ stroke: var(--series-actual); }}
.dot {{ stroke: var(--surface); stroke-width: 2; }}
.dot-goal {{ fill: var(--series-goal); }}
.dot-actual {{ fill: var(--series-actual); }}
.legend {{ display: flex; gap: 16px; margin: 6px 0 10px; font-size: 0.82rem;
  color: var(--secondary); }}
.key {{ display: inline-flex; align-items: center; gap: 6px; }}
.swatch {{ width: 12px; height: 3px; border-radius: 2px; display: inline-block; }}
.swatch.goal {{ background: var(--series-goal); }}
.swatch.actual {{ background: var(--series-actual); }}
.tip {{
  position: fixed; pointer-events: none; z-index: 10;
  background: var(--surface); border: 1px solid var(--axis); border-radius: 8px;
  padding: 8px 10px; font-size: 0.82rem; box-shadow: 0 4px 16px rgba(0,0,0,0.12);
}}
.tip .tip-line {{ display: flex; gap: 10px; justify-content: space-between; }}
.tip b {{ display: block; margin-bottom: 4px; }}
.hit {{ cursor: crosshair; }}

.recs {{ display: grid; gap: 12px; grid-template-columns: repeat(2, 1fr); }}
.rec {{ border: 1px solid var(--border); border-left: 3px solid var(--grid);
  border-radius: 8px; padding: 14px 16px; background: var(--plane); }}
.rec-serious {{ border-left-color: var(--status-serious); }}
.rec-warning {{ border-left-color: var(--status-warning); }}
.rec-critical {{ border-left-color: var(--status-critical); }}
.rec-good {{ border-left-color: var(--status-good); }}
.rec header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
  flex-wrap: wrap; }}
.badge {{ display: inline-flex; align-items: center; gap: 5px; padding: 2px 8px;
  border-radius: 999px; font-size: 0.74rem; font-weight: 700; letter-spacing: 0.02em;
  border: 1px solid currentColor; }}
.badge-serious {{ color: var(--status-serious); }}
.badge-warning {{ color: #8a6200; }}
.badge-critical {{ color: var(--status-critical); }}
.badge-good {{ color: var(--status-good); }}
.urgent {{ margin-left: auto; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--status-critical); }}
.headline {{ margin: 0 0 10px; font-size: 0.88rem; color: var(--secondary); }}
.daily {{ display: flex; align-items: baseline; gap: 8px; margin-bottom: 10px;
  flex-wrap: wrap; }}
.daily .from, .daily .to {{ font-size: 1.15rem; font-weight: 600;
  font-variant-numeric: tabular-nums; }}
.daily small {{ font-size: 0.7rem; font-weight: 400; color: var(--muted);
  margin-left: 3px; }}
.daily .arrow {{ color: var(--muted); }}
.daily .change {{ color: var(--secondary); font-size: 0.82rem; }}
.facts {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 0 0 10px; }}
.facts div {{ display: flex; flex-direction: column; }}
.facts dt {{ color: var(--muted); font-size: 0.7rem; text-transform: uppercase;
  letter-spacing: 0.04em; }}
.facts dd {{ margin: 0; font-size: 0.88rem; font-variant-numeric: tabular-nums; }}
.drivers {{ list-style: none; margin: 0; padding: 8px 0 0; border-top: 1px solid var(--grid); }}
.drivers li {{ display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline;
  padding: 3px 0; font-size: 0.8rem; }}
.campaign {{ flex: 1 1 60%; }}
.share {{ color: var(--secondary); font-variant-numeric: tabular-nums; }}
.trend {{ font-size: 0.72rem; padding: 0 6px; border-radius: 999px; background: var(--grid);
  color: var(--secondary); }}
.holds {{ margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--grid); }}
.holds ul {{ list-style: none; margin: 6px 0 0; padding: 0; }}
.holds li {{ display: flex; justify-content: space-between; gap: 12px; padding: 3px 0;
  font-size: 0.85rem; }}
.warnings {{ margin: 6px 0 0; padding-left: 18px; }}
.warnings li {{ margin: 4px 0; font-size: 0.88rem; }}
.warn {{ border-left: 3px solid var(--status-warning); }}
.empty {{ color: var(--muted); font-size: 0.88rem; }}
footer.page {{ color: var(--muted); font-size: 0.8rem; padding-top: 10px; }}
details.data {{ margin-top: 10px; }}
details.data summary {{ cursor: pointer; color: var(--secondary); font-size: 0.82rem; }}
details.data pre {{ overflow-x: auto; font-size: 0.75rem; color: var(--secondary); }}

@media (max-width: 860px) {{
  .tiles {{ grid-template-columns: repeat(2, 1fr); }}
  .recs {{ grid-template-columns: 1fr; }}
  .facts {{ grid-template-columns: repeat(2, 1fr); }}
}}
@media (prefers-reduced-motion: no-preference) {{
  .rec {{ transition: border-color .15s ease; }}
}}
</style>
<div class="wrap">
  <header class="page">
    <h1>ExamFX pacing &middot; {_esc(month)}</h1>
    <p class="meta">Through {_esc(_long_date(report.as_of))}
       &middot; generated {_esc(generated.isoformat())}{_esc(source_note)}</p>
  </header>

  {_tiles(report, rows)}
  <section class="panel">{_line_chart(labels, goals, actuals)}</section>
  {_pacing_table(rows)}
  {_recommendation_cards(recommendations)}
  {_warnings(report)}

  <footer class="page">
    <p>Spend from Windsor.ai; budgets from the ExamFX x HMDE budget tracker.
       Rebuilt every Monday. Figures are platform-reported spend, which can
       still settle for a day or two after the fact.</p>
    <details class="data">
      <summary>Table view (raw figures)</summary>
      <pre>{_esc(json.dumps(table_rows, indent=2))}</pre>
    </details>
  </footer>
</div>
<script>
(function () {{
  var tip = document.querySelector('.tip');
  if (!tip) return;
  document.querySelectorAll('.hit').forEach(function (hit) {{
    function show(e) {{
      tip.innerHTML = '<b>' + hit.dataset.label + '</b>' +
        '<span class="tip-line"><span>Goal</span><span>' + hit.dataset.goal + '</span></span>' +
        '<span class="tip-line"><span>Actual</span><span>' + hit.dataset.actual + '</span></span>';
      tip.hidden = false;
      var pt = e.touches ? e.touches[0] : e;
      tip.style.left = Math.min(pt.clientX + 14, window.innerWidth - 180) + 'px';
      tip.style.top = (pt.clientY + 14) + 'px';
    }}
    hit.addEventListener('mousemove', show);
    hit.addEventListener('touchstart', show, {{passive: true}});
    hit.addEventListener('mouseleave', function () {{ tip.hidden = true; }});
  }});
}})();
</script>
"""

    if not standalone:
        return page
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        + page.replace("</style>", "</style>\n</head>\n<body>", 1)
        + "\n</body>\n</html>\n"
    )
