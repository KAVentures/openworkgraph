# Source-operation verification for known OWG cases

Additive, read-only receipt service:
`GET /v1/record-graph/operation-receipt?resource_ref=owg:r:...&expected_operation=...`
and local MCP `verify_case_operation`.

Allowed provider operations:
- `github_pr_merged`: fresh GitHub authenticated source says the PR merged;
- `jira_issue_resolved`: independently sourced Jira resolution receipt;
- `stripe_invoice_paid`: Stripe account-pinned processor reports paid;
- `stripe_refund_processor_succeeded`: Stripe reports processor succeeded.

Requires a previously observed, permitted record and complete/non-conflicting
record identity. On every request it uses the **existing separately opt-in**
source reader, rejects provider/type mismatch, stale receipts (default maximum
15 minutes), unavailable source state, source/operational conflict and incomplete
history. Responses never include original invoice IDs, PR numbers, customers,
amounts, titles, credentials or source-system write capabilities.

`operation_verified=true` means only that the **specified provider-reported
operational event** was corroborated by an authenticated source read. It does
not establish customer satisfaction, contractual compliance, business-objective
completion, the AI agent's causal contribution or permission to take actions.
All those claims remain false/unknown even when the provider operation is
verified.

The local MCP tool passes the normal per-AI-app access check and uses the
existing history guard on `/v1/record-graph/operation-receipt`, so a saved-
history OFF setting restricts it to the current recording boundary. The hosted
OWG connector, source inventories and autonomous agent integrations are
unchanged.

This completes a narrow **operational outcome receipt primitive**, not the
end-to-end business-outcome feedback and learning layer in issue #247.
A real business pilot must independently validate customer outcomes with the
authorized system of record and compare agent runs with/without OWG.

## Explicit experimental local MCP catalog opt-in

The stable default compact MCP catalog **does not change**. New Open Work
tools are registered only when the user's local MCP launcher process has
`OWG_OPEN_WORK_EXPERIMENTAL_TOOLS=1`. This is an experimental developer
switch, not a UI consent grant or authorization to access source inventories.

Even with the flag, every invocation still checks the user's regular
per-client AI access, the current history lease and the independent source
provider read setting. Turning the flag off restores the exact prior tool
catalog. Existing hosted/cloud MCP tools remain unchanged.
