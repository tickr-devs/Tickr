"""GET /advisor: the AI advisor's runs for the apps' Lab and the Overview tag, read-only.

Each run folder under `[advisor] proposals_dir` holds `run.json` (advisor-run-v1), any
`judge-*.json` (advisor-judge-v1) and the owner's `decision.json` (advisor-decision-v1); the
formats are in advisor.record. A folder that cannot be read exactly (no run.json: a run from
before these files, a missing key, an unknown status) is listed under `unreadable` with its
cause, never guessed. Nothing here copies, loads or runs a strategy.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from tickr.advisor import record
from tickr.api import contract as api

RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]*$")
"""advisor.run names each folder "<%Y%m%dT%H%M%SZ>-<strategy name>"."""
NO_FOLDER = "no advisor run yet: the proposals folder does not exist"
_AWAITING = (api.AdvisorRunStatus.PROPOSED, api.AdvisorRunStatus.IDEA)


class Unreadable(ValueError):
    """A run file is not exactly its format; the message is the cause."""


def advisor_document(folder: Path, rows: int) -> api.AdvisorRunsV1:
    if not folder.is_dir():
        return api.AdvisorRunsV1((), (), 0, NO_FOLDER)
    ids = sorted((p.name for p in folder.iterdir() if p.is_dir() and RUN_ID.match(p.name)),
                 reverse=True)
    runs: list[api.AdvisorRunV1] = []
    unreadable: list[api.AdvisorUnreadableV1] = []
    awaiting = 0
    for index, run_id in enumerate(ids):
        try:
            run = read_run(folder / run_id)
        except Unreadable as exc:
            if index < rows:
                unreadable.append(api.AdvisorUnreadableV1(run_id, str(exc)))
            continue
        if run.status in _AWAITING and run.decision is None:
            awaiting += 1
        if index < rows:
            runs.append(run)
    return api.AdvisorRunsV1(tuple(runs), tuple(unreadable), awaiting, None)


def read_run(path: Path) -> api.AdvisorRunV1:
    raw = _json(path / record.RUN_FILE, record.RUN_SCHEMA)
    judges = tuple(_judge(p) for p in sorted(path.glob(f"{record.JUDGE_PREFIX}*.json")))
    decision_path = path / record.DECISION_FILE
    decision = _decision(decision_path) if decision_path.exists() else None
    try:
        idea = raw["idea"]
        return api.AdvisorRunV1(
            id=path.name,
            at=_time(raw["at"]),
            mode=api.AdvisorMode(raw["mode"]),
            status=api.AdvisorRunStatus(raw["status"]),
            base=_text(raw["base"]),
            proposed=None if raw["proposed"] is None else _text(raw["proposed"]),
            model=_text(raw["model"]),
            evidence=_text(raw["evidence"]),
            tuned_on=_text(raw["tuned_on"]),
            judge_on=_text(raw["judge_on"]),
            summary=_text(raw["summary"]),
            changes=tuple(api.AdvisorChangeV1(*(_text(c[k]) for k in (
                "section", "key", "now", "proposed", "reason", "evidence")))
                for c in raw["changes"]),
            expected_effect=_text(raw["expected_effect"]),
            risks=tuple(_text(r) for r in raw["risks"]),
            no_change_reason=_text(raw["no_change_reason"]),
            cause=_text(raw["cause"]),
            idea=None if idea is None else api.AdvisorIdeaV1(*(_text(idea[k]) for k in (
                "name", "hypothesis", "entry_rule", "exit_rule", "filters", "evidence",
                "test_plan"))),
            judges=judges,
            decision=decision,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Unreadable(f"{record.RUN_FILE}: {type(exc).__name__}: {exc}") from exc


def _json(path: Path, schema: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text("utf-8"))
    except FileNotFoundError as exc:
        raise Unreadable(f"no {path.name} (a run written before the apps read runs, or "
                         "an interrupted one)") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unreadable(f"{path.name}: {type(exc).__name__}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != schema:
        raise Unreadable(f"{path.name} is not {schema}")
    return raw


def _judge(path: Path) -> api.AdvisorJudgeV1:
    raw = _json(path, record.JUDGE_SCHEMA)
    try:
        return api.AdvisorJudgeV1(
            candidate=_text(raw["candidate"]),
            base=_text(raw["base"]),
            holdout_starts=date.fromisoformat(raw["holdout_starts"]),
            verdict=api.HoldoutVerdict(raw["verdict"]),
            base_verdict=api.HoldoutVerdict(raw["base_verdict"]),
            holdout=_stats(raw["holdout"]),
            base_holdout=_stats(raw["base_holdout"]),
            fitting=_stats(raw["fitting"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise Unreadable(f"{path.name}: {type(exc).__name__}: {exc}") from exc


def _decision(path: Path) -> api.AdvisorDecisionV1:
    raw = _json(path, record.DECISION_SCHEMA)
    try:
        return api.AdvisorDecisionV1(api.OwnerVerdict(raw["verdict"]), _time(raw["at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise Unreadable(f"{path.name}: {type(exc).__name__}: {exc}") from exc


def _stats(raw: Any) -> api.AdvisorStatsV1 | None:
    if raw is None:
        return None
    interval = raw["mean_r_interval"]
    if interval is not None and len(interval) != len(("low", "high")):
        raise ValueError(f"mean_r_interval is not two numbers: {interval!r}")
    trades = raw["trades"]
    return api.AdvisorStatsV1(
        trades=None if trades is None else int(trades),
        win_rate_percent=_number(raw["win_rate_percent"]),
        mean_r=_number(raw["mean_r"]),
        median_r=_number(raw["median_r"]),
        total_r=_number(raw["total_r"]),
        profit_factor=_number(raw["profit_factor"]),
        mean_r_interval_low=None if interval is None else _number(interval[0]),
        mean_r_interval_high=None if interval is None else _number(interval[-1]),
    )


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"number {value!r} is not text")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError(f"number {value!r} is not finite")
    return number


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{value!r} is not text")
    return value


def _time(value: Any) -> datetime:
    at = datetime.fromisoformat(_text(value))
    if at.tzinfo is None:
        raise ValueError(f"time {value!r} has no zone")
    return at
