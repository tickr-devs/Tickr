"""Replay under the live platform rules (backlog T12): one shared admission path."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from conftest import BOT_TOML, CLOCK, FLAT, enter, series

from tickr import config
from tickr.replay.__main__ import clock_of
from tickr.replay.engine import SignalOutcome, replay
from tickr.risk.admission import AdmissionLimits
from tickr.risk.simulated import SimulationRules

CFG = config.load(BOT_TOML)
CALENDAR_CLOCK = clock_of(CFG)
# ENTER window 10.05..10.15, stop 9.95: FILL fills it, STOP_OUT stops it out.
FILL = ("10.10", "10.20", "10.05", "10.15")
STOP_OUT = ("10.00", "10.00", "9.90", "9.92")


def rules(**changes: object) -> SimulationRules:
    return SimulationRules(replace(AdmissionLimits.from_config(CFG), **changes))


def _enter(quantity: str = "10") -> dict[str, object]:
    return enter("10.05", "10.15", "9.95") | {"quantity": Decimal(quantity)}


def _outcomes(result) -> list[tuple[str, SignalOutcome, str]]:
    return [(s.symbol, s.outcome, s.cause.split(":")[0]) for s in result.signals]


def test_without_rules_symbols_are_independent(settings, scripted):
    bars = {s: series("10:00", [FLAT, FILL, FLAT, FLAT], s) for s in ("AAA", "BBB")}
    result = replay(lambda: scripted({0: _enter()}), bars, settings, CALENDAR_CLOCK)
    assert [o for _, o, _ in _outcomes(result)] == [SignalOutcome.FILLED] * 2
    assert result.rules is None


def test_position_limit_holds_across_symbols(settings, scripted):
    bars = {s: series("10:00", [FLAT, FILL, FLAT, FLAT], s) for s in ("AAA", "BBB")}
    result = replay(lambda: scripted({0: _enter()}), bars, settings, CALENDAR_CLOCK,
                    rules(max_open_positions=1))
    assert _outcomes(result) == [("AAA", SignalOutcome.FILLED, ""),
                                 ("BBB", SignalOutcome.REJECTED, "POSITION_LIMIT_REACHED")]
    assert "risk.max_open_positions=1" in (result.rules or "")


def test_slot_is_free_again_after_the_exit(settings, scripted):
    bars = {"AAA": series("10:00", [FLAT, FILL, STOP_OUT, FLAT, FLAT], "AAA"),
            "BBB": series("10:00", [FLAT, FLAT, FLAT, FLAT, FILL], "BBB")}
    result = replay(lambda: scripted({0: _enter(), 3: _enter()}), bars, settings,
                    CALENDAR_CLOCK, rules(max_open_positions=1))
    assert ("BBB", SignalOutcome.FILLED, "") in _outcomes(result)


def test_pause_after_a_loss_in_the_symbol(settings, scripted):
    bars = {"AAA": series("10:00", [FLAT, FILL, STOP_OUT, FLAT, FLAT, FLAT], "AAA")}
    script = {0: _enter(), 3: _enter()}
    result = replay(lambda: scripted(script), bars, settings, CALENDAR_CLOCK, rules())
    assert _outcomes(result) == [("AAA", SignalOutcome.FILLED, ""),
                                 ("AAA", SignalOutcome.REJECTED, "SYMBOL_COOLDOWN")]
    off = replay(lambda: scripted(script), bars, settings, CALENDAR_CLOCK,
                 rules(symbol_cooldown_minutes_after_loss=None))
    assert _outcomes(off)[1][1] is not SignalOutcome.REJECTED


def test_daily_loss_ceiling_counts_the_day(settings, scripted):
    bars = {"AAA": series("10:00", [FLAT, FILL, STOP_OUT, FLAT, FLAT, FLAT], "AAA")}
    # The first trade loses more than 2 USD, so less than the minimum room is left.
    result = replay(lambda: scripted({0: _enter(), 3: _enter()}), bars, settings,
                    CALENDAR_CLOCK, rules(symbol_cooldown_minutes_after_loss=None,
                                          max_daily_loss_usd=Decimal(5),
                                          min_daily_room_usd=Decimal(3)))
    assert _outcomes(result)[1] == ("AAA", SignalOutcome.REJECTED, "DAILY_CEILING")


def test_size_and_decision_rules(settings, scripted):
    bars = {"AAA": series("10:00", [FLAT, FILL, FLAT], "AAA")}
    big = replay(lambda: scripted({0: _enter("1000")}), bars, settings, CALENDAR_CLOCK,
                 rules())
    assert _outcomes(big)[0][1] is SignalOutcome.REJECTED
    assert _outcomes(big)[0][2] in {"PLANNED_LOSS_OVER_LIMIT", "NOTIONAL_OVER_CAP"}
    no_quantity = replay(lambda: scripted({0: enter("10.05", "10.15", "9.95")}), bars,
                         settings, CALENDAR_CLOCK, rules())
    assert _outcomes(no_quantity)[0] == ("AAA", SignalOutcome.REJECTED, "DECISION_INCOMPLETE")


def test_a_clock_without_calendar_day_refuses_under_rules(settings, scripted):
    bars = {"AAA": series("10:00", [FLAT, FILL, FLAT], "AAA")}
    result = replay(lambda: scripted({0: _enter()}), bars, settings, CLOCK, rules())
    assert _outcomes(result)[0] == ("AAA", SignalOutcome.REJECTED, "CALENDAR_UNKNOWN")
