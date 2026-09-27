from __future__ import annotations

"""Conservative edge-case extensions for AI-context person redaction.

The core detector stays the primary implementation. This module handles a few
work-title forms that become awkward after the normal presentation pass:
surname-first directory order where the surname was already tokenized,
slash-separated people, lower-case full names, possessives, and common surname
particles. It only replaces spans supported by the bundled first/surname lists.
"""

import re
from typing import Any, Callable

from . import presentation
from .contextual_redaction import AMBIGUOUS_NAMES, FIRST_NAMES, SURNAMES

_WORD = r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*"
_TOKEN = r"(?:OWNER|PERSON(?:_[0-9A-F]{4,})?|[A-Z][A-Z_]*_[0-9A-F]{4,})"
_PARTICLES = frozenset({"van", "von", "de", "da", "del", "della", "der", "den", "af", "bin", "al", "el", "la", "le", "di", "du"})
_POSSESSIVE_RE = re.compile(rf"\b({_WORD})\s+({_WORD})(['’]s)\b")
_PARTICLE_RE = re.compile(rf"\b({_WORD})\s+({_WORD})\s+({_WORD})\b")
_LOWER_PAIR_RE = re.compile(rf"\b({_WORD})\s+({_WORD})\b")
_TOKEN_COMMA_RE = re.compile(rf"\b({_TOKEN}),\s+({_WORD})\b")
_TOKEN_SLASH_AFTER_RE = re.compile(rf"\b({_TOKEN})\s*/\s*({_WORD})\b")
_TOKEN_SLASH_BEFORE_RE = re.compile(rf"\b({_WORD})\s*/\s*({_TOKEN})\b")
_SEGMENT_SPLIT_RE = re.compile(r"\s+[-–—|·]\s+|\s*[|·]\s*|:\s+")
_STRONG_CUE_RE = re.compile(
    r"(?:chat|meeting|meet|call|message|email|mail|reply|cc|bcc|attendee|assigned|assignee|owner|contact|"
    r"dear|hi|hello|hej|möte|samtal|ring|patient|candidate)\s+(?:with|from|to|med|från)?\s*$",
    re.I,
)

_FIRST = frozenset(name.casefold() for name in FIRST_NAMES)
_LAST = frozenset(name.casefold() for name in SURNAMES)
_AMBIG = frozenset(name.casefold() for name in AMBIGUOUS_NAMES)


def _base(value: str) -> str:
    return str(value or "").strip("'’- ").casefold()


def _token(name: str) -> str:
    normalized = re.sub(r"\s+", " ", str(name or "")).strip().casefold()
    return presentation._token("PERSON", normalized)


def _in_never(text: str, start: int, end: int, never: tuple[str, ...]) -> bool:
    candidate = text[start:end].casefold()
    return any(candidate == phrase.casefold() for phrase in never if phrase.strip())


def _pre_redact(text: str, never: tuple[str, ...]) -> str:
    out = text

    def possessive(match: re.Match[str]) -> str:
        first, last, suffix = match.group(1), match.group(2), match.group(3)
        if _base(first) in _FIRST and _base(last) in _LAST and not _in_never(match.string, match.start(), match.end(2), never):
            return _token(f"{first} {last}") + suffix
        return match.group(0)

    out = _POSSESSIVE_RE.sub(possessive, out)

    def particle(match: re.Match[str]) -> str:
        first, middle, last = match.group(1), match.group(2), match.group(3)
        if (
            _base(first) in _FIRST
            and _base(middle) in _PARTICLES
            and _base(last) in _LAST
            and not _in_never(match.string, match.start(), match.end(), never)
        ):
            return _token(f"{first} {middle} {last}")
        return match.group(0)

    out = _PARTICLE_RE.sub(particle, out)

    # Lower-case pairs are accepted only when the whole title segment is the
    # pair or it follows a strong person cue. This keeps common words/products
    # from becoming names merely because they occur in the lexicons.
    rebuilt = out
    offset = 0
    pieces = list(_SEGMENT_SPLIT_RE.finditer(out))
    segment_bounds = []
    left = 0
    for split in pieces:
        segment_bounds.append((left, split.start()))
        left = split.end()
    segment_bounds.append((left, len(out)))

    for match in list(_LOWER_PAIR_RE.finditer(out)):
        first, last = match.group(1), match.group(2)
        fk, lk = _base(first), _base(last)
        if first != first.lower() or last != last.lower():
            continue
        if fk not in _FIRST or lk not in _LAST or fk in _AMBIG or lk in _AMBIG:
            continue
        seg_exact = any(
            out[a:b].strip(" ,;()[]") == match.group(0)
            for a, b in segment_bounds
            if a <= match.start() and match.end() <= b
        )
        cue = bool(_STRONG_CUE_RE.search(out[:match.start()]))
        if not (seg_exact or cue) or _in_never(out, match.start(), match.end(), never):
            continue
        start, end = match.start() + offset, match.end() + offset
        replacement = _token(match.group(0))
        rebuilt = rebuilt[:start] + replacement + rebuilt[end:]
        offset += len(replacement) - (match.end() - match.start())
    return rebuilt


def _post_redact(text: str, never: tuple[str, ...]) -> str:
    def comma(match: re.Match[str]) -> str:
        token, given = match.group(1), match.group(2)
        key = _base(given)
        if key in _FIRST and key not in _AMBIG and not _in_never(match.string, match.start(2), match.end(2), never):
            return f"{token}, {_token(given)}"
        return match.group(0)

    out = _TOKEN_COMMA_RE.sub(comma, text)

    def slash_after(match: re.Match[str]) -> str:
        token, given = match.group(1), match.group(2)
        key = _base(given)
        if key in _FIRST and key not in _AMBIG and not _in_never(match.string, match.start(2), match.end(2), never):
            return f"{token} / {_token(given)}"
        return match.group(0)

    def slash_before(match: re.Match[str]) -> str:
        given, token = match.group(1), match.group(2)
        key = _base(given)
        if key in _FIRST and key not in _AMBIG and not _in_never(match.string, match.start(1), match.end(1), never):
            return f"{_token(given)} / {token}"
        return match.group(0)

    out = _TOKEN_SLASH_AFTER_RE.sub(slash_after, out)
    out = _TOKEN_SLASH_BEFORE_RE.sub(slash_before, out)
    return out


def augment(redact: Callable[[str], str], settings: dict[str, Any] | None = None) -> Callable[[str], str]:
    never = tuple(str(x) for x in (settings or {}).get("never_redact", []) if str(x).strip())

    def wrapped(text: str) -> str:
        value = _pre_redact(str(text or ""), never)
        value = redact(value)
        return _post_redact(value, never)

    return wrapped


def install(ai_context_module: Any) -> None:
    """Patch the AI-context redactor factory once without altering storage."""
    if getattr(ai_context_module, "_DOGFOOD_REDACTION_EXTENSIONS", False):
        return
    original = ai_context_module.contextual_text_redactor

    def contextual_text_redactor(settings: dict[str, Any] | None = None):
        effective = settings or ai_context_module.user_settings()
        return augment(original(effective), effective)

    ai_context_module.contextual_text_redactor = contextual_text_redactor
    ai_context_module._DOGFOOD_REDACTION_EXTENSIONS = True


__all__ = ["augment", "install"]
