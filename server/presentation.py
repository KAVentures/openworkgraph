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
TITLE_WORD_RE = re.compile(r"(?<![\w_])([A-ZÅÄÖÉÜ][a-zåäöéüàáâãèêëíîïóôõúûüçñ'’.-]{1,})(?![\w_])", re.UNICODE)

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
NAME_CONTEXT_MARKERS = (
    "gmail", "outlook", "mail.google.com", "teams", "slack", "calendar", "meeting",
    "salesforce", "hubspot", "contact", "customer", "document", "docs.google.com",
    "drive.google.com", "sharepoint", "notion",
)

_TOKEN_CACHE: dict[tuple[str, str], str] = {}
_KEY_CACHE: dict[str, bytes] = {}
_OWNER_CACHE: dict[str, tuple[dict[str, str], set[str], set[str]]] = {}


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


def _load_people_registry() -> dict[str, str]:
    try:
        data = json.loads(_people_registry_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    except Exception:
        pass
    return {}


def _clean_name_candidate(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip(" \t\r\n\"'()[]{}:-"))


def _name_words(value: str) -> list[str]:
    return [w.strip(".,") for w in re.split(r"\s+", _clean_name_candidate(value)) if w.strip(".,")]


def _remember_alias(alias: str, token: str, registry: dict[str, str]) -> None:
    cleaned = _clean_name_candidate(alias)
    if not cleaned:
        return
    variants = [cleaned]
    words = _name_words(cleaned)
    if len(words) > 1 and len(words[0]) >= 3 and words[0].casefold() not in NON_NAME_WORDS:
        variants.append(words[0])
    changed = False
    for variant in variants:
        key = _alias_hash(variant)
        if registry.get(key) != token:
            registry[key] = token
            changed = True
    if changed:
        try:
            path = _people_registry_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(registry, sort_keys=True), encoding="utf-8")
        except Exception:
            pass


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
    raw_aliases: list[str] = []
    configured = cfg.get("owner_aliases", [])
    if isinstance(configured, str):
        configured = [configured]
    if isinstance(configured, list):
        raw_aliases.extend(str(v).strip() for v in configured if str(v).strip())

    for candidate in (_windows_display_name(), _posix_display_name(), getpass.getuser()):
        candidate = str(candidate or "").strip()
        if candidate:
            raw_aliases.append(candidate)

    aliases: dict[str, str] = {}
    for raw in raw_aliases:
        cleaned = _clean_name_candidate(raw)
        if not cleaned:
            continue
        aliases[cleaned.casefold()] = "OWNER"
        words = _name_words(cleaned)
        if len(words) > 1 and len(words[0]) >= 3 and words[0].casefold() not in NON_NAME_WORDS:
            aliases.setdefault(words[0].casefold(), "OWNER")

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


def _contains_name_sensitive_context(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if _contains_email_context(value):
        return True
    for key in ("surface", "app", "hostname", "window_title", "resource_title", "context_text", "event_type"):
        text = str(value.get(key) or "").casefold()
        if any(marker in text for marker in NAME_CONTEXT_MARKERS):
            return True
    return False


def _discover_aliases(value: Any) -> dict[str, str]:
    """Learn only high-confidence people from structured/cued evidence."""
    aliases: dict[str, str] = {}
    registry = _load_people_registry()

    def process_text(text: str, *, email_context: bool) -> None:
        for match in DISPLAY_EMAIL_RE.finditer(text):
            _prefix, name = _tail_name_candidate(match.group("name"))
            email = match.group("email").casefold()
            if name:
                token = _token("PERSON", email)
                aliases[name.casefold()] = token
                _remember_alias(name, token, registry)

        for email_match in EMAIL_RE.finditer(text):
            email = email_match.group(1)
            local = email.split("@", 1)[0].casefold()
            if local in GENERIC_LOCALPARTS:
                continue
            parts = [p for p in re.split(r"[._-]+", local) if p.isalpha() and len(p) > 1]
            if 2 <= len(parts) <= 4:
                alias = " ".join(parts)
                token = _token("PERSON", email.casefold())
                aliases.setdefault(alias.casefold(), token)
                _remember_alias(alias.title(), token, registry)

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
                token = _token("PERSON", name)
                aliases.setdefault(name.casefold(), token)
                _remember_alias(name, token, registry)

        if email_context:
            for match in EMAIL_SELECT_RE.finditer(text):
                name = _clean_name_candidate(match.group("name"))
                if _looks_like_person_name(name, allow_single=True):
                    token = _token("PERSON", name)
                    aliases.setdefault(name.casefold(), token)
                    _remember_alias(name, token, registry)

    def visit(item: Any, *, inherited_email_context: bool = False) -> None:
        if isinstance(item, dict):
            email_context = inherited_email_context or _contains_email_context(item)
            for child in item.values():
                visit(child, inherited_email_context=email_context)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, inherited_email_context=inherited_email_context)
        elif isinstance(item, str):
            process_text(item, email_context=inherited_email_context)

    visit(value)
    return aliases


