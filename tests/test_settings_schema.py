from __future__ import annotations

import re
import tomllib
from decimal import Decimal
from pathlib import Path

import pytest

from tickr import settings_schema as schema
from tickr.settings_schema import Applies, Cause, Kind, Loosens

ROOT = Path(__file__).resolve().parent.parent
FILES = {
    schema.PLATFORM: ROOT / "config" / "bot.toml",
    schema.PA3: ROOT / "strategies" / schema.PA3 / "strategy.toml",
}
_TABLE = re.compile(r"^\[([A-Za-z0-9_.\-]+)\]\s*$")
_KEY = re.compile(r"^([A-Za-z0-9_]+)\s*=(.*)$")


def _current(owner: str) -> dict:
    return schema.flatten(tomllib.loads(FILES[owner].read_text("utf-8")))


def _comments(text: str) -> dict[str, str]:
    """Dotted key -> its comment: the block of comment lines right above it plus an inline one."""
    found: dict[str, str] = {}
    table, block = "", []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            block.append(stripped.lstrip("# "))
            continue
        if header := _TABLE.match(stripped):
            table = header.group(1)
        elif key := _KEY.match(stripped):
            inline = key.group(2).split("#", 1)[1] if "#" in key.group(2) else ""
            found[f"{table}.{key.group(1)}"] = " ".join([*block, inline])
        block = [] if not stripped or not stripped.startswith("#") else block
    return found


IDS = [f"{s.owner}:{s.key}" for s in schema.SETTINGS]


@pytest.mark.parametrize("spec", schema.SETTINGS, ids=IDS)
def test_every_setting_exists_in_its_file_with_its_kind(spec):
    value = _current(spec.owner)[spec.key]
    expected = {Kind.BOOL: (bool,), Kind.INT: (int,), Kind.DECIMAL: (int, float)}[spec.kind]
    assert isinstance(value, expected)
    assert spec.kind is Kind.BOOL or not isinstance(value, bool)


# Keys whose comment names the unit after its first sentence, with the reason.
UNIT_LATER = {
    "indicators.warmup_period_multiple": "the unit is its own second sentence",
    "liquidity.max_spread_ratio": "the [liquidity] table comment comes first",
}


@pytest.mark.parametrize("spec", schema.SETTINGS, ids=IDS)
def test_every_setting_comment_names_its_unit(spec):
    comment = _comments(FILES[spec.owner].read_text("utf-8"))[spec.key].lower()
    if spec.key not in UNIT_LATER:
        comment = comment.split(". ", 1)[0]
    assert any(word in comment for word in schema.UNIT_WORDS[spec.unit]), (spec.unit, comment)


@pytest.mark.parametrize("spec", schema.SETTINGS, ids=IDS)
def test_every_record_is_complete(spec):
    assert spec.en.strip() and spec.es.strip() and spec.group.strip()
    assert (spec.minimum is None) == (spec.kind is Kind.BOOL) == (spec.step is None)
    assert spec.owner == schema.PLATFORM or spec.applies is Applies.WHEN_FLAT


def test_keys_are_unique_and_the_moving_sizing_keys_stay_out():
    keys = [(s.owner, s.key) for s in schema.SETTINGS]
    assert len(keys) == len(set(keys))
    assert not {"risk.planned_loss_usd", "risk.position_notional_cap_usd"} & {k for _, k in keys}
    assert not [k for _, k in keys if k.startswith("sizing.")]


def test_rules_name_schema_keys_on_the_left_and_existing_keys_on_the_right():
    for rule in schema.RULES:
        assert rule.left in schema.settings_of(rule.owner)
        assert rule.right in _current(rule.owner)
        assert rule.en.strip() and rule.es.strip()


@pytest.mark.parametrize("owner", sorted(FILES))
def test_the_shipped_values_pass(owner):
    current = _current(owner)
    draft = {key: current[key] for key in schema.settings_of(owner)}
    assert schema.validate(owner, draft, current) == []


def _causes(owner: str, draft: dict) -> list[tuple[str, Cause]]:
    return [(r.key, r.cause) for r in schema.validate(owner, draft, _current(owner))]


