"""Settings fingerprint of a strategy (backlog S3).

The bot computes a strategy's settings version from its values; the owner only names it
(`[strategy] version`). The fingerprint is a SHA-256 over a canonical text of the fully
resolved values (after any merge of a variant with its base), so it changes exactly when a
value changes:

- table keys are sorted, so the order of keys in the file does not count;
- numbers are written as their exact decimal value with no trailing zeros or exponent, so
  `1.5`, `1.50` and `15e-1` are one value, and so are `2` and `2.0`;
- strings are quoted, so the number 1 and the text "1" differ; booleans stay booleans;
- `[strategy] version` is left out: it is the owner's name for these values, not a value.
  `[strategy] name` is kept (it chooses the module that reads the values).

A value of any other kind (a TOML date or time, NaN, infinity, a non-text key) is refused
with ValueError: it is never turned into text by a guess.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

IDENTITY_TABLE = "strategy"  # [strategy]: name and version
NAME_KEY = "version"  # the owner's name for the values, outside the fingerprint


def fingerprint(values: Mapping[str, Any]) -> str:
    """The SHA-256 (hex) of the canonical text of `values` without `[strategy] version`."""
    resolved = dict(values)
    identity = resolved.get(IDENTITY_TABLE)
    if isinstance(identity, Mapping):
        resolved[IDENTITY_TABLE] = {k: v for k, v in identity.items() if k != NAME_KEY}
    return hashlib.sha256(canonical_text(resolved).encode("utf-8")).hexdigest()


def canonical_text(value: Any, path: str = "") -> str:
    """One text per value, the same however the value was written (see the module text)."""
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{path or 'values'}: a key is not text")
        return "{" + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{canonical_text(value[key], f'{path}{key}.')}"
            for key in sorted(value)) + "}"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return _number(value, path)
    if isinstance(value, Sequence):
        return "[" + ",".join(canonical_text(item, path) for item in value) + "]"
    raise ValueError(f"{path.rstrip('.') or 'values'}: {type(value).__name__} has no "
                     "canonical form")


def _number(value: int | float | Decimal, path: str) -> str:
    # str(float) is the shortest text that reads back as the same float.
    number = Decimal(value) if isinstance(value, int) else Decimal(str(value))
    if not number.is_finite():
        raise ValueError(f"{path.rstrip('.')}: {value!r} is not a finite number")
    if number.is_zero():
        return "0"
    return format(number.normalize(), "f")
