# Portable workforce procurement audit dossiers (R26)

Customers and their auditors should be able to reproduce the same evaluation
without sharing local databases, agent credentials or purchasing a particular
automation vendor's reporting tool. A dossier captures the currently selected
complete signed packet cohort and the exactly recomputed signed review. It is
not an independently attested financial statement or a deployment approval.

## Export and transfer

Open Process catalog → retained signed installation inbox. Load status for the
intended namespace and maximum age, then select **Export independently
verifiable signed audit dossier**. Every active enrolled installation must
have a fresh, signature-verified packet. The server checks both the registry
and inbox revision in one local transaction, rereads packet contents, and
recomputes the review. The export does not advance accepted review watermarks.
The existing registered review remains a separate explicit operation.

Confirm export of linkable opaque case/source tags and transfer the downloaded
JSON through an approved channel. Communicate its displayed dossier digest
through a separately trusted channel. The dossier digest is SHA-256 over
canonical JSON of the dossier excluding dossier_sha256, not the bytewise hash
of the downloadable file. Obtain each installation's public key and alias
mapping independently; do not treat keys shipped by an untrusted dossier
sender as trusted. No public-key trust list, signing seeds, correlation keys,
raw case inputs, or producer credentials are included in the dossier.

This dossier does not include sticky historical inbox incident rows (which
may reference packets no longer retained). Export the inbox status separately
when that evidence is needed. A clean current dossier does not clear a
previously recorded rollback-review referral.

API: dashboard-session-only POST
/v1/process-catalog/procurement-workforce/exchange/inbox-dossier, standard
JSON-text document envelope. The document has the same fields as inbox-review
(expected_revision, expected_inbox_revision, correlation_namespace_sha256,
maximum_packet_age_ms, confirmed=true). The response is no-store. There is
no provider call, remote endpoint or external action.

## Independent local verification

Use the installed OWG Python environment and code version implementing
this dossier format:

```sh
python -m server.agent_procurement_workforce_dossier verify dossier.json trust.json --sha256 <separately-obtained-digest> receipt.json
```

The trust file has this shape (replace placeholder values):

```json
{
  "expected_correlation_namespace_sha256": "<64 lowercase hex characters>",
  "trust": [
    {
      "installation_alias": "site-a",
      "public_key_hex": "<32-byte Ed25519 public key, lowercase hex>",
      "correlation_namespace_sha256": "<same namespace digest>"
    }
  ]
}
```

Include exactly the trusted aliases for this archive. The verifier checks the
independently provided dossier digest, canonical packet representation, exact
namespace and trust set, signatures, timestamp freshness relative to the
recorded review clock, overlap exclusion, exact rational accounting and the
entire recomputed report. It refuses altered results even if they have been
rehashed. The verifier does not establish a trusted timestamp, freshness at
verification time, organization identity, current key revocation, source
truth, or whether the installations represent the entire organization.

For customers who prefer no local dashboard dependency for review, create a
dossier directly from the existing signed-review request format (including
separately pinned public keys and confirmed=true):

```sh
python -m server.agent_procurement_workforce_dossier freeze signed-review-request.json dossier.json --confirm-linkable-export
```

## Compare historical snapshots

```sh
python -m server.agent_procurement_workforce_dossier compare earlier.json later.json trust.json --previous-sha256 <earlier-digest> --current-sha256 <later-digest> delta.json
```

Both dossiers are independently recomputed. Comparison requires the same
namespace, exact trusted alias/key mapping, same freshness policy, advancing
review clock, and no older or conflicting packet for an installation. For
each opaque journal, inspect present/absent/changed cohort status, exclusion
reasons, recorded counts and rollback-review flags from both snapshots.
The comparison retains a prior flag even when the journal disappears. It does
not sum repeated snapshots, infer resolution or compute financial improvement
from different cohort populations. Key rotation or changed participating
installations need separately verified dossiers and human review.

