# Jira source-audit process preview (opt-in)

OWG now supports an **explicit read of a single previously observed Jira
issue's source changelog**, using the EXISTING Jira OAuth source-read adapter.
This is not a new broad Jira integration or tenant-wide scan.

## User flow

1. The browser sensor must already have stored an opted-in, normalized opaque
   resource reference to the Jira issue.
2. Jira source audit must separately be enabled for the correct verified
   tenant, with existing Jira OAuth scopes/credentials.
3. On **Overview → Record-linked observations**, expand a Jira record and
   click **Read opted-in Jira audit**. The UI asks for explicit confirmation.
4. OWG reads *only that exact issue* through the existing source-evidence
   adapter and shows a bounded observed sequence of status/assignee changes.
5. The view explains whether the source audit page was complete or truncated.

The endpoint `GET /v1/process-mining/jira-case` requires a currently valid
**local dashboard session** AND `confirm_source_read=true` AND an opaque
`owg:r:...` reference. API/MCP bearer tokens alone cannot initiate this read.
There are no automatic refreshes or source writes.

## Boundaries

- The existing Jira adapter authorizes tenant, host, scopes, resource identity
  and source-read opt-in before requesting changelog; this feature cannot
  bypass those checks.
- This feature never sends data to a hosted LLM or stores a second copy of
  the provider's response. No new credentials or background-sync permissions.
- The pure converter sees **already minimized** audit records only. It outputs
  structural event labels ("Status transition" and "Assignee transition"),
  times, and bounded aggregate process variants.
- No native issue keys, actor IDs, customer names, field values, raw status IDs,
  or source audit event refs appear in the returned process preview.
- Source-verified issue resolution is *not* a verified wider customer outcome.
  Assignee changes do not imply that the new worker accepted the work, nor
  do status reversals prove process failure.
- Only one opaque record per user click. No cross-record case joins, batch
  background scans or claim of full process mining coverage. Truncated/missing
  audits are explicitly marked; missing events are not absence of work.

## Next stages

Real multi-case source logs, verified tenant-local cross-system case correlation,
formal handoff/latency analysis, normalizing different provider event types,
and independent business-outcome validation. All need separately scoped consent,
auditable reads, robust case ID matching and realistic synthetic tests.
