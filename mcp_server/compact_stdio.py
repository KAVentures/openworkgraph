from __future__ import annotations

import os

from . import compact as _compact
from . import secure_runtime as _secure_runtime
from .automation_guidance import register_automation_guidance
from .compact_hardening import apply_compact_hardening
from .context_index import register_context_index
from .history_guard import install_history_guard
from .history_tools import register_history_tools
from .workflow_evidence_tools import register_workflow_evidence_tools
from .record_graph_tools import register_record_graph_tools
from .case_context_tools import register_case_context_tools
from .outcome_receipt_tools import register_outcome_receipt_tools
from server.open_work_mcp_settings import experimental_tools_enabled


install_history_guard(_secure_runtime)
apply_compact_hardening(_compact)
mcp = _compact.mcp
register_history_tools(mcp, _secure_runtime)
register_context_index(mcp)
register_automation_guidance(mcp)
register_workflow_evidence_tools(mcp, _secure_runtime)
register_record_graph_tools(mcp, _secure_runtime)
if experimental_tools_enabled():
    register_case_context_tools(mcp, _secure_runtime)
    register_outcome_receipt_tools(mcp, _secure_runtime)


if __name__ == "__main__":
    mcp.run(transport="stdio")
