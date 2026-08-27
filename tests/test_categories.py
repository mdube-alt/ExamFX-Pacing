"""Campaign-name mapping, including the cases that are easy to get wrong."""

from __future__ import annotations

import json

import pytest

from examfx_pacing.categories import (
    UNMAPPED,
    CategoryMapper,
    CategoryRule,
    load_rules,
)


@pytest.mark.parametrize(
    "campaign,expected",
    [
        # A business line that contains the word "Brand" is NOT the Brand line.
        ("B2C - Insurance - Life & Health - Brand - PPC", "Insurance"),
        ("B2C - Securities - Brand - PPC", "Securities"),
        ("B2C - Adjusters - Brand - PPC", "Adjusters"),
        # The standalone brand campaign is.
        ("B2C - ExamFX - Brand - PPC", "Brand"),
        # Non-Brand and Performance Max variants.
        ("B2C - Insurance - Property & Casualty - Non Brand - PPC", "Insurance"),
        ("B2C - Insurance - Performance Max (OTM)", "Insurance"),
        ("B2C - YouTube - Insurance Remarketing", "Insurance"),
        ("B2C - Securities - Non-Brand - High Priority - PPC", "Securities"),
        # Meta uses underscores and a different word order.
        ("B2C_General_Insurance_Prospecting_Meta_LAL", "Insurance"),
        ("B2C_Exam FX_Securities_Meta_Retargeting_2025", "Securities"),
        # Case should not matter.
        ("b2c - insurance - non brand - ppc", "Insurance"),
    ],
)
def test_known_campaign_names(campaign, expected):
    assert CategoryMapper().category_for(campaign) == expected


@pytest.mark.parametrize("campaign", ["", "   ", "Untitled campaign", "Q4 Test"])
def test_unrecognised_names_are_flagged_not_guessed(campaign):
    assert CategoryMapper().category_for(campaign) == UNMAPPED


def test_rule_order_decides_ties():
    """A campaign naming two lines resolves to whichever rule comes first."""
    mapper = CategoryMapper()
    assert mapper.category_for("Insurance and Securities combined") == "Securities"

    reordered = CategoryMapper(
        [CategoryRule("Insurance", r"insurance"), CategoryRule("Securities", r"securit")]
    )
    assert reordered.category_for("Insurance and Securities combined") == "Insurance"


def test_categories_are_listed_in_rule_order():
    assert CategoryMapper().categories() == ["Adjusters", "Securities", "Insurance", "Brand"]


def test_rules_load_from_json(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([{"category": "CE", "pattern": "continuing.?ed"}]))
    mapper = CategoryMapper(load_rules(path))
    assert mapper.category_for("B2C - Continuing Ed - PPC") == "CE"
    assert mapper.category_for("B2C - Insurance - PPC") == UNMAPPED


def test_malformed_rules_are_rejected_clearly(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"category": "CE"}]))
    with pytest.raises(ValueError, match="needs 'category' and 'pattern'"):
        load_rules(path)
