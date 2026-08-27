"""Account, channel and sheet configuration for the ExamFX pacing run."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

__all__ = [
    "ChannelSource",
    "PacingConfig",
    "DEFAULT_CHANNELS",
    "MANUAL_CHANNELS",
    "SPREADSHEET_ID",
    "RECOMMENDATIONS_TAB",
    "load_config",
]

#: ExamFX x HMDE - Budget Tracker
SPREADSHEET_ID = "1CqEBATyQEzti8CKyknlyb9Iq_rwlcWUuI3DX_KayD4U"

#: Tab holding the weekly pacing table this tool maintains.
PACING_TAB = "WoW Pacing"

#: Tab holding per-month, per-category/channel budgets.
TRACKER_TAB = "2026 Monthly Tracker"

#: Tab the budget recommendations are written to. Created if it does not exist.
RECOMMENDATIONS_TAB = "Budget Recommendations"


@dataclass(frozen=True)
class ChannelSource:
    """A tracker channel and the Windsor connector/account backing it."""

    channel: str
    connector: str
    account: str
    #: Windsor field holding the campaign name (uniform across our connectors).
    campaign_field: str = "campaign"
    #: Windsor field holding spend (uniform across our connectors).
    spend_field: str = "spend"


#: Channels pulled automatically. Account IDs come from the HMDE client registry.
DEFAULT_CHANNELS: tuple[ChannelSource, ...] = (
    ChannelSource("Google", "google_ads", "997-052-9086"),
    ChannelSource("Bing", "bing", "180013684"),
    ChannelSource("Meta", "facebook", "253084931845072"),
    ChannelSource("LinkedIn", "linkedin", "518468129"),
)

#: Channels that exist in the tracker but have no Windsor connector. Rows for
#: these are preserved as-is rather than overwritten with zero.
MANUAL_CHANNELS: tuple[str, ...] = ("Programmatic",)


@dataclass
class PacingConfig:
    """Everything a pacing run needs."""

    spreadsheet_id: str = SPREADSHEET_ID
    pacing_tab: str = PACING_TAB
    tracker_tab: str = TRACKER_TAB
    recommendations_tab: str = RECOMMENDATIONS_TAB
    channels: tuple[ChannelSource, ...] = DEFAULT_CHANNELS
    manual_channels: tuple[str, ...] = MANUAL_CHANNELS
    windsor_api_key: str | None = None
    windsor_base_url: str = "https://connectors.windsor.ai"
    google_credentials_file: str | None = None
    #: Spend below this is treated as zero when deciding whether to emit a row.
    spend_epsilon: float = 0.005
    extra: dict = field(default_factory=dict)


def load_config(**overrides) -> PacingConfig:
    """Build config from environment variables, then apply explicit overrides."""
    cfg = PacingConfig(
        spreadsheet_id=os.environ.get("EXAMFX_SPREADSHEET_ID", SPREADSHEET_ID),
        pacing_tab=os.environ.get("EXAMFX_PACING_TAB", PACING_TAB),
        tracker_tab=os.environ.get("EXAMFX_TRACKER_TAB", TRACKER_TAB),
        recommendations_tab=os.environ.get(
            "EXAMFX_RECOMMENDATIONS_TAB", RECOMMENDATIONS_TAB
        ),
        windsor_api_key=os.environ.get("WINDSOR_API_KEY"),
        windsor_base_url=os.environ.get(
            "WINDSOR_BASE_URL", "https://connectors.windsor.ai"
        ),
        google_credentials_file=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
    )
    for key, value in overrides.items():
        if value is not None and hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg
