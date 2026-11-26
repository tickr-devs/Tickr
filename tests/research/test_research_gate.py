"""R23 Step 0: the gate's numeric criteria on synthetic trades, both money lines."""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal

import pytest
from research_helpers import HERE

from tickr.research.__main__ import main
from tickr.research.gate import MANUAL, GateRefused, Trade, gate, load_trades

SETTINGS = str(HERE / "research_settings.toml")
CLOCK = ["--replay-settings", str(HERE / "replay_settings.toml"),
         "--config", str(HERE.parent.parent / "config" / "bot.toml")]
CAP = Decimal(1000)
ALL = Decimal(4000)


def _t(day: str, result: str, session: str = "regular", cost: str = "0.1") -> Trade:
    return Trade(date.fromisoformat(day), session, Decimal(result), Decimal(cost))


def _gate(trades, k=2, m=3, drop="1"):
    return gate(trades, CAP, ALL, k, m, Decimal(drop))


def _write(path, header, rows):
    path.write_text("\n".join([",".join(header), *(",".join(r) for r in rows)]) + "\n")
    return path


def test_all_three_pass_and_money_lines():
    trades = [_t("2026-08-03", "1"), _t("2026-09-01", "2"), _t("2026-09-01", "-1"),
              _t("2026-10-05", "1.5"), _t("2026-10-06", "-0.5", "extended")]
    result = _gate(trades)
    assert result.window == ("2026-08", "2026-09", "2026-10")
    assert result.criteria == {"net": True, "months": True, "drop_best": True}
    assert result.numeric_passed
    main_line, all_line = result.lines
    assert (main_line.trades, main_line.days, main_line.days_in_plus) == (4, 3, 3)
    assert (main_line.earned, main_line.lost, main_line.net) == (45, 10, 35)
    assert main_line.costs == 4  # 4 trades x 0.1% of 1000
    assert main_line.dropped == 1 and main_line.net_without_best == 15  # best +20 removed
    assert all_line.sessions == "all" and all_line.position_usd == ALL
    assert (all_line.earned, all_line.lost, all_line.net) == (180, 60, 120)
    assert all_line.days_in_plus == 3 and all_line.days == 4


def test_months_criterion_counts_empty_month_as_not_in_plus():
    # July in plus is outside the window; September has no trades; only October is in plus.
    trades = [_t("2026-07-01", "9"), _t("2026-08-03", "-1"), _t("2026-10-05", "2")]
    result = _gate(trades)
    line = result.lines[0]
    assert [(m.month, m.trades, m.in_plus) for m in line.months] == [
        ("2026-08", 1, False), ("2026-09", 0, False), ("2026-10", 1, True)]
    assert line.months_in_plus == 1
    assert result.criteria == {"net": True, "months": False, "drop_best": True}
    assert not result.numeric_passed


def test_window_crosses_the_year():
    result = _gate([_t("2025-11-28", "1"), _t("2026-01-02", "1")])
    assert result.window == ("2025-11", "2025-12", "2026-01")


def test_drop_best_fails_when_one_trade_carries_the_period():
    trades = [_t("2026-08-03", "10"), *(_t("2026-09-01", "-0.1") for _ in range(50)),
              _t("2026-10-05", "0.5")]
    result = _gate(trades)
    line = result.lines[0]
    assert line.net > 0
    assert line.dropped == 1  # ceil(52 x 1%)
    assert line.net_without_best < 0
    assert result.criteria["drop_best"] is False


def test_drop_count_rounds_up():
    trades = [_t("2026-08-03", "1")] * 101 + [_t("2026-10-05", "1")]
    assert _gate(trades).lines[0].dropped == 2  # ceil(102 x 1%)
    assert _gate(trades, drop="0").lines[0].dropped == 0


def test_criterion_must_pass_on_both_lines():
    trades = [_t("2026-08-03", "1"), _t("2026-09-01", "1"), _t("2026-10-05", "1"),
              _t("2026-10-05", "-5", "extended")]
    result = _gate(trades)
    assert result.lines[0].passed["net"] and not result.lines[1].passed["net"]
    assert result.criteria["net"] is False


def test_all_sessions_line_says_regular_only_without_extended_trades():
    result = _gate([_t("2026-08-03", "1"), _t("2026-10-05", "1")])
    assert result.lines[1].sessions == "all (regular only: no extended trades)"


@pytest.mark.parametrize(
    ("trades", "cause"),
    [
        ([], GateRefused.EMPTY_PERIOD),
        ([_t("2026-08-03", "1", "extended"), _t("2026-10-03", "1", "extended")],
         GateRefused.EMPTY_PERIOD),
        ([_t("2026-09-03", "1"), _t("2026-10-30", "1")], GateRefused.TOO_FEW_MONTHS),
    ],
)
def test_gate_refusals(trades, cause):
    with pytest.raises(GateRefused) as info:
        _gate(trades)
    assert info.value.cause == cause


def test_non_finite_line_size_refused():
    with pytest.raises(GateRefused) as info:
        gate([_t("2026-08-03", "1")], CAP, Decimal("NaN"), 2, 3, Decimal(1))
    assert info.value.cause == GateRefused.NOT_FINITE


HEADER = ["day", "session", "result_pct", "cost_pct"]


@pytest.mark.parametrize(
    ("header", "row", "cause"),
    [
        (["day", "session", "result_pct"], ("2026-08-03", "regular", "1"),
         GateRefused.MISSING_COLUMN),
        (HEADER, ("2026-08-03", "regular", "NaN", "0.1"), GateRefused.NOT_FINITE),
        (HEADER, ("2026-08-03", "regular", "1", "Infinity"), GateRefused.NOT_FINITE),
        (HEADER, ("2026-08-03", "regular", "x", "0.1"), GateRefused.BAD_VALUE),
        (HEADER, ("2026-08-03", "regular", "1", "-0.1"), GateRefused.BAD_VALUE),
        (HEADER, ("2026-08-03", "premarket", "1", "0.1"), GateRefused.BAD_VALUE),
        (HEADER, ("03.08.2026", "regular", "1", "0.1"), GateRefused.BAD_VALUE),
    ],
)
def test_load_refusals(tmp_path, header, row, cause):
    path = _write(tmp_path / "t.csv", header, [row])
    with pytest.raises(GateRefused) as info:
        load_trades(path)
    assert info.value.cause == cause


def test_empty_file_refused(tmp_path):
    with pytest.raises(GateRefused) as info:
        load_trades(_write(tmp_path / "t.csv", HEADER, []))
    assert info.value.cause == GateRefused.EMPTY_PERIOD


def _cli(tmp_path, path):
    out = tmp_path / "o"
    code = main(["gate", "--trades", str(path), "--all-sessions-usd", "4000",
                 "--model-version", "layer2-test", "--results", "simulated",
                 "--settings", SETTINGS, "--out", str(out), *CLOCK])
    return code, out


def test_cli_writes_csv_and_json(tmp_path, capsys):
    path = _write(tmp_path / "t.csv", ["outcome", "p_layer2", *HEADER],
                  [("1", "0.7", "2026-08-03", "regular", "1", "0.1"),
                   ("0", "0.4", "2026-09-01", "regular", "-0.5", "0.1"),
                   ("1", "0.8", "2026-10-05", "regular", "2", "0.1")])
    code, out = _cli(tmp_path, path)
    assert code == 0
    printed = capsys.readouterr().out
    assert '"numeric_criteria_passed": true' in printed
    with open(out / "gate.csv", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["line"], r["criterion"], r["passed"]) for r in rows[:3]] == [
        ("main_session", "net", "True"), ("main_session", "months", "True"),
        ("main_session", "drop_best", "True")]
    assert rows[0]["measured"] == "25.0000"
    assert [r["criterion"] for r in rows if r["passed"] == "checked by hand"] == list(MANUAL)
    summary = json.loads((out / "gate_summary.json").read_text())
    assert summary["model_version"] == "layer2-test" and summary["label"] == "simulated"
    assert summary["period"] == {"first_day": "2026-08-03", "last_day": "2026-10-05"}
    assert summary["lines"]["all_sessions"]["net"] == "100.0000"
    assert summary["lines"]["main_session"]["costs"] == "3.0000"
    assert all(v == "checked by hand" for v in summary["checked_by_hand"].values())
    with open(out / "gate_months.csv", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 6
    assert (out / "gate_lines.csv").exists()


def test_cli_refuses_with_cause(tmp_path, capsys):
    path = _write(tmp_path / "t.csv", HEADER, [("2026-10-05", "regular", "1", "0.1")])
    code, _ = _cli(tmp_path, path)
    assert code == 2
    assert "too_few_months" in capsys.readouterr().err
