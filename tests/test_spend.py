"""Spend fetching: response parsing, retries and failure reporting."""

from __future__ import annotations

from datetime import date

import urllib.error

import pytest

from examfx_pacing.config import ChannelSource
from examfx_pacing.spend import (
    CsvSpendSource,
    WindsorError,
    WindsorSpendSource,
    _coerce_spend,
    _extract_rows,
)

CHANNEL = ChannelSource("Google", "google_ads", "997-052-9086")


@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"campaign": "A", "spend": 1}]},
        {"result": [{"campaign": "A", "spend": 1}]},
        {"rows": [{"campaign": "A", "spend": 1}]},
        [{"campaign": "A", "spend": 1}],
    ],
)
def test_every_windsor_envelope_shape_is_accepted(payload):
    """Windsor has renamed this envelope before; don't break when it does."""
    assert _extract_rows(payload) == [{"campaign": "A", "spend": 1}]


def test_a_windsor_error_payload_is_surfaced():
    with pytest.raises(WindsorError, match="quota exceeded"):
        _extract_rows({"error": "quota exceeded"})


@pytest.mark.parametrize(
    "raw,expected",
    [(None, 0.0), ("", 0.0), ("null", 0.0), (12, 12.0), (1.5, 1.5),
     ("$1,234.56", 1234.56), ("  7.25 ", 7.25)],
)
def test_spend_values_are_coerced(raw, expected):
    assert _coerce_spend(raw) == expected


def test_a_non_numeric_spend_is_an_error():
    with pytest.raises(WindsorError, match="non-numeric"):
        _coerce_spend("n/a")


def test_an_api_key_is_required():
    with pytest.raises(WindsorError, match="API key"):
        WindsorSpendSource("")


def test_the_request_url_carries_every_required_parameter():
    source = WindsorSpendSource("secret-key")
    url = source._build_url(CHANNEL, date(2026, 8, 1), date(2026, 8, 26), ["campaign", "spend"])
    assert url.startswith("https://connectors.windsor.ai/google_ads?")
    for fragment in (
        "api_key=secret-key",
        "date_from=2026-08-01",
        "date_to=2026-08-26",
        "fields=campaign%2Cspend",
        "select_accounts=997-052-9086",
    ):
        assert fragment in url


def test_a_daily_pull_asks_for_the_date_field():
    source = WindsorSpendSource("k")
    url = source._build_url(CHANNEL, date(2026, 8, 1), date(2026, 8, 2), ["date", "campaign", "spend"])
    assert "fields=date%2Ccampaign%2Cspend" in url


def test_empty_responses_are_retried_then_treated_as_zero(monkeypatch):
    """Windsor cold-starts return nothing, so retry -- but a channel that is
    genuinely quiet is zero spend, not a failed run."""
    source = WindsorSpendSource("secret-key", max_attempts=3, backoff_seconds=(0, 0))
    calls = []
    monkeypatch.setattr(source, "_request", lambda url: calls.append(url) or [])

    rows = source.fetch([CHANNEL], date(2026, 8, 1), date(2026, 8, 26))

    assert list(rows) == []
    assert len(calls) == 3, "the cold-start retries still happen"


def test_a_failing_connector_reports_without_leaking_the_key(monkeypatch):
    """A real transport failure aborts, and the error must stay redacted."""
    source = WindsorSpendSource("secret-key", max_attempts=3, backoff_seconds=(0, 0))
    calls = []

    def boom(url):
        calls.append(url)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(source, "_request", boom)

    with pytest.raises(WindsorError) as excinfo:
        source.fetch([CHANNEL], date(2026, 8, 1), date(2026, 8, 26))

    assert len(calls) == 3
    message = str(excinfo.value)
    assert "failed after 3 attempts" in message
    assert "google_ads" in message and "997-052-9086" in message
    assert "secret-key" not in message, "the API key must not leak into logs"
    assert "***" in message


def test_a_late_success_is_returned(monkeypatch):
    source = WindsorSpendSource("k", max_attempts=3, backoff_seconds=(0, 0))
    responses = [[], [{"campaign": "B2C - Insurance - Brand - PPC", "spend": "42.50"}]]
    monkeypatch.setattr(source, "_request", lambda url: responses.pop(0))

    rows = source.fetch([CHANNEL], date(2026, 8, 1), date(2026, 8, 26))
    assert len(rows) == 1
    assert rows[0].spend == 42.50
    assert rows[0].channel == "Google"


