"""Orchestrate a full pacing run: budgets in, spend in, report out."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .categories import CategoryMapper
from .config import PacingConfig
from .pacing import PacingReport, build_report, cumulative_spend_by_week
from .recommendations import Recommendation, RecommendationSettings, build_recommendations
from .sheets import SheetsClient, parse_budgets, parse_notes
from .spend import CampaignSpend, SpendSource
from .weeks import build_weeks

log = logging.getLogger(__name__)

__all__ = ["RunResult", "run_pacing"]


@dataclass
class RunResult:
    report: PacingReport
    recommendations: list[Recommendation]
    daily_spend: list[CampaignSpend]
    rows_written: int = 0


def _load_budget_override(path: str | Path) -> dict[tuple[str, str], float]:
    """Read budgets from JSON: ``{"Insurance": {"Google": 40000}, ...}``."""
    data = json.loads(Path(path).read_text())
    budgets: dict[tuple[str, str], float] = {}
    for category, channels in data.items():
        for channel, amount in channels.items():
            budgets[(category, channel)] = float(amount)
    return budgets


def run_pacing(
    *,
    year: int,
    month: int,
    as_of: date,
    spend_source: SpendSource,
    config: PacingConfig,
    mapper: CategoryMapper | None = None,
    sheets: SheetsClient | None = None,
    budget_override: str | Path | None = None,
    write: bool = False,
    recommendation_settings: RecommendationSettings | None = None,
) -> RunResult:
    """Pull spend, rebuild the pacing table, and optionally write it back."""
    mapper = mapper or CategoryMapper()
    weeks = build_weeks(year, month)
    month_start = weeks[0].month_start
    month_end = weeks[-1].month_end
    through = min(as_of, month_end)

    if budget_override:
        budgets = _load_budget_override(budget_override)
        notes: dict[tuple[int, str, str], str] = {}
        log.info("using budget override from %s", budget_override)
    else:
        if sheets is None:
            raise ValueError("either a SheetsClient or a budget override is required")
        budgets = parse_budgets(sheets.read(config.tracker_tab), year, month)
        notes = parse_notes(sheets.read(config.pacing_tab))
        log.info("loaded %d budget lines and %d notes from the tracker", len(budgets), len(notes))

    fetch_daily = getattr(spend_source, "fetch_daily", spend_source.fetch)
    daily_spend = list(fetch_daily(config.channels, month_start, through))
    log.info("fetched %d campaign/day spend rows", len(daily_spend))

    spend_by_week, unmapped = cumulative_spend_by_week(daily_spend, weeks, through, mapper)

    report = build_report(
        year=year,
        month=month,
        as_of=through,
        budgets=budgets,
        spend_by_week=spend_by_week,
        unmapped=unmapped,
        existing_notes=notes,
        config=config,
    )

    recommendations = build_recommendations(
        report, daily_spend, mapper, recommendation_settings
    )

    rows_written = 0
    if write:
        if sheets is None:
            raise ValueError("writing requires a SheetsClient")
        rows_written = sheets.write_pacing(config.pacing_tab, report)

    return RunResult(
        report=report,
        recommendations=recommendations,
        daily_spend=daily_spend,
        rows_written=rows_written,
    )
