from __future__ import annotations

"""Structured email-row subject/header privacy policy (v0.41).

v0.40 intentionally stopped treating arbitrary title-cased text as a person. That
fixed organization/product false positives, but it also removed too much privacy
inside Gmail/Outlook accessibility rows: sender names in row variants without a
leading unread-state and people mentioned inside the subject could remain visible.

This policy restores the stronger behavior *only after a row is structurally
identified as mail*. Ordinary window/document titles remain conservative.
"""

import re
from typing import Any

from . import mail_row_policy

TITLEISH_FIELDS = mail_row_policy.TITLEISH_FIELDS

_WORD = r"[A-ZÅÄÖÉÜ][A-Za-zÅÄÖåäöÉéÜüÀ-ÖØ-öø-ÿ'’.-]*"
_TIME_RE = re.compile(r"^\s*\d{1,2}:\d{2}(?:\s*[AP]M)?\s*$", re.IGNORECASE)
_ROW_TRAILER_RE = re.compile(r"(?:AXCheckBox|AXRow|AXCell|…|\.\.\.)", re.IGNORECASE)

# Subject wording that strongly indicates the following token(s) refer to a
# person. Keep the case-insensitive flag scoped to the cue itself; candidate
# words must still be title-cased.
_PERSON_CUE_RE = re.compile(
    rf"(?P<prefix>\b(?i:add|adding|invite|inviting|invited|introduced?|introducing|"
    rf"meet(?:ing)?\s+with|call(?:ing)?\s+with|message\s+from|reply\s+to|"
    rf"connect(?:ed|ing)?\s+with|connection\s+with|from|to|with|welcome)\s+)"
    rf"(?P<name>{_WORD}(?:\s+{_WORD}){{0,3}})"
)

# Notifications frequently begin with a person name followed by a human action,
# e.g. "Julia sent you a message" or "Scott Walchek shared a document".
_LEADING_PERSON_ACTION_RE = re.compile(
    rf"^(?P<lead>\s*)(?P<name>{_WORD}(?:\s+{_WORD}){{0,3}})"
    rf"(?=\s+(?i:accepted|added|applied|commented|connected|endorsed|followed|"
    rf"invited|joined|liked|mentioned|messaged|posted|reacted|replied|requested|"
    rf"sent|shared|viewed|wants)\b)"
)

# Subject headings such as "Anna Svensson - Consultant" are also strongly
# person-shaped in a confirmed mail row. Organization/product markers below
# prevent common enterprise names from being swallowed by this rule.
_LEADING_NAME_DELIM_RE = re.compile(
    rf"^(?P<lead>\s*)(?P<name>{_WORD}(?:\s+{_WORD}){{0,3}})(?=\s*(?:[-–—|:]|\())"
)

_ORG_SUBJECT_WORDS = {
    "ab", "ag", "association", "bank", "cars", "checkout", "clinic", "company",
    "corp", "corporation", "foundation", "gmbh", "group", "healthcare", "hospital",
    "kommun", "kommunen", "ltd", "municipality", "oy", "region", "service",
    "services", "sjukhus", "support", "sverige", "team", "university",
}


def _looks_org_or_product(value: str, presentation: Any) -> bool:
    if mail_row_policy._looks_organization_like(value, presentation):
        return True
    cleaned = presentation._clean_name_candidate(value)
    words = presentation._name_words(cleaned)
    return any(word.casefold().strip(".,") in _ORG_SUBJECT_WORDS for word in words)


def _person_replacement(value: str, presentation: Any) -> str | None:
    cleaned = presentation._clean_name_candidate(value)
    if not cleaned or mail_row_policy._ALREADY_SAFE_RE.fullmatch(cleaned):
        return None

    owner_aliases, _owner_emails, _owner_phones = presentation._owner_identity()
    if cleaned.casefold() in owner_aliases:
        return "OWNER"
    if _looks_org_or_product(cleaned, presentation):
        return None
    if not presentation._looks_like_person_name(cleaned, allow_single=True):
        return None

    words = presentation._name_words(cleaned)
    if len(words) == 1:
        return "PERSON"
    return presentation._token("PERSON", cleaned)


def _replace_match_name(match: re.Match[str], presentation: Any) -> str:
    name = match.group("name")
    replacement = _person_replacement(name, presentation)
    if replacement is None:
        return match.group(0)
    whole = match.group(0)
    rel_start = match.start("name") - match.start(0)
    rel_end = match.end("name") - match.start(0)
    return whole[:rel_start] + replacement + whole[rel_end:]


