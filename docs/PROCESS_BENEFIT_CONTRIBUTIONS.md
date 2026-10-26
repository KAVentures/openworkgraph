# Versioned scenario benefit contributions

The shared process portal can link existing scenario-bearing process revisions
into a versioned contribution plan. Each link explicitly includes or excludes
that scenario from an estimate. Assumptions remain in the existing process model;
the plan stores exact workflow/revision/digest references and scope declarations.
A separate organization portfolio counts a reused scenario process once.

This is modeled opportunity value, **not measured employee effort, realized
savings, independent financial approval or execution authorization**. Existing
organization opportunity ranking remains unchanged and still has no company
savings total. These new calculations require an explicit, separate review.

## Process owner workflow

1. Open a process in `/processes`, then **Load process benefit plan**.
2. Load a page of process choices and choose an active process with a saved
   opportunity scenario. Add its exact revision as a contribution. New links are
   excluded by default; no totals are calculated from mere linking.
3. Give each link a stable ASCII benefit-scope key. This labels the population
   of work whose benefit is being assumed. Reuse the same scenario identity and
   scope when linking a shared automation from several root processes.
4. Explicitly include each contributing scenario. For multiple included
   scenarios, review their scopes and declare them disjoint. Distinct keys alone
   do not establish that the underlying work is disjoint.
5. Review the complete plan and explicitly confirm organization sharing, then
   save. Edits clear sharing/scope confirmation and previous portfolio results.
   Saved-plan estimates always describe the saved revision; unsaved edits are
   excluded. Refresh exact process references explicitly after design changes.

Current root owner/editor/admin and `org:process:read/write` permissions govern
plan changes. Root owner/admin may erase all plan history, including after root
archive or plan corruption. Readers can inspect and export but cannot edit.
Process reading remains tenant-wide for the explicit process-read scope;
collaborator removal revokes editing/review, not design reading. There are no new
scopes, default grants, service-token fallback or per-process confidentiality claims.

The root process and every **included** scenario must be current, active,
integrity-checked and independently design-reviewed at calculation time. Each
included scenario must use the plan currency. A missing, archived, corrupt,
changed, unreviewed or wrong-currency contributor withholds the entire plan
estimate. A missing or wrong-currency **excluded** link remains visible but does
not block the other included contributions. No contributions yields unknown/no
estimate, rather than a claim of zero measured savings.

Repeated scope keys across different included scenarios block the sum. The
root's own scenario can be linked explicitly; it is not added automatically.
Separate repository hierarchy maps do not imply contribution links or aggregate
benefits, and plans do not expand child plans recursively.

## Organization review and duplicate prevention

Choose a currency and **Load current benefit portfolio**. Inspect all contributing
plans, exact references and benefit scopes. The preview returns no organization
total. Confirm the complete current selection and the declaration that distinct
included scopes do not overlap, then calculate the organization estimate.

- The same scenario workflow ID in several plans contributes its modeled hours,
  value, recurring cost and implementation cost **once**.
- A reused ID must have the same revision, digest and scope key everywhere.
  Conflicting declarations withhold the entire estimate; historical and current
  versions are not combined or silently selected.
- Different scenario IDs sharing a scope key block the sum as overlapping
  alternatives. Exclude an alternative explicitly rather than counting both.
- Distinct scope keys require a human disjointness declaration. They are not
  evidence of case-level independence. Separate copies of a scenario under new
  workflow IDs are not automatically recognized as one real automation. Reuse
  the original identity; verify actual overlap independently before business use.
- Every contributing plan's root/current included references and local scope
  review must remain eligible. One unavailable/corrupt plan of unknown inclusion
  or currency also withholds totals. Successful partial subtotals are not returned
  inside the organization report.
- A snapshot scans at most **100 latest plans**, with at most **20 links each**.
  A 101st plan sets truncation and blocks the estimate; no complete-company claim
  is made from a partial scan. Plans in known other currencies are counted but
  excluded from this selected-currency report. There is no currency conversion.

The inspection returns a semantic portfolio SHA256 of the pre-calculation
declarations and eligibility diagnostics, not a signature of a report or proof
of review authority. Estimating requires this
exact fingerprint and current permissions. Changes to plans, references, review status, archives, ownership/access
metadata or reported diagnostics require reload and fresh review. Non-affecting
review statements that leave the reported eligibility unchanged are not separate
portfolio inputs. An exported report is also freshly authenticated, fingerprint-checked
and audited; downloading a stale in-memory result cannot bypass this check.
A withheld preview may still be explicitly exported for diagnosis.

