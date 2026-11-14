#!/usr/bin/env python3
"""Fail CI when the apps' Swift code breaks the code rules (CLAUDE.md, docs/design-system.md).

Rules, checked in every Swift file of apps/ (tests and Package.swift excepted):

text     No words in code. A string literal holding a letter may appear only
         - in an `L10n.swift` file, the one place per target where user-facing text is defined,
           each entry as `LocalizedStringResource("key", defaultValue: "English")`;
         - as an SF Symbol name (after `systemName:` / `systemImage:`, or dotted like "chart.bar");
         - in a documented `static let` constant (an identifier, a user-defaults key) or as an
           enum case raw value (a protocol value);
         - in an `@available` attribute (messages for developers).
         So `Text("Overview")`, `Button("Quit")`, `return "Flatten All"` and
         `Text(verbatim: "Aperture")` all fail. Interpolations and punctuation (" · ") pass.
numbers  No magic numbers: see check_swift_literals.py.
indent   2-space indentation: no tabs, and a line indents at most 2 deeper than the line before.
catalog  Every key of an `L10n.swift` has a Russian translation in the target's String Catalog
         (`*.xcstrings` next to it).

Existing violations are listed per rule and file in swift-rules-baseline.json. A file may never
have more violations than its baseline (new code is always clean), and the baseline must be
lowered in the same pull request that removes violations (`--write-baseline`), so it only shrinks.

Usage: check_swift_rules.py [--write-baseline] [--base-baseline FILE]
  --base-baseline FILE  the baseline of the branch a pull request targets; fails when this
                        branch's baseline allows more than it in any rule and file, so a pull
                        request cannot raise the baseline to let new violations in.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_swift_literals  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("apps/shared", "apps/macos", "apps/ios")
BASELINE = Path(__file__).resolve().parent / "swift-rules-baseline.json"
SKIPPED_FILES = {"Package.swift", "Package@swift-6.0.swift"}
SKIPPED_DIRS = {"Tests", ".build", "build", "DerivedData", "SourcePackages"}
L10N_FILE = "L10n.swift"
INDENT = 2

SYMBOL_ARGUMENT = re.compile(r"(?:systemName|systemImage)\s*:\s*$")
SYMBOL_NAME = re.compile(r"^[a-z0-9]+(?:\.[a-z0-9]+)+$")
ESCAPE = re.compile(r"\\(?:u\{[0-9A-Fa-f]+\}|.)")
LETTER = re.compile(r"[A-Za-zЀ-ӿ]")
L10N_KEY = re.compile(r'LocalizedStringResource\(\s*"([^"\\]+)"')


# MARK: - string literals


def string_literals(text: str) -> list[tuple[int, str, str]]:
    """(line, literal text without interpolations, code before it on its line) for each literal.

    Skips comments; reads string interpolations as code, so literals nested in them count too.
    """
    found: list[tuple[int, str, str]] = []
    i, line, n = 0, 1, len(text)
    line_start = 0

    def read_string(start: int, hashes: int, multiline: bool) -> int:
        """Read a literal whose opening quote(s) end at `start`; return the index after it."""
        nonlocal line, line_start
        literal_line, prefix = line, text[line_start:start].rstrip('"#')
        closing = ('"""' if multiline else '"') + "#" * hashes
        parts: list[str] = []
        j = start
        while j < n:
            if text.startswith(closing, j):
                found.append((literal_line, "".join(parts), prefix))
                return j + len(closing)
            ch = text[j]
            if ch == "\\" and text.startswith("#" * hashes, j + 1):
                k = j + 1 + hashes
                if k < n and text[k] == "(":
                    j = read_code(k + 1, close=")")
                    continue
                parts.append(text[j:k + 1])
                j = k + 1
                continue
            if ch == "\n":
                line += 1
                line_start = j + 1
            parts.append(ch)
            j += 1
        return j

    def read_code(start: int, close: str | None) -> int:
        nonlocal line, line_start
        j, depth = start, 0
        while j < n:
            if text.startswith("//", j):
                end = text.find("\n", j)
                j = n if end < 0 else end
                continue
            if text.startswith("/*", j):
                end = text.find("*/", j)
                end = n if end < 0 else end + 2
                line += text.count("\n", j, end)
                if "\n" in text[j:end]:
                    line_start = text.rfind("\n", j, end) + 1
                j = end
                continue
            ch = text[j]
            if ch == '"' or (ch == "#" and re.match(r'#+"', text[j:])):
                hashes = 0
                while text[j] == "#":
                    hashes += 1
                    j += 1
                multiline = text.startswith('"""', j)
                j = read_string(j + (3 if multiline else 1), hashes, multiline)
                continue
            if ch == "\n":
                line += 1
                line_start = j + 1
            elif close and ch == "(":
                depth += 1
            elif close and ch == close:
                if depth == 0:
                    return j + 1
                depth -= 1
            j += 1
        return j

    read_code(i, close=None)
    return found


def text_violations(path: Path) -> list[tuple[int, str]]:
    if path.name == L10N_FILE:
        return []
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    documented = documented_constant_lines(lines)
    found = []
    for number, literal, prefix in string_literals(source):
        if not LETTER.search(ESCAPE.sub("", literal)):
            continue
        if "@available(" in prefix:
            continue
        if SYMBOL_ARGUMENT.search(prefix) or SYMBOL_NAME.match(literal):
            continue
        if number in documented or check_swift_literals.ENUM_CASE.search(prefix):
            continue
        found.append((number, literal))
    return found


