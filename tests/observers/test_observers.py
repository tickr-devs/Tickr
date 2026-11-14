from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aperture import config
from aperture.observers.errors import ObserverError
from aperture.observers.loader import LoadedStrategy, load_observers, parse_modules
from aperture.observers.log import DecisionLog
from aperture.observers.runner import ObserverSet
from aperture.research.records import Admission, load_records
from aperture.strategy.base import Decision, PositionView, Role, SymbolContext, Verdict
from aperture.types import Bar

ROOT = Path(__file__).resolve().parent.parent.parent
NY = ZoneInfo("America/New_York")
M1 = timedelta(minutes=1)
START = datetime.combine(date(2026, 11, 10), time(10, 0), NY).astimezone(UTC)


def bars(n: int = 3, symbol: str = "ABC") -> tuple[Bar, ...]:
    p = Decimal(10)
    return tuple(Bar(symbol, START + i * M1, p, p, p, p, Decimal(100)) for i in range(n))


def context(n: int = 3, position: PositionView | None = None) -> SymbolContext:
    return SymbolContext("ABC", bars(n), position=position)


class Fake:
    def __init__(self, name="fake", version="1", verdict=Verdict.WATCH, raises=None, tag=None):
        self.name, self.version = name, version
        self.verdict, self.raises, self.tag = verdict, raises, tag
        self.seen: list[SymbolContext] = []

    def evaluate(self, ctx: SymbolContext) -> Decision:
        self.seen.append(ctx)
        if self.raises:
            raise self.raises
        name, version = self.tag or (self.name, self.version)
        extra = {}
        if self.verdict is Verdict.ENTER:
            extra = dict(entry_low=Decimal(10), entry_high=Decimal("10.1"), stop=Decimal("9.8"))
        return Decision(name, version, ctx.symbol, self.verdict, (), **extra)


def observer_set(tmp_path, *fakes, max_seconds="0.5", clock=None):
    loaded = [LoadedStrategy(f"folder-{i}", f) for i, f in enumerate(fakes)]
    log = DecisionLog(tmp_path / "decisions")
    return ObserverSet(loaded, log, Decimal(max_seconds), clock or (lambda: 0.0)), log


# ------------------------------------------------------------------ runner


def test_each_observer_is_recorded_as_observer_without_the_position(tmp_path):
    watch, enter = Fake("a"), Fake("b", verdict=Verdict.ENTER)
    observers, log = observer_set(tmp_path, watch, enter)
    position = PositionView(Decimal(10), Decimal(5), Decimal(9))
    at = START + 3 * M1 + timedelta(seconds=2)
    assert observers.on_evaluation(context(position=position), at) == ()
    log.close()
    assert watch.seen[0].position is None and len(watch.seen[0].bars) == 3
    records = load_records([log.path(date(2026, 11, 10))])
    assert [(r.decision.strategy, r.role, r.admission) for r in records] == [
        ("a", Role.OBSERVER, Admission.NOT_APPLICABLE),
        ("b", Role.OBSERVER, Admission.NOT_EVALUATED),
    ]
    assert records[0].bar_start == START + 2 * M1 and records[0].time == at


def test_a_raising_observer_is_switched_off_and_others_continue(tmp_path):
    bad, good = Fake("bad", raises=RuntimeError("boom")), Fake("good")
    observers, log = observer_set(tmp_path, bad, good)
    (failure,) = observers.on_evaluation(context(), START + 3 * M1)
    assert failure.identity == "bad v1" and "RuntimeError: boom" in failure.cause
    assert observers.on_evaluation(context(4), START + 4 * M1) == ()
    assert [o.identity for o in observers.enabled] == ["good v1"]
    assert len(bad.seen) == 1 and len(good.seen) == 2


def test_a_wrong_tag_is_a_contract_failure(tmp_path):
    observers, _ = observer_set(tmp_path, Fake("x", tag=("other", "9")))
    (failure,) = observers.on_evaluation(context(), START + 3 * M1)
    assert failure.cause == "decision tagged other v9"
    assert observers.enabled == ()


def test_a_slow_observer_is_recorded_then_switched_off(tmp_path):
    ticks = iter([0.0, 2.0])
    observers, log = observer_set(tmp_path, Fake("slow"), clock=lambda: next(ticks))
    (failure,) = observers.on_evaluation(context(), START + 3 * M1)
    assert failure.cause.startswith("evaluation took 2.000 s")
    log.close()
    assert len(load_records([log.path(date(2026, 11, 10))])) == 1


def test_log_write_failure_switches_every_observer_off(tmp_path):
    blocker = tmp_path / "decisions"
    blocker.write_text("not a folder")
    observers, _ = observer_set(tmp_path, Fake("a"), Fake("b"))
    failures = observers.on_evaluation(context(), START + 3 * M1)
    assert {f.identity for f in failures} == {"a v1", "b v1"}
    assert all("decision log write failed" in f.cause for f in failures)
    assert observers.enabled == ()


