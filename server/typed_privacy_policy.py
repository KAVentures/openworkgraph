from __future__ import annotations

"""Evidence-based presentation privacy policy (v0.40).

The older presentation layer used application-name context plus title casing as a
proxy for personhood. That was too broad for enterprise/EHR data. This policy
keeps deterministic structured mail behavior, removes generic title-case guessing,
handles typed identifiers, and treats URL fields as structured data rather than
prose.
"""

import re
from typing import Any

from browser_privacy import sanitize_pathname, sanitize_url_value
from sensitive_identifiers import redact_sensitive_identifiers

STRUCTURED_URL_FIELDS = {
    "url", "href", "uri", "frame_url", "resource_locator", "origin", "pathname",
}
HOST_FIELDS = {"hostname", "host"}
PATIENT_NAME_FIELDS = {
    "patient", "patient_name", "patientname", "patientnamn", "patient_display_name",
}
SINGLE_PERSON_ROLE_FIELDS = {
    "sender", "recipient", "contact", "participant", "attendee", "assignee", "assigned_to",
}

_WORD = r"[A-ZÅÄÖÉÜ][A-Za-zÅÄÖåäöÉéÜüÀ-ÖØ-öø-ÿ'’.-]*"
_PATIENT_LABEL_RE = re.compile(
    rf"(?P<prefix>\b(?i:patient|patientnamn|patient name|patientens namn)\s*[:\-]\s*)"
    rf"(?P<name>{_WORD}(?:\s+{_WORD}){{0,3}})"
)
_NAME_BEFORE_TOKEN_RE = re.compile(
    rf"(?P<name>{_WORD}(?:\s+{_WORD}){{1,3}})\s*,?\s*"
    r"(?:(?i:personnummer|personnr|person\s*nr|pnr|samordningsnummer|samordningsnr|ssn)\s*[:=#-]?\s*)"
    r"(?P<token>PERSONNUMMER_[0-9A-F]{6})"
)
_NAME_DIRECT_TOKEN_RE = re.compile(
    rf"(?P<name>{_WORD}(?:\s+{_WORD}){{1,3}})\s+(?P<token>PERSONNUMMER_[0-9A-F]{{6}})"
)
_PATIENT_CUE_RE = re.compile(
    r"\b(?:patient|patientnamn|patient\s+name|patientens\s+namn)\s*[:\-]?\s+",
    re.IGNORECASE,
)


def _sanitize_locator(value: str, field_name: str) -> str:
    low = field_name.casefold()
    if low in HOST_FIELDS:
        return value
    if low == "pathname":
        return sanitize_pathname(value)
    return sanitize_url_value(value)


def _redact_evidence_bound_names(text: str, presentation: Any) -> str:
    out = redact_sensitive_identifiers(text, redact_adjacent_name=True)

    def patient_repl(match: re.Match[str]) -> str:
        name = match.group("name")
        owner_aliases, _emails, _phones = presentation._owner_identity()
        replacement = (
            "OWNER"
            if name.casefold() in owner_aliases
            else presentation._token("PERSON", name)
        )
        return match.group("prefix") + replacement

    out = _PATIENT_LABEL_RE.sub(patient_repl, out)

    def token_name_repl(match: re.Match[str]) -> str:
        name = match.group("name")
        token = match.group("token")
        owner_aliases, _emails, _phones = presentation._owner_identity()
        replacement = (
            "OWNER"
            if name.casefold() in owner_aliases
            else presentation._token("PERSON", token)
        )
        whole = match.group(0)
        rel_start = match.start("name") - match.start(0)
        rel_end = match.end("name") - match.start(0)
        return whole[:rel_start] + replacement + whole[rel_end:]

    out = _NAME_BEFORE_TOKEN_RE.sub(token_name_repl, out)
    out = _NAME_DIRECT_TOKEN_RE.sub(token_name_repl, out)
    return out


def install(presentation: Any) -> None:
    """Install after first-name and mail-row policies."""
    previous = presentation.redact_for_display
    if getattr(previous, "_openworkgraph_typed_privacy_policy", False):
        return

    # App/vendor names and capitalization are no longer evidence of personhood.
    # Structured mail-row/select parsing remains active because that is a real UI
    # role signal rather than a generic title-case guess.
    presentation._contains_name_sensitive_context = lambda _value: False
    presentation._embedded_name_spans = lambda _text: []
    presentation._redact_email_title_segments = lambda text, *, owner_aliases: text
    presentation.CUE_RE = _PATIENT_CUE_RE

    # Generic multi-word sender/contact/display-name fields can denote
    # organizations. Explicit person/patient fields remain inherently person-valued.
    from . import first_name_policy, mail_row_policy
    first_name_policy.PERSON_FIELDS = {
        "person", "patient", "patient_name", "patientname", "patientnamn",
        "patient_display_name",
    }

    def preprocess(item: Any, *, field_name: str = "") -> Any:
        if isinstance(item, dict):
            return {key: preprocess(value, field_name=str(key)) for key, value in item.items()}
        if isinstance(item, list):
            return [preprocess(value, field_name=field_name) for value in item]
        if isinstance(item, tuple):
            return tuple(preprocess(value, field_name=field_name) for value in item)
        if not isinstance(item, str):
            return item

        low = field_name.casefold()
        if low in STRUCTURED_URL_FIELDS or low in HOST_FIELDS:
            return _sanitize_locator(item, low)

        out = _redact_evidence_bound_names(item, presentation)
        if low in PATIENT_NAME_FIELDS and presentation._looks_like_person_name(out, allow_single=True):
            owner_aliases, _emails, _phones = presentation._owner_identity()
            if out.casefold() in owner_aliases:
                return "OWNER"
            return presentation._token("PERSON", out)

        # A one-word value in a strongly person-oriented role is enough to hide
        # the literal name, but not enough to claim a stable identity. Multi-word
        # values remain available unless stronger evidence identifies them.
        if low in SINGLE_PERSON_ROLE_FIELDS:
            cleaned = presentation._clean_name_candidate(out)
            words = presentation._name_words(cleaned)
            if (
                len(words) == 1
                and presentation._looks_like_person_name(cleaned, allow_single=True)
                and not mail_row_policy._looks_organization_like(cleaned, presentation)
            ):
                owner_aliases, _emails, _phones = presentation._owner_identity()
                return "OWNER" if cleaned.casefold() in owner_aliases else "PERSON"
        return out

    def redact_for_display(value: Any) -> Any:
        # Structured URL/identifier handling happens before the older redactor so
        # prose rules never get a chance to produce half-masked URL values.
        return previous(preprocess(value))

    redact_for_display._openworkgraph_typed_privacy_policy = True  # type: ignore[attr-defined]
    presentation.redact_for_display = redact_for_display
