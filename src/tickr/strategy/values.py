"""A strategy folder's values: its `strategy.toml`, resolved over the strategy it extends.

A variant stores only its differences (backlog T5). Its `[strategy]` table names the folder it
extends and that folder's version:

    [strategy]
    name = "price-action-v2"
    version = "2"
    extends = "price-action-v1"
    extends_version = "1"

Resolution happens once, when the file is read: the parent's values (itself resolved) are the
base, and every table of the variant replaces the parent's keys one by one; a table only the
variant has is added. `[strategy]` is never inherited: name and version are the variant's own,
and `extends`/`extends_version` are dropped from the result, so a strategy's `create()` sees the
same flat values as before.

Refused with `StrategyValuesError`: an unreadable file, a parent folder that does not exist, a
cycle, a parent whose version is not `extends_version` (a changed parent never changes a variant
silently), and a variant key equal to the value it would inherit (it must store differences only).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

STRATEGY_FILE = "strategy.toml"
IDENTITY = "strategy"  # the [strategy] table: name and version, never inherited
EXTENDS = "extends"  # [strategy] key: folder (next to this one) whose values are the base
EXTENDS_VERSION = "extends_version"  # [strategy] key: the version that folder must declare


class StrategyValuesError(ValueError):
    """A strategy file that cannot be read or resolved; the message names the file and cause."""


def load_values(path: Path) -> dict[str, Any]:
    """The resolved values of the strategy file at `path` (strategies/<folder>/strategy.toml)."""
    return _resolve(path, ())


def files(path: Path) -> tuple[Path, ...]:
    """`path` and every file it extends, nearest first: the files its values come from."""
    found = [path]
    while (parent := _parent(found[-1], _read(found[-1]), tuple(found[:-1]))) is not None:
        found.append(parent[0])
    return tuple(found)


def _read(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StrategyValuesError(f"cannot read {path}: {exc}") from exc


def _parent(path: Path, raw: dict[str, Any],
            seen: tuple[Path, ...]) -> tuple[Path, str] | None:
    """(parent file, the version it must declare), or None when `raw` extends nothing."""
    table = raw.get(IDENTITY)
    if not isinstance(table, dict) or EXTENDS not in table:
        if isinstance(table, dict) and EXTENDS_VERSION in table:
            raise StrategyValuesError(f"{path}: [strategy] {EXTENDS_VERSION} without {EXTENDS}")
        return None
    folder, version = table[EXTENDS], table.get(EXTENDS_VERSION)
    if not isinstance(folder, str) or not folder or "/" in folder or folder.startswith("."):
        raise StrategyValuesError(f"{path}: [strategy] {EXTENDS} must be a folder name")
    if not isinstance(version, str) or not version:
        raise StrategyValuesError(f"{path}: [strategy] {EXTENDS} needs an {EXTENDS_VERSION} string")
    parent = path.parent.parent / folder / STRATEGY_FILE
    chain = (*seen, path)
    if parent in chain:
        names = " -> ".join(p.parent.name for p in (*chain, parent))
        raise StrategyValuesError(f"{path}: [strategy] {EXTENDS} makes a cycle: {names}")
    if not parent.is_file():
        raise StrategyValuesError(f"{path}: [strategy] {EXTENDS} names no strategy: {parent}")
    return parent, version


def _resolve(path: Path, seen: tuple[Path, ...]) -> dict[str, Any]:
    raw = _read(path)
    parent = _parent(path, raw, seen)
    if parent is None:
        return raw
    parent_path, wanted = parent
    base = _resolve(parent_path, (*seen, path))
    declared = base.get(IDENTITY, {}).get("version")
    if declared != wanted:
        raise StrategyValuesError(
            f"{path}: extends {parent_path.parent.name} version {wanted!r}, "
            f"but it declares {declared!r}")
    resolved = {key: value for key, value in base.items() if key != IDENTITY}
    for key, value in raw.items():
        if key == IDENTITY:
            continue
        inherited = base.get(key)
        if not isinstance(value, dict) or not isinstance(inherited, dict):
            if key in base and inherited == value:
                raise StrategyValuesError(f"{path}: {key!r} repeats the inherited value")
            resolved[key] = value
            continue
        repeats = sorted(k for k, v in value.items() if k in inherited and inherited[k] == v)
        if repeats:
            raise StrategyValuesError(
                f"{path}: [{key}] repeats the inherited value of {', '.join(repeats)}")
        resolved[key] = {**inherited, **value}
    identity = {k: v for k, v in raw.get(IDENTITY, {}).items()
                if k not in (EXTENDS, EXTENDS_VERSION)}
    return {IDENTITY: identity, **resolved}