All commands bound file sizes to 40 MiB, reject duplicate JSON keys and
non-finite constants, and refuse to overwrite any existing output. New
CLI files are created with mode 0600 on POSIX; the dashboard download uses
browser-controlled storage permissions. A failed write may leave a partial
file; never treat it as a valid dossier. Do not send linkable artifacts to
unapproved recipients. Exact historical recomputation can require the same
OWG report implementation; no indefinite cross-version compatibility or
trusted archival storage is claimed.


## Multi-snapshot independent audit timeline

For an audit involving 2–12 historical dossiers, including installations that
have temporarily disappeared or changed signing keys, obtain a separately
trusted manifest of the ordered dossier digests and historical trust sets.
Use this format:

```json
{
  "format": "openworkgraph.workforce-audit-timeline-manifest.v1",
  "entries": [
    {
      "dossier_sha256": "<first independently obtained digest>",
      "trust": {
        "expected_correlation_namespace_sha256": "<namespace>",
        "trust": [
          {
            "installation_alias": "site-a",
            "public_key_hex": "<historical public key>",
            "correlation_namespace_sha256": "<namespace>"
          }
        ]
      }
    },
    {
      "dossier_sha256": "<second independently obtained digest>",
      "trust": {
        "expected_correlation_namespace_sha256": "<namespace>",
        "trust": [
          {
            "installation_alias": "site-a",
            "public_key_hex": "<later historical public key>",
            "correlation_namespace_sha256": "<namespace>"
          }
        ]
      }
    }
  ]
}
```

Obtain the manifest's canonical JSON SHA-256 separately from the authorized
audit coordinator. The manifest must not be trusted merely because it was
supplied alongside the dossiers. Then run:

```sh
python -m server.agent_procurement_workforce_timeline manifest.json --manifest-sha256 <separately-obtained-digest> --output timeline.json first.json second.json
```

Files must appear in manifest order. The timeline recomputes every signed
report, checks strict review-clock progression and a single namespace,
rejects duplicate dossiers, backwards or conflicting per-installation
packets (including after a site disappears and reappears), key reassignment
to a different alias, case-only alias changes, and reuse of a previously
retired key. A separately pinned new key can be recorded as a historical
key change; the tool does not verify who authorized that change. Changes in
the declared freshness policy and participating installation set are
displayed, not silently normalized.

The resulting minimized timeline shows the first/last snapshot and
observation count for every seen journal; changed or unavailable case
cohorts; exclusions; cutoff regressions against the historical high-water
mark; and every supplied snapshot in which a rollback-review signal was
observed. It never interprets later absence or a clean snapshot as clearance.
The timeline does not contain case/source correlation tags or raw packets;
journal tags and installation aliases remain linkable. No across-snapshot
financial sums or causal comparisons are calculated.

The manifest and dossiers must come from trusted custody. Strictly advancing
declared times do not prove trusted chronology; a missing snapshot may
contain an unobserved incident. The report does not verify revocation,
organization identity, completeness of historical custody or live rollout
authority. Individual input files and the output are capped at 40 MiB;
aggregate dossier input is capped at 160 MiB. Output is exclusive/private
under the same conditions as the original audit CLI.


## Local dashboard verification workbench

The receiving auditor does not need to enroll keys in the local trust registry,
retain packets or use the command line for smaller archives.

1. Open **Process catalog → Exchange minimized monitoring summaries across
   installations → Independently verify historical workforce audit evidence**.
2. Select a single dossier or ordered timeline. Supply the separately obtained
   expected SHA-256 digest (dossier digest for a single review, manifest digest
   for a timeline), and the corresponding independent trust JSON or manifest.
3. Select each dossier individually in exact manifest order. Confirm the
   local recomputation. No requests are initiated solely by selecting files.
4. Review the independently recomputed historical report, or inspect the
   timeline snapshot summaries and filter/search/page through the complete
   minimized journal queue. Download the minimized review for your records.
   Changing an input clears the prior result and ignores a pending response.

