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
    (
        "BANK_ACCOUNT",
        re.compile(
            r"(?P<label>\b(?:bankgiro|plusgiro|bank\s*account|bankkonto)\b\s*[:=#-]?\s*)"
            r"(?P<value>[0-9][0-9 .-]{5,24}[0-9])",
            re.IGNORECASE,
        ),
    ),
    (
        "ORG_ID",
        re.compile(
            r"(?P<label>\b(?:org\.?\s*(?:nr|nummer)|organisationsnummer|vat(?:\s*(?:nr|number))?|moms(?:reg(?:istrerings)?nummer)?)\b\s*[:=#-]?\s*)"
            r"(?P<value>(?:SE\s*)?[0-9]{6}[- ]?[0-9]{4}(?:01)?|SE[0-9]{12})",
            re.IGNORECASE,
        ),
    ),
)

_IBAN_RE = re.compile(
    r"(?<![A-Z0-9])(?P<value>[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30})(?![A-Z0-9])",
    re.IGNORECASE,
)
_PAN_RE = re.compile(r"(?<!\d)(?P<value>(?:\d[ -]?){12,18}\d)(?!\d)")

# Explicit business-reference cues take precedence over card-shape heuristics.
# We inspect both sides of a candidate because UIs commonly render "1234… (OCR)".
_NON_CARD_CUE_RE = re.compile(
    r"(?i)\b(?:ocr(?:[-\s]?nummer|[-\s]?nr)?|referens(?:nummer)?|reference|ref|"
    r"betalningsreferens|payment\s*reference|tracking(?:\s*(?:no|number))?|kolli|"
    r"sändningsnummer|kundnummer|customer\s*(?:no|number)|faktura(?:nummer)?|"
    r"invoice(?:\s*(?:no|number))?|order(?:\s*(?:no|number))?)\b"
)
_CARD_CUE_RE = re.compile(
    r"(?i)\b(?:payment\s*card|credit\s*card|debit\s*card|card\s*(?:no|number)|"
    r"kortnummer|kort\s*(?:nr|nummer)|visa|mastercard|amex|american\s+express|"
    r"discover|diners|jcb|unionpay|maestro)\b"
)

# Common card issuer ranges. This is only used for otherwise-unlabelled numeric
# strings; an explicit card cue masks any Luhn-valid 13–19 digit candidate.
_CARD_IIN_RE = re.compile(
    r"^(?:"
    r"4"
    r"|5[1-5]"
    r"|2(?:2(?:2[1-9]|[3-9]\d)|[3-6]\d{2}|7(?:[01]\d|20))"
    r"|3[47]"
    r"|3(?:0[0-5]|[68-9])"
    r"|6(?:011|5|4[4-9]|22(?:12[6-9]|1[3-9]\d|[2-8]\d{2}|9(?:[01]\d|2[0-5])))"
    r"|35(?:2[89]|[3-8]\d)"
    r"|62"
    r"|5[06789]"
    r")"
)

_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"AKIA[0-9A-Z]{16}"
    r"|ASIA[0-9A-Z]{16}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{30,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r")(?![A-Za-z0-9])"
)

_SECRET_NAME = (
    r"[A-Za-z0-9_.-]*"
    r"(?:secret|token|api[_-]?key|access[_-]?key|private[_-]?key|password|passwd|pwd)"
    r"[A-Za-z0-9_.-]*"
)
_SECRET_QUOTED_ASSIGN_RE = re.compile(
    rf"(?P<label>(?<![A-Za-z0-9])(?:export\s+)?{_SECRET_NAME}\s*[:=]\s*)"
    r"(?P<quote>[\"'])(?P<value>[^\r\n\"']{4,})(?P=quote)",
    re.IGNORECASE,
)
_SECRET_ASSIGN_RE = re.compile(
    rf"(?P<label>(?<![A-Za-z0-9])(?:export\s+)?{_SECRET_NAME}\s*[:=]\s*)"
    r"(?P<value>[^\s\"',;]{8,})",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(
    r"(?i)\b(?P<label>(?:authorization\s*:\s*)?bearer\s+)"
    r"(?P<value>[A-Za-z0-9._~+/-]{16,}=*)"
)
_CONN_PW_RE = re.compile(
    r"(?i)\b(?P<pre>[a-z][a-z0-9+.-]*://[^\s:/@]+:)"
    r"(?P<value>[^\s@]{4,})(?P<post>@)"
)
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?P<kind>[A-Z0-9 ]*PRIVATE KEY)-----"
    r".*?"
    r"-----END (?P=kind)-----",
    re.DOTALL,
)

