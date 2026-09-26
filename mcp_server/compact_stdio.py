from __future__ import annotations

from . import compact as _compact
from .compact_hardening import apply_compact_hardening


apply_compact_hardening(_compact)
mcp = _compact.mcp


if __name__ == "__main__":
    mcp.run(transport="stdio")