The browser enforces an 8 MiB combined original-text request limit, including
the JSON wrapper; larger requests must use the offline CLI. Invalid UTF-8,
non-object files, malformed/duplicate JSON, wrong pins, altered reports and
out-of-order timelines are refused. Browser syntax checks do not replace the
server's strict parser or cryptographic verification. Numeric text is not
re-serialized by the browser before verification.

The workbench does not retain dossiers, trust keys, manifest contents or
reports in the local registry/database, and requires a dashboard session and
explicit operator confirmation (AI-context and bearer-token requests are
refused). The response contains only a minimized recomputed report/receipt
or timeline, not the underlying signed packets. File selection and processing
take place on the local installation. Browser downloads and any OS/browser
caches remain the user's responsibility.

API actions: POST
/v1/process-catalog/procurement-workforce/exchange/audit-verify
with the standard JSON-text document envelope containing
`confirmed=true`, `expected_dossier_sha256`, `trust` and `dossier`;
POST /v1/process-catalog/procurement-workforce/exchange/audit-timeline
with `confirmed=true`, `expected_manifest_sha256`, `manifest` and
the ordered `dossiers` array. The server requires the exact keys and
strict confirmation, recomputes all reports, returns no-store responses,
and does not consult the retained trust registry. Neither endpoint confers
current trust, verified chronology, incident clearance or execution authority.


## Evidence-pinned follow-up worklist

After independently recomputing a timeline, expand **Prioritized audit
findings and follow-up assignments**. The worklist deterministically derives
findings from all supplied snapshots, including prior rollback referrals,
source attention flags, cutoff regressions, changed or unavailable cohorts,
excluded journals, journals absent in the latest snapshot, absent
installations, declared key changes, unreadable journals and changed
freshness policy. A later clean snapshot does not discard an earlier flag.

Priority 0 is a historical rollback referral; priority 1 is an evidence
coverage or cutoff warning; priority 2 is another review finding. These
priorities are review ordering, not a diagnosis of severity or actual harm.
The worklist pins exact dossier and report digests for every finding.
Filter and search by finding kind, installation, journal or finding ID;
pagination does not change the underlying worklist or selected findings.

Select up to 200 findings, enter a declared reviewer alias, and record
investigate, escalate, request_evidence or acknowledge for each selection.
A rollback referral cannot be merely acknowledged. Any non-acknowledgment
disposition requires a declared follow-up owner. Optional external references
are opaque aliases, not URLs or ticket lookups. The server independently
recomputes the timeline and worklist from the original archives before
issuing a receipt, rejects changed pins or duplicated findings, and reports
all selected/unselected counts including unselected rollback referrals.
Changing a selection or reviewer clears the visible stale receipt.

No external issue is created, no assignment is sent to another person, and
no incident is cleared. Reviewer/owner aliases and external references are
operator declarations, not authenticated identities. A receipt covering
every finding is not evidence that work was performed. The worklist and
receipt are portable, minimized exports; neither is retained by the local
server, and no credentials, signed packets or source case tags are in them.

For larger archives, build the worklist offline:

```sh
python -m server.agent_procurement_workforce_audit_triage manifest.json --manifest-sha256 <independent-digest> --output worklist.json first.json second.json
```

Create a separate decision JSON containing the exact worklist_sha256 (as
expected_worklist_sha256), reviewer_alias, confirmed=true, and selections.
Each selection contains finding_id, expected_finding_sha256 (from the
worklist), disposition, owner_alias (or null for acknowledgment), and
optional external_reference. Then run the same command with
`--decision decision.json --output receipt.json` instead of the worklist
output. Original dossiers and independent manifest are always required.

The dashboard-only POST
/v1/process-catalog/procurement-workforce/exchange/audit-triage accepts the
audit-timeline document plus a decision object as described above; the
same exact-text, 8 MiB request, explicit confirmation and no-store
boundaries apply. The CLI has the historical timeline aggregate input cap,
exclusive output and a maximum 30,000 findings; exceeding any cap is
refused rather than silently truncating the worklist.


