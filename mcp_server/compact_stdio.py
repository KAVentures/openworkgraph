from __future__ import annotations

from . import compact as _compact
from . import secure_runtime as _secure_runtime
from .automation_guidance import register_automation_guidance
from .compact_hardening import apply_compact_hardening
from .history_guard import install_history_guard
from .history_tools import register_history_tools


install_history_guard(_secure_runtime)
apply_compact_hardening(_compact)
mcp = _compact.mcp
register_history_tools(mcp, _secure_runtime)
register_automation_guidance(mcp)


if __name__ == "__main__":
    mcp.run(transport="stdio")