Read-only calculations and exports do not persist results or alter model review.
They are point-in-time reads of shared design declarations, **not live source
monitoring**. A business system can change without changing a saved process model.
No system-of-record reads or outcome verification are performed by this feature.
Disconnect/identity/selection changes clear page state and suppress held responses.

## Arithmetic and provenance

The calculator reuses validated `OpportunityScenario` inputs: monthly cases,
minutes assumed saved per case, eligibility share, loaded hourly cost, recurring
monthly cost, implementation cost and sensitivity. It computes from raw normalized
assumptions with decimal arithmetic and rounds **once after summation**, half up
to two decimal places. It does not add already-rounded child projections.

Totals are JSON **decimal strings**, including large totals that JavaScript
numbers cannot represent safely. Hours, gross and net monthly value, recurring
and implementation costs, sensitivity range and simple payback remain separate.
Nonpositive net value yields no positive simple payback. Sensitivity is a modeled
range, not a statistical confidence interval. No adoption, review/error/runtime
costs, actual baseline/intervention cohorts or realized financial value are
inferred. Entered assumptions may still be wrong even if arithmetic is correct.

Plan history stores immutable declarations, server author and saved time. Current
reference diagnostics and estimates are separate, so an old plan artifact stays
byte-reproducible while its references can become ineligible. There are at most
20 plan revisions per root; nothing is pruned automatically. Owner erasure removes
all of that root's plan revisions; recreation starts a new history whose digests
must be checked. Deleting the root process erases its plans atomically. Deleting
a referenced scenario retains an unavailable declaration in other roots' plans.
Downloaded artifacts are separate user-controlled copies.

Storage reuses `workflow_knowledge` under the three-component domain
`["organization-process-benefit-plans", organization, "v1"]`, distinct from
personal knowledge and shared models/maps. There is no database schema migration.
Root and referenced process metadata locks are acquired in consistent ID order;
CAS protects plan/access revisions. Read snapshots are repeatable on PostgreSQL.
Read/export audits are separate from the read-only transaction and withhold the
response on failure. Write/erase audit failures roll back the mutation. Audit
records contain IDs, digests and counts, not titles, scope keys, assumptions or
computed monetary values.

Normal process JSON/BPMN/PDD/SDK exports and hashes do not change or include these
plans. Use the separate plan manifest and portfolio report for contribution
lineage. These references are descriptive; they do not approve automation,
propagate model review or add executable calls.

## API

All endpoints use the existing human process authentication and are no-store:

- `GET/POST /v1/human/processes/{id}/benefits`: detail or explicitly reviewed save.
- `GET …/benefits?revision=N` and `GET …/benefits/export?revision=N`: immutable
  declaration plus current diagnostics, with audited manifest export.
- `POST …/benefits/delete`: owner/admin-confirmed history erasure with CAS.
- `GET /v1/human/process-benefit-portfolio?currency=USD`: complete bounded preview.
- `POST …/process-benefit-portfolio/estimate`: currency, expected portfolio SHA256,
  explicit review and disjoint-scope confirmation.
- `POST …/process-benefit-portfolio/export`: same current fingerprint and explicit
  export confirmation; disjoint-scope confirmation requests the reviewed estimate,
  otherwise exports the current preview without totals.

Mutation bodies reject unknown/duplicate keys and nonstandard JSON constants.
Plan writes are bounded to64 KiB; erasure/portfolio review to4 KiB. References,
booleans, counts and revision counters use the existing strict bounded contracts.
Source references and sharing confirmation must match the current organization
and selected root. Source revisions require valid payload digests and author
ledger attribution, not merely a caller-supplied SHA256.

## Research and remaining work

The public [UiPath rolled-up benefit guide](https://docs.uipath.com/automation-hub/automation-cloud/latest/user-guide/rolled-up-benefit)
was retrieved successfully on26 October2026. It documents explicit per-link
inclusion and withholding the complete total when a contributing automation cannot
be viewed. OWG implements this useful functional pattern over its current scopes,
plus conservative reuse/declared-overlap checks; no proprietary implementation or
assets are copied.

R15/R16 remain partial: a shared opportunity pipeline with dedicated ownership,
budgets/dependencies/stages, independent benefit-plan approval, allocation weights,
measured overlap, actual baseline/intervention comparisons, adoption and operational
costs, authorized outcome receipts and verified realized-value reporting remain
open in #370. A design review, explicit scope declaration or arithmetic test does
not close those acceptance gates.
