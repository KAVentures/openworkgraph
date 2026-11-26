"""Text encodings of CSV event logs other than UTF-8.

Spreadsheets and older systems often save CSV files as Windows-1252 (Western European "ANSI"),
and Excel's "Unicode text" export is UTF-16 with a byte-order mark. A CSV mapping can name the
file's ``encoding``; such a file is converted to UTF-8 in a temporary file next to the workspace
data before it is read, so every CSV rule (columns, timestamps, rejected rows) stays the same.
Decoding is strict: a byte that is not valid in the named encoding refuses the file with its
offset rather than being replaced. The quality report's digests describe the original bytes.
"""
from __future__ import annotations

import codecs
import os
import tempfile
from pathlib import Path
from typing import Any

from shared.event_log_formats import EventLogFormatError

ENCODINGS = {"utf-8": "utf-8-sig", "windows-1252": "cp1252", "utf-16": "utf-16"}
_CHUNK = 1 << 20


def encoding(mapping: dict[str, Any] | None) -> str:
    """The mapping's declared encoding name, "utf-8" when none is given."""
    value = (mapping or {}).get("encoding") or "utf-8"
    if value not in ENCODINGS:
        raise EventLogFormatError("the CSV encoding must be utf-8, windows-1252 or utf-16")
    return value


def to_utf8(path: Path, name: str, folder: Path) -> Path:
    """A UTF-8 copy of ``path`` decoded from ``name``, in a new temporary file under ``folder``."""
    decoder = codecs.getincrementaldecoder(ENCODINGS[name])(errors="strict")
    handle, target = tempfile.mkstemp(prefix="owg-csv-utf8-", suffix=".csv", dir=str(folder))
    offset = 0
    try:
        with open(handle, "w", encoding="utf-8", newline="") as out, path.open("rb") as raw:
            while True:
                chunk = raw.read(_CHUNK)
                try:
                    out.write(decoder.decode(chunk, final=not chunk))
                except UnicodeDecodeError as exc:
                    raise EventLogFormatError(
                        f"the file is not valid {name} text (byte {offset + exc.start:,}); choose its encoding") from None
                if not chunk:
                    break
                offset += len(chunk)
    except BaseException:
        os.unlink(target)
        raise
    return Path(target)


__all__ = ["ENCODINGS", "encoding", "to_utf8"]
