"""Drive an Engine: step every detail_poll_seconds, reconcile every reconcile_interval_seconds."""

from __future__ import annotations

import time
from collections.abc import Callable

from aperture.engine.core import Engine


def run(engine: Engine, *, should_stop: Callable[[], bool],
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic) -> None:
    """Recover, then loop until `should_stop()` is true. Never sends on its own: it only
    observes, protects and tears down what the engine already owns."""
    orders = engine.config.orders
    engine.recover()
    last_reconcile = monotonic()
    while not should_stop():
        sleep(float(orders.detail_poll_seconds))
        if monotonic() - last_reconcile >= float(orders.reconcile_interval_seconds):
            engine.reconcile()  # includes a step
            last_reconcile = monotonic()
        else:
            engine.step()
