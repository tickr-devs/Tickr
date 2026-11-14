"""Run the local API: `python -m aperture.api [--config config/bot.toml] [serve|pair]`.

`pair` makes a new bearer token for the phone (remote access through `[api] remote_hosts`), keeps
only its SHA-256 under data_dir, and shows the token once so the owner can enter it on the phone.
A new pairing replaces the old one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlencode

from aperture import config as config_module
from aperture.api.server import make_server, pair, settings_from_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aperture.api")
    parser.add_argument("--config", type=Path, default=Path("config/bot.toml"),
                        help="path to bot.toml")
    parser.add_argument("command", nargs="?", choices=("serve", "pair"), default="serve",
                        help="serve the API (default) or pair a phone")
    args = parser.parse_args(argv)
    try:
        settings = settings_from_config(config_module.load(args.config))
        if args.command == "pair":
            token = pair(settings)
    except config_module.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if args.command == "pair":
        address = f"https://{settings.remote_hosts[0]}"
        link = "aperture://pair?" + urlencode({"url": address, "token": token})
        print("Paired a new phone token; any earlier phone token no longer works.\n"
              f"On the phone: Settings > Pair with Mac, paste this link:\n\n{link}\n\n"
              "Shown once. The Mac keeps only its SHA-256. Do not paste it anywhere else.")
        return 0
    server = make_server(settings)
    print(f"aperture api on http://{settings.host}:{server.server_port}/", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
