"""tickr.strategy.values: a variant stores only its differences, resolved once over its parent."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tickr.strategy.values import StrategyValuesError, files, load_values

ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = ROOT / "strategies"


def _path(folder: str, root: Path = STRATEGIES) -> Path:
    return root / folder / "strategy.toml"


def _settings(values: dict) -> dict:
    return {k: v for k, v in values.items() if k != "strategy"}


def _leaves(values: dict) -> int:
    return sum(len(table) for table in _settings(values).values())


def test_a_file_without_extends_is_read_unchanged():
    path = _path("price-action-v1")
    assert load_values(path) == tomllib.loads(path.read_text("utf-8"))
    assert files(path) == (path,)


def test_price_action_v2_is_v1_plus_its_two_tables():
    v1, v2 = load_values(_path("price-action-v1")), load_values(_path("price-action-v2"))
    assert v2["strategy"] == {"name": "price-action-v2", "version": "2"}
    added = {"confirmation", "no_progress"}
    assert {k: v for k, v in _settings(v2).items() if k not in added} == _settings(v1)
    assert v2["confirmation"] == {"confirmation_quotes": 2}
    assert v2["no_progress"] == {"minimum_progress_r": 0.5, "window_minutes": 15}
    assert _leaves(v2) == _leaves(v1) + 3
    raw = tomllib.loads(_path("price-action-v2").read_text("utf-8"))
    assert set(raw) == {"strategy", *added}  # the file holds only its differences
    assert files(_path("price-action-v2")) == (_path("price-action-v2"), _path("price-action-v1"))


@pytest.mark.parametrize("value", [1.5, 1.75])
def test_relative_volume_variants_are_v3_with_one_value(value):
    folder = f"price-action-v3-relvol-{value}"
    base, variant = load_values(_path("price-action-v3")), load_values(_path(folder))
    assert variant["strategy"] == {"name": "price-action-v3", "version": f"2-relvol-{value}"}
    expected = _settings(base)
    expected["indicators"] = {**expected["indicators"], "minimum_relative_volume": value}
    assert _settings(variant) == expected
    raw = tomllib.loads(_path(folder).read_text("utf-8"))
    assert raw["indicators"] == {"minimum_relative_volume": value}


def _write(root: Path, folder: str, text: str) -> Path:
    path = _path(folder, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


BASE = '[strategy]\nname = "s"\nversion = "1"\n[a]\nx = 1\ny = 2\n'


def _child(extends: str, version: str = "1", body: str = "[a]\nx = 5\n") -> str:
    return (f'[strategy]\nname = "s"\nversion = "2"\nextends = "{extends}"\n'
            f'extends_version = "{version}"\n{body}')


def test_resolution_overrides_key_by_key_and_drops_the_extends_keys(tmp_path):
    _write(tmp_path, "base", BASE)
    child = _write(tmp_path, "child", _child("base", body="[a]\nx = 5\n[b]\nz = 3\n"))
    _write(tmp_path, "grandchild", _child("child", "2", "[b]\nz = 4\n"))
    assert load_values(child) == {"strategy": {"name": "s", "version": "2"},
                                  "a": {"x": 5, "y": 2}, "b": {"z": 3}}
    grand = load_values(_path("grandchild", tmp_path))
    assert grand == {"strategy": {"name": "s", "version": "2"},
                     "a": {"x": 5, "y": 2}, "b": {"z": 4}}
    assert [p.parent.name for p in files(_path("grandchild", tmp_path))] == [
        "grandchild", "child", "base"]


@pytest.mark.parametrize(("folders", "cause"), [
    ({"child": _child("missing")}, "names no strategy"),
    ({"child": _child("child")}, "cycle: child -> child"),
    ({"child": _child("other"), "other": _child("child")}, "cycle: child -> other -> child"),
    ({"base": BASE, "child": _child("base", version="7")}, "extends base version '7'"),
    ({"base": BASE, "child": _child("base", body="[a]\nx = 1\n")}, r"\[a\] repeats .* x"),
    ({"child": _child("../base")}, "must be a folder name"),
    ({"child": '[strategy]\nname = "s"\nversion = "2"\nextends = "base"\n'},
     "needs an extends_version"),
    ({"child": '[strategy]\nname = "s"\nversion = "2"\nextends_version = "1"\n'},
     "extends_version without extends"),
])
def test_unresolvable_variants_are_refused(tmp_path, folders, cause):
    for folder, text in folders.items():
        _write(tmp_path, folder, text)
    with pytest.raises(StrategyValuesError, match=cause):
        load_values(_path("child", tmp_path))


def test_every_shipped_strategy_resolves():
    for path in sorted(STRATEGIES.glob("*/strategy.toml")):
        assert load_values(path)["strategy"]["version"]
