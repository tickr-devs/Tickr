"""render-systemd (golden files) and the server deploy script's flat gate."""

from __future__ import annotations

import json
import os
import pwd
import re
import stat
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from tickr import config as config_module
from tickr.runtime import __main__ as runtime_main
from tickr.runtime import systemd
from tickr.runtime.__main__ import main
from tickr.types import Position

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "systemd"
SCRIPT = ROOT / "ops" / "systemd" / "deploy-server.sh"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
# A made-up registry: a real one holds the AWS account id, which never enters the repo.
REGISTRY = "registry.example/tickr-bot"
IMAGE = f"{REGISTRY}:{COMMIT}"
PATHS = systemd.Paths(repo=Path("/srv/tickr/repo"), config_host=Path("/srv/tickr/etc/bot.toml"),
                      env_file=Path("/run/tickr/env"), data=Path("/srv/tickr/data"),
                      user="tickr", image=IMAGE)
ZONE = "America/New_York"
# The values the golden files were made with: set in the test config, so the golden files do
# not move when the owner edits config/bot.toml.
PINNED = {"preflight_start_local": '"03:30"', "bot_start_local": '"04:00"',
          "watchdog_start_local": '"03:30"', "nightly_fetch_start_local": '"19:00"',
          "launch_weekdays": "[1, 2, 3, 4, 5]", "bot_exit_timeout_seconds": "120",
          "port": "8766", "remote_port": "8767"}


def _pinned(config_path: Path) -> config_module.Config:
    text = config_path.read_text("utf-8")
    for key, value in PINNED.items():
        text, count = re.subn(rf"(?m)^{key} = .*$", f"{key} = {value}", text)
        assert count >= 1, key
    config_path.write_text(text, "utf-8")
    return config_module.load(config_path)


def _render(config_path: Path, out: Path, paths: systemd.Paths = PATHS) -> list[Path]:
    cfg = _pinned(config_path)
    return systemd.render_all(cfg, config_path.parents[1] / "ops" / "systemd", out,
                              systemd.values(cfg, paths, ZONE))


def test_units_match_the_golden_files(config_path, repo, tmp_path):
    written = _render(config_path, tmp_path / "out")
    golden = sorted(p.name for p in GOLDEN.iterdir())
    assert sorted(p.name for p in written) == golden
    for path in written:
        assert path.read_text("utf-8") == (GOLDEN / path.name).read_text("utf-8"), path.name


def test_times_and_ports_come_from_config_only(config_path, repo, tmp_path):
    _pinned(config_path)
    text = config_path.read_text("utf-8")
    text = text.replace('bot_start_local = "04:00"', 'bot_start_local = "04:15"')
    text = text.replace("remote_port = 8767", "remote_port = 9999")
    config_path.write_text(text, "utf-8")
    cfg = config_module.load(config_path)
    out = tmp_path / "out"
    systemd.render_all(cfg, repo / "ops" / "systemd", out, systemd.values(cfg, PATHS, ZONE))
    assert "OnCalendar=Mon,Tue,Wed,Thu,Fri *-*-* 04:15:00 America/New_York" in (
        out / "tickr-bot.timer").read_text("utf-8")
    assert (out / "ports.env").read_text("utf-8") == (
        "TICKR_API_PORT=8766\nTICKR_API_REMOTE_PORT=9999\n")


def test_templates_hold_no_clock_time(repo):
    for template in (repo / "ops" / "systemd").glob("*.template"):
        assert not re.search(r"\b\d{1,2}:\d{2}\b", template.read_text("utf-8")), template.name


def test_every_job_requires_the_secrets_unit_and_holds_no_value(config_path, repo, tmp_path):
    out = tmp_path / "out"
    _render(config_path, out)
    for job in ("preflight", "bot", "watchdog", "nightly-fetch"):
        text = (out / f"tickr-{job}.service").read_text("utf-8")
        assert "Requires=docker.service tickr-secrets.service\n" in text
        assert "After=docker.service tickr-secrets.service\n" in text
        assert "Environment=TICKR_ENV_FILE_HOST=/run/tickr/env\n" in text
    api = (out / "tickr-api.service").read_text("utf-8")
    assert "tickr-secrets.service" not in api and "TICKR_ENV_FILE_HOST=" not in api


