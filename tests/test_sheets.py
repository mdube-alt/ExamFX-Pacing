"""Reading budgets and notes out of the real tracker layout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from examfx_pacing.pacing import HEADERS
from examfx_pacing.sheets import SheetsError, parse_budgets, parse_notes

TRACKER = Path("tests/fixtures/tracker_tab_values.json")


@pytest.fixture
def tracker_values():
    """A real capture of the '2026 Monthly Tracker' tab."""
    return json.loads(TRACKER.read_text())


def test_august_budgets_match_the_tracker(tracker_values):
    budgets = parse_budgets(tracker_values, 2026, 8)
    assert budgets[("Insurance", "Google")] == 40000.0
    assert budgets[("Insurance", "Bing")] == 6500.0
    assert budgets[("Insurance", "Meta")] == 4000.0
    assert budgets[("Insurance", "Programmatic")] == 2500.0
    assert budgets[("Securities", "Google")] == 4000.0
    assert budgets[("Securities", "Bing")] == 800.0
    assert budgets[("Adjusters", "Google")] == 3000.0
    assert budgets[("Adjusters", "Bing")] == 1000.0
    assert budgets[("Brand", "Google")] == 0.0


def test_a_different_month_reads_a_different_column(tracker_values):
    """The month column is located by its header, not by a fixed letter."""
    july = parse_budgets(tracker_values, 2026, 7)
    assert july[("Insurance", "Google")] == 52000.0
    assert july[("Securities", "Meta")] == 2000.0
    assert july[("Brand", "Google")] == 1000.0
    assert july != parse_budgets(tracker_values, 2026, 8)


def test_subtotal_rows_are_not_mistaken_for_channels(tracker_values):
    budgets = parse_budgets(tracker_values, 2026, 8)
    channels = {channel for _, channel in budgets}
    assert not channels & {"", "Paid Search Total", "Paid Social Total", "Total"}


def test_a_missing_month_fails_loudly(tracker_values):
    with pytest.raises(SheetsError, match="could not find a Budget column"):
        parse_budgets(tracker_values, 2031, 4)


def test_notes_are_recovered_by_week_and_line():
    values = [
        HEADERS,
        ["Week 4", "8/17 - 8/23", "Insurance", "Meta", "", "", "", "", "", "", "", "Under",
         "Budget increased"],
        ["Week 4", "8/17 - 8/23", "Securities", "Google", "", "", "", "", "", "", "", "Under",
         "Decreased tROAS slightly -Maddi"],
        ["Week 4", "8/17 - 8/23", "Adjusters", "Bing", "", "", "", "", "", "", "", "Over", ""],
    ]
    notes = parse_notes(values)
    assert notes[(4, "Insurance", "Meta")] == "Budget increased"
    assert notes[(4, "Securities", "Google")] == "Decreased tROAS slightly -Maddi"
    assert (4, "Adjusters", "Bing") not in notes, "blank notes are not stored"


def test_notes_survive_short_rows():
    """Sheets omits trailing empty cells; that must not raise."""
    values = [HEADERS, ["Week 1", "8/1 - 8/2", "Insurance", "Google"]]
    assert parse_notes(values) == {}


def test_notes_on_an_empty_tab():
    assert parse_notes([]) == {}
