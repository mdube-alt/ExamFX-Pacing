"""Fetch campaign-level spend.

Two sources are supported:

``WindsorSpendSource``
    Queries the Windsor.ai REST API, one request per connector/account.

``CsvSpendSource``
    Reads campaign spend from CSV files. Used by the tests and useful for
    backfills or when Windsor is unavailable.

Both return the same shape: a list of :class:`CampaignSpend`.
"""

from __future__ import annotations

import csv
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Protocol

from .config import ChannelSource

log = logging.getLogger(__name__)

__all__ = [
    "CampaignSpend",
    "SpendSource",
    "WindsorSpendSource",
    "CsvSpendSource",
    "WindsorError",
]


class WindsorError(RuntimeError):
    """Raised when Windsor cannot be reached or returns an unusable payload."""


@dataclass(frozen=True)
class CampaignSpend:
    """Spend for one campaign on one channel.

    ``day`` is set when the row came from a day-by-day pull; it is ``None``
    for a single aggregated window.
    """

    channel: str
    campaign: str
    spend: float
    day: date | None = None


class SpendSource(Protocol):
    """Anything that can return campaign spend for a date window."""

    def fetch(
        self, channels: Iterable[ChannelSource], date_from: date, date_to: date
    ) -> list[CampaignSpend]:  # pragma: no cover - protocol
        ...


def _parse_day(value) -> date | None:
    """Parse a Windsor date cell (``2026-08-01``) into a ``date``."""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise WindsorError(f"unrecognised date value from Windsor: {value!r}")


