"""Replay settings: every number the simulation uses, loaded from a commented TOML file.

The file has one table `[replay]` with exactly the keys in `KEYS`. Missing or unknown keys,
wrong types and out-of-range values are refused with a `SettingsError`. An example with the
required comment for every key is in `docs/replay.md`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aperture.replay.errors import SettingsError

KEYS = frozenset(
    {
        "slippage_percent",
        "slippage_min_usd",
        "entry_wait_bars",
        "session_timezone",
        "regular_open",
        "output_decimal_places",
    }
)


@dataclass(frozen=True)
class ReplaySettings:
    slippage_percent: Decimal
    """Adverse slippage per simulated fill, percent of the fill price."""
    slippage_min_usd: Decimal
    """Minimum adverse slippage per simulated fill, USD per share."""
    entry_wait_bars: int
    """Bars after the ENTER decision during which the simulated entry may fill."""
    session_timezone: ZoneInfo
    """Exchange time zone: session dates and the clock parameters are read in it."""
    regular_open: time
    """Regular session open (exchange time); no entry is decided on a bar before it."""
    output_decimal_places: int
    """Decimal places for prices, R and percent in the outputs."""

    def slippage(self, price: Decimal) -> Decimal:
        """Adverse slippage per share for a fill at `price`."""
        return max(price * self.slippage_percent / 100, self.slippage_min_usd)


def _number(key: str, raw: Any) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise SettingsError(f"{key} must be a number, got {type(raw).__name__}")
    value = Decimal(str(raw))
    if value < 0:
        raise SettingsError(f"{key} must not be negative: {raw}")
    return value


def _count(key: str, raw: Any, minimum: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SettingsError(f"{key} must be an integer, got {type(raw).__name__}")
    if raw < minimum:
        raise SettingsError(f"{key} must be at least {minimum}: {raw}")
    return raw


def parse_clock(key: str, raw: Any) -> time:
    """Parse an `HH:MM` exchange-time string."""
    if not isinstance(raw, str):
        raise SettingsError(f"{key} must be an HH:MM string")
    try:
        parsed = time.fromisoformat(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} is not HH:MM: {raw!r}") from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise SettingsError(f"{key} must be HH:MM without seconds or offset: {raw!r}")
    return parsed


def settings_from_mapping(data: dict[str, Any]) -> ReplaySettings:
    if set(data) != {"replay"} or not isinstance(data["replay"], dict):
        raise SettingsError("settings must contain exactly one table: [replay]")
    table = data["replay"]
    missing = sorted(KEYS - set(table))
    unknown = sorted(set(table) - KEYS)
    if missing or unknown:
        raise SettingsError(f"[replay] keys mismatch: missing={missing} unknown={unknown}")
    zone_name = table["session_timezone"]
    if not isinstance(zone_name, str):
        raise SettingsError("session_timezone must be a string")
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SettingsError(f"session_timezone is unknown: {zone_name!r}") from exc
    return ReplaySettings(
        slippage_percent=_number("slippage_percent", table["slippage_percent"]),
        slippage_min_usd=_number("slippage_min_usd", table["slippage_min_usd"]),
        entry_wait_bars=_count("entry_wait_bars", table["entry_wait_bars"], 1),
        session_timezone=zone,
        regular_open=parse_clock("regular_open", table["regular_open"]),
        output_decimal_places=_count("output_decimal_places", table["output_decimal_places"], 0),
    )


def load_settings(path: Path) -> ReplaySettings:
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{path}: invalid TOML: {exc}") from exc
    return settings_from_mapping(data)