def test_daily_rows_carry_their_date(monkeypatch):
    source = WindsorSpendSource("k")
    monkeypatch.setattr(
        source, "_request",
        lambda url: [{"date": "2026-08-04", "campaign": "X", "spend": 1.0}],
    )
    rows = source.fetch_daily([CHANNEL], date(2026, 8, 1), date(2026, 8, 26))
    assert rows[0].day == date(2026, 8, 4)


def test_csv_source_reads_dates_and_channels(tmp_path):
    path = tmp_path / "spend.csv"
    path.write_text(
        "date,channel,campaign,spend\n"
        "2026-08-01,Google,B2C - Insurance - Brand - PPC,10.50\n"
        "2026-08-02,Bing,B2C - Securities - Brand - PPC,\n"
    )
    rows = CsvSpendSource(path).fetch([CHANNEL], date(2026, 8, 1), date(2026, 8, 26))
    assert [r.channel for r in rows] == ["Google", "Bing"]
    assert rows[0].day == date(2026, 8, 1)
    assert rows[1].spend == 0.0


def test_per_channel_csv_files_only_load_requested_channels(tmp_path):
    google = tmp_path / "google.csv"
    google.write_text("campaign,spend\nB2C - Insurance - Brand - PPC,10\n")
    bing = tmp_path / "bing.csv"
    bing.write_text("campaign,spend\nB2C - Securities - Brand - PPC,5\n")

    rows = CsvSpendSource({"Google": google, "Bing": bing}).fetch(
        [CHANNEL], date(2026, 8, 1), date(2026, 8, 26)
    )
    assert [r.channel for r in rows] == ["Google"]


def test_the_api_key_never_reaches_an_error_message(monkeypatch):
    """Exception text can echo the request URL -- scrub it."""
    import urllib.error

    source = WindsorSpendSource("super-secret", max_attempts=1, backoff_seconds=(0,))

    def explode(url):
        raise urllib.error.URLError(f"failed fetching {url}")

    monkeypatch.setattr(source, "_request", explode)
    with pytest.raises(WindsorError) as excinfo:
        source.fetch([CHANNEL], date(2026, 8, 1), date(2026, 8, 26))
    assert "super-secret" not in str(excinfo.value)


# --- A channel with no spend must not fail the whole run ----------------------


class _ScriptedWindsor(WindsorSpendSource):
    """Windsor source whose transport is a scripted list of outcomes."""

    def __init__(self, outcomes, **kwargs):
        super().__init__("key", backoff_seconds=(0.0,), **kwargs)
        self.outcomes = list(outcomes)
        self.calls = 0

    def _request(self, url):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


LINKEDIN = ChannelSource("LinkedIn", "linkedin", "518468129")


def test_a_channel_with_no_rows_is_zero_not_an_error():
    """LinkedIn reports nothing most months; that must not abort the run."""
    source = _ScriptedWindsor([[]], max_attempts=3)

    rows = source._fetch_channel(LINKEDIN, date(2026, 9, 1), date(2026, 9, 15))

    assert rows == []
    assert source.calls == 3, "still retried, in case it was a cold start"


def test_a_transport_failure_is_still_fatal():
    """An unreachable connector is a real problem and must not read as zero."""
    source = _ScriptedWindsor([urllib.error.URLError("connection refused")], max_attempts=2)

    with pytest.raises(WindsorError) as excinfo:
        source._fetch_channel(LINKEDIN, date(2026, 9, 1), date(2026, 9, 15))
    assert "connection refused" in str(excinfo.value)


def test_a_cold_start_that_warms_up_still_returns_data():
    """Empty then populated is exactly what the retries exist for."""
    source = _ScriptedWindsor(
        [[], [{"campaign": "B2C - Insurance - PPC", "spend": 12.5}]], max_attempts=3
    )

    rows = source._fetch_channel(LINKEDIN, date(2026, 9, 1), date(2026, 9, 15))

    assert [r.campaign for r in rows] == ["B2C - Insurance - PPC"]
    assert source.calls == 2, "stopped as soon as rows arrived"


def test_an_error_followed_by_empty_is_still_fatal():
    """A flaky connector that ends quiet must not be mistaken for zero spend."""
    source = _ScriptedWindsor(
        [urllib.error.URLError("reset by peer"), []], max_attempts=2
    )

    with pytest.raises(WindsorError):
        source._fetch_channel(LINKEDIN, date(2026, 9, 1), date(2026, 9, 15))
