from __future__ import annotations

"""Read authorization shared by privacy-safe agent report surfaces.

The local dashboard authenticates with a process-scoped ``OWG-Session`` capability,
while API/MCP callers use the broader API bearer. Agent ingest credentials are
write-only and intentionally match neither branch.
"""

from .local_auth import bearer_matches, dashboard_session_valid


def agent_read_authorized(header: str | None) -> bool:
    raw = str(header or "")
    if bearer_matches(raw):
        return True
    if not raw.lower().startswith("owg-session "):
        return False
    return dashboard_session_valid(raw[len("owg-session "):].strip())