def _redact_subject_segment(text: str, presentation: Any) -> str:
    out = str(text or "")
    if not out.strip() or _TIME_RE.fullmatch(out.strip()):
        return out

    # OWNER and identities learned from stronger evidence have already been
    # handled by earlier presentation layers. These rules cover the remaining
    # names that can be inferred from the mail-row grammar itself. Accessibility
    # trailers may share this same string, so they must not suppress redaction.
    out = _PERSON_CUE_RE.sub(lambda m: _replace_match_name(m, presentation), out)
    out = _LEADING_PERSON_ACTION_RE.sub(lambda m: _replace_match_name(m, presentation), out)
    out = _LEADING_NAME_DELIM_RE.sub(lambda m: _replace_match_name(m, presentation), out)
    return out


def _state_sender_index(parts: list[str]) -> int | None:
    count = mail_row_policy._segment_count(parts)
    sender_index = 0
    while sender_index < count - 1:
        state = mail_row_policy._norm_state(mail_row_policy._get_segment(parts, sender_index))
        if state not in mail_row_policy.MAIL_ROW_STATE_WORDS:
            break
        sender_index += 1
    if sender_index == 0 or sender_index >= count - 1:
        return None
    return sender_index


def _unstated_sender_index(parts: list[str], *, has_action_prefix: bool) -> int | None:
    """Recognize mail rows where Gmail/Chrome omitted unread/selected states.

    We require an action prefix plus both a timestamp and an accessibility-row
    trailer. This avoids treating arbitrary comma-separated prose as mail.
    """
    if not has_action_prefix:
        return None
    count = mail_row_policy._segment_count(parts)
    if count < 3:
        return None
    segments = [mail_row_policy._get_segment(parts, i).strip() for i in range(count)]
    has_time = any(_TIME_RE.fullmatch(segment) for segment in segments[1:])
    has_trailer = any(_ROW_TRAILER_RE.search(segment) for segment in segments[1:])
    return 0 if has_time and has_trailer else None


def _redact_confirmed_mail_row(text: str, presentation: Any) -> str:
    raw = str(text or "")
    action = mail_row_policy._ACTION_PREFIX_RE.match(raw)
    prefix = action.group("prefix") if action else ""
    body = action.group("body") if action else raw
    parts = mail_row_policy._split_segments(body)
    count = mail_row_policy._segment_count(parts)
    if count < 3:
        return raw

    sender_index = _state_sender_index(parts)
    if sender_index is None:
        sender_index = _unstated_sender_index(parts, has_action_prefix=bool(action))
    if sender_index is None:
        return raw

    sender = mail_row_policy._get_segment(parts, sender_index)
    if not _looks_org_or_product(sender, presentation):
        replacement = mail_row_policy._sender_replacement(sender, presentation)
        if replacement is not None:
            mail_row_policy._set_segment(parts, sender_index, replacement)

    # A subject may itself contain commas, so everything after the sender is
    # treated as subject/header material. Timestamps naturally no-op above and
    # accessibility trailer strings are preserved while nearby names are masked.
    for index in range(sender_index + 1, count):
        original = mail_row_policy._get_segment(parts, index)
        redacted = _redact_subject_segment(original, presentation)
        if redacted != original:
            mail_row_policy._set_segment(parts, index, redacted.strip())

    return prefix + "".join(parts)


def install(presentation: Any) -> None:
    """Install after typed_privacy_policy as the final mail-specific pass."""
    previous = presentation.redact_for_display
    if getattr(previous, "_openworkgraph_mail_subject_policy", False):
        return

    def redact_for_display(value: Any) -> Any:
        safe = previous(value)

        def transform(item: Any, *, email_context: bool = False, field_name: str = "") -> Any:
            if isinstance(item, dict):
                scoped = email_context or presentation._contains_email_context(item)
                return {
                    key: transform(child, email_context=scoped, field_name=str(key))
                    for key, child in item.items()
                }
            if isinstance(item, list):
                return [transform(child, email_context=email_context, field_name=field_name) for child in item]
            if isinstance(item, tuple):
                return tuple(transform(child, email_context=email_context, field_name=field_name) for child in item)
            if (
                isinstance(item, str)
                and email_context
                and field_name.casefold() in TITLEISH_FIELDS
            ):
                return _redact_confirmed_mail_row(item, presentation)
            return item

        return transform(safe)

    redact_for_display._openworkgraph_mail_subject_policy = True  # type: ignore[attr-defined]
    presentation.redact_for_display = redact_for_display
