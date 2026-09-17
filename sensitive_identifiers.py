from __future__ import annotations

"""High-confidence sensitive identifier handling.

This module is intentionally independent of presentation-name heuristics. It
pseudonymizes identifiers whose literal value is not needed for workflow
analysis while preserving stable local correlation.
"""

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

_PERSONNUMMER_RE = re.compile(
    r"(?<!\d)(?P<date>\d{8}|\d{6})(?P<sep>[-+]?)(?P<serial>\d{4})(?!\d)"
)
_PERSONNUMMER_CUE_RE = re.compile(
    r"(?:personnummer|personnr|person\s*nr|pnr|samordningsnummer|samordningsnr|ssn)\s*[:=#-]?\s*$",
    re.IGNORECASE,
)
_PERSON_WORD = r"[A-ZÅÄÖÉÜ][A-Za-zÅÄÖåäöÉéÜüÀ-ÖØ-öø-ÿ'’.-]*"
_NAME_BEFORE_ID_RE = re.compile(
    rf"(?P<name>{_PERSON_WORD}(?:\s+{_PERSON_WORD}){{1,3}})\s*,?\s*"
    r"(?:personnummer|personnr|person\s*nr|pnr|samordningsnummer|samordningsnr|ssn)\s*[:=#-]?\s*$",
    re.IGNORECASE,
)
_NAME_DIRECTLY_BEFORE_ID_RE = re.compile(
    rf"(?P<name>{_PERSON_WORD}(?:\s+{_PERSON_WORD}){{1,3}})\s+$"
)

_EXPLICIT_ID_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "PATIENT_ID",
        re.compile(
            r"(?P<label>\b(?:patient(?:[_\s-]?(?:id|nr|nummer))|patient-id)\b\s*[:=#-]?\s*)"
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9._/-]{3,63})",
            re.IGNORECASE,
        ),
    ),
    (
        "JOURNAL_ID",
        re.compile(
            r"(?P<label>\b(?:journal(?:[_\s-]?(?:id|nr|nummer)))\b\s*[:=#-]?\s*)"
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9._/-]{3,63})",
            re.IGNORECASE,
        ),
    ),
    (
        "CASE_ID",
        re.compile(
            r"(?P<label>\b(?:case\s*(?:id|number|nr)|ärende(?:[_\s-]?(?:id|nr|nummer)))\b\s*[:=#-]?\s*)"
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9._/-]{3,63})",
            re.IGNORECASE,
        ),
    ),
    (
        "ACCOUNT_ID",
        re.compile(
            r"(?P<label>\b(?:account\s*(?:id|number|nr)|konto(?:[_\s-]?(?:id|nr|nummer)))\b\s*[:=#-]?\s*)"
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9._/-]{3,63})",
            re.IGNORECASE,
        ),
    ),
)

_ALREADY_TOKEN_RE = re.compile(
    r"^(?:PERSONNUMMER|PATIENT_ID|JOURNAL_ID|CASE_ID|ACCOUNT_ID|PERSON)_[0-9A-F]{6}$"
)

_STRUCTURED_ID_KINDS = {
    "personnummer": "PERSONNUMMER",
    "personnr": "PERSONNUMMER",
    "pnr": "PERSONNUMMER",
    "samordningsnummer": "PERSONNUMMER",
    "samordningsnr": "PERSONNUMMER",
    "patientid": "PATIENT_ID",
    "patientnr": "PATIENT_ID",
    "patientnummer": "PATIENT_ID",
    "journalid": "JOURNAL_ID",
    "journalnr": "JOURNAL_ID",
    "journalnummer": "JOURNAL_ID",
    "caseid": "CASE_ID",
    "casenumber": "CASE_ID",
    "casenr": "CASE_ID",
    "ärendeid": "CASE_ID",
    "ärendenr": "CASE_ID",
    "ärendenummer": "CASE_ID",
    "accountid": "ACCOUNT_ID",
    "accountnumber": "ACCOUNT_ID",
    "accountnr": "ACCOUNT_ID",
    "kontoid": "ACCOUNT_ID",
    "kontonr": "ACCOUNT_ID",
    "kontonummer": "ACCOUNT_ID",
}


def _data_dir() -> Path:
    return Path(os.getenv("WORKFLOW_OBSERVER_DATA", ROOT / "data"))


def _local_key() -> bytes:
    data_dir = _data_dir()
    identity_path = data_dir / "identity.json"
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        installation_id = str(identity.get("installation_id") or "").strip()
        if installation_id:
            return hashlib.sha256(("openworkgraph-sensitive:" + installation_id).encode("utf-8")).digest()
    except Exception:
        pass

    key_path = data_dir / ".sensitive_identifier_key"
    try:
        raw = key_path.read_text(encoding="ascii").strip()
        if raw:
            return bytes.fromhex(raw)
    except Exception:
        pass

    key = secrets.token_bytes(32)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        key_path.write_text(key.hex(), encoding="ascii")
    except Exception:
        pass
    return key