_ALREADY_TOKEN_RE = re.compile(
    r"^(?:PERSONNUMMER|PATIENT_ID|JOURNAL_ID|CASE_ID|ACCOUNT_ID|BANK_ACCOUNT|ORG_ID|IBAN|PAYMENT_CARD|SENSITIVE_NUMBER|SECRET|PERSON|OCR_REFERENCE)_[0-9A-F]{6}$"
)
_LEGACY_OCR_TOKEN_RE = re.compile(
    r"(?P<cue>\b(?:ocr(?:[-\s]?(?:nummer|nr))?|referens(?:nummer)?|betalningsreferens|payment\s*reference)\b"
    r"[^A-Za-z0-9]{0,16})PAYMENT_CARD_(?P<digest>[0-9A-F]{6})",
    re.IGNORECASE,
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
    "iban": "IBAN",
    "cardnumber": "PAYMENT_CARD",
    "kortnummer": "PAYMENT_CARD",
    "organisationsnummer": "ORG_ID",
    "orgnummer": "ORG_ID",
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
            try:
                os.chmod(key_path, 0o600)
            except Exception:
                pass
            return bytes.fromhex(raw)
    except Exception:
        pass

    key = secrets.token_bytes(32)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        key_path.write_text(key.hex(), encoding="ascii")
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass
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


def _luhn_checksum_valid(value: str) -> bool:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _luhn_valid(value: str) -> bool:
    return len(value) == 10 and value.isdigit() and _luhn_checksum_valid(value)


def _iban_valid(value: str) -> bool:
    canonical = re.sub(r"\s+", "", str(value or "")).upper()
    if not 15 <= len(canonical) <= 34:
        return False
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", canonical):
        return False
    rearranged = canonical[4:] + canonical[:4]
    remainder = 0
    for char in rearranged:
        fragment = char if char.isdigit() else str(ord(char) - 55)
        for digit in fragment:
            remainder = (remainder * 10 + int(digit)) % 97
    return remainder == 1


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


def _nearby_has(pattern: re.Pattern[str], text: str, start: int, end: int, *, radius: int = 32) -> bool:
    before = text[max(0, start - radius):start]
    after = text[end:min(len(text), end + radius)]
    return bool(pattern.search(before) or pattern.search(after))


def redact_sensitive_identifiers(text: str, *, redact_adjacent_name: bool = False) -> str:
    """Pseudonymize high-confidence identifiers in human-readable text.

    Storage redaction is deliberately narrow: validated payment identifiers,
    credential-shaped secrets, Swedish personal identifiers and explicitly
    labelled IDs. Ordinary business names, amounts, project/deal names and order
    descriptions remain available for workflow understanding.
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

    def iban_repl(match: re.Match[str]) -> str:
        value = match.group("value")
        if not _iban_valid(value):
            return value
        return stable_token("IBAN", re.sub(r"\s+", "", value).upper())

    out = _IBAN_RE.sub(iban_repl, out)

    def pan_repl(match: re.Match[str]) -> str:
        value = match.group("value")
        digits = re.sub(r"\D", "", value)
        if not 13 <= len(digits) <= 19 or not _luhn_checksum_valid(digits):
            return value
        if _nearby_has(_NON_CARD_CUE_RE, out, match.start(), match.end()):
            return value
        explicit_card = _nearby_has(_CARD_CUE_RE, out, match.start(), match.end())
        if explicit_card or _CARD_IIN_RE.match(digits):
            return stable_token("PAYMENT_CARD", digits)
        # Privacy-first fallback: a Luhn-valid long number with no reference cue
        # is still masked, but we do not tell downstream AI it was definitely a card.
        return stable_token("SENSITIVE_NUMBER", digits)

    out = _PAN_RE.sub(pan_repl, out)

    # Known secret shapes first, then contextual assignments/URLs/headers.
    out = _PRIVATE_KEY_BLOCK_RE.sub(
        lambda m: stable_token("SECRET", m.group(0)),
        out,
    )
    out = _SECRET_RE.sub(lambda m: stable_token("SECRET", m.group(0)), out)

    def quoted_secret_assignment_repl(match: re.Match[str]) -> str:
        value = match.group("value")
        if _ALREADY_TOKEN_RE.fullmatch(value):
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group('label')}{quote}{stable_token('SECRET', value)}{quote}"

    out = _SECRET_QUOTED_ASSIGN_RE.sub(quoted_secret_assignment_repl, out)

    def secret_assignment_repl(match: re.Match[str]) -> str:
        value = match.group("value")
        if _ALREADY_TOKEN_RE.fullmatch(value):
            return match.group(0)
        return f"{match.group('label')}{stable_token('SECRET', value)}"

    out = _SECRET_ASSIGN_RE.sub(secret_assignment_repl, out)

    def bearer_repl(match: re.Match[str]) -> str:
        value = match.group("value")
        if _ALREADY_TOKEN_RE.fullmatch(value):
            return match.group(0)
        return f"{match.group('label')}{stable_token('SECRET', value)}"

    out = _BEARER_RE.sub(bearer_repl, out)

    def conn_repl(match: re.Match[str]) -> str:
        value = match.group("value")
        if _ALREADY_TOKEN_RE.fullmatch(value):
            return match.group(0)
        return f"{match.group('pre')}{stable_token('SECRET', value)}{match.group('post')}"

    out = _CONN_PW_RE.sub(conn_repl, out)

    # Old v0.45 rows may already have lost the literal OCR value. We cannot
    # reconstruct it, but we can repair the semantics so an AI no longer reads
    # an OCR/reference token as a payment card.
    out = _LEGACY_OCR_TOKEN_RE.sub(
        lambda m: f"OCR_REFERENCE_{m.group('digest').upper()}",
        out,
    )
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
    if "metadata" in e:
        e["metadata"] = walk(e.get("metadata") or {})
    return e
