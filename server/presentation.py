from __future__ import annotations

"""Non-destructive presentation redaction.

This module is deliberately downstream of capture, storage, contextualization and
workflow inference. It transforms copies of values only when they are about to be
shown through the local API, dashboard, MCP or export surfaces.

The raw event database remains untouched. That separation is load-bearing: names,
email addresses and phone numbers can be hidden without deleting the semantic
labels that task inference already used.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

EMAIL_RE = re.compile(
    r"(?<![\w.+-])([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+)(?![\w.-])",
    re.IGNORECASE,
)
DISPLAY_EMAIL_RE = re.compile(
    r"(?P<name>[^<>\n]{2,100}?)\s*<\s*(?P<email>[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+)\s*>",
    re.IGNORECASE,
)
PHONE_CANDIDATE_RE = re.compile(
    r"(?<!\w)(?:\+\d[\d\s().-]{6,}\d|\(\d{2,4}\)[\d\s.-]{5,}\d|\d{2,4}[\s]\d{2,4}(?:[\s-]\d{2,4}){1,3})(?!\w)"
)
CUE_RE = re.compile(
    r"\b(reply\s+to|from|to|cc|bcc|sender|recipient|message|call|meeting\s+with|assigned\s+to|owner|contact)\s*[:\-]?\s+",
    re.IGNORECASE,
)

GENERIC_LOCALPARTS = {
    "admin", "billing", "careers", "contact", "hello", "help", "hr", "info",
    "mail", "marketing", "no-reply", "noreply", "notifications", "office",
    "sales", "security", "service", "support", "team", "webmaster",
}

# These terms are common enough in work-resource titles that treating them as
# human names would hide useful workflow semantics. This is intentionally
# conservative: a missed name is preferable to destroying the visible task label.
NON_NAME_WORDS = {
    "account", "approval", "approve", "browser", "case", "chatgpt", "chrome",
    "client", "compose", "contract", "customer", "dashboard", "deploy", "document",
    "email", "excel", "finance", "form", "forward", "github", "gmail", "google",
    "invoice", "issue", "mail", "marketing", "meeting", "message", "microsoft",
    "new", "order", "outlook", "payment", "pricing", "private", "project",
    "question", "renewal", "reply", "request", "review", "sales", "salesforce",
    "search", "send", "sheet", "sheets", "slack", "subject", "support", "task",
    "teams", "ticket", "update", "workflow", "workspace",
}

EMAIL_CONTEXT_MARKERS = (
    "gmail", "mail.google.com", "outlook", "outlook.office.com",
    "outlook.office365.com", "outlook.live.com",
)

_TOKEN_CACHE: dict[tuple[str, str], str] = {}
_KEY_CACHE: dict[str, bytes] = {}


def _data_dir() -> Path:
    return Path(os.getenv("WORKFLOW_OBSERVER_DATA", ROOT / "data"))


def _local_key() -> bytes:
    """Return a stable installation-local key without an external service.

    The key file wins once created, so pseudonyms do not change depending on
    whether the API or desktop collector happens to start first.
    """
    data_dir = _data_dir()
    cache_key = str(data_dir.resolve())
    if cache_key in _KEY_CACHE:
        return _KEY_CACHE[cache_key]

    key_path = data_dir / ".display_redaction_key"
    key: bytes | None = None
    try:
        if key_path.exists():
            raw = key_path.read_text(encoding="ascii").strip()
            if raw:
                key = bytes.fromhex(raw)
    except Exception:
        key = None

    if key is None:
        identity_path = data_dir / "identity.json"
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            installation_id = str(identity.get("installation_id") or "").strip()
            if installation_id:
                key = hashlib.sha256(("openworkgraph-display:" + installation_id).encode("utf-8")).digest()
        except Exception:
            pass

    if key is None:
        key = secrets.token_bytes(32)

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        if not key_path.exists():
            key_path.write_text(key.hex(), encoding="ascii")
    except Exception:
        # Read-only/test environments still keep a process-local stable key.
        if key is None:
            key = hashlib.sha256(cache_key.encode("utf-8", errors="ignore")).digest()

    _KEY_CACHE[cache_key] = key
    return key


def _token(kind: str, canonical: str) -> str:
    normalized = re.sub(r"\s+", " ", str(canonical or "")).strip().casefold()
    cache_key = (kind, normalized)
    if cache_key in _TOKEN_CACHE:
        return _TOKEN_CACHE[cache_key]
    digest = hmac.new(_local_key(), normalized.encode("utf-8", errors="ignore"), hashlib.sha256).hexdigest()[:6].upper()
    value = f"{kind}_{digest}"
    _TOKEN_CACHE[cache_key] = value
    return value


def _phone_token(match: re.Match[str]) -> str:
    value = match.group(0)
    digits = re.sub(r"\D", "", value)
    # Avoid dates, times, small numeric fragments and identifiers that merely
    # happen to contain separators.
    if not 7 <= len(digits) <= 15:
        return value
    if re.fullmatch(r"20\d{2}[ -]\d{1,2}[ -]\d{1,2}", value.strip()):
        return value
    return _token("PHONE", digits)


def _clean_name_candidate(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" \t\r\n\"'()[]{}:-"))


def _name_words(value: str) -> list[str]:
    return [w.strip(".,") for w in re.split(r"\s+", _clean_name_candidate(value)) if w.strip(".,")]


def _looks_like_person_name(value: str, *, allow_single: bool = False) -> bool:
    words = _name_words(value)
    if not words or len(words) > 4 or (len(words) == 1 and not allow_single):
        return False
    for word in words:
        bare = word.strip(".'’-")
        if not bare or any(ch.isdigit() for ch in bare) or "@" in bare:
            return False
        if bare.casefold() in NON_NAME_WORDS:
            return False
        first = bare[0]
        if not first.isalpha() or not first.isupper():
            return False
    return True


def _tail_name_candidate(value: str) -> tuple[str, str]:
    """Return (prefix, plausible-name-tail) from a mixed header string."""
    raw = str(value or "")
    pieces = re.split(r"(\s+(?:[-–—|·])\s+|\b(?:from|to|cc|bcc|sender|recipient)\s*:\s*)", raw, flags=re.I)
    for i in range(len(pieces) - 1, -1, -1):
        candidate = _clean_name_candidate(pieces[i])
        if candidate and _looks_like_person_name(candidate, allow_single=True):
            pos = raw.rfind(pieces[i])
            if pos >= 0:
                return raw[:pos] + pieces[i][: len(pieces[i]) - len(pieces[i].lstrip())], candidate
    candidate = _clean_name_candidate(raw)
    if _looks_like_person_name(candidate, allow_single=True):
        pos = raw.find(candidate)
        return raw[:pos], candidate
    return raw, ""


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)


def _discover_aliases(value: Any) -> dict[str, str]:
    """Learn person aliases already evidenced by email/cued UI strings.

    The map exists only for this presentation call; raw aliases are not persisted.
    """
    aliases: dict[str, str] = {}
    for text in _walk_strings(value):
        for match in DISPLAY_EMAIL_RE.finditer(text):
            _prefix, name = _tail_name_candidate(match.group("name"))
            email = match.group("email").casefold()
            if name:
                aliases[name.casefold()] = _token("PERSON", email)
        for email_match in EMAIL_RE.finditer(text):
            email = email_match.group(1)
            local = email.split("@", 1)[0].casefold()
            if local in GENERIC_LOCALPARTS:
                continue
            parts = [p for p in re.split(r"[._-]+", local) if p.isalpha() and len(p) > 1]
            if 2 <= len(parts) <= 4:
                alias = " ".join(parts)
                aliases.setdefault(alias.casefold(), _token("PERSON", email.casefold()))

        # Cued names such as "Reply to Anna Svensson" are structurally much less
        # ambiguous than arbitrary title-case words in a document subject.
        for match in CUE_RE.finditer(text):
            tail = text[match.end():]
            words: list[str] = []
            for raw in re.findall(r"[^\s,;<>|()]+", tail)[:4]:
                candidate = raw.strip(" \t\r\n\"'[]{}:-")
                if not candidate:
                    break
                tentative = " ".join(words + [candidate])
                if _looks_like_person_name(tentative, allow_single=True):
                    words.append(candidate)
                else:
                    break
            if words and _looks_like_person_name(" ".join(words), allow_single=True):
                name = " ".join(words)
                aliases.setdefault(name.casefold(), _token("PERSON", name))
    return aliases


def _contains_email_context(value: Any) -> bool:
    if isinstance(value, dict):
        for key in ("surface", "app", "hostname", "window_title", "resource_title", "context_text"):
            text = str(value.get(key) or "").casefold()
            if any(marker in text for marker in EMAIL_CONTEXT_MARKERS):
                return True
    return False


def _replace_aliases(text: str, aliases: dict[str, str]) -> str:
    out = text
    # Longest first prevents a short alias from partially replacing a full name.
    for alias, replacement in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if not alias:
            continue
        out = re.sub(rf"(?<!\w){re.escape(alias)}(?!\w)", replacement, out, flags=re.IGNORECASE)
    return out


def _redact_display_email(match: re.Match[str]) -> str:
    raw_name = match.group("name")
    email = match.group("email").casefold()
    prefix, name = _tail_name_candidate(raw_name)
    if name:
        person = _token("PERSON", email)
        return f"{prefix}{person} <{_token('EMAIL', email)}>"
    return f"{raw_name} <{_token('EMAIL', email)}>"


def _redact_email_title_segments(text: str) -> str:
    """Best-effort masking for person-only segments in Gmail/Outlook titles.

    This is display-only. False positives cannot delete or alter the stored event.
    We deliberately require a standalone title-case segment and reject common
    workflow words to avoid turning useful subjects such as "Contract Renewal"
    into PERSON tokens.
    """
    # Some mail clients render "Person Name: Subject" rather than dash-separated
    # segments. Mask only when the leading segment is strongly name-like.
    colon = re.match(r"^(?P<head>[^:]{2,80})(?P<sep>:\s+)(?P<rest>.+)$", text)
    if colon and _looks_like_person_name(colon.group("head")):
        text = f"{_token('PERSON', colon.group('head'))}{colon.group('sep')}{colon.group('rest')}"

    parts = re.split(r"(\s+(?:[-–—|·])\s+)", text)
    if len(parts) < 3:
        return text
    for i in range(0, len(parts), 2):
        segment = parts[i].strip()
        if not segment or any(marker in segment.casefold() for marker in EMAIL_CONTEXT_MARKERS):
            continue
        if _looks_like_person_name(segment):
            parts[i] = parts[i].replace(segment, _token("PERSON", segment))
    return "".join(parts)


def redact_text(text: str, *, aliases: dict[str, str] | None = None, email_context: bool = False, field_name: str = "") -> str:
    """Return a display-safe string while preserving non-PII semantics."""
    if not text:
        return text
    if re.fullmatch(r"(?:PERSON|EMAIL|PHONE)_[0-9A-F]{6}", text):
        return text

    aliases = aliases or {}
    out = _replace_aliases(text, aliases)

    # Handle display-name + email structures as one unit so a name is still
    # hidden when the local part is initials or otherwise cannot teach an alias.
    out = DISPLAY_EMAIL_RE.sub(_redact_display_email, out)

    # Explicit person-valued fields are safe to pseudonymize when they are a
    # plausible name. We do not apply this to arbitrary titles.
    if field_name.casefold() in {"sender", "recipient", "contact", "owner", "person", "display_name"} and _looks_like_person_name(out, allow_single=True):
        out = _token("PERSON", out)

    # Remaining email addresses and phone numbers are deterministic
    # high-confidence cases.
    out = EMAIL_RE.sub(lambda m: _token("EMAIL", m.group(1).casefold()), out)
    out = PHONE_CANDIDATE_RE.sub(_phone_token, out)

    # Aliases may have appeared next to an email that was just replaced.
    out = _replace_aliases(out, aliases)
    if email_context:
        out = _redact_email_title_segments(out)
    return out


def redact_for_display(value: Any) -> Any:
    """Deep-copy/redact a payload for UI, API, MCP or export presentation.

    This function never mutates ``value`` and never writes to event/context tables.
    Structural values (event IDs, timestamps, counts, durations, booleans) are
    preserved exactly. Only human-readable string values can be pseudonymized.
    """
    aliases = _discover_aliases(value)

    def transform(item: Any, *, inherited_email_context: bool = False, field_name: str = "") -> Any:
        if isinstance(item, dict):
            email_context = inherited_email_context or _contains_email_context(item)
            return {
                key: transform(val, inherited_email_context=email_context, field_name=str(key))
                for key, val in item.items()
            }
        if isinstance(item, list):
            return [transform(v, inherited_email_context=inherited_email_context, field_name=field_name) for v in item]
        if isinstance(item, tuple):
            return tuple(transform(v, inherited_email_context=inherited_email_context, field_name=field_name) for v in item)
        if isinstance(item, str):
            # Machine identity fields are not user-facing content and must remain
            # byte-for-byte stable for linking/filtering.
            if field_name in {
                "event_id", "session_id", "device_id", "sensor_id", "organization_id",
                "actor_id", "schema_version", "browser_session_id", "work_session_id",
                "observed_at", "generated_at", "run_started_at",
            }:
                return item
            return redact_text(item, aliases=aliases, email_context=inherited_email_context, field_name=field_name)
        return item

    return transform(value)
