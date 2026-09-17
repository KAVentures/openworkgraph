from __future__ import annotations

"""Explicit presentation privacy pipeline.

Production code imports this module directly.  The individual policy modules remain
small, testable transforms; they no longer depend on import order or mutate each
other's module globals.
"""

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


_VIEW = _PolicyView()

# Read-only display pipeline.  It can learn aliases within a single payload, but
# persistent registry writes are reserved for ingest via learn_persistent_identities.
_redactor = first_name_policy.build_redactor(_VIEW, persist_registry=False)
_redactor = mail_row_policy.wrap_redactor(_redactor, _VIEW)
_redactor = typed_privacy_policy.wrap_redactor(_redactor, _VIEW)
_redactor = mail_subject_policy.wrap_redactor(_redactor, _VIEW)

# Same first-stage logic with persistence enabled.  Running it on ingest learns
# only hashed aliases/tokens; no literal person name is written to the registry.
_identity_learner = first_name_policy.build_redactor(_VIEW, persist_registry=True)


def initialize_privacy_state() -> None:
    """Create/load installation-local privacy state during startup, never on GET."""
    _presentation._local_key()


def redact_for_display(value: Any) -> Any:
    return _redactor(value)


def learn_persistent_identities(value: Any) -> None:
    """Persist high-confidence identity aliases from newly ingested evidence."""
    _identity_learner(value)