def documented_constant_lines(lines: list[str]) -> set[int]:
    """Line numbers of `static let` constants documented with `///` (same rule as numbers)."""
    result, documented = set(), False
    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("///"):
            documented = True
            continue
        is_constant = bool(check_swift_literals.CONSTANT.match(raw))
        if is_constant and documented:
            result.add(number)
            continue
        if stripped and not is_constant:
            documented = False
    return result


# MARK: - indentation


def indent_violations(path: Path) -> list[tuple[int, str]]:
    found = []
    previous = 0
    in_multiline = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if raw.count('"""') % 2 == 1:
            in_multiline = not in_multiline
            if not in_multiline:
                continue
        elif in_multiline or not raw.strip():
            continue
        leading = raw[: len(raw) - len(raw.lstrip())]
        if "\t" in leading:
            found.append((number, "tab"))
            continue
        width = len(leading)
        if width > previous + INDENT:
            found.append((number, f"+{width - previous}"))
        previous = width
    return found


# MARK: - String Catalog


def catalog_violations(path: Path) -> list[tuple[int, str]]:
    """Keys of an L10n.swift without a Russian translation in the String Catalog beside it."""
    if path.name != L10N_FILE:
        return []
    catalogs = sorted(path.parent.rglob("*.xcstrings"))
    lines = path.read_text(encoding="utf-8").splitlines()
    keys = [(n, m.group(1)) for n, raw in enumerate(lines, 1) for m in L10N_KEY.finditer(raw)]
    if not catalogs:
        return [(1, "no String Catalog (*.xcstrings) next to L10n.swift")] if keys else []
    strings: dict = {}
    for catalog in catalogs:
        strings.update(json.loads(catalog.read_text(encoding="utf-8")).get("strings", {}))
    found = []
    for number, key in keys:
        unit = strings.get(key, {}).get("localizations", {}).get("ru", {}).get("stringUnit", {})
        variations = strings.get(key, {}).get("localizations", {}).get("ru", {}).get("variations")
        if not variations and (unit.get("state") != "translated" or not unit.get("value")):
            found.append((number, key))
    return found


# MARK: - running


def number_violations(path: Path) -> list[tuple[int, str]]:
    if path.name in check_swift_literals.SKIPPED_FILES:
        return []
    return check_swift_literals.violations(path)


RULES = {
    "text": (text_violations, "words in code; define them in L10n.swift, translated to Russian"),
    "numbers": (number_violations, "magic number; use a token or a documented constant"),
    "indent": (indent_violations, "indent with 2 spaces"),
    "catalog": (catalog_violations, "L10n key without a Russian translation in the catalog"),
}


def swift_files() -> list[Path]:
    files = []
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.swift")):
            relative = path.relative_to(REPO)
            if path.name in SKIPPED_FILES or SKIPPED_DIRS.intersection(relative.parts):
                continue
            files.append(path)
    return files


def collect() -> dict[str, dict[str, list[tuple[int, str]]]]:
    result: dict[str, dict[str, list[tuple[int, str]]]] = {rule: {} for rule in RULES}
    for path in swift_files():
        key = path.relative_to(REPO).as_posix()
        for rule, (check, _) in RULES.items():
            found = check(path)
            if found:
                result[rule][key] = found
    return result


def raised(baseline: dict, base: dict | None) -> int:
    """Count (and print) baseline entries above the target branch's; None = no base yet."""
    if base is None:
        return 0
    count = 0
    for rule, files in baseline.items():
        for path, limit in files.items():
            before = base.get(rule, {}).get(path, 0)
            if limit > before:
                count += 1
                print(f"{path}: {rule} baseline raised from {before} to {limit}; "
                      "fix the new violations instead")
    return count


def main(argv: list[str]) -> int:
    found = collect()
    if "--write-baseline" in argv:
        counts = {rule: {path: len(items) for path, items in sorted(files.items())}
                  for rule, files in found.items()}
        BASELINE.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE.relative_to(REPO)}")
        return 0
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    failures = 0
    if "--base-baseline" in argv:
        base_path = Path(argv[argv.index("--base-baseline") + 1])
        base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else None
        failures += raised(baseline, base)
    for rule, (_, message) in RULES.items():
        allowed = baseline.get(rule, {})
        for path in sorted(set(found[rule]) | set(allowed)):
            items = found[rule].get(path, [])
            limit = allowed.get(path, 0)
            if len(items) > limit:
                failures += 1
                print(f"{path}: {len(items)} {rule} violation(s), baseline allows {limit}: "
                      f"{message}")
                for line, detail in items:
                    print(f"  {path}:{line}: {detail}")
            elif len(items) < limit:
                failures += 1
                print(f"{path}: {rule} baseline is {limit} but only {len(items)} remain; "
                      "lower it: python3 apps/scripts/check_swift_rules.py --write-baseline")
    if failures:
        print("Swift code rules failed; see CLAUDE.md (Swift code rules) and docs/design-system.md")
        return 1
    total = sum(len(v) for files in found.values() for v in files.values())
    print(f"Swift code rules pass ({total} baselined violation(s) left to remove)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
