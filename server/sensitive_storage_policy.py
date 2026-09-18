from __future__ import annotations

"""Compatibility shim for storage hardening.

The policy now lives directly in server.db so importing modules cannot change
persistence behavior. This module remains only for older imports/tests.
"""

from typing import Any

MIGRATION_KEY = "sensitive_identifiers_v45"


def install(db: Any) -> None:
    # Intentionally no-op: db.insert_events/init_db enforce the policy directly.
    return None
