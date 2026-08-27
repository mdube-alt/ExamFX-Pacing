"""Map ad-platform campaign names onto tracker categories.

The tracker groups spend into business lines (Insurance, Securities,
Adjusters, Brand). Campaign naming differs by platform -- Google/Bing use
``B2C - Insurance - Life & Health - Non Brand - PPC`` while Meta uses
``B2C_General_Insurance_Prospecting_Meta_LAL`` -- so matching is done on
case-insensitive keywords rather than on a strict naming convention.

Rules are ordered and first-match-wins. "Brand" is deliberately last: a
campaign such as ``B2C - Insurance - Life & Health - Brand - PPC`` is
*Insurance* spend (brand terms within the Insurance line), whereas
``B2C - ExamFX - Brand - PPC`` is the standalone Brand line.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "UNMAPPED",
    "CategoryRule",
    "CategoryMapper",
    "DEFAULT_RULES",
    "load_rules",
]

#: Category assigned when no rule matches. Surfaced rather than silently dropped.
UNMAPPED = "Unmapped"


@dataclass(frozen=True)
class CategoryRule:
    """A single ordered match rule."""

    category: str
    pattern: str

    def matches(self, campaign: str) -> bool:
        return re.search(self.pattern, campaign, re.IGNORECASE) is not None


#: Ordered defaults, validated against live ExamFX campaign names.
DEFAULT_RULES: tuple[CategoryRule, ...] = (
    CategoryRule("Adjusters", r"adjuster"),
    CategoryRule("Securities", r"securit"),
    CategoryRule("Insurance", r"insurance"),
    # Standalone brand line: only reached when no business line matched.
    CategoryRule("Brand", r"brand"),
)


class CategoryMapper:
    """Resolve campaign names to categories using ordered rules."""

    def __init__(self, rules: tuple[CategoryRule, ...] | list[CategoryRule] | None = None):
        self.rules = tuple(rules) if rules is not None else DEFAULT_RULES

    def category_for(self, campaign: str) -> str:
        campaign = (campaign or "").strip()
        if not campaign:
            return UNMAPPED
        for rule in self.rules:
            if rule.matches(campaign):
                return rule.category
        return UNMAPPED

    def categories(self) -> list[str]:
        """Distinct categories in rule order."""
        seen: list[str] = []
        for rule in self.rules:
            if rule.category not in seen:
                seen.append(rule.category)
        return seen


def load_rules(path: str | Path) -> tuple[CategoryRule, ...]:
    """Load ordered rules from JSON: ``[{"category": ..., "pattern": ...}, ...]``."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of rules")
    rules = []
    for i, entry in enumerate(data):
        try:
            rules.append(CategoryRule(entry["category"], entry["pattern"]))
        except (TypeError, KeyError) as exc:
            raise ValueError(f"{path}: rule {i} needs 'category' and 'pattern'") from exc
    return tuple(rules)
