from __future__ import annotations

"""Deciding when a same-app window title is a different document.

Titles change for many reasons that are not a new document: unread counts,
unsaved markers, "Edited" suffixes, progress percentages, "Not Responding".
``document_key`` removes that noise so only material changes count, and the
collector additionally requires the new title to persist for a few polls.
"""

import re

_BADGE = r"(?:[\(\[]\s*\d{1,5}\+?\s*(?:unread|new|olästa|nya)?\s*[\)\]])"
_LEADING = re.compile(rf"^\s*(?:{_BADGE}|[•●◉∙*✱]+)\s*", re.I)
_INNER_BADGE = re.compile(rf"\s*{_BADGE}", re.I)
_TRAILING = re.compile(
    r"\s*(?:"
    r"[—–-]\s*(?:edited|modified|saved|saving…?|autosaved|not responding|redigerad|sparad|svarar inte)"
    r"|\(\s*(?:not responding|read-only|edited|svarar inte|skrivskyddad)\s*\)"
    r"|[•●*]"
    r"|\d{1,3}\s?%"
    r")\s*$",
    re.I,
)


def document_key(title: str) -> str:
    """A normalized title for comparing documents within one application."""
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    previous = None
    while previous != text:
        previous = text
        text = _LEADING.sub("", text)
        text = _TRAILING.sub("", text)
        text = _INNER_BADGE.sub("", text).strip()
    return text.casefold()


def is_material_change(current_key: str, candidate_key: str) -> bool:
    """A switch to an empty title (a dialog, an untitled moment) is not a document change."""
    return bool(candidate_key) and candidate_key != current_key
