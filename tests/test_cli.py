"""End-to-end CLI behaviour against the checked-in fixtures."""

from __future__ import annotations

import csv
import json

import pytest

from examfx_pacing.cli import main

BUDGETS = {
    "Insurance": {"Google": 40000, "Bing": 6500, "Meta": 4000, "Programmatic": 2500},
    "Securities": {"Google": 4000, "Bing": 800, "Meta": 0},
    "Adjusters": {"Google": 3000, "Bing": 1000},
    "Brand": {"Google": 0, "Bing": 0},
}


@pytest.fixture
def budget_file(tmp_path):
    path = tmp_path / "budgets.json"
    path.write_text(json.dumps(BUDGETS))
    return str(path)


def _run(args, capsys):
    code = main(args)
    return code, capsys.readouterr().out


def test_dry_run_prints_pacing_and_recommendations(budget_file, capsys):
    code, out = _run(
        ["--month", "2026-08", "--as-of", "2026-08-23",
         "--spend-csv", "tests/fixtures/2026-08_week4_mtd.csv", "--budgets", budget_file],
        capsys,
    )
    assert code == 0
    assert "ExamFX pacing - August 2026, Week 4" in out
    assert "Budget recommendations" in out
    # The week-4 Insurance/Google figure from the tracker.
    assert "35,331" in out


def test_recommendations_can_be_suppressed(budget_file, capsys):
    _, out = _run(
        ["--month", "2026-08", "--as-of", "2026-08-23", "--no-recommendations",
         "--spend-csv", "tests/fixtures/2026-08_week4_mtd.csv", "--budgets", budget_file],
        capsys,
    )
    assert "Budget recommendations" not in out


def test_csv_export_has_a_row_per_line_and_week(budget_file, tmp_path, capsys):
    out_path = tmp_path / "pacing.csv"
    _run(
        ["--month", "2026-08", "--as-of", "2026-08-23", "--csv", str(out_path),
         "--spend-csv", "tests/fixtures/2026-08_week4_mtd.csv", "--budgets", budget_file],
        capsys,
    )
    rows = list(csv.DictReader(out_path.open()))
    assert rows, "the export should not be empty"
    assert {"Week", "Category", "Channel", "Pacing Goal (To-Date)", "Status"} <= set(rows[0])
    assert {row["Week"] for row in rows} == {f"Week {n}" for n in range(1, 5)}
    assert "Programmatic" not in {row["Channel"] for row in rows}


def test_a_bad_month_is_rejected(capsys):
    with pytest.raises(SystemExit):
        main(["--month", "August"])


def test_missing_windsor_key_fails_cleanly(monkeypatch, budget_file, capsys):
    monkeypatch.delenv("WINDSOR_API_KEY", raising=False)
    assert main(["--month", "2026-08", "--as-of", "2026-08-23", "--budgets", budget_file]) == 2
