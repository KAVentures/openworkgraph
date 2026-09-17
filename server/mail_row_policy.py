from __future__ import annotations

"""Structured mail-row presentation redaction.

Gmail/Outlook accessibility labels often flatten a message row into comma-separated
text such as ``oläst, Julia, RESCUE Sverige``.  The general person-name policy is
intentionally conservative and cannot know that the second field is the sender.
This policy uses the *mail-row structure* as the signal instead of globally
redacting every capitalized word.

This module only wraps ``presentation.redact_for_display``.  Capture, storage,
analytics, timing and task inference remain unchanged.
"""

import re
from typing import Any

# Common read/selection/state fields emitted by Gmail/Outlook accessibility trees.
# They are deliberately UI-state words, not person-name words.  Multiple state
# fields may precede the sender, e.g. ``unread, starred, Julia, Subject``.
MAIL_ROW_STATE_WORDS = {
    # English
    "unread", "read", "starred", "not starred", "important", "not important",
    "selected", "not selected", "checked", "unchecked",
    # Swedish
    "oläst", "läst", "stjärnmärkt", "inte stjärnmärkt", "viktigt", "inte viktigt",
    "markerad", "inte markerad", "vald", "inte vald",
    # Danish / Norwegian
    "ulæst", "læst", "stjernemarkeret", "ikke stjernemarkeret",
    "ulest", "lest", "stjernemerket", "ikke stjernemerket",
    # German
    "ungelesen", "gelesen", "markiert", "nicht markiert", "wichtig", "nicht wichtig",
    # French
    "non lu", "lu", "suivi", "non suivi", "important", "non important",
    # Spanish
    "no leído", "leído", "destacado", "no destacado", "importante", "no importante",
    # Italian
    "da leggere", "letto", "speciale", "non speciale", "importante", "non importante",
    # Dutch
    "ongelezen", "gelezen", "met ster", "zonder ster", "belangrijk", "niet belangrijk",
    # Finnish
    "lukematon", "luettu", "tähdellä merkitty", "ei tähdellä merkitty", "tärkeä",
    # Portuguese
    "não lido", "lido", "com estrela", "sem estrela", "importante", "não importante",
    # Polish
    "nieprzeczytane", "przeczytane", "oznaczone gwiazdką", "bez gwiazdki", "ważne",
}

# A few high-frequency service/brand senders that are single title-cased words.
# Without this exception, a privacy-biased sender-slot rule could call them a
# human first name.  Unknown cases remain conservative outside structured rows.
KNOWN_SERVICE_SENDERS = {
    "adobe", "apple", "asana", "atlassian", "chatgpt", "dropbox", "facebook",
    "figma", "github", "google", "hubspot", "linkedin", "meta", "microsoft",
    "notion", "openai", "salesforce", "slack", "stripe", "supabase", "teams",
    "twitter", "vercel", "x", "zoom",
}

ORG_MARKERS = {
    "ab", "oy", "asa", "as", "aps", "a/s", "gmbh", "ag", "sa", "sarl", "srl",
    "ltd", "limited", "llc", "inc", "corp", "corporation", "company", "group",
    "foundation", "association", "university", "hospital", "clinic", "team",
    "support", "billing", "notifications", "service", "services", "sverige",
}

TITLEISH_FIELDS = {
    "label", "title", "window_title", "resource_title", "context_text", "subject",
    "suggested_label", "label_evidence", "name", "description",
}

_ACTION_PREFIX_RE = re.compile(
    r"^(?P<prefix>\s*(?:click|right\s+click|double\s+click|activate|select|deselect|"
    r"control\s+change|focus(?:\s+control)?|open)\s*:\s*)(?P<body>.*)$",
    re.IGNORECASE,
)
_ALREADY_SAFE_RE = re.compile(
    r"^(?:OWNER|PERSON|PERSON_[0-9A-F]{6}|EMAIL_[0-9A-F]{6}|PHONE_[0-9A-F]{6}|SENDER)$",
    re.IGNORECASE,
)


def _norm_state(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip(" \t\r\n.:;()[]{}\"'")).casefold()


def _split_segments(body: str) -> list[str]:
    # Keep comma delimiters as their own elements so replacement does not rewrite
    # the subject or change useful punctuation/spacing.
    return re.split(r"(,\s*)", body)


def _segment_count(parts: list[str]) -> int:
    return (len(parts) + 1) // 2


def _get_segment(parts: list[str], index: int) -> str:
    pos = index * 2
    return parts[pos] if 0 <= pos < len(parts) else ""