## Reconcile follow-up receipts

After reviewing the original historical timeline, expand **Reconcile
follow-up receipts from multiple reviewers**. Select one to forty exported
triage receipts and supply the separately communicated SHA-256 pin for
each. The server independently recomputes the original worklist and every
receipt, compares their exact canonical representations, and rejects
duplicate pins, malformed or rewritten receipts and receipts belonging
to a different worklist. Digest pins do not authenticate the reviewer.

The reconciliation includes every original finding, including those with
no submitted declaration. It distinguishes unaddressed, single declaration,
matching declarations and conflicting declarations. Disagreement in
disposition, owner or external reference is reported without choosing a
winner. All competing declarations and their receipt pins remain visible.
Case-folded reviewer alias variants and conflicting declarations under the
same alias are identified; this is not identity resolution or proof of
independent reviewers. Upload order does not affect the result.

The dashboard offers status filtering, search, paginated detail and a
minimized JSON download. Changing a receipt file or pin invalidates the
visible result and ignores stale responses. No external tickets, assignments,
incident clearances or production authorizations are created.

For offline reconciliation, provide a receipt-set JSON object containing
`confirmed: true` and a `receipts` list, where every entry contains
`expected_triage_sha256` and the complete exported `receipt` object.
Obtain pins from the relevant trusted administrative channel rather than
assuming an uploaded receipt is its own trust anchor. Run:

```sh
python -m server.agent_procurement_workforce_audit_reconcile manifest.json --manifest-sha256 <independent-digest> --receipts receipts.json --output reconciliation.json first.json second.json
```

The dashboard-only POST action
/v1/process-catalog/procurement-workforce/exchange/audit-reconcile accepts
the original audit-timeline request plus this object as `receipt_set`.
The same strict JSON-text, session authentication, explicit confirmation,
no-store and 8 MiB request limits apply. The CLI has the historical aggregate
dossier input cap and exclusive 40 MiB portable output cap. Larger output
is refused instead of truncating findings. No reconciliation is retained
by the server; separately preserve exports according to your audit policy.


## Persistent local remediation registers

The previous review, triage and reconciliation operations remain transient.
For longer-running follow-up, recompute a historical timeline, then choose
**Retain exact worklist for local remediation** and confirm local retention.
The server recomputes the signed input against independent trust pins and
requires the exact worklist digest. Only minimized finding identifiers,
kind, priority, scope and basis hashes are retained initially. No signed
packets, source case tags, trust keys, triage receipts or raw source data are
retained by this register. Original evidence must remain in separate custody.

Open **Local audit remediation registers** to reload a register, filter its
findings, assign owners, record investigation and evidence-request progress,
record a declared follow-up with an opaque evidence reference, or reopen a
finding. Every change requires the exact worklist/finding pins, current
revision and history digest, declared reviewer and owner, and explicit
confirmation. A stale revision is refused. Reassignment must be explicit;
reported follow-up requires a reference and is not closure or verified work.
Historical rollback referrals remain visible in every state.

A register has a frozen evidence basis. Importing the same worklist is
idempotent and preserves progress; a different worklist is a separate
register, not silent replacement or migration. Local progress events are
hash-linked and replayed on read; this detects inconsistent local history,
but a database writer can rewrite an entire chain. Neither the chain nor
the local clock establishes trusted chronology, identity or external
evidence validity. Export and retain a separate history pin as appropriate.

Export the full minimized register and history before using **Remove local
remediation register**. Removal requires the exact latest revision/history
pin and a declaration that the export was made. It does not erase backups,
downloads or the independent signed evidence. There is no automatic
retention expiry. Caps are 20 registers, 2,000 findings per register,
5,000 events per register and 32 MiB total encoded register storage; exceeding
a cap refuses the action rather than dropping evidence.

Dashboard-session-only POST actions under
/v1/process-catalog/procurement-workforce/exchange/:

- remediation-create: original audit-timeline request plus
  expected_worklist_sha256 and confirm_local_retention=true.
