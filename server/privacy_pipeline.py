from __future__ import annotations

"""Explicit presentation privacy pipeline.

Production code imports this module directly. The individual policy modules remain
small, testable transforms; they no longer depend on import order or mutate each
other's module globals.
"""

import os
import re
from typing import Any

from . import presentation as _presentation
from . import first_name_policy, mail_row_policy, typed_privacy_policy, mail_subject_policy


class _PolicyView:
    """Read-through view with the v0.40 conservative person-context overrides."""

    CUE_RE = typed_privacy_policy._PATIENT_CUE_RE

    def __getattr__(self, name: str) -> Any:
        return getattr(_presentation, name)

    def _contains_name_sensitive_context(self, _value: Any) -> bool:
        return False

    def _embedded_name_spans(self, _text: str) -> list[tuple[int, int, str]]:
        return []

    def _redact_email_title_segments(self, text: str, *, owner_aliases: dict[str, str]) -> str:
        return text


# Identity learning must be stricter than display-time recognition. Bare "to",
# "with", "message" and "call" are common workflow language and can permanently
# teach status/team labels as people. These cues remain available only in forms
# that strongly imply a person.
_STRONG_IDENTITY_CUE_RE = re.compile(
    r"\b(?:"
    r"reply\s+to|email\s+to|message\s+to|message\s+from|email\s+from|"
    r"from|cc|bcc|sender|recipient|"
    r"meeting\s+with|call\s+with|assigned\s+to|"
    r"owner|contact|participant|attendee|assignee"
    r")\s*[:\-]?\s+",
    re.IGNORECASE,
)

_LEARNING_NON_NAME_WORDS = set(_presentation.NON_NAME_WORDS) | {
    "team", "group", "department", "progress", "done", "backlog", "board",
    "queue", "sprint", "legal", "finance", "support", "review", "todo", "do",
}


class _IdentityLearningView(_PolicyView):
    # Ingest-time learning uses only strong person cues plus the email/name
    # evidence handled by first_name_policy itself.
    CUE_RE = _STRONG_IDENTITY_CUE_RE
    NON_NAME_WORDS = _LEARNING_NON_NAME_WORDS

    def _looks_like_person_name(self, value: str, *, allow_single: bool = False) -> bool:
        """Apply learning-only vocabulary before the base title-case heuristic.

        The base helper closes over presentation.NON_NAME_WORDS, so merely exposing
        a stricter NON_NAME_WORDS attribute on this view is not enough. Check each
        candidate word here before delegating to the existing heuristic.
        """
        words = _presentation._name_words(value)
        for word in words:
            bare = word.strip(".'’-_").casefold()
            if bare in self.NON_NAME_WORDS:
                return False
        return _presentation._looks_like_person_name(value, allow_single=allow_single)


_VIEW = _PolicyView()
_LEARNING_VIEW = _IdentityLearningView()

# Read-only display pipeline. It can use aliases already learned on write paths,
# but never persists new registry state while serving a GET.
_redactor = first_name_policy.build_redactor(_VIEW, persist_registry=False)
_redactor = mail_row_policy.wrap_redactor(_redactor, _VIEW)
_redactor = typed_privacy_policy.wrap_redactor(_redactor, _VIEW)
_redactor = mail_subject_policy.wrap_redactor(_redactor, _VIEW)

# Ingest-time learner persists only hashed aliases/tokens from strong evidence.
_identity_learner = first_name_policy.build_redactor(_LEARNING_VIEW, persist_registry=True)


def _chmod_private(path) -> None:
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except Exception:
        # Windows ACLs/packaged environments may not expose POSIX chmod semantics.
        # Privacy initialization must never prevent OpenWorkGraph from starting.
        pass


def initialize_privacy_state() -> None:
    """Create/load installation-local privacy state during startup, never on GET."""
    _presentation._local_key()
    data_dir = _presentation._data_dir()
    _chmod_private(data_dir / ".display_redaction_key")
    _chmod_private(data_dir / ".presentation_people.json")


def reset_persistent_identities() -> bool:
    """Delete only learned person aliases; captured workflow evidence is untouched."""
    path = _presentation._people_registry_path()
    try:
        lock = getattr(_presentation, "_REGISTRY_LOCK", None)
        if lock is None:
            existed = path.exists()
            path.unlink(missing_ok=True)
            return existed
        with lock:
            existed = path.exists()
            path.unlink(missing_ok=True)
            return existed
    except Exception:
        return False


def redact_for_display(value: Any) -> Any:
    return _redactor(value)


def learn_persistent_identities(value: Any) -> None:
    """Persist high-confidence identity aliases from newly ingested evidence."""
    _identity_learner(value)
