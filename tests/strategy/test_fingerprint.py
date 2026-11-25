"""Settings fingerprint (backlog S3): one value set, one fingerprint, however it is written."""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path

import pytest

from tickr.observers.errors import ObserverError
from tickr.observers.loader import LoadedStrategy, read_fingerprint, register_version
from tickr.store import Store, StrategyVersionReused
from tickr.strategy.fingerprint import fingerprint

ROOT = Path(__file__).resolve().parent.parent.parent

BASE = """
[strategy]
name = "price-action-v3"
version = "2"

[indicators]
fast_ema_period = 9
minimum_relative_volume = 1.5
refuse_when_relative_volume_unavailable = true
sessions = [1, 2.0]

[sizing]
planned_loss_usd = 50
"""


def values(text: str) -> dict:
    return tomllib.loads(text)


def test_key_order_and_number_formatting_do_not_change_it():
    reordered = """
[sizing]
planned_loss_usd = 50.0

[indicators]
sessions = [1.0, 2]
refuse_when_relative_volume_unavailable = true
minimum_relative_volume = 1.50
fast_ema_period = 9.0

[strategy]
version = "2"
name = "price-action-v3"
"""
    assert fingerprint(values(BASE)) == fingerprint(values(reordered))
    assert fingerprint(values(BASE.replace("1.5", "15e-1"))) == fingerprint(values(BASE))


def test_the_version_name_is_outside_it_and_every_value_is_inside():
    same = fingerprint(values(BASE))
    assert fingerprint(values(BASE.replace('version = "2"', 'version = "renamed"'))) == same
    for old, new in (("1.5", "1.75"), ("= 9", "= 10"), ("true", "false"),
                     ('"price-action-v3"', '"price-action-v2"'), ("[1, 2.0]", "[2, 1]")):
        assert fingerprint(values(BASE.replace(old, new))) != same, (old, new)


def test_a_number_and_its_text_differ_and_a_boolean_is_not_a_number():
    assert fingerprint({"a": {"x": 1}}) != fingerprint({"a": {"x": "1"}})
    assert fingerprint({"a": {"x": True}}) != fingerprint({"a": {"x": 1}})
    assert fingerprint({"a": {"x": 0.0}}) == fingerprint({"a": {"x": -0.0}}) == \
        fingerprint({"a": {"x": 0}})


@pytest.mark.parametrize("bad", [date(2026, 11, 25), float("nan"), float("inf")])
def test_a_value_without_a_canonical_form_is_refused(bad):
    with pytest.raises(ValueError, match="a.x"):
        fingerprint({"a": {"x": bad}})


def test_shipped_variants_differ_from_their_base():
    base = read_fingerprint(ROOT, "price-action-v3")
    assert len(base) == 64
    assert read_fingerprint(ROOT, "price-action-v3-relvol-1.5") != base


class Named:
    prior_sessions_needed = 0

    def __init__(self, version: str) -> None:
        self.name, self.version = "s", version


def test_a_version_name_reused_with_other_values_is_refused(tmp_path):
    store = Store.open(tmp_path / "t.sqlite3", busy_timeout_seconds=0)
    try:
        store.register_strategy_version("s", "1", "aaa")
        store.register_strategy_version("s", "1", "aaa")  # the same values: fine
        store.register_strategy_version("s", "2", "bbb")
        with pytest.raises(StrategyVersionReused, match="s version 1 .* aaa.* bbb"):
            store.register_strategy_version("s", "1", "bbb")
        with pytest.raises(ObserverError, match="folder: strategy s version 2"):
            register_version(store, LoadedStrategy("folder", Named("2"), "ccc"))
        with pytest.raises(ObserverError, match="no settings fingerprint"):
            register_version(store, LoadedStrategy("folder", Named("3")))
        rows = store.query("SELECT strategy, version, fingerprint FROM strategy_versions "
                           "ORDER BY version")
        assert [tuple(r) for r in rows] == [("s", "1", "aaa"), ("s", "2", "bbb")]
    finally:
        store.close()