def test_decision_time_before_bar_end_is_a_caller_error(tmp_path):
    observers, _ = observer_set(tmp_path, Fake())
    with pytest.raises(ValueError, match="before the bar completed"):
        observers.on_evaluation(context(), START + 2 * M1)


def test_log_rolls_over_by_new_york_day(tmp_path):
    observers, log = observer_set(tmp_path, Fake())
    observers.on_evaluation(context(), START + 3 * M1)
    next_day = tuple(replace(b, start=b.start + timedelta(days=1)) for b in bars())
    observers.on_evaluation(SymbolContext("ABC", next_day), next_day[-1].start + M1)
    log.close()
    for day in (date(2026, 11, 10), date(2026, 11, 11)):
        assert len(load_records([log.path(day)])) == 1


# ------------------------------------------------------------------ loader


def _config(tmp_path, observers=(), modules=None, max_observers=None):
    cfg = config.load(ROOT / "config" / "bot.toml")
    shutil.copytree(ROOT / "strategies", tmp_path / "strategies")
    obs = cfg.observers
    if modules is not None:
        obs = replace(obs, strategy_modules=tuple(modules))
    if max_observers is not None:
        obs = replace(obs, max_observers=max_observers)
    return replace(cfg, root=tmp_path, observers=obs,
                   strategy=replace(cfg.strategy, observers=tuple(observers)))


def _variant(tmp_path, folder, version, change=("lookback_bars = 5", "lookback_bars = 8")):
    source = (ROOT / "strategies" / "price-action-v1" / "strategy.toml").read_text()
    text = source.replace('version = "1"', f'version = "{version}"').replace(*change)
    (tmp_path / "strategies" / folder).mkdir()
    (tmp_path / "strategies" / folder / "strategy.toml").write_text(text)


def test_loads_a_value_variant_of_the_active_strategy(tmp_path):
    cfg = _config(tmp_path, ["pa-lookback-8"])
    _variant(tmp_path, "pa-lookback-8", "2")
    (observer,) = load_observers(cfg)
    assert observer.identity == "price-action-v1 v2" and observer.folder == "pa-lookback-8"


def test_no_observers_loads_nothing(tmp_path):
    assert load_observers(_config(tmp_path)) == ()


def test_same_identity_as_the_active_is_refused(tmp_path):
    cfg = _config(tmp_path, ["copy"])
    _variant(tmp_path, "copy", "1")
    with pytest.raises(ObserverError, match="same identity as price-action-v1"):
        load_observers(cfg)


@pytest.mark.parametrize(
    ("kwargs", "cause"),
    [
        ({"observers": ["missing"]}, "cannot read"),
        ({"observers": ["v2"], "modules": ["other=x.y"]}, "no module for strategy"),
        ({"observers": ["v2"], "modules": ["price-action-v1=no.such.module"]},
         "cannot be imported"),
        ({"observers": ["v2", "v2"]}, "folder twice"),
        ({"observers": ["v2"], "max_observers": 0}, "at most 0 allowed"),
    ],
)
def test_bad_observer_setup_is_refused(tmp_path, kwargs, cause):
    cfg = _config(tmp_path, **kwargs)
    _variant(tmp_path, "v2", "2")
    with pytest.raises(ObserverError, match=cause):
        load_observers(cfg)


def test_values_refused_by_the_strategy(tmp_path):
    cfg = _config(tmp_path, ["broken"])
    _variant(tmp_path, "broken", "2", change=("lookback_bars = 5", "lookback_bars = 5\nx = 1"))
    with pytest.raises(ObserverError, match="values refused"):
        load_observers(cfg)


@pytest.mark.parametrize("entries", [["no-equals"], ["a=x", "a=y"], ["=x"]])
def test_malformed_module_list_is_refused(entries):
    with pytest.raises(ObserverError):
        parse_modules(entries)


def test_log_from_config_path(tmp_path):
    cfg = _config(tmp_path)
    log = DecisionLog.from_config(cfg)
    assert log.path(date(2026, 11, 10)) == (
        tmp_path / cfg.run.data_dir / "decisions" / cfg.environment_name / "2026-11-10"
        / "decisions.jsonl")


def test_real_strategy_variant_runs_as_observer(tmp_path):
    cfg = _config(tmp_path, ["v2"])
    _variant(tmp_path, "v2", "2")
    observers = ObserverSet(load_observers(cfg), DecisionLog(tmp_path / "d"), Decimal(5))
    assert observers.on_evaluation(context(), START + 3 * M1) == ()
