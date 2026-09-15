"""Which channels are pulled, and which are left alone."""

from __future__ import annotations

from examfx_pacing.config import DEFAULT_CHANNELS, MANUAL_CHANNELS, load_config


def _pulled():
    return [source.channel for source in DEFAULT_CHANNELS]


def test_linkedin_is_not_pulled():
    """LinkedIn is paused, so there is nothing to fetch for it."""
    assert "LinkedIn" not in _pulled()


def test_linkedin_is_flagged_rather_than_forgotten():
    """If it is funded again without being re-enabled, it must not go silent."""
    assert "LinkedIn" in MANUAL_CHANNELS


def test_the_pulled_channels_are_the_live_ones():
    assert _pulled() == ["Google", "Bing", "Meta"]


def test_nothing_is_both_pulled_and_manual():
    """A channel cannot be fetched and preserved-as-is at the same time."""
    assert not set(_pulled()) & set(MANUAL_CHANNELS)


def test_every_pulled_channel_has_a_connector_and_account():
    for source in DEFAULT_CHANNELS:
        assert source.connector, f"{source.channel} has no connector"
        assert source.account, f"{source.channel} has no account"


def test_config_carries_the_channel_lists():
    config = load_config()
    assert config.channels == DEFAULT_CHANNELS
    assert config.manual_channels == MANUAL_CHANNELS
