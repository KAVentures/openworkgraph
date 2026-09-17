from __future__ import annotations

"""Compatibility shim for v0.40 storage hardening.

The policy now lives directly in server.db so importing modules cannot change
persistence behavior.  This module remains only for older imports.
"""

from typing import Any


def install(db: Any) -> None:
    # Intentionally no-op: db.insert_events/init_db enforce the policy directly.
    return None
