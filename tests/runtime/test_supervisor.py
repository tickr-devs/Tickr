from __future__ import annotations

import os
import signal
import subprocess
import sys
from decimal import Decimal

from aperture.runtime.signals import StopRequest, install_stop_handler, restore_handlers
from aperture.runtime.supervisor import ChildExit, supervise

POLL = Decimal("0.05")
# A child that exits 0 on SIGTERM and otherwise waits for a long time.
CLEAN_ON_TERM = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
    "print('ready', flush=True)\n"
    "time.sleep(60)\n"
    "sys.exit(9)\n"
)


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_exit_code_is_reported():
    ended = supervise(_python("import sys; sys.exit(4)"), poll_seconds=POLL, stop=StopRequest())
    assert ended == ChildExit(4, False)
    assert not ended.clean and ended.describe() == "bot exited with code 4"


def test_clean_exit():
    ended = supervise(_python("pass"), poll_seconds=POLL, stop=StopRequest())
    assert ended.clean


def test_stop_request_forwards_one_sigterm():
    stop = StopRequest()
    sent = []

    def spawn(command):
        child = subprocess.Popen(command, stdout=subprocess.PIPE)
        assert child.stdout.readline() == b"ready\n"
        original = child.send_signal

        def send(sig):
            sent.append(sig)
            original(sig)

        child.send_signal = send
        stop._handle(signal.SIGTERM, None)
        return child

    ended = supervise(_python(CLEAN_ON_TERM), poll_seconds=POLL, stop=stop, spawn=spawn)
    assert ended == ChildExit(0, True) and ended.clean
    assert sent == [signal.SIGTERM]


def test_real_sigterm_to_supervisor_reaches_child():
    stop = StopRequest()
    previous = install_stop_handler(stop)

    def spawn(command):
        child = subprocess.Popen(command, stdout=subprocess.PIPE)
        assert child.stdout.readline() == b"ready\n"
        os.kill(os.getpid(), signal.SIGTERM)  # what launchd does to the job process
        return child

    try:
        ended = supervise(_python(CLEAN_ON_TERM), poll_seconds=POLL, stop=stop, spawn=spawn)
    finally:
        restore_handlers(previous)
    assert ended.clean and ended.stop_forwarded


def test_killed_child_described():
    assert ChildExit(-9, False).describe() == "bot killed by signal SIGKILL"
