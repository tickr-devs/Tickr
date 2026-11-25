#!/bin/sh
# Install the Tickr systemd units on a Linux server, and only while the bot is flat.
#
#   ops/systemd/deploy-server.sh [REPO_DIR [ETC_DIR]]
#
#   REPO_DIR  the clone with ops/docker and a built `.venv` (default /srv/tickr/repo)
#   ETC_DIR   folder of bot.toml; ports.env is installed here (default /srv/tickr/etc)
#
# Order: (1) prove flat with a fresh read-only broker read and the database: `require-flat`
# runs in the bot's own image (compose service `preflight`, so the env file is mounted and
# sourced as for a real job) and never sends an order; any doubt refuses and nothing is changed.
# (2) render the units and ports.env into a scratch folder with `render-systemd`; (3) install
# them and reload systemd; (4) enable the timers and the API. The bot is never started or stopped
# here: a running job keeps the code it started with, the next scheduled start uses the new units.
#
# Overrides (names only): TICKR_UNITS_DIR (default /etc/systemd/system), TICKR_SYSTEMCTL
# (default systemctl), TICKR_DOCKER (default docker), TICKR_ENV_FILE_HOST (default
# /run/tickr/env). This script never reads the env file, prints the environment or traces the
# shell (docs/deploy-server.md).
set -eu

if [ "$#" -gt 2 ]; then
  echo "usage: $0 [REPO_DIR [ETC_DIR]]" >&2
  exit 2
fi
repo=${1:-/srv/tickr/repo}
etc=${2:-/srv/tickr/etc}
units_dir=${TICKR_UNITS_DIR:-/etc/systemd/system}
systemctl=${TICKR_SYSTEMCTL:-systemctl}
docker=${TICKR_DOCKER:-docker}
TICKR_CONFIG_HOST="$etc/bot.toml"
TICKR_ENV_FILE_HOST=${TICKR_ENV_FILE_HOST:-/run/tickr/env}
export TICKR_CONFIG_HOST TICKR_ENV_FILE_HOST

cd "$repo"
if ! "$docker" compose -f ops/docker/compose.yaml --profile jobs run --rm preflight \
    python -m tickr.runtime --config /app/config/bot.toml require-flat; then
  echo "refused: the bot is not proven flat (see the require-flat line above); nothing was changed" >&2
  exit 1
fi

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
if ! .venv/bin/python -m tickr.runtime --config "$etc/bot.toml" render-systemd \
    --repo "$repo" --etc "$etc" --env-file "$TICKR_ENV_FILE_HOST" --out "$scratch" >/dev/null; then
  echo "refused: render-systemd failed; run it by hand to read the cause" >&2
  exit 1
fi

cp "$scratch"/tickr-*.service "$scratch"/tickr-*.timer "$units_dir"/
cp "$scratch"/ports.env "$etc"/ports.env
"$systemctl" daemon-reload
"$systemctl" enable tickr-preflight.timer tickr-bot.timer tickr-watchdog.timer tickr-api.service
"$systemctl" start tickr-preflight.timer tickr-bot.timer tickr-watchdog.timer
"$systemctl" restart tickr-api.service
echo "deployed: units installed in $units_dir, ports.env in $etc"
