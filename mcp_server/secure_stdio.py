from __future__ import annotations

from . import secure_runtime as _secure_runtime
from .secure_runtime import mcp
from .agent_tools import register_agent_tools
from .automation_guidance import LEGACY_MCP_SERVER_INSTRUCTIONS, register_automation_guidance
from .history_guard import install_history_guard
from .workflow_evidence_tools import register_workflow_evidence_tools


install_history_guard(_secure_runtime)
register_agent_tools(mcp)
register_automation_guidance(mcp, instructions=LEGACY_MCP_SERVER_INSTRUCTIONS)
register_workflow_evidence_tools(mcp, _secure_runtime)


if __name__ == "__main__":
    mcp.run(transport="stdio")
