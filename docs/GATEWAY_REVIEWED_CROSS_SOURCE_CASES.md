# Reviewed cross-source enterprise cases

Part of enterprise delivery #1572. Builds on the exact connector snapshot
publication flow from #1578 and the native enterprise feed stack through
#1581. The source logs remain canonical. No automatic cross-provider identity
matching, raw identifier disclosure, transitive closure or business outcome
inference is introduced.

## Workflow

1. Publish at least two separately approved snapshots into the same
   organization using the existing source-host publication workflow.
2. Sign in as a Gateway owner and open **Connector event integration →
   Review cross-source business case linkage**. Choose two to ten exact
   published snapshots. Authorize inspection of their pseudonymous cases,
   counts and observed time ranges.
3. Review the case catalog using the paginated source filter. Independently
   inspect authorized business records or native reference evidence in the
   original systems. Use their source-host pseudonym mapping to identify
   the intended cases. The Gateway catalog alone cannot establish that
   records represent the same business case. Do not enter raw identifiers
   or credentials into the review UI.
4. Select case members from at least two distinct source jobs and add a
   group. Record the evidence basis: reviewed native reference, reviewed
   business record, or operator assertion only. These are operator-declared
   categories, not proof that external evidence was actually examined.
   Each input case may belong to exactly one group. Multiple records
   from the same source in one group need an additional acknowledgement.
5. Choose whether to keep unlinked cases independent or exclude them.
   Preview the exact plan, including source case/event coverage, case
   count reduction, group membership and event ranges, timestamp ties,
   source switch observations and day precision warnings.
6. Confirm the exact linkage assertions and the export separately. The
   server revalidates the original publication pins, row digests,
   memberships and review SHA-256 inside an organization transaction
   before returning JSON or machine-import CSV.

## Contracts and provenance

All reads are owner-only, organization-scoped and limited to already
published, unexpired disclosure grants. A worker token cannot inspect
another source or review links. The case catalog binds each page to the
exact selected grant IDs, manifest hashes and publication revisions.
Every catalog/preview/export operation uses the same organization
transaction as the publication workflow. Deletion or expiry of any selected
snapshot invalidates the plan; reapproving identical source bytes under a
new grant does not resurrect it. Audit entries record bounded counts and
digests, never full case identities or event rows.

Groups are disjoint explicit sets. Ordering of groups, members and
snapshots is canonicalized; no inferred edges or closure are applied.
A group is not an authoritative entity-resolution result. Its identifier
is a SHA-256 of exact scope and members. The complete review digest also
covers evidence basis, group summaries, many-to-one acknowledgement and
unlinked policy. Changing these requires a new preview.

Export preserves all original source event pseudonyms and source namespaces.
Each linked group becomes one case; unlinked cases, if retained, receive
distinct scope-qualified identifiers. Activity labels are prefixed with the
source job ID to avoid implying that similar labels from different providers
mean the same thing. Original source-specific activity codes are not decoded.

Each export returns a mapping for the existing supplied-source-key CSV
importer, including a common review-specific case namespace. Import into
a **new local mining log** for each changed review. Reimport of the exact
same review into its own log is replay-safe. The JSON is the provenance
sidecar; keep it with the CSV and its mapping. CSV is intended for machine
import, not spreadsheet viewing. Gateway expiry/deletion does not recall
already downloaded copies. No derived event dataset or link plan is stored
at the Gateway; the review must be recreated for a different scope.

## Time semantics

Observed spans are ranges of returned timestamps, not verified cycle times.
Strict adjacent source switches count only consecutive distinct timestamp
buckets with exactly one source each and different jobs; they are not
verified handoffs or causal transitions. Cross-source ties are counted
separately. For any group containing the day-only SAP procurement adapter,
numeric observed span and switch counts are suppressed. Other source
timestamps may also have limitations; the absence of a SAP warning does
not establish full timestamp precision. The exported flat event log still
contains original timestamps, so mining consumers must respect these
limitations and not infer within-day order.

## API

All endpoints are POST under /v1/admin/connector-events/{organization_id}:

| Endpoint | Input / output |
|---|---|
| case-catalog | exact 2–10 publication pins, inspection consent, source filter, page offset/limit and previous scope digest |
| case-review | exact pins, explicit groups, many-to-one acknowledgement and unlinked policy; returns impact and review digest |
| case-export | same plan, review digest and separate confirmations; returns exact JSON and mapping |
| case-export-csv | same approval contract; returns machine-import CSV |

Bounds: 20,000 input events, 500 groups, 2,000 linked members, 20 members
per group, 100 catalog rows per page and 512 KiB plan request.
These are bounded interactive reviews, not streaming global entity resolution.

Regression tests exercise the existing real retained-source publication
fixtures, SQLite/PostgreSQL review transactions, exact source-key mining
import and replay, unlinked inclusion/exclusion, stale/expired grants,
tenant boundaries, timestamp ties, many-to-one safeguards and HTTP owner
authorization. CI registration includes the disposable PostgreSQL backend.
Tests and interactive browser acceptance must be run before production use.

Follow-ups: source-host crosswalk preparation with independently reviewed
native identifiers, verified linkage evidence receipts, versioned review
persistence and revocation-aware shared analytics, richer many-to-many OCEL
object relationships, semantic activity mapping, timestamp uncertainty
propagation and enterprise-scale matching performance.
