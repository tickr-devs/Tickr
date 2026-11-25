from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from research_helpers import HERE

from tickr.config import uncommented_keys
from tickr.research.errors import ResearchSettingsError
from tickr.research.settings import (
    KEYS,
    load_research_settings,
    settings_from_mapping,
    strategy_atr_period,
)

ROOT = HERE.parent.parent
CONFIG = ROOT / "config" / "research.toml"


@pytest.mark.parametrize("path", [CONFIG, HERE / "research_settings.toml"], ids=str)
def test_every_key_is_commented(path: Path):
    assert uncommented_keys(path.read_text("utf-8")) == []


def test_repository_settings_load():
    settings = load_research_settings(CONFIG)
    assert settings.horizons_minutes == (1, 5, 15, 30, 60)


def test_numbers_owned_elsewhere_are_not_repeated():
    """Output decimal places belong to config/replay.toml and the ATR length to the strategy
    (backlog T6): the research file must not hold a second copy."""
    table = tomllib.loads(CONFIG.read_text("utf-8"))["research"]
    assert {"atr_period", "output_decimal_places"}.isdisjoint(table)
    with pytest.raises(ResearchSettingsError, match=r"unknown=\['atr_period'\]"):
        settings_from_mapping(_table(atr_period=14))


def test_atr_period_is_the_strategys_own():
    assert strategy_atr_period({"indicators": {"atr_period": 14}}, "s v1") == 14
    with pytest.raises(ResearchSettingsError, match=r"s v1 has no \[indicators\] atr_period"):
        strategy_atr_period({"liquidity": {}}, "s v1")
    with pytest.raises(ResearchSettingsError, match="must be an integer"):
        strategy_atr_period({"indicators": {"atr_period": 1.5}}, "s v1")


def _table(**changes):
    table = tomllib.loads(CONFIG.read_text("utf-8"))["research"]
    table.update(changes)
    return {"research": table}


@pytest.mark.parametrize(
    ("changes", "cause"),
    [
        ({"horizons_minutes": [5, 1]}, "strictly increase"),
        ({"horizons_minutes": []}, "non-empty list"),
        ({"horizons_minutes": [0, 5]}, "at least 1"),
        ({"horizons_minutes": [1.5]}, "must be an integer"),
        ({"session_timezone": "Mars/Base"}, "unknown"),
        ({"surprise": 1}, r"unknown=\['surprise'\]"),
    ],
)
def test_bad_settings_refused(changes, cause):
    with pytest.raises(ResearchSettingsError, match=cause):
        settings_from_mapping(_table(**changes))


def test_missing_key_refused():
    table = _table()
    del table["research"]["session_timezone"]
    with pytest.raises(ResearchSettingsError, match=r"missing=\['session_timezone'\]"):
        settings_from_mapping(table)
    assert "session_timezone" in KEYS
