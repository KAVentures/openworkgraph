"""Shared high-precision person-introduction/signature redaction cues.

This helper is deliberately independent of storage, token-key generation and
transport. Local and hosted callers choose their own existing stable-token
function. One conservative cue grammar avoids drifting privacy behavior.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Collection

_SELF_NAME_CUE = re.compile(
    r"(?i:(?<!\\w)(?:mitt\\s+namn\\s+är|jag\\s+heter|namnet\\s+är|"
    r"my\\s+name\\s+is|vänliga\\s+hälsningar|med\\s+vänliga\\s+hälsningar|"
    r"hälsningar|best\\s+regards|kind\\s+regards|regards))"
    r"\\s*[, :\\-]?\\s*([A-ZÅÄÖ][a-zåäö]{2,40})\\b"
)
_SLASH_SIGNATURE = re.compile(
    r"(?<![\\w/])/[ \\t]+([A-ZÅÄÖ][a-zåäö]{2,40})(?=\\s*(?:$|[\\n.,;]))"
)
_GREETING_NAME_CUE = re.compile(
    r"(?i:(?<!\\w)(?:välkommen|hej|hejsan|hello|hi|dear|welcome))"
    r"[ \\t]*[,!]?\\s+([A-ZÅÄÖ][a-zåäö]{2,40})(?=[\\s!,.?:;]|$)"
)
_GENERIC_GREETING = frozenset(
    "alla all back everyone everybody team world there kollegor vänner tillbaka".split()
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
    return text


__all__ = ["redact_explicit_person_cues"]
