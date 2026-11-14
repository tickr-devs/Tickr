"""Run the bot as a child of the launchd job and report how it ended.

launchd stops a job with SIGTERM to the job's process. The supervisor's handler only records the
request (see signals.py); the supervision loop then forwards one SIGTERM to the bot and waits for
it to finish its own clean shutdown. The supervisor never restarts the bot: a non-zero exit is
reported as a crash (spec section 11: no automatic relaunch after a crash).
"""

from __future__ import annotations

import signal
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from aperture.runtime.signals import StopRequest

Spawn = Callable[[Sequence[str]], subprocess.Popen[bytes]]


@dataclass(frozen=True)
class ChildExit:
    return_code: int
    stop_forwarded: bool

    @property
    def clean(self) -> bool:
        return self.return_code == 0

    def describe(self) -> str:
        if self.return_code < 0:
            how = f"killed by signal {signal.Signals(-self.return_code).name}"
        else:
            how = f"exited with code {self.return_code}"
        return f"bot {how}" + (" after a stop request" if self.stop_forwarded else "")


def _spawn(command: Sequence[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(list(command))


def supervise(
    command: Sequence[str],
    *,
    poll_seconds: Decimal,
    stop: StopRequest,
    spawn: Spawn = _spawn,
) -> ChildExit:
    """Start `command`, forward at most one SIGTERM when `stop` is requested, wait for the end."""
    if not command:
        raise ValueError("no bot command given")
    child = spawn(command)
    forwarded = False
    timeout = float(poll_seconds)
    while True:
        try:
            return ChildExit(child.wait(timeout=timeout), forwarded)
        except subprocess.TimeoutExpired:
            pass
        if stop.requested and not forwarded:
            child.send_signal(signal.SIGTERM)
            forwarded = True