def _coerce_spend(value) -> float:
    """Windsor may return spend as a number, a numeric string, or null."""
    if value in (None, "", "null"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError as exc:
        raise WindsorError(f"non-numeric spend value: {value!r}") from exc


def _extract_rows(payload) -> list[dict]:
    """Pull the row list out of a Windsor response.

    Windsor has used both ``{"data": [...]}`` and ``{"result": [...]}``
    envelopes, and can return a bare list. Accept all three rather than
    breaking on an envelope rename.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "result", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if "error" in payload:
            raise WindsorError(f"Windsor returned an error: {payload['error']}")
    raise WindsorError(f"unrecognised Windsor response shape: {type(payload).__name__}")


class WindsorSpendSource:
    """Campaign spend from the Windsor.ai REST API.

    The endpoint is ``{base_url}/{connector}`` with the API key, date window,
    field list and account passed as query parameters. Base URL and the
    account parameter name are configurable so a Windsor API change does not
    require a code change.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://connectors.windsor.ai",
        *,
        max_attempts: int = 4,
        backoff_seconds: tuple[float, ...] = (2.0, 3.0, 5.0),
        account_param: str = "select_accounts",
        timeout: float = 120.0,
    ):
        if not api_key:
            raise WindsorError("a Windsor API key is required (set WINDSOR_API_KEY)")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.account_param = account_param
        self.timeout = timeout

    def _build_url(
        self,
        source: ChannelSource,
        date_from: date,
        date_to: date,
        fields: list[str],
    ) -> str:
        params = {
            "api_key": self.api_key,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "fields": ",".join(fields),
            self.account_param: source.account,
            "_renderer": "json",
        }
        return f"{self.base_url}/{source.connector}?{urllib.parse.urlencode(params)}"

    def _request(self, url: str) -> list[dict]:
        with urllib.request.urlopen(url, timeout=self.timeout) as response:
            body = response.read().decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise WindsorError(f"Windsor returned non-JSON: {body[:200]!r}") from exc
        return _extract_rows(payload)

    def _fetch_channel(
        self,
        source: ChannelSource,
        date_from: date,
        date_to: date,
        daily: bool = False,
    ) -> list[CampaignSpend]:
        """Fetch one channel, retrying through Windsor's cold-start failures."""
        fields = [source.campaign_field, source.spend_field]
        if daily:
            fields.insert(0, "date")
        url = self._build_url(source, date_from, date_to, fields)
        redacted = url.replace(self.api_key, "***")
        last_error: Exception | None = None

        def scrub(value) -> str:
            """Never let the API key reach a log line or an error message."""
            return str(value).replace(self.api_key, "***")

        for attempt in range(1, self.max_attempts + 1):
            try:
                rows = self._request(url)
            except (urllib.error.URLError, WindsorError, TimeoutError) as exc:
                last_error = exc
                log.warning(
                    "Windsor %s attempt %d/%d failed: %s",
                    source.connector, attempt, self.max_attempts, scrub(exc),
                )
            else:
                if rows:
                    return [
                        CampaignSpend(
                            channel=source.channel,
                            campaign=str(row.get(source.campaign_field, "")),
                            spend=_coerce_spend(row.get(source.spend_field)),
                            day=_parse_day(row.get("date")) if daily else None,
                        )
                        for row in rows
                    ]
                last_error = WindsorError("empty result set")
                log.warning(
                    "Windsor %s attempt %d/%d returned no rows",
                    source.connector, attempt, self.max_attempts,
                )

            if attempt < self.max_attempts:
                delay = self.backoff_seconds[
                    min(attempt - 1, len(self.backoff_seconds) - 1)
                ]
                time.sleep(delay)

        raise WindsorError(
            f"Windsor failed after {self.max_attempts} attempts\n"
            f"  Connector: {source.connector}\n"
            f"  Account: {source.account}\n"
            f"  Date range: {date_from} -> {date_to}\n"
            f"  Fields: {','.join(fields)}\n"
            f"  URL: {redacted}\n"
            f"  Last error: {scrub(last_error)}"
        )

    def fetch(
        self, channels: Iterable[ChannelSource], date_from: date, date_to: date
    ) -> list[CampaignSpend]:
        """Total spend per campaign across the whole window."""
        results: list[CampaignSpend] = []
        for source in channels:
            results.extend(self._fetch_channel(source, date_from, date_to))
        return results

    def fetch_daily(
        self, channels: Iterable[ChannelSource], date_from: date, date_to: date
    ) -> list[CampaignSpend]:
        """Day-by-day spend per campaign.

        One request per channel covers the whole month; every week's
        cumulative total is then derived locally, instead of re-querying
        Windsor once per week boundary.
        """
        results: list[CampaignSpend] = []
        for source in channels:
            results.extend(self._fetch_channel(source, date_from, date_to, daily=True))
        return results


class CsvSpendSource:
    """Campaign spend from CSV files -- one per channel, or one combined file.

    Expected columns: ``channel``, ``campaign``, ``spend``, and optionally
    ``date``. A per-channel file may omit ``channel``, in which case it is
    taken from the mapping key.
    """

    def __init__(self, paths: dict[str, str | Path] | str | Path):
        self.paths = paths

    @staticmethod
    def _read(path: str | Path, default_channel: str | None) -> list[CampaignSpend]:
        rows: list[CampaignSpend] = []
        with open(path, newline="", encoding="utf-8") as handle:
            for record in csv.DictReader(handle):
                channel = (record.get("channel") or default_channel or "").strip()
                if not channel:
                    raise ValueError(f"{path}: row is missing a channel")
                rows.append(
                    CampaignSpend(
                        channel=channel,
                        campaign=(record.get("campaign") or "").strip(),
                        spend=_coerce_spend(record.get("spend")),
                        day=_parse_day(record.get("date")),
                    )
                )
        return rows

    def fetch(
        self, channels: Iterable[ChannelSource], date_from: date, date_to: date
    ) -> list[CampaignSpend]:
        if isinstance(self.paths, (str, Path)):
            return self._read(self.paths, None)
        wanted = {source.channel for source in channels}
        results: list[CampaignSpend] = []
        for channel, path in self.paths.items():
            if channel in wanted:
                results.extend(self._read(path, channel))
        return results

    #: Daily and windowed reads are the same operation for a CSV.
    fetch_daily = fetch
