"""Requests under /demo/ go to the demo day's API (api/demo_forward.py): the apps reach the demo
through their usual address, the phone through its paired connection, and the token never leaves
this API."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tickr import config
from tickr.api import contract
from tickr.api.server import make_remote_server, make_server, settings_from_config

from .samples import T0
from .test_remote import REMOTE, paired, remote
from .test_server import ROOT, Clock, doc, request

DEMO_ANSWER = b'{"demo": true}'


class Upstream(ThreadingHTTPServer):
    """Stands in for the demo's API: records each request and answers DEMO_ANSWER."""

    def __init__(self) -> None:
        self.seen: list[dict[str, object]] = []
        super().__init__(("127.0.0.1", 0), _UpstreamHandler)


class _UpstreamHandler(BaseHTTPRequestHandler):
    server: Upstream

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.server.seen.append({"method": self.command, "path": self.path,
                                 "headers": dict(self.headers.items()),
                                 "body": self.rfile.read(length) if length else b""})
        self.send_response(202 if self.command == "POST" else 200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(DEMO_ANSWER)))
        self.send_header("Set-Cookie", "never=passed")
        self.end_headers()
        self.wfile.write(DEMO_ANSWER)

    do_GET = do_POST = _answer  # noqa: N815 - stdlib naming

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass


def _serve(server):
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    return server


@pytest.fixture
def upstream():
    server = _serve(Upstream())
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def settings(tmp_path, upstream):
    cfg = config.load(ROOT / "config" / "bot.toml", root=tmp_path)
    cfg = replace(cfg, api=replace(cfg.api, remote_hosts=(REMOTE,)))
    return replace(settings_from_config(cfg), port=0, remote_port=0,
                   demo_port=upstream.server_port)


@pytest.fixture
def local_api(settings):
    server = _serve(make_server(settings, Clock(T0 + timedelta(seconds=2))))
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def remote_api(local_api):
    server = _serve(make_remote_server(local_api))
    yield server
    server.shutdown()
    server.server_close()


def test_shipped_config_forwards_to_the_demo_port(tmp_path):
    cfg = config.load(ROOT / "config" / "bot.toml", root=tmp_path)
    assert cfg.api.demo_port not in (0, cfg.api.port, cfg.api.remote_port)
    assert settings_from_config(cfg).demo_port == cfg.api.demo_port


@pytest.mark.parametrize("key", ["port", "remote_port"])
def test_demo_port_must_differ_from_the_api_ports(tmp_path, key):
    cfg = config.load(ROOT / "config" / "bot.toml", root=tmp_path)
    bad = replace(cfg, api=replace(cfg.api, demo_port=getattr(cfg.api, key)))
    with pytest.raises(config.ConfigError, match="demo_port"):
        settings_from_config(bad)


def test_get_is_forwarded_with_its_query_and_answered_as_it_came(local_api, upstream):
    resp, payload = request(local_api, "GET", "/demo/days/2026-11-13?x=1")
    assert resp.status == 200 and payload == DEMO_ANSWER
    assert resp.getheader("Set-Cookie") is None  # only the listed headers come back
    seen = upstream.seen[-1]
    assert seen["method"] == "GET" and seen["path"] == "/days/2026-11-13?x=1"
    assert seen["headers"]["Host"] == f"127.0.0.1:{upstream.server_port}"


def test_the_demo_root_is_the_demo_page(local_api, upstream):
    resp, _ = request(local_api, "GET", "/demo")
    assert resp.status == 200 and upstream.seen[-1]["path"] == "/"


def test_a_path_that_only_starts_like_demo_is_not_forwarded(local_api, upstream):
    resp, _ = request(local_api, "GET", "/demonstration")
    assert resp.status == 404 and upstream.seen == []


def test_a_command_goes_to_the_demo_never_to_the_bot(local_api, upstream, settings):
    resp, payload = request(local_api, "POST", "/demo/commands/flatten", body={"symbol": "MVLD"},
                            headers={"Idempotency-Key": "k-1"})
    assert resp.status == 202 and payload == DEMO_ANSWER
    seen = upstream.seen[-1]
    assert seen["path"] == "/commands/flatten"
    assert json.loads(seen["body"]) == {"symbol": "MVLD"}
    assert seen["headers"]["Idempotency-Key"] == "k-1"
    assert seen["headers"]["Content-Type"] == "application/json"
    assert contract.pending_commands(settings.inbox) == []  # the bot's inbox is untouched


def test_an_oversized_demo_body_is_refused_here(local_api, upstream, settings):
    body = b"{" + b" " * settings.max_request_body_bytes + b"}"
    resp, payload = request(local_api, "POST", "/demo/commands/flatten", body=body)
    assert resp.status == 413, payload
    assert upstream.seen == []


def test_remote_needs_the_token_and_never_forwards_it(remote_api, upstream, settings):
    resp, _ = remote(remote_api, "GET", "/demo/status")
    assert resp.status == 401 and upstream.seen == []
    token = paired(remote_api, settings)
    resp, payload = remote(remote_api, "GET", "/demo/status", token=token,
                           headers={"Cookie": "a=b"})
    assert resp.status == 200 and payload == DEMO_ANSWER
    sent = {name.lower() for name in upstream.seen[-1]["headers"]}
    assert "authorization" not in sent and "cookie" not in sent
    assert token.encode() not in upstream.seen[-1]["body"]


def test_no_demo_port_means_not_found(tmp_path):
    cfg = config.load(ROOT / "config" / "bot.toml", root=tmp_path)
    server = _serve(make_server(replace(settings_from_config(cfg), port=0, demo_port=0),
                                Clock(T0)))
    try:
        resp, payload = request(server, "GET", "/demo/status")
        assert resp.status == 404 and doc(payload)["error"] == "not_found"
    finally:
        server.shutdown()
        server.server_close()


def test_demo_not_running_is_unavailable(local_api, upstream):
    upstream.shutdown()
    upstream.server_close()
    resp, payload = request(local_api, "GET", "/demo/status")
    assert resp.status == 503
    assert "local.tickr.demo" in doc(payload)["cause"]
