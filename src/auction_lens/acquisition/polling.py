"""How often the fetcher is allowed to contact a provider.

The limits are enforced against a file on disk rather than against memory, so
a crashed run, a rerun, or a scheduler firing twice all still count as attempts
that already happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import AcquisitionConfig
from ..file_io import read_json, write_json_atomically

PRODUCTION = "production"
DEVELOPMENT = "development"
RUN_MODES = frozenset({PRODUCTION, DEVELOPMENT})

# Enough history to explain today's decisions without growing without bound.
RETAINED_ATTEMPTS = 60

ATTEMPTS_KEY = "attempts"


@dataclass(frozen=True)
class PollLedger:
    """The timestamps of previous requests, recorded before each attempt."""

    path: Path

    @classmethod
    def at(cls, path: str | Path) -> "PollLedger":
        return cls(Path(path))

    def attempts(self) -> list[datetime]:
        stored = read_json(self.path, default={}).get(ATTEMPTS_KEY, [])
        return [_parse_attempt(value) for value in stored]

    def record(self, instant: datetime) -> None:
        """Append one attempt, keeping only the most recent entries."""
        attempts = [*self.attempts(), instant.astimezone(timezone.utc)]
        write_json_atomically(
            self.path,
            {ATTEMPTS_KEY: [value.isoformat() for value in attempts[-RETAINED_ATTEMPTS:]]},
        )


def enforce_request_limits(
    attempts: list[datetime],
    config: AcquisitionConfig,
    instant: datetime,
) -> None:
    """Raise unless another request is allowed right now.

    Production counts requests against the provider's own calendar day, because
    that is the day a provider would see in its logs. Development only spaces
    requests out, so that iterating on a parser stays polite without a quota.
    """
    if config.run_mode == PRODUCTION:
        _enforce_daily_limit(attempts, config, instant)
    if attempts and instant - max(attempts) < _minimum_interval(config):
        raise RuntimeError(f"{config.run_mode} minimum interval has not elapsed")


def _enforce_daily_limit(
    attempts: list[datetime],
    config: AcquisitionConfig,
    instant: datetime,
) -> None:
    zone = ZoneInfo(config.timezone)
    today = _local_date(instant, zone)
    used = [value for value in attempts if _local_date(value, zone) == today]
    if len(used) >= config.max_requests_per_day:
        raise RuntimeError(f"daily request limit reached for {today}")


def _minimum_interval(config: AcquisitionConfig) -> timedelta:
    if config.run_mode == PRODUCTION:
        return timedelta(minutes=config.minimum_interval_minutes)
    return timedelta(seconds=config.development_minimum_interval_seconds)


def _local_date(instant: datetime, zone: ZoneInfo) -> date:
    return instant.astimezone(zone).date()


def _parse_attempt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("ledger timestamps must be timezone-aware")
    return parsed
