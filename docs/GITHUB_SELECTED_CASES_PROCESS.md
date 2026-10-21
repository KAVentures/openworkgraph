# Selected GitHub review and lifecycle histories

The local Overview's exact-record lanes now offer **Read opted-in GitHub review
history** for a retained pull-request reference. A separate confirmation and the
existing GitHub source-status opt-in/local CLI authentication are required.
No repository inventory is scanned, credentials configured or business action
performed. Normal observation consent and AI access remain separate.

The source read uses the existing fixed `gh pr view` request and its already
requested review/current lifecycle fields. The record graph first proves the
exact reference, GitHub/pull-request/github.com identity and complete retained
identity window. Missing, conflicting, truncated or other-provider identity
blocks the read before any source adapter runs. A locally resolvable locator
alone cannot authorize a request.

Supported source steps are review approval, changes requested, review comment,
current merge timestamp and current closed-without-merge timestamp. Stable
provider review IDs deduplicate repeated rows; distinct reviews sharing a
timestamp remain separate. Conflicting review records are omitted. Pending or
dismissed reviews do not establish a submission/dismissal action at an invented
time. Invalid merge timestamps cannot establish a merged source state.

Review body/author, repository, PR number and native review IDs are excluded
from the process output. Temporary event references use a process-secret HMAC
scoped to the selected record; they are not persisted and change on restart.
Closed/reopened history, commits, checks, provider events outside the bounded
read, dismissed decisions and other business activities may be absent. This
is always a **partial review/lifecycle projection**, not a complete business
case, verified waiting/labor, approved process, customer outcome or execution
permission.

## Shared analysis and design

After the read, **Explore GitHub source process** opens the existing event-log
workspace without another source read. The same bridge is available for Jira
single/selected-case projections. Inspect observed maps, variants, event counts,
case examples and elapsed gaps with the existing keyboard-accessible renderer.
The heading and warnings distinguish source projections from imported CSV files.

Source previews stay ephemeral. They clear any previously analyzed file and
cannot enable CSV snapshot retention. A pending older CSV response cannot
replace the newly selected source projection. **Design improved TO-BE workflow**
creates an unsaved catalog draft with `selected_source_audit` and
`partial_source_audit` provenance; it retains neither native IDs nor source
payloads. The person supplies a business goal and reviews the design before Save.
No source-derived decision becomes business policy or permission.

## API and limits

`POST /v1/process-mining/github-selected-cases` accepts
`{"resource_refs":["owg:r:..."],"confirm_source_reads":true}` with a local dashboard
session. Ordinary API/MCP bearers cannot trigger this read. JSON is limited to
2 KiB, rejects duplicate keys/nonstandard constants, and accepts one to eight
distinct exact retained references. Each read independently repeats the identity
and consent checks. Responses are `Cache-Control: no-store`.

The projector considers at most 40 supported review rows and one terminal
lifecycle fact per reference. It reuses the same CSV case/variant engine with
temporary ordinal cases, while explicitly denying cross-record identity and
business-outcome inference. Missing/unsupported receipts add no synthetic events.
An ordinal case labels a selected reference; equivalence between different
references is not inferred or certified. No result or source record is retained.

Compatibility was checked against the public GitHub CLI
[`PullRequestReview` JSON fields](https://github.com/cli/cli/blob/7948e8056e45ac3c729b829e0eafe2d0797a95c6/api/queries_pr_review.go#L38).
Review `id` is available alongside `state` and `submittedAt`; the existing CLI
request is unchanged. No GitHub CLI code is copied. Tests use an owned CLI fake,
the actual dashboard/API/projector flow and canonical-row preservation; they
do not certify a customer's current source access or complete workflow history.