- remediation-list: empty JSON object.
- remediation-status: worklist_sha256.
- remediation-change: worklist_sha256, expected_revision,
  expected_history_sha256, finding_id, expected_finding_sha256, state,
  owner_alias, reviewer_alias, optional evidence_reference, confirmed=true.
- remediation-forget: worklist_sha256, expected_revision,
  expected_history_sha256, confirmed_exported=true.

All use the standard JSON-text document envelope and no-store response.
These actions never create or update external tickets, verify actual
remediation, clear incidents, or authorize deployment.


## Cross-worklist continuity

When a new verified dossier is appended to the audit timeline, review the
extended timeline and expand **Carry remediation into an appended audit
history**. Load and select the earlier retained register and enter its
independently obtained manifest digest. The previous manifest must be an
exact strict prefix of the new manifest (including its original trust
entries and signed dossier pins). Both worklists are recomputed from the
original evidence; a different or rewritten history is refused.

The preview distinguishes unchanged evidence pins, changed evidence pins,
new findings and findings not present in the successor. Missing findings
are not cleared and remain in the original register. Only a finding with
an exact unchanged pin and an existing owner is eligible for explicit
owner carry. Select up to 200 such owners and supply a declared reviewer.
Each selected owner is recorded as a fresh assignment with no carried
progress or evidence reference. Changed evidence requires new review.

Creating a successor requires the exact previous register revision and
history pin, previous manifest digest, new worklist pin and continuity
preview digest, plus explicit local retention consent. Creation is atomic;
an existing successor is never overwritten. The previous register remains
untouched, and the successor retains minimized, hash-bound lineage to its
previous register and selected owner assignments. Keep original evidence
and prior exported registers separately; removing an earlier local register
does not remove its independently retained successor or erase exported
lineage.

The dashboard-only POST action
`/v1/process-catalog/procurement-workforce/exchange/remediation-continuity-preview`
accepts the original audit-timeline request plus a `continuity` object:
`previous_worklist_sha256`, `expected_previous_revision`,
`expected_previous_history_sha256`, `previous_manifest_sha256`,
`expected_current_worklist_sha256`, and `confirmed: true`.
The corresponding `remediation-continuity-carry` action requires the
additional `expected_continuity_sha256`, `reviewer_alias`,
`carry_finding_ids` and `confirm_local_retention: true`.
Existing dashboard authentication, JSON-text and storage bounds apply.
No actual remediation, incident clearance or external ticket action is
verified by these operations.


## Read-only Jira ticket synchronization

An assigned local remediation finding can be explicitly mapped to an exact
locally observed Jira Cloud issue. Open its register, expand **Read-only
Jira ticket synchronization**, load the ledger, select the finding and
enter the opaque `owg:r:` resource reference obtained from OWG's observed
record evidence. Native Jira issue keys and URLs are not accepted. The
mapping is immutable for that register; one resolved record identity cannot
be bound to multiple findings in the same register. Mapping is an operator
declaration, not independently established semantic equivalence.

**Refresh Jira ticket state** explicitly uses OWG's existing default-off
Jira source adapter. The exact observed-record guard, configured tenant,
site authorization, read scope and OAuth controls remain in force. No
network call occurs during binding. Reads are outside the database write
transaction and the exact register and ticket ledger revisions are checked
again before recording the result. A concurrent change discards the result.
Unavailable or stale source data remains unknown, not resolved.

The retained ledger contains opaque references, local keyed identity pins,
source-reported state (new, indeterminate, done or unknown), resolution
flag/timestamp where established, read time, bounded changelog completeness
flag and hash-linked local history. No native ticket keys, provider URLs,
tokens, comments, names, titles or raw source payloads are retained by this
ledger. A check from a prior register revision is identified as historical.
The read-only ledger never changes remediation state, infers reviewer or
owner identity, creates tickets or establishes incident clearance.

