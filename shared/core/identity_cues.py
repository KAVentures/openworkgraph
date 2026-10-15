"""Shared high-precision person-introduction/signature redaction cues.

This helper is deliberately independent of storage, token-key generation and
transport. Local and hosted callers choose their own existing stable-token
function. One conservative cue grammar avoids drifting privacy behavior.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Collection

_SELF_NAME_CUE = re.compile(
    r"(?i:(?<!\w)(?:mitt\s+namn\s+är|jag\s+heter|namnet\s+är|"
    r"my\s+name\s+is|vänliga\s+hälsningar|med\s+vänliga\s+hälsningar|"
    r"hälsningar|best\s+regards|kind\s+regards|regards))"
    r"\s*[, :\-]?\s*([A-ZÅÄÖ][a-zåäö]{2,40})\b"
)
_SLASH_SIGNATURE = re.compile(
    r"(?<![\w/])/[ \t]+([A-ZÅÄÖ][a-zåäö]{2,40})(?=\s*(?:$|[\n.,;]))"
)
_GREETING_NAME_CUE = re.compile(
    r"(?i:(?<!\w)(?:välkommen|hej|hejsan|hello|hi|dear|welcome))"
    r"[ \t]*[,!]?\s+([A-ZÅÄÖ][a-zåäö]{2,40})(?=[\s!,.?:;]|$)"
)
_GENERIC_GREETING = frozenset(
    "alla all back everyone everybody team world there kollegor vänner tillbaka".split()
)
# A high-precision social-activity cue: an unknown first name followed by an
# observed social verb should not be stored/shown in clear text. Do not infer
# arbitrary capitalized words as people without this grammatical context.
_SOCIAL_ACTIVITY = re.compile(
    r"(?<!\w)([A-ZÅÄÖ][a-zåäö]{2,40})"
    r"(?=\s+(?i:kommenterade|gillade|delade|reagerade|svarade|"
    r"skrev|commented|liked|shared|replied|har\s+lagt\s+till|"
    r"är\s+frånvarande|is\s+absent|is\s+away)\b)"
)
# Imperative requests name the addressee but are not a generic invitation
# to classify every capitalized UI noun as a person's name.
_DIRECT_ADDRESS = re.compile(
    r"(?i:(?<!\w)(?:hälsa|hälsa\s+på|greet|say\s+hello\s+to))"
    r"\s+([A-ZÅÄÖ][a-zåäö]{2,40})\b"
)


def redact_explicit_person_cues(
    value: str,
    *,
    person_token: Callable[[str], str],
    blocked_names: Collection[str] = (),
) -> str:
    """Tokenize a name after explicit introductions/closing signatures.

    Generic greetings or known non-person UI vocabulary are left intact.
    Other title text is preserved. Processing is bounded by the input length,
    and already-tokenized names cannot be matched as ordinary first names.
    """
    text = str(value or "")
    blocked = frozenset(str(word).casefold() for word in blocked_names)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.casefold() in blocked:
            return match.group(0)
        return match.group(0)[:-len(name)] + person_token(name)

    text = _SELF_NAME_CUE.sub(replace, text)
    text = _SLASH_SIGNATURE.sub(replace, text)
    text = _GREETING_NAME_CUE.sub(
        lambda match: replace(match)
        if match.group(1).casefold() not in _GENERIC_GREETING
        else match.group(0),
        text,
    )
    text = _SOCIAL_ACTIVITY.sub(replace, text)
    text = _DIRECT_ADDRESS.sub(replace, text)
    return text



# Deliberately field-scoped, never a general capitalized-words detector.
# The final (?![\w]) prevents matching just "EMAIL" in EMAIL_A1B2...,
# or a prefix of a previously tokenized PERSON/OWNER identifier.
_LABELED_PERSON = re.compile(
    r"(?i:(?<!\w)(?:customer|client|requester|recipient|contact|"
    r"kund|kundnamn|kontaktperson|mottagare)\s*(?:name|namn)?\s*[:=]\s*)"
    r"([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]{1,39}"
    r"(?:[ \t]+[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]{1,39})?)(?![\w])"
)
_FIELD_NON_NAMES = frozenset(
    "none unknown pending refund refunds status paid unpaid open closed "
    "new unassigned empty redacted withheld unavailable loading approved rejected "
    "sent error failed processing draft email person owner org phone".split()
)


def redact_labeled_person_fields(
    value: str,
    *,
    person_token: Callable[[str], str],
    blocked_names: Collection[str] = (),
) -> str:
    """Redact a one/two-word customer/contact name in an explicit identity field.

    Works for unlisted and lowercase names without classifying arbitrary
    multiword UI prose as people. Email, OWNER, PERSON and other pre-tokenized
    values are immutable; callers supply their own stable token function.
    """
    blocked = _FIELD_NON_NAMES | frozenset(str(v).casefold() for v in blocked_names)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        words = name.split()
        if any(word.casefold() in blocked for word in words):
            return match.group(0)
        if "_" in name or any(re.fullmatch(r"[A-Z][A-Z_]*_[0-9A-F]{4,}", w) for w in words):
            return match.group(0)
        return match.group(0)[:match.start(1)-match.start()] + person_token(name)

    return _LABELED_PERSON.sub(replace, str(value or ""))

__all__ = ["redact_explicit_person_cues", "redact_labeled_person_fields"]
