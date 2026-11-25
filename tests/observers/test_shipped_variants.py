"""The observers config/bot.toml ships with load, and each relative-volume variant
(`<base>-relvol-<value>`) differs from its base strategy folder only in that value and the
version: it extends the base (tickr.strategy.values) and writes only those two."""

from __future__ import annotations

from pathlib import Path

from tickr import config
from tickr.observers.loader import STRATEGIES_DIR, STRATEGY_FILE, load_observers
from tickr.strategy.values import load_values

ROOT = Path(__file__).resolve().parents[2]
CFG = config.load(ROOT / "config" / "bot.toml")


def _values(folder: str) -> dict:
    return load_values(ROOT / STRATEGIES_DIR / folder / STRATEGY_FILE)


def _flat(values: dict) -> dict[tuple[str, str], object]:
    return {(section, key): value for section, table in values.items()
            for key, value in table.items()}


def test_shipped_observers_load_with_distinct_identities():
    loaded = load_observers(CFG)
    assert [o.folder for o in loaded] == list(CFG.strategy.observers)
    assert loaded


VARIANT = "-relvol-"


def test_relative_volume_variants_change_only_that_value_and_the_version():
    variants = [f for f in CFG.strategy.observers if VARIANT in f]
    for folder in variants:
        base = _flat(_values(folder.split(VARIANT)[0]))
        variant = _flat(_values(folder))
        changed = {k for k in base.keys() | variant.keys() if base.get(k) != variant.get(k)}
        assert changed == {("strategy", "version"), ("indicators", "minimum_relative_volume")}
        assert variant[("indicators", "minimum_relative_volume")] < base[
            ("indicators", "minimum_relative_volume")]