def _set_segment(parts: list[str], index: int, replacement: str) -> None:
    pos = index * 2
    if not 0 <= pos < len(parts):
        return
    original = parts[pos]
    leading = original[: len(original) - len(original.lstrip())]
    trailing = original[len(original.rstrip()):]
    parts[pos] = f"{leading}{replacement}{trailing}"


def _looks_organization_like(value: str, presentation: Any) -> bool:
    cleaned = presentation._clean_name_candidate(value)
    words = presentation._name_words(cleaned)
    if not words:
        return False
    if len(words) == 1 and words[0].casefold() in KNOWN_SERVICE_SENDERS:
        return True
    if any(w.casefold().strip(".,") in ORG_MARKERS for w in words):
        return True
    # Acronym-like multiword senders such as ``RESCUE Sverige`` should remain
    # available as organization/workflow context instead of becoming PERSON_x.
    if len(words) > 1 and any(len(w) > 1 and w.isupper() for w in words):
        return True
    return False


def _sender_replacement(value: str, presentation: Any) -> str | None:
    cleaned = presentation._clean_name_candidate(value)
    if not cleaned or _ALREADY_SAFE_RE.fullmatch(cleaned):
        return None

    owner_aliases, _owner_emails, _owner_phones = presentation._owner_identity()
    if cleaned.casefold() in owner_aliases:
        return "OWNER"

    if _looks_organization_like(cleaned, presentation):
        return None

    words = presentation._name_words(cleaned)
    if not presentation._looks_like_person_name(cleaned, allow_single=True):
        return None

    # A single display name in the structurally identified sender slot is known
    # to be a contact label, but not enough to resolve identity.  Generic PERSON
    # avoids both leakage and false PERSON_x links between two people named Julia.
    if len(words) == 1:
        return "PERSON"

    # For a full human-looking sender name, retaining a stable local pseudonym is
    # useful for recurrence/relationship analysis and matches the existing policy.
    return presentation._token("PERSON", cleaned)


def _redact_mail_row_text(text: str, presentation: Any) -> str:
    raw = str(text or "")
    match = _ACTION_PREFIX_RE.match(raw)
    prefix = match.group("prefix") if match else ""
    body = match.group("body") if match else raw

    parts = _split_segments(body)
    count = _segment_count(parts)
    if count < 3:
        return raw

    # One or more known accessibility-state fields must lead the row.  This is
    # what prevents arbitrary comma-separated prose from being interpreted as an
    # email sender/subject structure.
    sender_index = 0
    while sender_index < count - 1:
        state = _norm_state(_get_segment(parts, sender_index))
        if state not in MAIL_ROW_STATE_WORDS:
            break
        sender_index += 1

    if sender_index == 0 or sender_index >= count - 1:
        return raw

    sender = _get_segment(parts, sender_index)
    replacement = _sender_replacement(sender, presentation)
    if replacement is None:
        return raw

    _set_segment(parts, sender_index, replacement)
    return prefix + "".join(parts)


def wrap_redactor(previous: Any, presentation: Any):
    """Compose structured mail-row masking around an existing redactor."""

    def redact_for_display(value: Any) -> Any:
        safe = previous(value)

        def transform(
            item: Any,
            *,
            inherited_email_context: bool = False,
            field_name: str = "",
        ) -> Any:
            if isinstance(item, dict):
                email_context = inherited_email_context or presentation._contains_email_context(item)
                return {
                    key: transform(
                        child,
                        inherited_email_context=email_context,
                        field_name=str(key),
                    )
                    for key, child in item.items()
                }
            if isinstance(item, list):
                return [
                    transform(
                        child,
                        inherited_email_context=inherited_email_context,
                        field_name=field_name,
                    )
                    for child in item
                ]
            if isinstance(item, tuple):
                return tuple(
                    transform(
                        child,
                        inherited_email_context=inherited_email_context,
                        field_name=field_name,
                    )
                    for child in item
                )
            if (
                isinstance(item, str)
                and inherited_email_context
                and field_name.casefold() in TITLEISH_FIELDS
            ):
                return _redact_mail_row_text(item, presentation)
            return item

        return transform(safe)

    redact_for_display._openworkgraph_mail_row_policy = True  # type: ignore[attr-defined]
    return redact_for_display


def install(presentation: Any) -> None:
    """Backward-compatible installer; production uses privacy_pipeline explicitly."""
    previous = presentation.redact_for_display
    if getattr(previous, "_openworkgraph_mail_row_policy", False):
        return
    presentation.redact_for_display = wrap_redactor(previous, presentation)