def _replace_aliases(text: str, aliases: dict[str, str]) -> str:
    out = text
    for alias, replacement in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if alias:
            out = re.sub(rf"(?<!\w){re.escape(alias)}(?!\w)", replacement, out, flags=re.IGNORECASE)
    return out


def _replace_known_people(text: str, registry: dict[str, str]) -> str:
    matches = list(WORD_RE.finditer(text))
    if not matches or not registry:
        return text
    replacements: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for size in (4, 3, 2, 1):
        for i in range(0, len(matches) - size + 1):
            start = matches[i].start()
            end = matches[i + size - 1].end()
            if any(not (end <= a or start >= b) for a, b in occupied):
                continue
            gaps = [text[matches[j].end():matches[j + 1].start()] for j in range(i, i + size - 1)]
            if any(not gap.isspace() for gap in gaps):
                continue
            candidate = text[start:end]
            token = registry.get(_alias_hash(candidate))
            if token:
                replacements.append((start, end, token))
                occupied.append((start, end))
    out = text
    for start, end, token in sorted(replacements, reverse=True):
        out = out[:start] + token + out[end:]
    return out


def _embedded_name_spans(text: str) -> list[tuple[int, int, str]]:
    """Find conservative 2-4 word title-case person candidates inside rich text.

    This is presentation-only and is used only in name-sensitive contexts. A span
    is rejected if any word is common workflow/resource vocabulary.
    """
    words = list(TITLE_WORD_RE.finditer(text))
    spans: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for size in (4, 3, 2):
        for i in range(0, len(words) - size + 1):
            start = words[i].start()
            end = words[i + size - 1].end()
            if any(not (end <= a or start >= b) for a, b in occupied):
                continue
            gaps = [text[words[j].end():words[j + 1].start()] for j in range(i, i + size - 1)]
            if any(not gap.isspace() for gap in gaps):
                continue
            candidate = text[start:end]
            parts = _name_words(candidate)
            if any(p.casefold() in NON_NAME_WORDS for p in parts):
                continue
            if not _looks_like_person_name(candidate):
                continue
            if re.search(r"\b(?:OWNER|PERSON|EMAIL|PHONE)_", candidate):
                continue
            spans.append((start, end, candidate))
            occupied.append((start, end))
    return spans


def _redact_embedded_names(text: str, *, owner_aliases: dict[str, str], registry: dict[str, str]) -> str:
    """Mask likely person spans while leaving surrounding subject/title text intact."""
    replacements: list[tuple[int, int, str]] = []
    for start, end, candidate in _embedded_name_spans(text):
        owner = owner_aliases.get(candidate.casefold())
        if owner:
            replacement = "OWNER"
        else:
            replacement = registry.get(_alias_hash(candidate)) or _token("PERSON", candidate)
        replacements.append((start, end, replacement))
    out = text
    for start, end, replacement in sorted(replacements, reverse=True):
        out = out[:start] + replacement + out[end:]
    return out


def _redact_display_email(match: re.Match[str], *, owner_aliases: dict[str, str], owner_emails: set[str]) -> str:
    raw_name = match.group("name")
    email = match.group("email").casefold()
    prefix, name = _tail_name_candidate(raw_name)
    email_token = "OWNER_EMAIL" if email in owner_emails else _token("EMAIL", email)
    if name:
        person = "OWNER" if name.casefold() in owner_aliases or email in owner_emails else _token("PERSON", email)
        return f"{prefix}{person} <{email_token}>"
    return f"{raw_name} <{email_token}>"


