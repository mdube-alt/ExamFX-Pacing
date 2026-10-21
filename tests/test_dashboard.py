"""The HTML dashboard: content, escaping, and the accessibility rules."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

import pytest

from examfx_pacing.categories import CategoryMapper
from examfx_pacing.config import PacingConfig
from examfx_pacing.dashboard import render_dashboard
from examfx_pacing.pacing import build_report, cumulative_spend_by_week
from examfx_pacing.recommendations import build_recommendations
from examfx_pacing.spend import CampaignSpend
from examfx_pacing.weeks import build_weeks

MAPPER = CategoryMapper()
BUDGETS = {("Insurance", "Google"): 48000.0, ("Securities", "Google"): 4500.0}


def _report(as_of=date(2026, 10, 12), spend=None):
    spend = spend if spend is not None else [
        CampaignSpend("Google", "B2C - Insurance - Brand - PPC", 400.0, date(2026, 10, d))
        for d in range(1, 13)
    ]
    weeks = build_weeks(2026, 10)
    by_week, unmapped = cumulative_spend_by_week(spend, weeks, as_of, MAPPER)
    return build_report(
        year=2026, month=10, as_of=as_of, budgets=BUDGETS,
        spend_by_week=by_week, unmapped=unmapped, config=PacingConfig(),
    ), spend


def _render(as_of=date(2026, 10, 12), spend=None):
    report, daily = _report(as_of, spend)
    recs = build_recommendations(report, daily, MAPPER)
    return render_dashboard(report, recs)


def test_the_page_is_self_contained():
    """It is published as a static file; a CDN it cannot reach breaks it."""
    html = _render()
    assert "<!doctype html>" in html.lower()
    for bad in ("http://", "src=\"//", "cdn."):
        assert bad not in html, f"external reference {bad!r} in a static page"
    # The only https: allowed is inside prose, not a fetched resource.
    assert "<script src=" not in html
    assert "<link rel=\"stylesheet\"" not in html


def test_the_headline_figures_are_present():
    html = _render()
    assert "ExamFX pacing" in html
    assert "October 2026" in html
    assert "Insurance / Google" in html


def test_campaign_names_are_escaped():
    """Campaign names come from the ad platforms, so treat them as hostile."""
    spend = [
        CampaignSpend(
            "Google", "B2C - Insurance - <script>alert(1)</script> & co", 500.0,
            date(2026, 10, d),
        )
        for d in range(1, 13)
    ]
    html = _render(spend=spend)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_status_is_never_colour_alone():
    """Every status pill carries its word, not just a colour."""
    html = _render()
    pills = re.findall(r'<span class="pill pill-\w+">([^<]+)</span>', html)
    assert pills, "no status pills rendered"
    assert all(p.strip() in {"Over", "Under"} for p in pills)


def test_dark_mode_is_defined_under_both_scopes():
    """The OS setting and an explicit theme stamp must both work."""
    html = _render()
    assert "@media (prefers-color-scheme: dark)" in html
    assert ':root[data-theme="dark"]' in html
    assert ':root:not([data-theme="light"])' in html


def test_two_series_get_a_legend():
    html = _render()
    assert "Pacing goal" in html and "Actual spend" in html
    assert 'class="legend"' in html


def test_a_single_day_explains_itself_instead_of_drawing_a_line():
    """A one-point line chart is meaningless; say so rather than draw it."""
    spend = [CampaignSpend("Google", "B2C - Insurance - Brand - PPC", 100.0,
                           date(2026, 10, 1))]
    html = _render(as_of=date(2026, 10, 1), spend=spend)
    assert "Not enough days yet" in html
    assert "<path" not in html
    # The heading stays, so the panel reads as pending rather than broken.
    assert "Cumulative pace" in html


def test_a_steady_driver_gets_no_chip_but_a_moving_one_does():
    """Steady is the default state; chipping it puts noise on every row."""
    steady = [
        CampaignSpend("Google", "B2C - Insurance - Brand - PPC", 400.0, date(2026, 10, d))
        for d in range(1, 13)
    ]
    assert "steady" not in _render(spend=steady)

    # Front-loaded: the last 7 days run well below the month's average.
    slowing = [
        CampaignSpend("Google", "B2C - Insurance - Brand - PPC",
                      1000.0 if d <= 5 else 10.0, date(2026, 10, d))
        for d in range(1, 13)
    ]
    assert "slowing" in _render(spend=slowing)


def test_the_raw_figures_are_available_as_a_table_view():
    """The colour rules require a table view to fall back on."""
    html = _render()
    assert "Table view" in html
    # The block is escaped before it goes into the <pre>, so it reads escaped.
    assert "&quot;status&quot;:" in html
    assert "&quot;variance&quot;:" in html


def test_warnings_reach_the_page():
    report, daily = _report()
    report.manual_budgets = {("Insurance", "Programmatic"): 6000.0}
    html = render_dashboard(report, build_recommendations(report, daily, MAPPER))
    assert "Needs attention" in html
    assert "Programmatic" in html


def test_an_empty_recommendation_list_drops_the_section():
    report, _ = _report()
    html = render_dashboard(report, [])
    assert "Budget recommendations" not in html
    assert "Pacing by line" in html


def test_the_artifact_form_omits_the_document_skeleton():
    """Published as an Artifact the platform supplies html/head/body; a nested
    one would be dropped or duplicated."""
    report, daily = _report()
    recs = build_recommendations(report, daily, MAPPER)
    fragment = render_dashboard(report, recs, standalone=False)

    for tag in ("<!doctype", "<html", "</head>", "<body>", "</html>"):
        assert tag not in fragment.lower()
    assert fragment.lstrip().startswith("<title>")
    assert "<style>" in fragment


def test_the_standalone_form_is_a_whole_document():
    report, daily = _report()
    html = render_dashboard(report, build_recommendations(report, daily, MAPPER))
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "</html>" in html
    assert html.count("<body>") == 1
    assert "<meta name=\"viewport\"" in html


def test_both_forms_carry_the_same_figures():
    report, daily = _report()
    recs = build_recommendations(report, daily, MAPPER)
    whole = render_dashboard(report, recs)
    fragment = render_dashboard(report, recs, standalone=False)
    assert "Pacing by line" in fragment
    # The wrapper injects </head><body> after the styles, so compare the part
    # below that seam rather than the whole fragment.
    body = fragment.split("</style>", 1)[1]
    assert body in whole
    for figure in ("Insurance / Google", "Pacing by line", "Cumulative pace"):
        assert figure in whole and figure in fragment


# --- The header states its window and its run time --------------------------


def test_the_header_names_the_window_and_the_run_time():
    """Two different dates that were previously conflated into one line."""
    report, daily = _report()
    html = render_dashboard(
        report,
        build_recommendations(report, daily, MAPPER),
        generated_at=datetime(2026, 10, 19, 13, 4, tzinfo=timezone.utc),
    )
    assert "Showing data for" in html
    assert "Last updated" in html
    assert "19 October 2026, 13:04 UTC" in html
    # The window starts at the 1st, not just "through" the end date.
    assert "1 - 12 October 2026" in html


def test_the_run_time_is_not_the_data_date():
    """It used to echo as_of, which made a stale page look freshly built."""
    report, daily = _report(as_of=date(2026, 10, 12))
    html = render_dashboard(
        report,
        build_recommendations(report, daily, MAPPER),
        generated_at=datetime(2026, 11, 2, 9, 30, tzinfo=timezone.utc),
    )
    assert "2 November 2026, 09:30 UTC" in html
    assert "1 - 12 October 2026" in html


def test_a_naive_date_is_still_accepted_as_a_run_time():
    report, daily = _report()
    html = render_dashboard(report, build_recommendations(report, daily, MAPPER),
                            generated_at=date(2026, 10, 19))
    assert "19 October 2026, 00:00 UTC" in html


def test_the_window_says_how_many_weeks_it_covers():
    report, daily = _report(as_of=date(2026, 10, 4))
    html = render_dashboard(report, build_recommendations(report, daily, MAPPER))
    assert "week 1" in html

    report, daily = _report(as_of=date(2026, 10, 18))
    html = render_dashboard(report, build_recommendations(report, daily, MAPPER))
    assert "weeks 1-3" in html


def test_a_range_inside_one_month_does_not_repeat_the_month():
    from examfx_pacing.dashboard import _date_range
    assert _date_range(date(2026, 10, 1), date(2026, 10, 4)) == "1 - 4 October 2026"
    assert _date_range(date(2026, 9, 28), date(2026, 10, 4)) == (
        "28 September - 4 October 2026"
    )
    assert _date_range(date(2026, 10, 4), date(2026, 10, 4)) == "Sunday 4 October 2026"


# --- The chart runs day by day ---------------------------------------------


def _daily(as_of=date(2026, 10, 11)):
    spend = [
        CampaignSpend("Google", "B2C - Insurance - Brand - PPC", 400.0, date(2026, 10, d))
        for d in range(1, as_of.day + 1)
    ]
    report, _ = _report(as_of, spend)
    return report, spend


def test_the_chart_plots_one_point_per_day():
    from examfx_pacing.dashboard import _daily_series
    report, spend = _daily()
    labels, goals, actuals = _daily_series(report, spend, MAPPER)

    assert len(labels) == 11, "1 - 11 October is eleven points, not two weeks"
    assert labels[0] == "10/1" and labels[-1] == "10/11"


def test_the_daily_series_is_cumulative():
    from examfx_pacing.dashboard import _daily_series
    report, spend = _daily()
    _, goals, actuals = _daily_series(report, spend, MAPPER)

    assert actuals == sorted(actuals), "cumulative spend never falls"
    assert goals == sorted(goals)
    assert actuals[0] == 400.0 and actuals[-1] == 4400.0


def test_the_last_point_matches_the_tiles():
    """Chart and stat tiles must not disagree about the same two numbers."""
    from examfx_pacing.dashboard import _daily_series, _latest_rows
    report, spend = _daily()
    _, goals, actuals = _daily_series(report, spend, MAPPER)
    rows = _latest_rows(report)

    assert actuals[-1] == pytest.approx(sum(r.actual_spend for r in rows), abs=0.01)
    assert goals[-1] == pytest.approx(sum(r.pacing_goal for r in rows), abs=0.01)


def test_spend_outside_the_window_is_not_counted():
    report, spend = _daily()
    from examfx_pacing.dashboard import _daily_series
    spend = spend + [
        CampaignSpend("Google", "B2C - Insurance - Brand - PPC", 9999.0,
                      date(2026, 10, 30)),
    ]
    _, _, actuals = _daily_series(report, spend, MAPPER)
    assert actuals[-1] == 4400.0, "a later day must not leak into the window"


def test_undated_spend_falls_back_to_weeks():
    """A CSV without a date column still gets a chart, just a coarser one."""
    from examfx_pacing.dashboard import _daily_series
    report, _ = _report()
    undated = [CampaignSpend("Google", "B2C - Insurance - Brand - PPC", 100.0)]
    assert _daily_series(report, undated, MAPPER) is None

    html = render_dashboard(report, [], daily_spend=undated, mapper=MAPPER)
    assert "Cumulative pace" in html


def test_only_the_latest_point_is_marked():
    """A dot a day would be 31 marks by month end."""
    report, spend = _daily()
    html = render_dashboard(report, [], daily_spend=spend, mapper=MAPPER)
    # Two permanent endpoint dots; the rest appear on hover only.
    assert html.count('class="dot dot-goal"/>') == 1
    assert html.count('class="dot dot-actual"/>') == 1
    assert "hover-dot" in html and "crosshair" in html


def test_the_x_axis_does_not_label_every_day():
    report, spend = _daily()
    html = render_dashboard(report, [], daily_spend=spend, mapper=MAPPER)
    labelled = re.findall(r'class="tick tick-x">([^<]+)<', html)
    assert 2 <= len(labelled) <= 7, labelled
    assert labelled[0] == "10/1" and labelled[-1] == "10/11"


# --- The budget-spent tile --------------------------------------------------


def _tiles_html(as_of=date(2026, 10, 18), spend=None):
    from examfx_pacing.dashboard import _latest_rows, _tiles
    report, _ = _report(as_of, spend)
    return _tiles(report, _latest_rows(report))


def test_the_tile_reports_budget_spent_against_the_elapsed_target():
    """The fixture spends 400/day for 12 days against a 52,500 budget."""
    html = _tiles_html()
    assert "Budget spent" in html
    # 4,800 / 52,500 = 9% spent, against 18/31 = 58% of the month elapsed.
    assert ">9%<" in html
    assert "58% target" in html


def test_the_gap_is_stated_in_points():
    html = _tiles_html()
    assert "points behind the 58% target" in html


def test_ahead_and_level_read_correctly():
    from examfx_pacing.dashboard import _tiles
    rows = _latest_rows_for(date(2026, 10, 18))

    # Spend exactly on target.
    for row in rows:
        row.actual_spend = row.monthly_budget * row.cumulative_pct
    assert "level with the 58% target" in _tiles(_REPORT, rows)

    # Spend well ahead of it.
    for row in rows:
        row.actual_spend = row.monthly_budget * 0.80
    assert "points ahead of the 58% target" in _tiles(_REPORT, rows)


_REPORT = None


def _latest_rows_for(as_of):
    from examfx_pacing.dashboard import _latest_rows
    global _REPORT
    _REPORT, _ = _report(as_of)
    return _latest_rows(_REPORT)


def test_the_meter_fills_to_spend_and_ticks_at_the_target():
    html = _tiles_html()
    fill = re.search(r'meter-fill" style="width:([\d.]+)%', html)
    tick = re.search(r'meter-target" style="left:([\d.]+)%', html)
    assert fill and tick
    assert float(fill.group(1)) == pytest.approx(4800 / 52500 * 100, abs=0.1)
    assert float(tick.group(1)) == pytest.approx(18 / 31 * 100, abs=0.1)


def test_overspending_clamps_the_bar_but_not_the_number():
    """A bar wider than its track would overflow the tile."""
    from examfx_pacing.dashboard import _tiles
    rows = _latest_rows_for(date(2026, 10, 18))
    for row in rows:
        row.actual_spend = row.monthly_budget * 1.4
    html = _tiles(_REPORT, rows)

    assert ">140%<" in html, "the figure still tells the truth"
    fill = re.search(r'meter-fill" style="width:([\d.]+)%', html)
    assert float(fill.group(1)) == 100.0, "the bar stops at the track"


def test_no_budget_does_not_divide_by_zero():
    from examfx_pacing.dashboard import _tiles
    rows = _latest_rows_for(date(2026, 10, 18))
    for row in rows:
        row.monthly_budget = 0.0
    html = _tiles(_REPORT, rows)
    assert "no budget on the lines shown" in html
    assert ">0%<" in html


def test_the_meter_is_described_for_screen_readers():
    html = _tiles_html()
    assert 'role="img"' in html
    assert "aria-label=" in html