@pytest.mark.parametrize("field,value", [
    ("repo", "/srv/with space"), ("repo", "/srv/a$b"), ("config_host", "/srv/a%h"),
    ("env_file", '/srv/"q"'), ("user", "a b"), ("repo", "relative/repo"),
    ("data", "relative/data"), ("data", "/srv/da ta"), ("image", "tickr-bot"),
    ("image", "registry.example/tickr-bot:a b"), ("image", "Registry/tickr-bot:x"),
    ("image", "registry.example/tickr-bot@sha256:00")])
def test_unsafe_or_relative_values_are_refused(config_path, repo, field, value):
    cfg = _pinned(config_path)
    bad = systemd.Paths(**{**vars(PATHS),
                           field: value if field in ("user", "image") else Path(value)})
    with pytest.raises(config_module.ConfigError):
        systemd.values(cfg, bad, ZONE)


def test_unknown_zone_is_refused(config_path, repo):
    with pytest.raises(config_module.ConfigError):
        systemd.values(_pinned(config_path), PATHS, "Mars/Base")


def test_a_bad_schedule_is_refused_like_launchd(config_path, repo):
    _pinned(config_path)
    text = config_path.read_text("utf-8").replace('bot_start_local = "04:00"',
                                                  'bot_start_local = "25:00"')
    config_path.write_text(text, "utf-8")
    cfg = config_module.load(config_path)
    with pytest.raises(config_module.ConfigError):
        systemd.values(cfg, PATHS, ZONE)


