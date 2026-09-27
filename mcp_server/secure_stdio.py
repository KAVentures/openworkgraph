from __future__ import annotations

from . import secure_runtime as _secure_runtime
from .agent_tools import register_agent_tools
from .history_guard import install_history_guard
from .history_tools import register_history_tools

install_history_guard(_secure_runtime)
mcp = _secure_runtime.mcp
register_agent_tools(mcp)
register_history_tools(mcp, _secure_runtime)

if __name__ == "__main__":
    mcp.run(transport="stdio")