P = schema.PLATFORM


@pytest.mark.parametrize(("draft", "expected"), [
    ({"risk.surprise": 1}, [("risk.surprise", Cause.UNKNOWN_KEY)]),
    ({"risk.max_open_positions": True}, [("risk.max_open_positions", Cause.WRONG_TYPE)]),
    ({"risk.max_open_positions": 2.0}, [("risk.max_open_positions", Cause.WRONG_TYPE)]),
    ({"risk.max_quote_age_seconds": "5"}, [("risk.max_quote_age_seconds", Cause.WRONG_TYPE)]),
    ({"risk.max_quote_age_seconds": float("nan")},
     [("risk.max_quote_age_seconds", Cause.WRONG_TYPE)]),
    ({"risk.symbol_cooldown_after_loss_enabled": 1},
     [("risk.symbol_cooldown_after_loss_enabled", Cause.WRONG_TYPE)]),
    ({"risk.max_working_entries": 0}, [("risk.max_working_entries", Cause.BELOW_MINIMUM)]),
    ({"risk.max_quote_age_seconds": 0}, [("risk.max_quote_age_seconds", Cause.BELOW_MINIMUM)]),
    ({"risk.max_clock_skew_seconds": 0}, []),
    ({"risk.max_quote_age_seconds": 2.55}, [("risk.max_quote_age_seconds", Cause.OFF_STEP)]),
    ({"risk.max_quote_age_seconds": Decimal("2.5")}, []),
    ({"risk.min_daily_room_usd": 5000}, [("risk.min_daily_room_usd", Cause.RULE_BROKEN)]),
    ({"risk.max_daily_loss_usd": 10}, [("risk.min_daily_room_usd", Cause.RULE_BROKEN)]),
    ({"risk.max_daily_loss_usd": 0}, [("risk.max_daily_loss_usd", Cause.BELOW_MINIMUM)]),
    ({"risk.max_open_positions": 10_000}, [("risk.max_open_positions", Cause.RULE_BROKEN)]),
])
def test_platform_draft_refusals(draft, expected):
    assert _causes(P, draft) == expected


def test_strategy_rule_and_a_missing_rule_value():
    assert _causes(schema.PA3, {"indicators.fast_ema_period": 20}) == [
        ("indicators.fast_ema_period", Cause.RULE_BROKEN)]
    refusals = schema.validate(P, {"risk.max_open_positions": 2}, {})
    assert [(r.key, r.cause) for r in refusals] == [
        ("market_data.stream_reserved_symbols", Cause.VALUE_MISSING)]


def test_a_maximum_is_enforced_when_a_record_has_one():
    spec = schema.Setting(P, "risk.x", Kind.INT, schema.Unit.COUNT, "g", "e", "s",
                          Applies.AT_ONCE, Loosens.RAISE, minimum=Decimal(1),
                          maximum=Decimal(3), step=Decimal(1))
    assert schema._check_value(spec, 4).cause is Cause.ABOVE_MAXIMUM
    assert schema._check_value(spec, 3) is None


def test_loosening_protection_waits_for_the_next_session_and_needs_face_id():
    specs = schema.settings_of(P)
    ceiling = specs["risk.max_daily_loss_usd"]
    assert schema.change_effect(ceiling, 5000, 6000) == schema.ChangeEffect(
        Applies.NEXT_SESSION, needs_face_id=True)
    assert schema.change_effect(ceiling, 5000, 4000) == schema.ChangeEffect(
        Applies.AT_ONCE, needs_face_id=False)
    cooldown = specs["risk.symbol_cooldown_after_loss_enabled"]
    assert schema.change_effect(cooldown, True, False).needs_face_id
    assert not schema.change_effect(cooldown, False, True).needs_face_id
    ema = schema.settings_of(schema.PA3)["indicators.fast_ema_period"]
    assert not ema.needs_face_id
    assert schema.change_effect(ema, 9, 12) == schema.ChangeEffect(
        Applies.WHEN_FLAT, needs_face_id=False)
