"""Command line entry point: ``python -m examfx_pacing``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from .categories import CategoryMapper, load_rules
from .config import load_config
from .recommendations import RecommendationSettings
from .report import render_csv, render_recommendations, render_text
from .run import run_pacing
from .sheets import SheetsClient, SheetsError
from .spend import CsvSpendSource, WindsorError, WindsorSpendSource

log = logging.getLogger("examfx_pacing")


def _parse_month(text: str) -> tuple[int, int]:
    try:
        parsed = datetime.strptime(text, "%Y-%m")
    except ValueError:
        raise argparse.ArgumentTypeError(f"month must look like 2026-08, got {text!r}")
    return parsed.year, parsed.month


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"date must look like 2026-08-24, got {text!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="examfx-pacing",
        description="Rebuild the ExamFX weekly pacing table from live ad spend.",
    )
    parser.add_argument(
        "--month", type=_parse_month,
        help="Month to pace, as YYYY-MM. Defaults to the month containing --as-of.",
    )
    parser.add_argument(
        "--as-of", type=_parse_date,
        help="Treat this as today's date. Defaults to the actual current date.",
    )
    parser.add_argument(
        "--write", action="store_true",
        help="Write the rebuilt table back to the 'WoW Pacing' tab.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the table without touching the spreadsheet (default).",
    )
    parser.add_argument(
        "--csv", metavar="PATH",
        help="Also write the full pacing table to a CSV file.",
    )
    parser.add_argument(
        "--spend-csv", metavar="PATH",
        help="Read spend from a CSV instead of Windsor (for backfills and testing).",
    )
    parser.add_argument(
        "--budgets", metavar="PATH",
        help="Read budgets from a JSON file instead of the tracker tab.",
    )
    parser.add_argument(
        "--rules", metavar="PATH",
        help="Campaign-to-category rules as JSON. Defaults to the built-in rules.",
    )
    parser.add_argument(
        "--no-recommendations", action="store_true",
        help="Skip the budget recommendations section.",
    )
    parser.add_argument(
        "--tolerance", type=float, default=0.05,
        help="Projected variance before a budget change is recommended (default 0.05).",
    )
    parser.add_argument(
        "--spreadsheet-id",
        help="Override the target spreadsheet.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    as_of = args.as_of or date.today()
    year, month = args.month or (as_of.year, as_of.month)

    config = load_config(spreadsheet_id=args.spreadsheet_id)
    mapper = CategoryMapper(load_rules(args.rules) if args.rules else None)

    if args.spend_csv:
        spend_source = CsvSpendSource(args.spend_csv)
    else:
        try:
            spend_source = WindsorSpendSource(
                config.windsor_api_key or "", config.windsor_base_url
            )
        except WindsorError as exc:
            log.error("%s", exc)
            return 2

    # Reading budgets and notes needs the sheet; writing obviously does too.
    sheets = None
    if args.write or not args.budgets:
        sheets = SheetsClient(config.spreadsheet_id, config.google_credentials_file)

    try:
        result = run_pacing(
            year=year,
            month=month,
            as_of=as_of,
            spend_source=spend_source,
            config=config,
            mapper=mapper,
            sheets=sheets,
            budget_override=args.budgets,
            write=args.write and not args.dry_run,
            recommendation_settings=RecommendationSettings(tolerance=args.tolerance),
            write_recommendations=not args.no_recommendations,
        )
    except (WindsorError, SheetsError) as exc:
        log.error("%s", exc)
        return 1

    print(render_text(result.report))

    if not args.no_recommendations:
        print()
        print(render_recommendations(result.recommendations))

    if args.csv:
        Path(args.csv).write_text(render_csv(result.report), encoding="utf-8")
        log.info("wrote the full pacing table to %s", args.csv)

    if args.write and args.dry_run:
        log.warning("--dry-run was set, so nothing was written to the spreadsheet")
    elif args.write:
        log.info("updated %d rows in %r", result.rows_written, config.pacing_tab)
        if not args.no_recommendations:
            log.info(
                "updated %d rows in %r",
                result.recommendation_rows_written,
                config.recommendations_tab,
            )

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
