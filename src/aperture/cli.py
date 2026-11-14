"""Command line entry point. Commands that only read local files; trading commands come later."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aperture import config as config_module


def _check_config(path: Path) -> int:
    text = path.read_text("utf-8")
    problems = config_module.uncommented_keys(text)
    for line, key in problems:
        print(f"{path}:{line}: key {key!r} has no comment")
    cfg = config_module.load(path)
    gate = config_module.trading_gate(cfg)
    print(f"environment: {cfg.environment_name}")
    print(f"config digest: {cfg.digest}")
    print(f"active strategy: {cfg.strategy.active}")
    print(f"trading gate: {'open' if gate.open else 'closed'} ({gate.cause})")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aperture")
    parser.add_argument("--config", type=Path, default=Path("config/bot.toml"),
                        help="path to bot.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config", help="validate config and show the trading gate")
    args = parser.parse_args(argv)
    try:
        if args.command == "check-config":
            return _check_config(args.config)
    except config_module.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
