"""Owner alerts (spec section 11): a macOS notification and an iMessage to the owner's phone.

`Alerter.alert(kind, text)` never raises and never retries. It returns an `AlertOutcome` that the
caller always writes to its journal, whether the notification was shown, suppressed as a repeat,
or could not be delivered. Repeats of the same kind and text inside the configured window are
suppressed so that a condition that persists does not flood the owner.

Alert text must never contain a secret value: callers pass variable NAMES, never contents.
The iMessage recipient is personal data: Aperture code only knows the NAME of the environment
variable that holds it (`[alerts] imessage_recipient_variable`) and checks its presence; the
AppleScript reads the value itself (`system attribute`), so it is never in Python, argv or logs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from aperture.config import AlertsConfig

# Protocol constants of macOS: the AppleScript runner and a script that takes its title and
# text as arguments, so alert text is never parsed as AppleScript.
OSASCRIPT = "/usr/bin/osascript"
_APPLESCRIPT = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
)
# Sends argv item 2 by iMessage to the address held by the environment variable NAMED in argv
# item 1. A missing recipient is an AppleScript error (non-zero exit), never a silent skip.
_IMESSAGE_SCRIPT = (
    "on run argv",
    "set recipient to system attribute (item 1 of argv)",
    'if recipient is "" then error "recipient variable not set"',
    'tell application "Messages"',
    "set imessage to 1st account whose service type = iMessage",
    "send (item 2 of argv) to participant recipient of imessage",
    "end tell",
    "end run",
)
DARWIN = "darwin"
MACOS = "macos"
IMESSAGE = "imessage"


class AlertKind(StrEnum):
    """Every alert the owner can receive (spec section 11). Values are stable journal names."""

    UNPROTECTED_POSITION = "unprotected-position"
    STOP_REJECTED = "stop-rejected"
    # A closing order was refused; the stop is back and the operator must look.
    EXIT_REFUSED = "exit-refused"
    UNKNOWN_ORDER_STATUS = "unknown-order-status"
    FOREIGN_EXPOSURE = "foreign-exposure"
    BROKER_READS_FAILING = "broker-reads-failing"
    STREAM_LOST_WITH_POSITION = "stream-lost-with-position"
    DAILY_CEILING_REACHED = "daily-ceiling-reached"
    BOT_CRASHED = "bot-crashed"
    BOT_DID_NOT_START = "bot-did-not-start"
    # Added to the spec list by review of #13: SIGTERM forwarded, bot still running.
    BOT_DID_NOT_STOP = "bot-did-not-stop"
    PREFLIGHT_FAILED = "preflight-failed"
    TOKEN_NEEDS_CONFIRMATION = "token-needs-confirmation"
    # The observer queue overflowed: observers are switched off for the run (trading goes on).
    OBSERVERS_STOPPED = "observers-stopped"
    # Broker reads after the flatten time (plus grace) still show our position or order.
    NOT_FLAT_AFTER_FLATTEN = "not-flat-after-flatten"
    # Raised by the separate watchdog job (runtime/watchdog.py), which outlives a crashed bot:
    # broker reads after the flatten time show any position or any open order;
    WATCHDOG_NOT_FLAT = "watchdog-not-flat"
    # the watchdog could not tell (broker read, database or calendar failed);
    WATCHDOG_CANNOT_CHECK = "watchdog-cannot-check"
    # during the session the bot process is not running while the database shows an open trade.
    BOT_DOWN_WITH_OPEN_TRADE = "bot-down-with-open-trade"


class Delivery(StrEnum):
    SHOWN = "shown"
    SUPPRESSED_REPEAT = "suppressed-repeat"
    NO_NOTIFIER = "no-notifier"
    FAILED = "failed"


class NotifyError(Exception):
    """The notification could not be shown; the message says why."""


class Notifier(Protocol):
    """One alert delivery channel (`MacNotifier`, `IMessageNotifier`), or several at once
    (`Channels`). `default_notifier` chooses them for the bot and the watchdog alike."""

    def notify(self, title: str, text: str) -> None:
        """Show one notification or raise NotifyError."""


Runner = Callable[..., subprocess.CompletedProcess[bytes]]


def _osascript(runner: Runner, timeout: float, script: tuple[str, ...],
               args: list[str]) -> None:
    """Run one AppleScript with `args` as its argv (an argument list, never a shell string).
    The error names the exit code only: stderr could echo the arguments."""
    argv = [OSASCRIPT]
    for line in script:
        argv += ["-e", line]
    argv += args
    try:
        result = runner(argv, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotifyError(f"osascript did not run: {type(exc).__name__}") from exc
    if result.returncode != 0:
        raise NotifyError(f"osascript exited with code {result.returncode}")


class MacNotifier:
    """macOS notification through osascript. Title and text go in argv, never into the script."""

    def __init__(self, timeout_seconds: Decimal, runner: Runner = subprocess.run) -> None:
        self._timeout = float(timeout_seconds)
        self._runner = runner

    def notify(self, title: str, text: str) -> None:
        _osascript(self._runner, self._timeout, _APPLESCRIPT, [title, text])


def recipient_missing(config: AlertsConfig, environ: Mapping[str, str]) -> str | None:
    """The recipient variable's NAME when it is not set, else None. Presence only: the value is
    compared with "" and never stored, printed or returned."""
    name = config.imessage_recipient_variable
    return None if environ.get(name) else name


class IMessageNotifier:
    """iMessage through Messages.app, driven by osascript. Messages must be signed in to
    iMessage on this Mac. The recipient never passes through Python: the script reads the
    variable named `variable`. One attempt per alert, never retried."""

    def __init__(self, config: AlertsConfig, environ: Mapping[str, str] = os.environ,
                 runner: Runner = subprocess.run) -> None:
        self._config = config
        self._environ = environ
        self._timeout = float(config.notification_timeout_seconds)
        self._runner = runner

    def notify(self, title: str, text: str) -> None:
        missing = recipient_missing(self._config, self._environ)
        if missing is not None:
            raise NotifyError(f"alert-recipient-missing: {missing} not set")
        _osascript(self._runner, self._timeout, _IMESSAGE_SCRIPT,
                   [self._config.imessage_recipient_variable, f"{title}\n{text}"])


class Channels:
    """Every alert through every channel: a failing channel never stops the others, and its
    failure is raised (after all were tried) so the caller records it."""

    def __init__(self, channels: tuple[tuple[str, Notifier], ...]) -> None:
        self._channels = channels

    def notify(self, title: str, text: str) -> None:
        failed, shown = [], []
        for name, channel in self._channels:
            try:
                channel.notify(title, text)
            except NotifyError as exc:
                failed.append(f"{name}: {exc}")
            else:
                shown.append(name)
        if failed:
            raise NotifyError("; ".join(failed) + f" (shown: {', '.join(shown) or 'none'})")


def default_notifier(config: AlertsConfig, platform: str = sys.platform,
                     environ: Mapping[str, str] = os.environ) -> Notifier | None:
    """The channels for this machine (Mac notification and iMessage), or None where there are
    none (CI, Linux)."""
    if platform == DARWIN:
        return Channels(((MACOS, MacNotifier(config.notification_timeout_seconds)),
                         (IMESSAGE, IMessageNotifier(config, environ))))
    return None


@dataclass(frozen=True)
class AlertOutcome:
    """What happened to one alert. The caller journals `journal_body()`."""

    kind: AlertKind
    text: str
    at: datetime
    delivery: Delivery
    error: str | None

    def journal_body(self) -> dict[str, str | None]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "at": self.at.isoformat(),
            "delivery": self.delivery.value,
            "error": self.error,
        }


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Alerter:
    """Shows alerts with repeat suppression. One instance per process."""

    def __init__(
        self,
        config: AlertsConfig,
        title: str,
        notifier: Notifier | None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._window = timedelta(seconds=float(config.dedupe_window_seconds))
        self._title = title
        self._notifier = notifier
        self._clock = clock
        self._last_shown: dict[tuple[AlertKind, str], datetime] = {}

    def alert(self, kind: AlertKind, text: str) -> AlertOutcome:
        if not isinstance(kind, AlertKind):
            raise TypeError(f"kind must be an AlertKind, got {kind!r}")
        now = self._clock()
        key = (kind, text)
        last = self._last_shown.get(key)
        if last is not None and now - last < self._window:
            return AlertOutcome(kind, text, now, Delivery.SUPPRESSED_REPEAT, None)
        if self._notifier is None:
            return AlertOutcome(kind, text, now, Delivery.NO_NOTIFIER, None)
        try:
            self._notifier.notify(f"{self._title}: {kind.value}", text)
        except NotifyError as exc:
            # Not marked as shown: the next occurrence tries again (never an automatic retry).
            return AlertOutcome(kind, text, now, Delivery.FAILED, str(exc))
        self._last_shown[key] = now
        return AlertOutcome(kind, text, now, Delivery.SHOWN, None)


def token_pending_text(wait_seconds: int) -> str:
    """Text of the TOKEN_NEEDS_CONFIRMATION alert: the SDK now waits for the owner."""
    return f"approve the API token in the Webull app within {wait_seconds} s"
