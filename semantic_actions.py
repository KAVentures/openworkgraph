from __future__ import annotations

"""Small bilingual, privacy-safe semantic action vocabulary.

Returned labels are fixed canonical strings. Arbitrary modifiers, names and
resource text are never copied into the output.
"""

import re
from typing import Any


def _clean(value: Any, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _target_text(target: dict[str, Any] | None) -> str:
    target = target or {}
    fields = [target.get(key) for key in ("label", "title", "description", "help", "name", "identifier")]
    return " | ".join(_clean(value) for value in fields if _clean(value))


_OBJECTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b(?:e-?mail|email|mail|mejl)\b", re.I)),
    ("message", re.compile(r"\b(?:message|meddelande)\b", re.I)),
    ("account", re.compile(r"\b(?:account|konto)\b", re.I)),
    ("ticket", re.compile(r"\b(?:ticket|ärende)\b", re.I)),
    ("invoice", re.compile(r"\b(?:invoice|faktura)\b", re.I)),
    ("conversation", re.compile(r"\b(?:conversation|konversation|chatt)\b", re.I)),
    ("status", re.compile(r"\bstatus\b", re.I)),
    ("attachment", re.compile(r"\b(?:attachment|bilaga)\b", re.I)),
    ("repository", re.compile(r"\b(?:repository|repo)\b", re.I)),
    ("issue", re.compile(r"\b(?:issue|problemrapport)\b", re.I)),
    ("case", re.compile(r"\b(?:case|ärende)\b", re.I)),
)

_VERBS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("open", re.compile(r"^\s*(?:open|öppna)\b", re.I)),
    ("create", re.compile(r"^\s*(?:create|new|skapa|nytt|ny|nya)\b", re.I)),
    ("update", re.compile(r"^\s*(?:update|uppdatera)\b", re.I)),
    ("archive", re.compile(r"^\s*(?:archive|arkivera)\b", re.I)),
    ("delete", re.compile(r"^\s*(?:delete|remove|radera|ta\s+bort)\b", re.I)),
    ("send", re.compile(r"^\s*(?:send|skicka)\b", re.I)),
    ("save", re.compile(r"^\s*(?:save|spara)\b", re.I)),
    ("reply", re.compile(r"^\s*(?:reply|svara)\b", re.I)),
    ("forward", re.compile(r"^\s*(?:forward|vidarebefordra)\b", re.I)),
    ("approve", re.compile(r"^\s*(?:approve|godkänn(?:a)?)\b", re.I)),
    ("reject", re.compile(r"^\s*(?:reject|decline|avvisa|neka)\b", re.I)),
    ("complete", re.compile(r"^\s*(?:complete|finish|slutför(?:a)?|avsluta)\b", re.I)),
    ("upload", re.compile(r"^\s*(?:upload|ladda\s+upp)\b", re.I)),
    ("download", re.compile(r"^\s*(?:download|ladda\s+ner)\b", re.I)),
    ("confirm", re.compile(r"^\s*(?:confirm|bekräfta)\b", re.I)),
    ("search", re.compile(r"^\s*(?:search|sök)\b", re.I)),
)


def _object(text: str) -> str:
    for name, pattern in _OBJECTS:
        if pattern.search(text):
            return name
    return ""


def safe_semantic_action_label(target: dict[str, Any] | None) -> str:
    """Return one fixed semantic label or ``""`` when no safe mapping exists."""
    text = _target_text(target)
    if not text:
        return ""

    verb = ""
    for canonical, pattern in _VERBS:
        if pattern.search(text):
            verb = canonical
            break
    if not verb:
        return ""

    obj = _object(text)
    if verb == "open" and obj in {"email", "message", "account", "ticket", "invoice", "conversation"}:
        return f"Open {obj}"
    if verb == "create" and obj in {"email", "message", "account", "ticket", "invoice", "conversation", "repository", "issue", "case"}:
        return f"Create {obj}"
    if verb == "update" and obj == "status":
        return "Update status"
    if verb == "archive":
        return "Archive"
    if verb == "delete":
        return "Delete"
    if verb == "send":
        return "Send"
    if verb == "save":
        return "Save"
    if verb == "reply":
        return "Reply"
    if verb == "forward":
        return "Forward"
    if verb == "approve":
        return "Approve invoice" if obj == "invoice" else "Approve"
    if verb == "reject":
        return "Reject"
    if verb == "complete":
        return "Complete case" if obj == "case" else "Complete"
    if verb == "upload":
        return "Upload attachment" if obj == "attachment" else "Upload"
    if verb == "download":
        return "Download"
    if verb == "confirm":
        return "Confirm"
    if verb == "search":
        return "Search"
    return ""


__all__ = ["safe_semantic_action_label"]
