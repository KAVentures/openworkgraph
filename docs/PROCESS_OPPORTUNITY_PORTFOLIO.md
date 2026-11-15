# R15 — Selected shared opportunity budget preview

The organization process portal has a read-only **selected opportunities**
budget preview. Users select opportunities on the currently loaded pipeline page,
state an envelope in a single currency, and explicitly confirm the request.

This is not an enterprise budget ledger, allocation authority, payments
automation, validated actual expenditure, realized return or a company-wide
portfolio valuation. It uses only existing shared opportunity assessments
and exact current process/catalog revisions; it never writes a business record
or creates a parallel opportunity store.

## Workflow and contract

1. Open the organization process portal and select **Load shared opportunity
   pipeline**. Each displayed opportunity provides a **Compare budget** toggle.
   Loading another page resets the selection and prior preview.
2. Enter one nonnegative decimal envelope (major units, max two decimal
   places), and choose USD, EUR, GBP or SEK. There is **no FX conversion**.
3. Explicitly confirm the comparison and select **Preview selected opportunity
   budgets**. The portal freshly reads each selected opportunity, retaining
   its exact latest revision and SHA-256. A changed revision requires reload.
4. A human-scoped, read-only endpoint:
   `POST /v1/human/process-opportunity-pipeline/preview`
   receives an `openworkgraph.selected-opportunity-budget-request.v1`
   body with `currency`, `ceiling`, `user_confirmed=true`, and 1–100
   `selections` of `workflow_id`, `revision`, `content_sha256`.
5. The Gateway resolves all selections in one organization-scoped read
   transaction, against the existing opportunity record and underlying
   source model. It refuses changed content, stale or unreviewed process
   references, mismatched currencies, missing assessments, inactive stages,
   corrupted records and inaccessible tenants.
6. If **any** selected record has a blocker, **all totals are withheld**. It
   never silently reports a partial portfolio. The response lists bounded,
   per-selected-record blocker codes but no unselected organization data.

If current, the response reports the sum of **human-declared implementation
budget estimates** in the selected currency, split between currently
approved *opportunity stage* and unapproved-stage estimates, and difference
from the caller's envelope. A negative headroom means the selected
assumptions exceed that declared cap. No spending approval is inferred,
not even for entries whose assessment stage is approved.

Root-access checks use existing organizational read scope, tenant separation
and current shared process records. The audit contains only selection count,
blocker count and status, never currency amounts, names or the selected IDs.
The endpoint makes no external provider calls, writes, automatic grants or
background scheduled jobs.

## Limitations, acceptance and future work

Two opportunities may refer to the same real-world work and benefits. This
first slice **does not deduplicate overlapping financial scope**, detect
double-counted case work, or equate entered implementation budgets to verified
spend or labor savings. The response prominently flags this. Historical
opportunities are refused for current totaling; their immutable revisions
remain accessible through the normal history API.

Full R15 governance still needs approved shared budgets and their role-scoped
decision/version history, portfolio lifecycle, people/team workload, dependency
graph across opportunities, named allocations, procurement/expense receipts
and stable scenario/case overlap identities. Those are separate authorized
deliveries, not implicit approvals created by this preview.
