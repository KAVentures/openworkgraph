from __future__ import annotations

from .secure_runtime import mcp
from .agent_tools import register_agent_tools


register_agent_tools(mcp)


if __name__ == "__main__":
    mcp.run(transport="stdio")