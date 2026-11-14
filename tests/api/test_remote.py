"""Remote access through an HTTPS proxy on the Mac (`tailscale serve`): bearer token required."""

from __future__ import annotations

import hashlib
import stat
import threading
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from aperture import config
from aperture.api import __main__ as api_main
from aperture.api import contract
from aperture.api.server import make_server, pair, settings_from_config

from .samples import T0
from .schema_check import errors
from .test_server import ROOT, Clock, doc, request, schema

REMOTE = "mac.example-tailnet.ts.net"


@pytest.fixture
def remote_settings(tmp_path):
    cfg = config.load(ROOT / "config" / "bot.toml", root=tmp_path)
    cfg = replace(cfg, api=replace(cfg.api, remote_hosts=(REMOTE,)))
    return replace(settings_from_config(cfg), port=0)


@pytest.fixture
def remote_api(remote_settings):
    server = make_server(remote_settings, Clock(T0 + timedelta(seconds=2)))
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def remote(server, method, path, token=None, body=None, headers=None):
    sent = dict(headers or {})
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    return request(server, method, path, body, headers=sent, host=REMOTE)


def test_shipped_config_has_remote_access_off(tmp_path):
    cfg = config.load(ROOT / "config" / "bot.toml", root=tmp_path)
    assert cfg.api.remote_hosts == ()


@pytest.mark.parametrize("name", ["Mac.ts.net", "https://mac.ts.net", "localhost", "mac", "a..b",
                                  "127.0.0.1", "mac.ts.net/x"])
def test_bad_remote_host_refused_at_startup(tmp_path, name):
    cfg = config.load(ROOT / "config" / "bot.toml", root=tmp_path)
    bad = replace(cfg, api=replace(cfg.api, remote_hosts=(name,)))
    with pytest.raises(config.ConfigError, match="remote_hosts"):
        settings_from_config(bad)


def test_remote_host_with_port_allowed(tmp_path):
    cfg = config.load(ROOT / "config" / "bot.toml", root=tmp_path)
    ok = replace(cfg, api=replace(cfg.api, remote_hosts=("mac.ts.net:8443",)))
    assert settings_from_config(ok).remote_hosts == ("mac.ts.net:8443",)


def test_remote_refused_before_pairing(remote_api):
    resp, payload = remote(remote_api, "GET", "/status", token="anything")
    assert resp.status == 401
    assert doc(payload)["error"] == "unauthorized"
    assert "pair" in doc(payload)["cause"]
    assert resp.getheader("WWW-Authenticate") == "Bearer"
    assert errors(schema("error-v1"), doc(payload)) == []


def test_pairing_stores_only_the_hash(remote_settings):
    token = pair(remote_settings)
    stored = remote_settings.token_hash_path.read_text("ascii").strip()
    assert stored == hashlib.sha256(token.encode()).hexdigest()
    assert token not in remote_settings.token_hash_path.read_text("ascii")
    assert stat.S_IMODE(remote_settings.token_hash_path.stat().st_mode) == 0o600
    assert len(token) >= remote_settings.token_bytes


def test_pairing_needs_a_remote_host(tmp_path):
    cfg = config.load(ROOT / "config" / "bot.toml", root=tmp_path)
    with pytest.raises(config.ConfigError, match="remote_hosts is empty"):
        pair(settings_from_config(cfg))


def test_paired_token_reads_status_and_queues_commands(remote_api, remote_settings):
    token = pair(remote_settings)
    resp, _ = remote(remote_api, "GET", "/status", token=token)
    assert resp.status == 200
    resp, payload = remote(remote_api, "POST", "/commands/entry-stop", token=token,
                           body={"state": "on"})
    assert resp.status == 202
    assert doc(payload)["command"]["kind"] == "entry-stop-on"
    assert contract.count_inbox(remote_settings.inbox) == 1


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Basic abc", "bearer {token}",
                                    "Bearer {token}x", "Bearer  {token}"])
def test_wrong_or_missing_token_refused_and_nothing_queued(remote_api, remote_settings, header):
    token = pair(remote_settings)
    headers = {} if header is None else {"Authorization": header.format(token=token)}
    resp, payload = remote(remote_api, "POST", "/commands/flatten", body={}, headers=headers)
    assert resp.status == 401
    assert doc(payload)["error"] == "unauthorized"
    assert token not in payload.decode()
    assert contract.count_inbox(remote_settings.inbox) == 0


def test_repairing_revokes_the_old_token(remote_api, remote_settings):
    old = pair(remote_settings)
    new = pair(remote_settings)
    assert remote(remote_api, "GET", "/status", token=old)[0].status == 401
    assert remote(remote_api, "GET", "/status", token=new)[0].status == 200


def test_remote_origin_must_match(remote_api, remote_settings):
    token = pair(remote_settings)
    ok = remote(remote_api, "GET", "/status", token=token, headers={"Origin": f"https://{REMOTE}"})
    assert ok[0].status == 200
    resp, payload = remote(remote_api, "POST", "/commands/flatten", token=token, body={},
                           headers={"Origin": f"http://{REMOTE}"})
    assert resp.status == 403
    assert doc(payload)["error"] == "forbidden_origin"
    assert contract.count_inbox(remote_settings.inbox) == 0


def test_loopback_needs_no_token(remote_api):
    resp, _ = request(remote_api, "GET", "/status")
    assert resp.status == 200


def test_remote_name_with_loopback_port_is_not_local(remote_api):
    resp, _ = request(remote_api, "GET", "/status", host=f"{REMOTE}:{remote_api.server_port}")
    assert resp.status == 403


def test_pair_command_prints_a_link_once(tmp_path, capsys, monkeypatch):
    text = (ROOT / "config" / "bot.toml").read_text("utf-8").replace(
        "remote_hosts = []", f'remote_hosts = ["{REMOTE}"]', 1)
    path = tmp_path / "config" / "bot.toml"
    path.parent.mkdir()
    path.write_text(text, "utf-8")
    for name in ("calendar",):
        (tmp_path / "config" / name).symlink_to(ROOT / "config" / name)
    assert api_main.main(["--config", str(path), "pair"]) == 0
    out = capsys.readouterr().out
    link = next(line for line in out.splitlines() if line.startswith("aperture://pair?"))
    query = parse_qs(urlparse(link).query)
    assert query["url"] == [f"https://{REMOTE}"]
    cfg = config.load(path)
    stored = (cfg.root / cfg.run.data_dir / cfg.api.remote_token_hash_file).read_text().strip()
    assert stored == hashlib.sha256(query["token"][0].encode()).hexdigest()
