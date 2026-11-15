"""BUG reproduction (fails until fixed), low severity: admit() raises decimal.Overflow
instead of returning a typed refusal for a finite but absurd quantity.

CLAUDE.md rule 7 ("Refuse, never guess": a typed refusal with one recorded cause) and the
admission docstring ("A refusal never guesses: missing or unknown values refuse"). Check 5
accepts any finite Decimal quantity; `check_size` (src/aperture/risk/sizing.py) then computes
`quantity * entry_high`, which overflows the default decimal context and raises. No current
strategy can produce such a quantity (price-action-v1 caps it by the notional cap), so this
matters only for a buggy or new strategy module (rule 8: strategies are swappable).
"""

from __future__ import annotations

import pytest

from aperture.risk.admission import admit
from aperture.risk.causes import AdmissionCause as C

from .conftest import D, decision, inputs


@pytest.mark.xfail(strict=True, reason="bug: admit() raises decimal.Overflow, rule 7")
def test_an_absurd_finite_quantity_is_refused_not_raised(lim):
    result = admit(inputs(decision=decision(quantity=D("1E+999999"))), lim)
    assert result.cause is C.NOTIONAL_OVER_CAP