def stable_token(kind: str, canonical: str) -> str:
    normalized = re.sub(r"\s+", "", str(canonical or "")).casefold()
    digest = hmac.new(
        _local_key(),
        (kind.casefold() + ":" + normalized).encode("utf-8", errors="ignore"),
        hashlib.sha256,
    ).hexdigest()[:6].upper()
    return f"{kind.upper()}_{digest}"


def _luhn_valid(value: str) -> bool:
    if len(value) != 10 or not value.isdigit():
        return False
    total = 0
    for index, char in enumerate(value[:-1]):
        digit = int(char) * (2 if index % 2 == 0 else 1)
        total += digit // 10 + digit % 10
    return (10 - (total % 10)) % 10 == int(value[-1])


def _date_shape_valid(date_digits: str) -> bool:
    if len(date_digits) == 8:
        years = [int(date_digits[:4])]
        month = int(date_digits[4:6])
        day = int(date_digits[6:8])
    elif len(date_digits) == 6:
        yy = int(date_digits[:2])
        years = [1900 + yy, 2000 + yy]
        month = int(date_digits[2:4])
        day = int(date_digits[4:6])
    else:
        return False

    if day >= 61:
        day -= 60
    if not 1 <= day <= 31:
        return False
    for year in years:
        try:
            date(year, month, day)
            return True
        except ValueError:
            continue
    return False


def _personnummer_is_sensitive(match: re.Match[str], text: str) -> bool:
    date_digits = match.group("date")
    serial = match.group("serial")
    if not _date_shape_valid(date_digits):
        return False

    canonical = date_digits[-6:] + serial
    if _luhn_valid(canonical):
        return True

    if match.group("sep") in {"-", "+"}:
        return True

    prefix = text[max(0, match.start() - 48):match.start()]
    return bool(_PERSONNUMMER_CUE_RE.search(prefix))


def _person_token_for_personnummer(canonical: str) -> str:
    return stable_token("PERSON", canonical)


def structured_identifier_kind(field_name: str) -> str | None:
    normalized = re.sub(r"[^0-9A-Za-zÅÄÖåäö]+", "", str(field_name or "")).casefold()
    return _STRUCTURED_ID_KINDS.get(normalized)


def tokenize_structured_identifier(field_name: str, value: str) -> str:
    raw = str(value or "")
    kind = structured_identifier_kind(field_name)
    if not raw or not kind or _ALREADY_TOKEN_RE.fullmatch(raw):
        return raw
    textual = redact_sensitive_identifiers(raw, redact_adjacent_name=False)
    if textual != raw:
        return textual
    canonical = re.sub(r"\s+", "", raw)
    return stable_token(kind, canonical)


def redact_sensitive_identifiers(text: str, *, redact_adjacent_name: bool = False) -> str:
    """Pseudonymize high-confidence identifiers in human-readable text.

    When ``redact_adjacent_name`` is true (presentation only), a full title-case
    name immediately tied to a Swedish personal identifier is also replaced.
    Storage callers leave names intact and pseudonymize only the identifier.
    """
    raw = str(text or "")
    if not raw or _ALREADY_TOKEN_RE.fullmatch(raw):
        return raw

    replacements: list[tuple[int, int, str]] = []

    for match in _PERSONNUMMER_RE.finditer(raw):
        if not _personnummer_is_sensitive(match, raw):
            continue
        canonical = match.group("date")[-6:] + match.group("serial")
        id_token = stable_token("PERSONNUMMER", canonical)
        replacements.append((match.start(), match.end(), id_token))

        if redact_adjacent_name:
            prefix = raw[:match.start()]
            name_match = _NAME_BEFORE_ID_RE.search(prefix)
            if not name_match and match.group("sep") in {"-", "+"}:
                name_match = _NAME_DIRECTLY_BEFORE_ID_RE.search(prefix)
            if name_match:
                start, end = name_match.span("name")
                replacements.append((start, end, _person_token_for_personnummer(canonical)))

    out = raw
    for start, end, replacement in sorted(replacements, reverse=True):
        out = out[:start] + replacement + out[end:]

    for kind, pattern in _EXPLICIT_ID_PATTERNS:
        def repl(match: re.Match[str], *, _kind: str = kind) -> str:
            value = match.group("value")
            if _ALREADY_TOKEN_RE.fullmatch(value):
                return match.group(0)
            return f"{match.group('label')}{stable_token(_kind, value)}"
        out = pattern.sub(repl, out)

    return out


def sanitize_event_identifiers(event: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy safe for persistence while preserving event structure."""
    e = copy.deepcopy(event)

    def walk(value: Any, *, key: str = "") -> Any:
        if isinstance(value, dict):
            return {str(k): walk(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, key=key) for v in value]
        if isinstance(value, tuple):
            return tuple(walk(v, key=key) for v in value)
        if isinstance(value, str):
            if structured_identifier_kind(key):
                return tokenize_structured_identifier(key, value)
            return redact_sensitive_identifiers(value, redact_adjacent_name=False)
        return value

    for field in ("app", "window_title"):
        if isinstance(e.get(field), str):
            e[field] = redact_sensitive_identifiers(str(e[field]), redact_adjacent_name=False)
    e["metadata"] = walk(e.get("metadata") or {})
    return e