Export the ledger separately before removing a register with ticket history.
Deletion requires the exact ticket history pin in addition to the register
revision/history pin; the associated ledger is removed atomically with the
register. Prior backups and exports are not erased. Successor registers do
not automatically inherit ticket mappings. Caps are 200 mappings and 1,000
checks per register and 16 MiB encoded ticket ledger storage in total.

Dashboard-only POST actions under
`/v1/process-catalog/procurement-workforce/exchange/`:
- remediation-ticket-status: worklist_sha256.
- remediation-ticket-bind: worklist_sha256, expected_revision,
  expected_history_sha256, expected_ticket_revision,
  expected_ticket_history_sha256, finding_id, expected_finding_sha256,
  resource_ref, confirm_operator_mapping=true, confirm_local_retention=true.
- remediation-ticket-check: the same register, ticket and finding pins,
  plus confirm_source_read=true (no resource_ref in the check request).
- remediation-forget additionally accepts expected_ticket_history_sha256,
  required when a ticket ledger has retained events.

Existing session-only, no-store and JSON-text request bounds apply.
No automatic scheduled refresh or external write is introduced.


## Explicit Jira write handoff (default off)

Administrators may separately provision Jira Cloud write capability. This is
not enabled by a read-only connector grant. The local server requires:

- OWG_JIRA_REMEDIATION_WRITE_ENABLED=1
- OWG_JIRA_HOST and OWG_JIRA_CLOUD_ID (exact configured tenant)
- OWG_JIRA_REMEDIATION_PROJECT (one project key)
- OWG_JIRA_REMEDIATION_ISSUE_TYPE_ID (one issue type ID)
- OWG_JIRA_REMEDIATION_WRITE_TOKEN (separately provisioned OAuth token)

Supply the token through local secret configuration, never a request body,
repository file or browser. The site grant must explicitly include
write:jira-work; Jira also enforces its own project permissions and required
fields. This path does not provision or renew a write token, alter the
existing read connector, or automatically broaden its scopes.

After assigning a finding, open **Explicit Jira ticket creation and follow-up
comments**, load attempts and preview the desired operation. The preview
displays the exact destination and generated Atlassian Document Format
payload. The operator must explicitly confirm that payload. It contains
structural finding kind, opaque finding and worklist digests, and declared
local state, not raw evidence, actor names, case tags or source text.

Create submits one issue to the configured project/type; comment appends
one review note to an already bound, exactly observed issue in that project.
No external assignment, transition, resolution, or edit of prior comments is
performed. A newly created issue must be observed locally and explicitly
bound before using the existing read-only status synchronization or comment
path. The provider-reported issue key/comment ID is retained in the attempt
history; unlike the read-only ledger, this history can contain native Jira
identifiers. The plan preview also displays the destination.

The server checks exact register and ticket revisions, finding and preview
digests, tenant and grant before durably reserving an operation. It sends at
most one POST per retained operation reservation. Create is reserved once per
finding within a worklist; comments once per finding per register-history
revision. A new worklist is a separate scope; operators must avoid creating
duplicate external tickets across worklists. A timeout, malformed response,
non-201 result or crash is uncertain, not proof of failure. Inspect Jira and
the retained attempt history manually. No automatic retry or rollback is
provided, and a reservation cannot be cleared via ordinary register deletion.
Registers with write attempts therefore cannot currently be deleted through
the ordinary removal action. There is a global 400-attempt cap.

Session-only POST actions (standard JSON-text document envelope) under
/v1/process-catalog/procurement-workforce/exchange/:

- remediation-jira-preview: exact register/ticket/finding pins used by
  remediation-ticket-check (without confirm_source_read), plus operation
  (create or comment).
- remediation-jira-dispatch: same request, expected_plan_sha256, and
  confirm_external_write=true.
- remediation-jira-status: worklist_sha256.

No write establishes actual remediation, incident clearance or independent
business outcome. The implementation uses Jira Cloud REST v3 issue creation
and add-comment endpoints; required custom fields, project-specific policies,
and live tenant compatibility must be validated before enabling it.
