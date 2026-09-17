from __future__ import annotations

"""Non-destructive presentation redaction.

This module is deliberately downstream of capture, storage, contextualization and
workflow inference. It transforms copies of values only when they are about to be
shown through the local API, dashboard, MCP or export surfaces.

The raw event database remains untouched. That separation is load-bearing: names,
email addresses and phone numbers can be hidden without deleting the semantic
labels that task inference already used.
"""

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
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
    r"\b(reply\s+to|from|to|cc|bcc|sender|recipient|message|call|meeting\s+with|assigned\s+to|owner|contact|participant|attendee)\s*[:\-]?\s+",
    re.IGNORECASE,
)
EMAIL_SELECT_RE = re.compile(
    r"\b(?:select|deselect)(?:\s+(?:email|message))?(?:\s+from)?\s+(?P<name>[^,;<>|]{1,100})(?=\s*[,;]|$)",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[^\W\d_][\w'’.-]*", re.UNICODE)

GENERIC_LOCALPARTS = {
    "admin", "billing", "careers", "contact", "hello", "help", "hr", "info",
    "mail", "marketing", "no-reply", "noreply", "notifications", "office",
    "sales", "security", "service", "support", "team", "webmaster",
}

# Work/resource vocabulary that should not be mistaken for a human name merely
# because it is title-cased. This intentionally errs toward preserving semantics.
NON_NAME_WORDS = {
    "account", "action", "analysis", "annual", "approval", "approve", "archive",
    "browser", "budget", "calendar", "case", "chatgpt", "chrome", "client",
    "compose", "contract", "customer", "dashboard", "demo", "deploy", "design",
    "document", "draft", "email", "estimate", "excel", "final", "finance",
    "follow", "form", "forward", "forecast", "github", "gmail", "google",
    "invoice", "issue", "item", "items", "launch", "mail", "marketing",
    "meeting", "message", "microsoft", "new", "notes", "notification", "offer",
    "opportunity", "order", "outlook", "payment", "plan", "pricing", "private",
    "product", "production", "project", "proposal", "purchase", "quarterly",
    "question", "quote", "release", "renewal", "reply", "report", "repository",
    "request", "research", "review", "roadmap", "sales", "salesforce", "schedule",
    "search", "select", "send", "sheet", "sheets", "slack", "staging", "status",
    "strategy", "subject", "summary", "support", "task", "teams", "test", "testing",
    "ticket", "update", "weekly", "workflow", "workspace",
}

EMAIL_CONTEXT_MARKERS = (
    "gmail", "mail.google.com", "outlook", "outlook.office.com",
    "outlook.office365.com", "outlook.live.com",
)
TITLEISH_FIELDS = {
    "label", "title", "window_title", "resource_title", "context_text", "subject",
    "suggested_label", "label_evidence", "name", "description",
}
_TOKEN_CACHE: dict[tuple[str, str], str] = {}
_KEY_CACHE: dict[str, bytes] = {}
_OWNER_CACHE: dict[str, tuple[dict[str, str], set[str], set[str]]] = {}
_REGISTRY_LOCK = threading.RLock()


def _data_dir() -> Path:
    return Path(os.getenv("WORKFLOW_OBSERVER_DATA", ROOT / "data"))


def _local_key() -> bytes:
    """Return a stable installation-local key without an external service."""
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
        pass

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


def _alias_hash(alias: str) -> str:
    normalized = re.sub(r"\s+", " ", str(alias or "")).strip().casefold()
    return hmac.new(_local_key(), ("person-alias:" + normalized).encode("utf-8"), hashlib.sha256).hexdigest()


def _people_registry_path() -> Path:
    return _data_dir() / ".presentation_people.json"


def _save_people_registry_data(data: dict[str, Any]) -> None:
    """Atomically persist hashed alias metadata; callers must be on a write path."""
    path = _people_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(data, sort_keys=True)
    with _REGISTRY_LOCK:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)


def _clean_name_candidate(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip(" \t\r\n\"'()[]{}:-"))


def _name_words(value: str) -> list[str]:
    return [w.strip(".,") for w in re.split(r"\s+", _clean_name_candidate(value)) if w.strip(".,")]


def _looks_like_person_name(value: str, *, allow_single: bool = False) -> bool:
    words = _name_words(value)
    if not words or len(words) > 4 or (len(words) == 1 and not allow_single):
        return False
    for word in words:
        bare = word.strip(".'’-_")
        if not bare or any(ch.isdigit() for ch in bare) or "@" in bare:
            return False
        if bare.casefold() in NON_NAME_WORDS:
            return False
        first = bare[0]
        if not first.isalpha() or not first.isupper():
            return False
    return True


def _tail_name_candidate(value: str) -> tuple[str, str]:
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


def _load_config() -> dict[str, Any]:
    path = ROOT / "config.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _windows_display_name() -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        secur32 = ctypes.WinDLL("secur32")
        get_name = secur32.GetUserNameExW
        get_name.argtypes = [ctypes.c_int, wintypes.LPWSTR, ctypes.POINTER(wintypes.ULONG)]
        get_name.restype = wintypes.BOOL
        size = wintypes.ULONG(0)
        get_name(3, None, ctypes.byref(size))
        if not size.value:
            return ""
        buf = ctypes.create_unicode_buffer(size.value)
        if get_name(3, buf, ctypes.byref(size)):
            return str(buf.value or "").strip()
    except Exception:
        pass
    return ""


def _posix_display_name() -> str:
    if os.name == "nt":
        return ""
    try:
        import pwd
        return str(pwd.getpwuid(os.getuid()).pw_gecos or "").split(",", 1)[0].strip()
    except Exception:
        return ""


def _owner_identity() -> tuple[dict[str, str], set[str], set[str]]:
    cache_key = str((ROOT / "config.json").resolve()) + "|" + str(_data_dir().resolve())
    if cache_key in _OWNER_CACHE:
        return _OWNER_CACHE[cache_key]

    cfg = _load_config()
    aliases: dict[str, str] = {}

    # Explicit configuration is authoritative, including an explicitly supplied
    # single first name.  Automatically discovered OS identity is deliberately
    # stricter so another person who merely shares the owner's first name is not
    # silently relabeled OWNER.
    configured = cfg.get("owner_aliases", [])
    if isinstance(configured, str):
        configured = [configured]
    if isinstance(configured, list):
        for value in configured:
            cleaned = _clean_name_candidate(str(value))
            if cleaned:
                aliases[cleaned.casefold()] = "OWNER"

    display_names: list[str] = []
    for candidate in (_windows_display_name(), _posix_display_name()):
        cleaned = _clean_name_candidate(str(candidate or ""))
        if not cleaned:
            continue
        # A full display name is strong evidence. A one-word OS display name is
        # too ambiguous to use globally.
        if len(_name_words(cleaned)) >= 2:
            aliases[cleaned.casefold()] = "OWNER"
            display_names.append(cleaned)

    username = _clean_name_candidate(getpass.getuser())
    if username:
        username_folded = re.sub(r"[^a-z0-9]", "", username.casefold())
        display_compacts = {
            re.sub(r"[^a-z0-9]", "", name.casefold())
            for name in display_names
        }
        # Keep a concatenated/full account identifier (e.g. koyarafrasyab), but
        # do not auto-promote a bare first-name username such as "koyar".
        if len(_name_words(username)) >= 2 or username_folded in display_compacts:
            aliases[username.casefold()] = "OWNER"

    emails_cfg = cfg.get("owner_emails", [])
    if isinstance(emails_cfg, str):
        emails_cfg = [emails_cfg]
    emails = {str(v).strip().casefold() for v in emails_cfg if "@" in str(v)} if isinstance(emails_cfg, list) else set()

    phones: set[str] = set()
    phones_cfg = cfg.get("owner_phones", [])
    if isinstance(phones_cfg, str):
        phones_cfg = [phones_cfg]
    if isinstance(phones_cfg, list):
        for value in phones_cfg:
            digits = re.sub(r"\D", "", str(value))
            if 7 <= len(digits) <= 15:
                phones.add(digits)

    result = (aliases, emails, phones)
    _OWNER_CACHE[cache_key] = result
    return result

def _contains_email_context(value: Any) -> bool:
    if isinstance(value, dict):
        for key in ("surface", "app", "hostname", "window_title", "resource_title", "context_text"):
            text = str(value.get(key) or "").casefold()
            if any(marker in text for marker in EMAIL_CONTEXT_MARKERS):
                return True
    return False


def _redact_display_email(match: re.Match[str], *, owner_aliases: dict[str, str], owner_emails: set[str]) -> str:
    raw_name = match.group("name")
    email = match.group("email").casefold()
    prefix, name = _tail_name_candidate(raw_name)
    email_token = "OWNER_EMAIL" if email in owner_emails else _token("EMAIL", email)
    if name:
        person = "OWNER" if name.casefold() in owner_aliases or email in owner_emails else _token("PERSON", email)
        return f"{prefix}{person} <{email_token}>"
    return f"{raw_name} <{email_token}>"


def redact_text(
    text: str,
    *,
    aliases: dict[str, str] | None = None,
    owner_aliases: dict[str, str] | None = None,
    owner_emails: set[str] | None = None,
    owner_phones: set[str] | None = None,
    known_people: dict[str, str] | None = None,
    email_context: bool = False,
    name_sensitive_context: bool = False,
    field_name: str = "",
) -> str:
    """Compatibility helper; production redaction is assembled in privacy_pipeline."""
    from .privacy_pipeline import redact_for_display as _redact
    key = field_name or "text"
    return str(_redact({key: text})[key])

def redact_for_display(value: Any) -> Any:
    """Compatibility entry point; delegates to the explicit privacy pipeline."""
    from .privacy_pipeline import redact_for_display as _redact
    return _redact(value)
