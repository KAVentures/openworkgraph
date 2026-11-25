"""Declared timestamp formats for CSV imports, and detecting them from sample values.

Many exports write times as ``03/04/2024 14:05``, ``04.03.2024 14:05:00`` or
``04-MAR-24 02.05.00 PM`` rather than ISO 8601. A CSV import can declare the
format of its timestamp column as a strptime pattern; every value must then
match it exactly (DuckDB ``try_strptime``: no trailing text, no impossible
dates). Nothing is guessed per row:

* a pattern with ``%z`` reads the offset from each value;
* a pattern without ``%z`` describes times without an offset, so the import
  also needs an explicit offset decision, or those rows are rejected.

Two named formats read Unix time instead of a pattern: ``unix-seconds`` (up to
10 digits, optionally with up to 6 decimals) and ``unix-milliseconds`` (up to 13
digits, optionally with up to 3 decimals), as APIs and JSON exports often write
times. They count from 1970-01-01 UTC, so they carry their own offset and need
no offset decision. Anything else in the column (signs, exponents, separators)
is rejected, not guessed.

``excel-serial`` reads Excel serial date numbers (days since 1899-12-30, the
1900 date system, with the time of day as the fraction), as CSV files saved
from spreadsheets sometimes contain. They are rounded to the millisecond. Like
a pattern without ``%z`` they describe local times, so the import also needs
an offset decision.

``detect`` tries the known patterns on sample values and reports every one that
reads all of them. When both a day-first and a month-first pattern fit (all
days are 12 or less) the result is marked ambiguous: the user decides.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import duckdb

FORMAT = "openworkgraph.event-log-timestamp-formats.v1"
MAX_PATTERN = 64
MAX_SAMPLES = 500
_DIRECTIVES = set("dmYyHIMSfpbBz")
_LITERALS = set(" /.-:,T")
_ISO_OFFSET = r"(Z|[+-][0-9]{2}:?[0-9]{2})$"
# Named Unix-time formats: (most integer digits, most decimals, microseconds per unit).
UNIX: dict[str, tuple[int, int, int]] = {"unix-seconds": (10, 6, 1_000_000), "unix-milliseconds": (13, 3, 1_000)}
EXCEL_SERIAL = "excel-serial"
_EXCEL_REGEX = r"[0-9]{1,6}(\.[0-9]{1,15})?"
# Detection offers serial numbers from 1954-10-04 (20000) to 2173-10-14 (99999).
_EXCEL_DETECT = re.compile(r"[2-9][0-9]{4}(\.[0-9]{1,15})?")
# Detection only offers Unix time for values of a plausible length (from 1973 or 2001 on).
_UNIX_DETECT = {"unix-seconds": 9, "unix-milliseconds": 12}


def _unix_regex(pattern: str) -> str:
    digits, decimals, _ = UNIX[pattern]
    return f"[0-9]{{1,{digits}}}(\\.[0-9]{{1,{decimals}}})?"

# (pattern, order); listed most specific first so the suggestion prefers the fuller pattern.
KNOWN: list[tuple[str, str]] = [
    ("%d/%m/%Y %H:%M:%S", "day-first"), ("%d/%m/%Y %H:%M", "day-first"),
    ("%d.%m.%Y %H:%M:%S.%f", "day-first"), ("%d.%m.%Y %H:%M:%S", "day-first"), ("%d.%m.%Y %H:%M", "day-first"),
    ("%d-%m-%Y %H:%M:%S", "day-first"), ("%d-%m-%Y %H:%M", "day-first"),
    ("%d/%m/%y %H:%M:%S", "day-first"), ("%d/%m/%y %H:%M", "day-first"), ("%d.%m.%y %H:%M", "day-first"),
    ("%m/%d/%Y %I:%M:%S %p", "month-first"), ("%m/%d/%Y %I:%M %p", "month-first"),
    ("%m/%d/%Y %H:%M:%S", "month-first"), ("%m/%d/%Y %H:%M", "month-first"),
    ("%m/%d/%y %I:%M %p", "month-first"), ("%m/%d/%y %H:%M", "month-first"), ("%m-%d-%Y %H:%M:%S", "month-first"),
    ("%d-%b-%Y %H:%M:%S", "day-first"), ("%d-%b-%y %I.%M.%S %p", "day-first"), ("%d-%b-%y %H:%M:%S", "day-first"),
    ("%d %b %Y %H:%M:%S", "day-first"), ("%d %b %Y %H:%M", "day-first"),
    ("%b %d, %Y %I:%M:%S %p", "month-first"), ("%b %d, %Y %H:%M:%S", "month-first"),
    ("%Y/%m/%d %H:%M:%S", "year-first"), ("%Y/%m/%d %H:%M", "year-first"), ("%Y.%m.%d %H:%M:%S", "year-first"),
    ("%Y%m%d%H%M%S", "year-first"), ("%Y-%m-%d %H:%M:%S %z", "year-first"),
    ("%d/%m/%Y", "day-first"), ("%d.%m.%Y", "day-first"), ("%d-%m-%Y", "day-first"),
    ("%m/%d/%Y", "month-first"), ("%Y/%m/%d", "year-first"), ("%Y%m%d", "year-first"),
]


class TimestampFormatError(ValueError):
    """The declared pattern is not an accepted timestamp format."""


def validate(pattern: Any) -> str:
    """The pattern if it is a bounded strptime pattern with a date or a Unix-time format, else TimestampFormatError."""
    if isinstance(pattern, str) and (pattern in UNIX or pattern == EXCEL_SERIAL):
        return pattern
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN:
        raise TimestampFormatError(f"the timestamp format must be a strptime pattern of at most {MAX_PATTERN} characters")
    directives, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "%":
            if i + 1 >= len(pattern) or pattern[i + 1] not in _DIRECTIVES:
                raise TimestampFormatError("the timestamp format may use only %d %m %Y %y %H %I %M %S %f %p %b %B %z")
            directives.append(pattern[i + 1])
            i += 2
            continue
        if ch not in _LITERALS:
            raise TimestampFormatError("the timestamp format may contain only directives, spaces and / . - : , T")
        i += 1
    if len(directives) != len(set(directives)):
        raise TimestampFormatError("the timestamp format repeats a directive")
    has = set(directives)
    if not (has & {"Y", "y"} and has & {"m", "b", "B"} and "d" in has):
        raise TimestampFormatError("the timestamp format needs a year, a month and a day")
    if ("I" in has) != ("p" in has) or ("H" in has and "I" in has):
        raise TimestampFormatError("use %I with %p for 12-hour times, or %H for 24-hour times")
    return pattern


def has_offset(pattern: str) -> bool:
    """Whether values in this format fix their own instant (``%z``, or Unix time, which is UTC)."""
    return "%z" in pattern or pattern in UNIX


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def offset_present_sql(raw: str, pattern: str) -> str:
    """SQL: whether the raw text is meant to carry its own offset."""
    if pattern:
        return "true" if has_offset(pattern) else "false"
    return f"regexp_matches(trim({raw}), '{_ISO_OFFSET}')"


def timestamp_sql(raw: str, offset: str, pattern: str) -> str:
    """SQL for a TIMESTAMPTZ read with a declared pattern; ``offset`` is the explicit decision for naive times."""
    if pattern in UNIX:
        micros = UNIX[pattern][2]
        return (f"CASE WHEN regexp_full_match(trim({raw}), '{_unix_regex(pattern)}') THEN try_cast(strftime(make_timestamp("
                f"CAST(round(CAST(trim({raw}) AS DECIMAL(24,6)) * {micros}) AS BIGINT)), '%Y-%m-%d %H:%M:%S.%f')"
                " || '+00:00' AS TIMESTAMPTZ) END")
    if pattern == EXCEL_SERIAL:
        parsed = (f"CASE WHEN regexp_full_match(trim({raw}), '{_EXCEL_REGEX}') THEN TIMESTAMP '1899-12-30 00:00:00'"
                  f" + to_microseconds(CAST(round(CAST(trim({raw}) AS DECIMAL(30,15)) * 86400000) AS BIGINT) * 1000) END")
    else:
        parsed = f"try_strptime(trim({raw}), {_literal(pattern)})"
    if has_offset(pattern):
        return f"CAST({parsed} AS TIMESTAMPTZ)"
    if not offset:
        return "CAST(NULL AS TIMESTAMPTZ)"
    suffix = "+00:00" if offset == "Z" else offset
    return (f"CASE WHEN trim(coalesce({raw}, '')) <> '' THEN try_cast(strftime({parsed}, '%Y-%m-%d %H:%M:%S.%f')"
            f" || '{suffix}' AS TIMESTAMPTZ) END")


def sample_column(text: str, delimiter: str, column: str, limit: int = MAX_SAMPLES,
                  time_column: str = "") -> list[str]:
    """Non-empty values of one column from the start of a CSV file; a cut-off last line is dropped.

    With ``time_column`` each value is the date column and the time-of-day column joined by a space,
    as the import reads them."""
    if delimiter not in {",", ";", "\t", "|"}:
        raise TimestampFormatError("unsupported delimiter")
    text = text.lstrip("﻿")
    if "\n" in text and not text.endswith("\n"):
        text = text[:text.rfind("\n") + 1]
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    # The header is the first of the first 50 lines that names the column (title rows or an Excel
    # "sep=" line may come before it).
    header: list[str] = []
    for _ in range(50):
        header = next(reader, None) or []
        if column in header or not header and reader.line_num > 50:
            break
    if column not in header:
        raise TimestampFormatError("the timestamp column is not in the file header")
    if time_column and time_column not in header:
        raise TimestampFormatError("the time-of-day column is not in the file header")
    index, values = header.index(column), []
    clock = header.index(time_column) if time_column else None
    for row in reader:
        if index < len(row) and row[index].strip():
            value = row[index].strip()
            if clock is not None and clock < len(row) and row[clock].strip():
                value += " " + row[clock].strip()
            values.append(value[:100])
            if len(values) >= limit:
                break
    return values


def detect(samples: list[str]) -> dict[str, Any]:
    """Which known patterns read every sample value, and whether the values are already ISO 8601."""
    values = [str(v).strip()[:100] for v in samples if isinstance(v, str) and v.strip()][:MAX_SAMPLES]
    result: dict[str, Any] = {"format": FORMAT, "samples": len(values), "iso_with_offset": 0, "iso_without_offset": 0,
                              "candidates": [], "ambiguous": False, "suggested": None}
    if not values:
        return result
    conn = duckdb.connect()
    try:
        conn.execute("SET TimeZone='UTC'")
        conn.execute("CREATE TEMP TABLE s(v VARCHAR)")
        conn.executemany("INSERT INTO s VALUES (?)", [[v] for v in values])
        with_offset, without = conn.execute(f"""SELECT
              count(*) FILTER (WHERE regexp_matches(v, '{_ISO_OFFSET}') AND try_cast(v AS TIMESTAMPTZ) IS NOT NULL),
              count(*) FILTER (WHERE NOT regexp_matches(v, '{_ISO_OFFSET}') AND try_cast(v AS TIMESTAMP) IS NOT NULL)
            FROM s""").fetchone()
        result.update(iso_with_offset=with_offset, iso_without_offset=without)
        for pattern, order in KNOWN:
            parsed, first = conn.execute("""SELECT count(*) FILTER (WHERE try_strptime(v, ?) IS NOT NULL),
                  min(strftime(try_strptime(v, ?)::TIMESTAMP, '%Y-%m-%d %H:%M:%S')) FILTER (WHERE v = (SELECT v FROM s LIMIT 1))
                FROM s""", [pattern, pattern]).fetchone()
            if parsed == len(values):
                result["candidates"].append({"pattern": pattern, "order": order, "has_offset": has_offset(pattern),
                                             "first_value": values[0], "first_value_reads_as": first})
    finally:
        conn.close()
    if all(_EXCEL_DETECT.fullmatch(v) for v in values):
        days = Decimal(values[0])
        stamp = datetime(1899, 12, 30) + timedelta(milliseconds=int((days * 86_400_000).to_integral_value()))
        result["candidates"].append({"pattern": EXCEL_SERIAL, "order": "excel-serial", "has_offset": False,
                                     "first_value": values[0],
                                     "first_value_reads_as": stamp.strftime("%Y-%m-%d %H:%M:%S")})
    for pattern, shortest in _UNIX_DETECT.items():
        rule = re.compile(_unix_regex(pattern))
        if all(rule.fullmatch(v) and len(v.split(".")[0]) >= shortest for v in values):
            stamp = datetime.fromtimestamp(float(Decimal(values[0]) * UNIX[pattern][2] / 1_000_000), timezone.utc)
            result["candidates"].append({"pattern": pattern, "order": "unix-time", "has_offset": True,
                                         "first_value": values[0],
                                         "first_value_reads_as": stamp.strftime("%Y-%m-%d %H:%M:%S") + " UTC"})
    orders = {c["order"] for c in result["candidates"]}
    result["ambiguous"] = {"day-first", "month-first"} <= orders
    if with_offset + without == len(values):
        result["note"] = "the values are already ISO 8601; no format is needed"
    elif result["candidates"] and not result["ambiguous"]:
        result["suggested"] = result["candidates"][0]["pattern"]
    elif result["ambiguous"]:
        result["note"] = ("every day in the sample is 12 or less, so day-first and month-first both fit; "
                          "choose the one the source system uses")
    else:
        result["note"] = "no known format reads every sample value; the values may be mixed or unusual"
    return result