def _redact_email_title_segments(text: str, *, owner_aliases: dict[str, str]) -> str:
    colon = re.match(r"^(?P<head>[^:]{2,80})(?P<sep>:\s+)(?P<rest>.+)$", text)
    if colon and _looks_like_person_name(colon.group("head")):
        head = colon.group("head")
        replacement = "OWNER" if head.casefold() in owner_aliases else _token("PERSON", head)
        text = f"{replacement}{colon.group('sep')}{colon.group('rest')}"

    parts = re.split(r"(\s+(?:[-–—|·])\s+)", text)
    if len(parts) < 3:
        return text
    for i in range(0, len(parts), 2):
        segment = parts[i].strip()
        if not segment or any(marker in segment.casefold() for marker in EMAIL_CONTEXT_MARKERS):
            continue
        if _looks_like_person_name(segment):
            replacement = "OWNER" if segment.casefold() in owner_aliases else _token("PERSON", segment)
            parts[i] = parts[i].replace(segment, replacement)
    return "".join(parts)


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
    if not text:
        return text
    if re.fullmatch(r"(?:OWNER|OWNER_EMAIL|OWNER_PHONE|PERSON_[0-9A-F]{6}|EMAIL_[0-9A-F]{6}|PHONE_[0-9A-F]{6})", text):
        return text

    aliases = aliases or {}
    owner_aliases = owner_aliases or {}
    owner_emails = owner_emails or set()
    owner_phones = owner_phones or set()
    known_people = known_people or {}
    out = text

    out = DISPLAY_EMAIL_RE.sub(
        lambda m: _redact_display_email(m, owner_aliases=owner_aliases, owner_emails=owner_emails),
        out,
    )
    out = EMAIL_RE.sub(
        lambda m: "OWNER_EMAIL" if m.group(1).casefold() in owner_emails else _token("EMAIL", m.group(1).casefold()),
        out,
    )

    def phone_replacement(match: re.Match[str]) -> str:
        value = match.group(0)
        digits = re.sub(r"\D", "", value)
        if not 7 <= len(digits) <= 15:
            return value
        if re.fullmatch(r"20\d{2}[ -]\d{1,2}[ -]\d{1,2}", value.strip()):
            return value
        return "OWNER_PHONE" if digits in owner_phones else _token("PHONE", digits)

    out = PHONE_CANDIDATE_RE.sub(phone_replacement, out)
    out = _replace_aliases(out, owner_aliases)
    out = _replace_aliases(out, aliases)
    out = _replace_known_people(out, known_people)

    if field_name.casefold() in {"sender", "recipient", "contact", "owner", "person", "display_name"} and _looks_like_person_name(out, allow_single=True):
        out = "OWNER" if out.casefold() in owner_aliases else _token("PERSON", out)

    if email_context:
        out = _redact_email_title_segments(out, owner_aliases=owner_aliases)

    # v0.36: names embedded *inside* subjects/titles/observed-action labels were
    # previously missed because the whole string was treated as free text. In a
    # name-sensitive context, redact only conservative title-case person spans.
    if (email_context or name_sensitive_context) and field_name.casefold() in TITLEISH_FIELDS:
        out = _redact_embedded_names(out, owner_aliases=owner_aliases, registry=known_people)

    return out


def redact_for_display(value: Any) -> Any:
    """Deep-copy/redact a payload for UI, API, MCP or export presentation.

    This function never mutates ``value`` and never writes to event/context tables.
    Structural values (event IDs, timestamps, counts, durations, booleans) are
    preserved exactly. Only human-readable string values can be pseudonymized.
    """
    owner_aliases, owner_emails, owner_phones = _owner_identity()
    aliases = _discover_aliases(value)
    known_people = _load_people_registry()

    def transform(
        item: Any,
        *,
        inherited_email_context: bool = False,
        inherited_name_sensitive_context: bool = False,
        field_name: str = "",
    ) -> Any:
        if isinstance(item, dict):
            email_context = inherited_email_context or _contains_email_context(item)
            name_sensitive_context = inherited_name_sensitive_context or _contains_name_sensitive_context(item)
            return {
                key: transform(
                    val,
                    inherited_email_context=email_context,
                    inherited_name_sensitive_context=name_sensitive_context,
                    field_name=str(key),
                )
                for key, val in item.items()
            }
        if isinstance(item, list):
            return [
                transform(
                    v,
                    inherited_email_context=inherited_email_context,
                    inherited_name_sensitive_context=inherited_name_sensitive_context,
                    field_name=field_name,
                )
                for v in item
            ]
        if isinstance(item, tuple):
            return tuple(
                transform(
                    v,
                    inherited_email_context=inherited_email_context,
                    inherited_name_sensitive_context=inherited_name_sensitive_context,
                    field_name=field_name,
                )
                for v in item
            )
        if isinstance(item, str):
            if field_name in {
                "event_id", "session_id", "device_id", "sensor_id", "organization_id",
                "actor_id", "schema_version", "browser_session_id", "work_session_id",
                "observed_at", "generated_at", "run_started_at",
            }:
                return item
            return redact_text(
                item,
                aliases=aliases,
                owner_aliases=owner_aliases,
                owner_emails=owner_emails,
                owner_phones=owner_phones,
                known_people=known_people,
                email_context=inherited_email_context,
                name_sensitive_context=inherited_name_sensitive_context,
                field_name=field_name,
            )
        return item

    return transform(value)
