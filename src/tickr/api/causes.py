"""What of an exception may reach an API response, a document's cause or a log line.

The text of a system exception (OSError, sqlite3, json, Unicode, a parser) carries the absolute
local path, an OS message or a piece of the data, none of which may leave this Mac. A refusal of
our own (a class defined in tickr) is written by us as a cause, so its text is kept. Everything
else is its type name, e.g. `PermissionError`. tests/api/test_no_exception_text.py holds the rule.
"""

from __future__ import annotations

from pathlib import PurePath

from tickr.research.errors import RecordFileError


def safe_cause(exc: BaseException) -> str:
    """The exception's own cause text when it is a tickr refusal, else its type name."""
    kind = type(exc)
    if isinstance(exc, RecordFileError):  # its text holds the file's absolute path and the data
        return f"{PurePath(exc.path).name}:{exc.line}: the record is malformed"
    if kind.__module__.startswith("tickr.") and not isinstance(exc, OSError):
        return str(exc) or kind.__name__
    return kind.__name__


class RowRefused(ValueError):
    """A captured row we parsed and found wrong; its text names the field and is ours to show."""
