"""GET /advisor: the AI advisor's runs as the advisor and its judge wrote them, read-only."""

from __future__ import annotations

import json
import threading
from dataclasses import replace

import pytest
from advisor.test_advisor import FakeClient, answer
from advisor.test_backtest_advisor import IDEA, make_root, run, write_results

from tickr.advisor.judge import judge
from tickr.advisor.record import DECISION_FILE, DECISION_SCHEMA, RUN_FILE
from tickr.api import contract
from tickr.api.advisor import NO_FOLDER, advisor_document
from tickr.api.server import make_server, settings_from_config
from tickr.config import ConfigError

from .samples import T0
from .schema_check import errors
from .test_server import Clock, doc, request


def _proposal_and_judge(tmp_path):
    cfg = make_root(tmp_path)
    result = run(tmp_path, FakeClient(answer([("breakout", "lookback_bars", "8")])), cfg=cfg)
    judge(result.folder, write_results(tmp_path / "candidate", version="2", hold_low="0.05",
                                       hold_high="0.40"))
    return cfg, result.folder


def test_a_judged_proposal_as_the_advisor_wrote_it(tmp_path):
    cfg, folder = _proposal_and_judge(tmp_path)
    settings = replace(settings_from_config(cfg), port=0, advisor_dir=folder.parent)
    server = make_server(settings, Clock(T0))
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        resp, payload = request(server, "GET", "/advisor")
        body = doc(payload)
        assert resp.status == 200
        assert errors(contract.json_schema(contract.AdvisorRunsV1), body) == []
        assert request(server, "POST", "/advisor", {})[0].status == 405
    finally:
        server.shutdown()
        server.server_close()
    assert body["awaiting_review"] == 1 and body["unreadable"] == [] and body["cause"] is None
    (shown,) = body["runs"]
    assert shown["id"] == folder.name and shown["mode"] == "backtest"
    assert (shown["status"], shown["base"], shown["proposed"]) == (
        "proposed", "price-action-v1 v1", "price-action-v1 v2")
    (change,) = shown["changes"]
    assert (change["section"], change["key"], change["proposed"]) == (
        "breakout", "lookback_bars", "8")
    assert change["now"].isdigit() and change["now"] != "8"  # the base value, as text
    (verdict,) = shown["judges"]
    assert (verdict["candidate"], verdict["verdict"], verdict["base_verdict"]) == (
        "price-action-v1 v2", "edge", "no_evidence")
    assert verdict["holdout"]["mean_r_interval_low"] == "0.05"
    assert verdict["fitting"]["trades"] == 6 and shown["decision"] is None


def test_decision_and_idea_and_unreadable_folders(tmp_path):
    cfg = make_root(tmp_path)
    idea = run(tmp_path, FakeClient(answer([], idea=IDEA)), cfg=cfg).folder
    out = idea.parent
    (out / DECISION_FILE).write_text("{}")  # not a run folder name: ignored
    (idea / DECISION_FILE).write_text(json.dumps(
        {"schema": DECISION_SCHEMA, "verdict": "accepted", "at": T0.isoformat()}))
    old = out / "20261101T000000Z-price-action-v1"
    old.mkdir()
    broken = out / "20261102T000000Z-price-action-v1"
    broken.mkdir()
    raw = json.loads((idea / RUN_FILE).read_text())
    (broken / RUN_FILE).write_text(json.dumps({**raw, "status": "maybe"}))
    document = advisor_document(out, 10)
    assert [r.id for r in document.runs] == [idea.name]
    (shown,) = document.runs
    assert shown.status is contract.AdvisorRunStatus.IDEA and shown.idea.name == "pullback-v1"
    assert shown.decision.verdict is contract.OwnerVerdict.ACCEPTED
    assert document.awaiting_review == 0  # decided
    assert [(u.id, u.cause.split(" ")[0]) for u in document.unreadable] == [
        (broken.name, "run.json:"), (old.name, "no")]
    assert advisor_document(out, 1).unreadable == ()  # only the newest folder is listed


def test_no_folder_yet(tmp_path):
    assert advisor_document(tmp_path / "missing", 5) == contract.AdvisorRunsV1(
        (), (), 0, NO_FOLDER)


def test_awaiting_counts_runs_beyond_the_listed(tmp_path):
    cfg, folder = _proposal_and_judge(tmp_path)
    second = folder.parent / "20991231T000000Z-price-action-v1"
    second.mkdir()
    (second / RUN_FILE).write_text((folder / RUN_FILE).read_text())
    document = advisor_document(folder.parent, 1)
    assert [r.id for r in document.runs] == [second.name] and document.awaiting_review == 2


def test_advisor_runs_must_be_positive(tmp_path):
    cfg = make_root(tmp_path)
    with pytest.raises(ConfigError):
        settings_from_config(replace(cfg, api=replace(cfg.api, advisor_runs=0)))
