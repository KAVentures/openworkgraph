from __future__ import annotations

"""Read-only MCP receipt for a known, previously observed source record."""
import re
from typing import Any

from mcp.types import ToolAnnotations
from mcp.server.mcpserver.exceptions import ToolError
from server.open_work_mcp_settings import experimental_tools_enabled

_REF = re.compile(r"^owg:r:[0-9a-f]{32}$")
_OPS = {
    "github_pr_merged", "jira_issue_resolved",
    "stripe_invoice_paid", "stripe_refund_processor_succeeded",
}
_REGISTERED: set[int] = set()


def register_outcome_receipt_tools(mcp: Any, runtime_module: Any) -> None:
    sid = id(mcp)
    if sid in _REGISTERED:
        return
    _REGISTERED.add(sid)

    @mcp.tool(annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True,
        openWorldHint=True,
    ))
    def verify_case_operation(
        resource_ref: str,
        expected_operation: str,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        """Independently check a known business record's provider operation state.

        Use after an existing authorized agent attempts a business operation.
        Requires an exact OWG-observed owg:r: record and an allowed operation
        kind. It fetches source-reported GitHub/Jira/Stripe state only when the
        user has separately enabled the provider source-read permission, with
        normal AI/history scopes. No write or broad source inventory occurs.

        A GitHub merge, resolved Jira issue or Stripe processor-paid status
        does NOT prove a customer outcome or establish that the agent caused
        it. Unverified, stale, missing and conflicting provider states are
        explicitly unknown, never success.
        """
        if not _REF.fullmatch(str(resource_ref or "")):
            raise ToolError("Use an exact OWG-observed owg:r: record reference.")
        if expected_operation not in _OPS:
            raise ToolError("Unsupported source operation.")
        # Settings OFF revokes newly exposed calls immediately, even if the
        # client has not yet refreshed its cached MCP tool catalog.
        if not experimental_tools_enabled():
            raise ToolError("Open Work experimental tools are switched off.")
        runtime_module.authorize_tool("verify_case_operation")
        params: dict[str, Any] = {
            "resource_ref": resource_ref,
            "expected_operation": expected_operation,
        }
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        result = runtime_module.secure_get("/v1/record-graph/operation-receipt", params)
        result["interpretation"] = (
            "Source-reported operational state only. Do not claim customer success, "
            "an agent-caused result, safety, policy compliance, or permission to execute."
        )
        return runtime_module.core._finish("verify_case_operation", result)


__all__ = ["register_outcome_receipt_tools"]