def test_render_systemd_command(config_path, repo, tmp_path, capsys):
    out = tmp_path / "units"
    assert main(["--config", str(config_path), "render-systemd", "--image", IMAGE,
                 "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(p.name for p in GOLDEN.iterdir())
    assert "Environment=TICKR_CONFIG_HOST=/srv/tickr/etc/bot.toml" in (
        out / "tickr-bot.service").read_text("utf-8")
    assert f"TICKR_IMAGE={IMAGE}\n" in (out / "limits.env").read_text("utf-8")


def test_render_systemd_has_no_default_image(config_path, repo, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--config", str(config_path), "render-systemd", "--out", str(tmp_path / "units")])
    assert exit_info.value.code == 2 and "--image" in capsys.readouterr().err
    assert not (tmp_path / "units").exists()


class _Broker:
    def __init__(self, positions=(), orders=()):
        self._positions, self._orders = positions, orders

    def positions(self):
        return list(self._positions)

    def open_orders(self):
        return list(self._orders)


def _require(config_path, monkeypatch, broker, capsys, open_trades=0):
    monkeypatch.setattr(runtime_main, "FLAT_BROKER", lambda cfg, pending: broker)
    monkeypatch.setattr(runtime_main.watchdog, "open_trades", lambda database: open_trades)
    code = main(["--config", str(config_path), "require-flat"])
    row = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return code, row


def test_require_flat_passes_only_when_everything_is_flat(config_path, monkeypatch, capsys):
    code, row = _require(config_path, monkeypatch, _Broker(), capsys)
    assert code == 0 and row["flat"] is True


@pytest.mark.parametrize("broker,trades,cause", [
    (_Broker(positions=[Position("ABCD", Decimal("10"), None)]), 0, "positions ['ABCD']"),
    (_Broker(), 1, "1 open trade"),
])
def test_require_flat_refuses_when_not_flat(config_path, monkeypatch, capsys, broker, trades,
                                            cause):
    code, row = _require(config_path, monkeypatch, broker, capsys, open_trades=trades)
    assert code == 1 and row["flat"] is False and cause in row["cause"]


def test_require_flat_refuses_when_the_read_fails(config_path, monkeypatch, capsys):
    class Broken(_Broker):
        def positions(self):
            raise OSError("down")

    code, row = _require(config_path, monkeypatch, Broken(), capsys)
    assert code == 1 and "broker read failed" in row["cause"]


def _fake_server(tmp_path: Path, flat: bool) -> tuple[Path, Path, Path, Path, Path]:
    """A repo whose `.venv` python renders, a docker that answers `require-flat`, a systemctl
    that only logs, the units folder and the etc folder."""
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    python = repo / ".venv" / "bin" / "python"
    python.write_text(
        '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do [ "$1" = --out ] && out=$2;'
        ' [ "$1" = --data ] && data=$2; [ "$1" = --image ] && image=$2; shift; done\n'
        'mkdir -p "$out"; : > "$out/tickr-bot.service"; : > "$out/tickr-bot.timer"\n'
        'echo "PathExists=$data/control/restart-request" > "$out/tickr-bot-restart.path"\n'
        ': > "$out/ports.env"\n'
        'printf "TICKR_BOT_EXIT_TIMEOUT=1\\nTICKR_IMAGE=%s\\n" "$image" > "$out/limits.env"\n',
        "utf-8")
    # The fake docker: `image inspect REF` succeeds only for the images in PRESENT (default: the
    # release image of COMMIT); a compose command fails unless TICKR_IMAGE (from the rendered
    # limits.env) is that image; `--entrypoint id preflight -u|-g` answers IMAGE_UID/IMAGE_GID
    # (default: the data folder's owner, this test's own ids) and require-flat the flat result.
    # Every call is logged.
    docker = tmp_path / "docker"
    docker.write_text(
        f'#!/bin/sh\necho "$@" >> {tmp_path}/docker.log\n'
        'if [ "$1 $2" = "image inspect" ]; then\n'
        f'  for ref in ${{PRESENT-{IMAGE}}}; do [ "$ref" = "$3" ] && exit 0; done; exit 1\nfi\n'
        f'[ "$TICKR_IMAGE" = "{IMAGE}" ] || exit 1\n'
        'case "$*" in\n'
        f'  *"--entrypoint id preflight -u") echo "${{IMAGE_UID:-{os.getuid()}}}" ;;\n'
        f'  *"--entrypoint id preflight -g") echo "${{IMAGE_GID:-{os.getgid()}}}" ;;\n'
        f'  *) exit {0 if flat else 1} ;;\nesac\n', "utf-8")
    mounted = tmp_path / "mountpoint"
    mounted.write_text('#!/bin/sh\n[ "$1" = -q ] && [ ! -e "$2/.not-mounted" ]\n', "utf-8")
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/systemctl.log\n', "utf-8")
    for script in (python, docker, systemctl, mounted):
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    units, etc = tmp_path / "units", tmp_path / "etc"
    units.mkdir()
    etc.mkdir()
    (tmp_path / "registry.env").write_text(f"TICKR_IMAGE_REPOSITORY={REGISTRY}\n", "utf-8")
    return repo, docker, systemctl, units, etc


def _script_env(units: Path, systemctl: Path, docker: Path, **extra: str) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "TICKR_UNITS_DIR": str(units),
            "TICKR_SYSTEMCTL": str(systemctl), "TICKR_DOCKER": str(docker),
            "TICKR_MOUNTPOINT": str(docker.parent / "mountpoint"),
            "TICKR_REGISTRY_FILE": str(docker.parent / "registry.env"),
            "TICKR_SERVICE_USER": pwd.getpwuid(os.getuid()).pw_name, **extra}


def _deploy(tmp_path: Path, flat: bool, commit: str = COMMIT, registry: str | None = None,
            no_registry: bool = False, **extra: str) -> subprocess.CompletedProcess:
    repo, docker, systemctl, units, etc = _fake_server(tmp_path, flat)
    if registry is not None:
        (tmp_path / "registry.env").write_text(registry, "utf-8")
    if no_registry:
        (tmp_path / "registry.env").unlink()
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return subprocess.run(
        [str(SCRIPT), commit, str(repo), str(etc), str(data)], capture_output=True, text=True,
        env=_script_env(units, systemctl, docker, **extra), check=False)


def _nothing_changed(tmp_path: Path) -> None:
    assert list((tmp_path / "units").iterdir()) == [] and list((tmp_path / "etc").iterdir()) == []
    assert not (tmp_path / "systemctl.log").exists()
    assert not (tmp_path / "data" / "control").exists()


@pytest.mark.parametrize("make", [None, "file", "link"])
def test_deploy_script_refuses_and_never_creates_a_missing_data_folder(tmp_path, make):
    data = tmp_path / "data"
    if make == "file":
        data.write_text("", "utf-8")
    elif make == "link":
        (tmp_path / "elsewhere").mkdir()
        data.symlink_to(tmp_path / "elsewhere")
    repo, docker, systemctl, units, etc = _fake_server(tmp_path, True)
    done = subprocess.run(
        [str(SCRIPT), COMMIT, str(repo), str(etc), str(data)], capture_output=True, text=True,
        env=_script_env(units, systemctl, docker), check=False)
    assert done.returncode == 1 and "refused: data-missing" in done.stderr
    assert list(units.iterdir()) == [] and list(etc.iterdir()) == []
    assert not (tmp_path / "systemctl.log").exists()
    assert make is not None or not data.exists()
    assert not (tmp_path / "elsewhere" / "control").exists()


def test_deploy_script_refuses_when_the_data_disk_is_not_mounted(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / ".not-mounted").touch()
    done = _deploy(tmp_path, flat=True)
    assert done.returncode == 1 and "refused: data-not-mounted" in done.stderr
    _nothing_changed(tmp_path)


@pytest.mark.parametrize("extra", [{"IMAGE_UID": "1000"}, {"IMAGE_GID": "1000"}])
def test_deploy_script_refuses_when_the_image_user_does_not_own_the_data(tmp_path, extra):
    if os.getuid() == 1000 or os.getgid() == 1000:
        extra = {name: "1500" for name in extra}
    done = _deploy(tmp_path, flat=True, **extra)
    assert done.returncode == 1 and "refused: data-owner-mismatch" in done.stderr
    _nothing_changed(tmp_path)


def test_every_unit_that_uses_the_data_folder_requires_its_mount(config_path, repo, tmp_path):
    out = tmp_path / "out"
    _render(config_path, out)
    for unit in [*out.glob("*.service"), *out.glob("*.path")]:
        assert "RequiresMountsFor=/srv/tickr/data\n" in unit.read_text("utf-8"), unit.name


def test_deploy_script_refuses_when_not_flat(tmp_path):
    done = _deploy(tmp_path, flat=False)
    assert done.returncode == 1 and "not proven flat" in done.stderr
    _nothing_changed(tmp_path)


def test_deploy_script_installs_the_units_when_flat(tmp_path):
    done = _deploy(tmp_path, flat=True)
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in (tmp_path / "units").iterdir()) == [
        "tickr-bot-restart.path", "tickr-bot.service", "tickr-bot.timer"]
    assert (tmp_path / "etc" / "ports.env").exists() and (tmp_path / "etc" / "limits.env").exists()
    log = (tmp_path / "systemctl.log").read_text("utf-8")
    assert "daemon-reload" in log and "tickr-bot.timer" in log
    assert re.search(r"(?m)^enable .*tickr-bot-restart\.path", log)
    assert re.search(r"(?m)^start .*tickr-bot-restart\.path", log)
    # The control folder: created by the deploy, the service account's only.
    control = tmp_path / "data" / "control"
    assert control.is_dir() and stat.S_IMODE(control.stat().st_mode) == 0o700
    assert control.stat().st_uid == os.getuid()
    # The bot unit is never started or restarted by a deploy.
    assert not re.search(r"(start|restart) .*tickr-bot\.service", log)


def test_the_bots_grace_period_is_the_exit_timeout_from_config_only(config_path, repo, tmp_path):
    cfg = _pinned(config_path)
    out = tmp_path / "out"
    systemd.render_all(cfg, repo / "ops" / "systemd", out, systemd.values(cfg, PATHS, ZONE))
    rendered = (out / "limits.env").read_text("utf-8")
    assert rendered == (f"TICKR_BOT_EXIT_TIMEOUT={cfg.runtime.bot_exit_timeout_seconds}\n"
                        f"TICKR_NIGHTLY_OUT={cfg.runtime.nightly_fetch_out}\n"
                        f"TICKR_NIGHTLY_SEED={cfg.runtime.nightly_fetch_seed}\n"
                        "TICKR_DATA_HOST=/srv/tickr/data\n"
                        f"TICKR_IMAGE={IMAGE}\n")
    compose = (ROOT / "ops" / "docker" / "compose.yaml").read_text("utf-8")
    # compose reads the rendered value and holds no number of its own.
    assert re.findall(r"(?m)^\s*stop_grace_period: (.*)$", compose) == [
        "${TICKR_BOT_EXIT_TIMEOUT:?run render-systemd and load its limits.env}s"]
    for job in ("api", "preflight", "bot", "watchdog", "nightly-fetch"):
        assert "EnvironmentFile=/srv/tickr/etc/limits.env\n" in (
            out / f"tickr-{job}.service").read_text("utf-8")


def test_the_nightly_fetch_unit_runs_the_compose_service_at_the_configured_time(
        config_path, repo, tmp_path):
    cfg = _pinned(config_path)
    out = tmp_path / "out"
    systemd.render_all(cfg, repo / "ops" / "systemd", out, systemd.values(cfg, PATHS, ZONE))
    assert "--profile jobs run --rm nightly-fetch\n" in (
        out / "tickr-nightly-fetch.service").read_text("utf-8")
    assert "OnCalendar=Mon,Tue,Wed,Thu,Fri *-*-* 19:00:00 America/New_York" in (
        out / "tickr-nightly-fetch.timer").read_text("utf-8")
    assert re.search(r"(?m)^  nightly-fetch:$", (ROOT / "ops/docker/compose.yaml").read_text())


def test_deploy_script_installs_nothing_when_a_copy_fails(tmp_path):
    repo, docker, systemctl, units, etc = _fake_server(tmp_path, True)
    # The units stage fine, then the etc files cannot be copied.
    missing = tmp_path / "no-such-etc"
    (tmp_path / "data").mkdir()
    done = subprocess.run(
        [str(SCRIPT), COMMIT, str(repo), str(missing), str(tmp_path / "data")],
        capture_output=True,
        text=True, check=False, env=_script_env(units, systemctl, docker))
    assert done.returncode == 1 and "nothing was installed" in done.stderr
    assert list(units.iterdir()) == []  # no unit, and no staged file left behind
    assert not (tmp_path / "systemctl.log").exists()  # no daemon-reload


def test_deploy_script_refuses_over_a_waiting_restart_request(tmp_path):
    repo, docker, systemctl, units, etc = _fake_server(tmp_path, True)
    control = tmp_path / "data" / "control"
    control.mkdir(parents=True)
    (control / "restart-request").write_text("{}\n", "utf-8")
    done = subprocess.run(
        [str(SCRIPT), COMMIT, str(repo), str(etc), str(tmp_path / "data")], capture_output=True,
        text=True, check=False, env=_script_env(units, systemctl, docker))
    assert done.returncode == 1 and "restart request is waiting" in done.stderr
    assert list(units.iterdir()) == [] and not (tmp_path / "systemctl.log").exists()
    assert (control / "restart-request").exists()  # left for the owner to read


def test_the_restart_unit_restarts_once_with_the_bots_grace_and_never_retries(
        config_path, repo, tmp_path):
    cfg = _pinned(config_path)
    out = tmp_path / "out"
    systemd.render_all(cfg, repo / "ops" / "systemd", out, systemd.values(cfg, PATHS, ZONE))
    service = (out / "tickr-bot-restart.service").read_text("utf-8")
    steps = re.findall(r"(?m)^ExecStart=(.*)$", service)
    assert steps == [
        "/bin/mv -fT /srv/tickr/data/control/restart-request "
        "/srv/tickr/data/control/restart-request.taken",
        "/usr/bin/docker compose -f /srv/tickr/repo/ops/docker/compose.yaml -p tickr "
        "--profile jobs up -d --force-recreate --no-deps --pull never --no-build "
        f"-t {cfg.runtime.bot_exit_timeout_seconds} bot",
        "/bin/cat /srv/tickr/data/control/restart-request.taken"]
    assert "Type=oneshot\n" in service
    assert not re.search(r"(?m)^(Restart|StartLimit|ExecStartPre|ExecStopPost)", service)
    path = (out / "tickr-bot-restart.path").read_text("utf-8")
    assert "PathExists=/srv/tickr/data/control/restart-request\n" in path
    assert "Unit=tickr-bot-restart.service\n" in path


def test_the_compose_project_name_is_tickr_and_data_is_the_host_folder():
    compose = (ROOT / "ops" / "docker" / "compose.yaml").read_text("utf-8")
    assert re.findall(r"(?m)^name: (.*)$", compose) == ["tickr"]
    # No Docker named volume: the host units see the containers' data folder.
    assert not re.search(r"(?m)^volumes:", compose)
    assert compose.count("source: ${TICKR_DATA_HOST:?") == 2
    assert compose.count("target: /app/data\n") == 2


COMPOSE_BOT = """name: tickr
services:
  api:
    stop_grace_period: 5s
  bot:
    image: tickr:local
    # a comment
{grace}    pid: host

  watchdog:
    stop_grace_period: 7s
"""


@pytest.mark.parametrize("grace,values,seconds", [
    ("    stop_grace_period: ${TICKR_BOT_EXIT_TIMEOUT:?load limits.env}s\n",
     {"TICKR_BOT_EXIT_TIMEOUT": "120"}, 120),
    ("    stop_grace_period: 45s\n", {}, 45),
    ('    stop_grace_period: "30"\n', {}, 30),
])
def test_the_bots_stop_grace_is_read_from_compose(grace, values, seconds):
    assert systemd.bot_stop_grace(COMPOSE_BOT.format(grace=grace), values) == seconds


@pytest.mark.parametrize("text,values", [
    (COMPOSE_BOT.format(grace=""), {}),  # none on the bot (api and watchdog's do not count)
    (COMPOSE_BOT.format(grace="    stop_grace_period: ${OTHER}s\n"), {}),  # not rendered
    (COMPOSE_BOT.format(grace="    stop_grace_period: 1m30s\n"), {}),  # another form
    (COMPOSE_BOT.format(grace="    stop_grace_period: 0s\n"), {}),
    (COMPOSE_BOT.format(grace="    stop_grace_period: ${T}s\n"), {"T": "soon"}),
    ("name: tickr\nservices:\n  api:\n    image: x\n", {}),  # no bot service
    ("name: tickr\n", {}),
])
def test_a_missing_or_unreadable_stop_grace_is_refused(text, values):
    with pytest.raises(config_module.ConfigError):
        systemd.bot_stop_grace(text, values)


def test_render_refuses_when_compose_gives_the_bot_no_grace(config_path, repo, tmp_path):
    cfg = _pinned(config_path)
    compose = repo / "ops" / "docker" / "compose.yaml"
    text = compose.read_text("utf-8")
    compose.write_text(re.sub(r"(?m)^\s*stop_grace_period: .*\n", "", text), "utf-8")
    with pytest.raises(config_module.ConfigError, match="no stop_grace_period"):
        systemd.values(cfg, PATHS, ZONE)


@pytest.mark.parametrize("commit", [
    "", "main", COMMIT[:12], COMMIT + "0", COMMIT.upper(), COMMIT[:-1] + "g",
    f"{COMMIT[:20]} {COMMIT[21:]}"])
def test_deploy_script_refuses_a_bad_commit(tmp_path, commit):
    done = _deploy(tmp_path, flat=True, commit=commit)
    assert done.returncode == 1 and "refused: bad-commit" in done.stderr
    _nothing_changed(tmp_path)
    assert not (tmp_path / "docker.log").exists()


def test_deploy_script_needs_the_commit(tmp_path):
    repo, docker, systemctl, units, etc = _fake_server(tmp_path, True)
    done = subprocess.run([str(SCRIPT)], capture_output=True, text=True, check=False,
                          env=_script_env(units, systemctl, docker))
    assert done.returncode == 2 and "usage:" in done.stderr and "COMMIT" in done.stderr


@pytest.mark.parametrize("registry", [
    None,  # no file
    "",  # empty file
    "AWS_DEFAULT_REGION=us-east-1\n",  # another variable only
    "TICKR_IMAGE_REPOSITORY=\n",
    f'TICKR_IMAGE_REPOSITORY="{REGISTRY}"\n',
    f"TICKR_IMAGE_REPOSITORY={REGISTRY}\nTICKR_IMAGE_REPOSITORY={REGISTRY}\n",
    f"TICKR_IMAGE_REPOSITORY={REGISTRY} extra\n",
    "TICKR_IMAGE_REPOSITORY=tickr-bot\n",  # no registry host
])
def test_deploy_script_refuses_an_unknown_registry(tmp_path, registry):
    done = _deploy(tmp_path, flat=True, registry=registry, no_registry=registry is None)
    assert done.returncode == 1 and "refused: registry-unknown" in done.stderr
    _nothing_changed(tmp_path)
    assert not (tmp_path / "docker.log").exists()


def test_deploy_script_reads_only_the_repository_from_the_registry_file(tmp_path):
    other = "TICKR_SOMETHING_ELSE=do-not-print-0451\n"
    done = _deploy(tmp_path, flat=True,
                   registry=f"# from user-data\n{other}TICKR_IMAGE_REPOSITORY={REGISTRY}\n")
    assert done.returncode == 0, done.stderr
    assert "do-not-print-0451" not in done.stdout + done.stderr
    # The registry address (an AWS account id on the server) is never printed either.
    assert REGISTRY not in done.stdout + done.stderr
    assert (tmp_path / "etc" / "limits.env").read_text("utf-8").endswith(f"TICKR_IMAGE={IMAGE}\n")


@pytest.mark.parametrize("present", ["", f"{REGISTRY}:{'f' * 40}", f"tickr:local {REGISTRY}"])
def test_deploy_script_refuses_when_the_release_image_is_not_on_the_host(tmp_path, present):
    done = _deploy(tmp_path, flat=True, PRESENT=present)
    assert done.returncode == 1 and "refused: image-missing" in done.stderr
    assert f"tickr-image-pull {COMMIT}" in done.stderr and REGISTRY not in done.stderr
    _nothing_changed(tmp_path)
    # Only the inspect ran: no pull, no compose command.
    assert (tmp_path / "docker.log").read_text("utf-8") == f"image inspect {IMAGE}\n"


def test_deploy_script_checks_the_uid_and_flat_in_the_release_image_and_never_pulls(tmp_path):
    done = _deploy(tmp_path, flat=True)
    assert done.returncode == 0, done.stderr
    calls = (tmp_path / "docker.log").read_text("utf-8").splitlines()
    assert calls[0] == f"image inspect {IMAGE}"
    # The fake docker fails every compose call whose TICKR_IMAGE is not the release image, so
    # the uid check and require-flat ran against it; none of them may pull.
    compose = calls[1:]
    assert len(compose) == 3 and sum("--entrypoint id preflight" in c for c in compose) == 2
    assert all(c.startswith("compose ") and "--pull never" in c for c in compose)


def test_deploy_script_refuses_when_the_uid_check_cannot_run_the_release_image(tmp_path):
    # The rendered limits.env names another image: the fake docker's compose calls fail.
    repo, docker, systemctl, units, etc = _fake_server(tmp_path, True)
    python = repo / ".venv" / "bin" / "python"
    python.write_text(python.read_text("utf-8").replace('"$image"', '"tickr:local"'), "utf-8")
    (tmp_path / "data").mkdir()
    done = subprocess.run(
        [str(SCRIPT), COMMIT, str(repo), str(etc), str(tmp_path / "data")], capture_output=True,
        text=True, check=False, env=_script_env(units, systemctl, docker))
    assert done.returncode == 1 and "refused: image-user-unknown" in done.stderr
    _nothing_changed(tmp_path)


def test_compose_runs_only_the_image_it_is_given():
    compose = (ROOT / "ops" / "docker" / "compose.yaml").read_text("utf-8")
    assert not re.search(r"(?m)^\s*build:", compose)
    assert re.findall(r"(?m)^\s*image: (.*)$", compose) == [
        "${TICKR_IMAGE:?image to run; on the server deploy-server.sh writes it into limits.env}"]
    assert re.findall(r"(?m)^\s*pull_policy: (.*)$", compose) == ["never"]


COMPOSE_VALUES = {"TICKR_CONFIG_HOST": "/srv/tickr/etc/bot.toml",
                  "TICKR_ENV_FILE_HOST": "/run/tickr/env", "TICKR_BOT_EXIT_TIMEOUT": "1",
                  "TICKR_NIGHTLY_OUT": "data/nightly", "TICKR_NIGHTLY_SEED": "data/backtest",
                  "TICKR_DATA_HOST": "/srv/tickr/data"}


def _compose_config(*args: str, **values: str) -> subprocess.CompletedProcess:
    # Only named variables reach compose (CLAUDE.md rule 10): never this process's environment.
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", **values}
    return subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "ops" / "docker" / "compose.yaml"),
         "--profile", "jobs", "config", *args], capture_output=True, text=True, check=False,
        env=env)


def _has_compose() -> bool:
    try:
        return _compose_config("--help").returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _has_compose(), reason="docker compose is not installed here "
                    "(ci.yml's docker-image job runs the same check)")
def test_compose_config_resolves_every_service_to_tickr_image():
    done = _compose_config("--images", **COMPOSE_VALUES, TICKR_IMAGE=IMAGE)
    assert done.returncode == 0, done.stderr
    assert set(done.stdout.split()) == {IMAGE}
    missing = _compose_config("-q", **COMPOSE_VALUES)
    assert missing.returncode != 0 and "TICKR_IMAGE" in missing.stderr
