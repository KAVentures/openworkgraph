# Service governance domain packs (R23)

Six versioned packs are available in **Domain packs** on a saved
event log. They use the same mapping, saved metric library, compliance engine,
reference-model checks, local authorization, installation digest and revision
history as the existing packs. They add no source connectors or permissions.

## IT change control — `change_control`

Scope to a selected population of planned change requests, each identified by
one stable local case alias. Map request, assessment, recorded approval,
recorded implementation and validation. Rework, rollback and closure are
optional.

- Metrics: request-to-assessment, assessment-to-approval,
  approval-to-implementation, implementation-to-validation, total elapsed
  time to validation, rework and rollback event counts.
- Review rules: assessment before approval, approval before implementation,
  implementation before validation or rollback, validation before closure,
  elapsed assessment/validation targets and repeated implementation.
- Request/approval separation of duties compares recorded resources; unknown
  resources do not establish independence.
- Reference model: a single planned change with optional closure. Emergency
  and pre-approved changes, phased rollout and rejected requests may need
  separate reviewed process models.

Defaults: 48 elapsed hours for assessment; 24 elapsed hours from implementation
to validation. Neither is a production change policy or approval.

## Customer complaint resolution — `customer_complaints`

Map intake, acknowledgement, investigation, recorded resolution and recorded
response. Escalation, reopening and closure are optional.

- Metrics: time to acknowledgement, resolution and response, resolution-to-response
  time, escalation and reopening counts.
- Review rules: expected earlier steps, acknowledgement/response targets,
  reopen events and response before closure.
- Reference model: one example complaint lifecycle with optional closure.

Defaults: 24 elapsed hours to acknowledge and 14 elapsed days to respond.
A response event does not verify delivery, resolution quality, customer
satisfaction or the merits of the complaint. Reopenings may be appropriate.

## Data access request operations — `data_access_requests`

Map intake, recorded verification, recorded search completion, response review
and recorded response. Clarification, extension, reopening and closure are
optional.

- Metrics: elapsed intake-to-verification, verification-to-search,
  search-to-review, intake-to-response and counts of clarification,
  extension and reopening events.
- Review rules: expected sequence, configurable elapsed verification/response
  targets and reopening evidence.
- Reference model: a simple operational sequence with optional closure.

Defaults: 72 elapsed hours for verification and 21 elapsed days for response.
These are illustrative operational review targets, not statutory deadlines.
Clarifications and extensions do not suspend or extend the measured interval.
Determine applicable obligations separately; the pack does not verify legal
compliance, requester identity, exemptions, scope, search completeness or
response delivery.

## Set up and inspect

1. Import a CSV with stable pseudonymous case aliases, literal activity
   labels and timezone-qualified timestamps. If authorized and necessary,
   add a minimized resource label for change-control separation-of-duties.
   Do not import complaint/request text, identity documents, response payloads,
   customer names, credentials or other sensitive contents for these packs.
2. Open the saved log's Domain packs section. Select the relevant pack and
   map its object role to the CSV's `case` type (or a selected object type in
   an OCEL log), then map each required activity to its exact source label.
3. Review illustrative thresholds; configure the targets appropriate to the
   selected log. Leave optional activities unmapped when unavailable. The
   affected definitions are reported as not applicable.
4. Install and run the report. Compare metric coverage, compliance examples
   and the reference model. These are descriptive observations; use the
   saved log's case explorer to inspect the underlying evidence.
5. Reconfigure or uninstall using the existing installation lifecycle. The
   pack owns only its own saved metrics, and cannot replace user-owned ones.

Example CSV for the change-control happy path:

```csv
record,event,time,actor
change-001,Requested,2026-01-01T09:00:00Z,role-a
change-001,Assessed,2026-01-01T10:00:00Z,role-b
change-001,Approved,2026-01-01T11:00:00Z,role-c
change-001,Implemented,2026-01-02T09:00:00Z,role-d
change-001,Validated,2026-01-02T10:00:00Z,role-e
```

The existing REST catalog lists each pack and its versioned definitions. See
[Domain packs](DOMAIN_PACKS.md) for routes, local session requirements and
installation semantics.

## Validation and limitations

Synthetic acceptance fixtures exercise each pack with five distinct cases:
an exact reference sequence, a late first response, a truncated case, an
out-of-order case and optional extra activities. Tests check literal-label
mapping, report evidence, revised thresholds, missing optional mappings,
required role rejection, unique metric names and uninstall isolation.

The fixtures are not provider exports and establish neither live-system
compatibility nor actual policy adherence. All timing measures include waiting
and nonworking hours. Tied timestamps use existing retained event ordering;
missing events are not proof that an action failed to occur. The underlying
rule and model results must be interpreted against the actual coverage and
declared case population.


## Warehouse outbound fulfillment — `warehouse_fulfillment`

Map release, pick complete, pack complete, recorded shipment and carrier
handoff. Stock exceptions, repacking and closure are optional. Seven metrics
measure the five elapsed intervals and two exception counts. Rules review
precedence, illustrative 24-hour pick and 48-hour handoff targets, and repeated
shipment events. Scope to one shipment or fulfillment record; multi-parcel
orders and parallel operations need separate reviewed models. Recorded carrier
handoff is not proof of physical custody or successful delivery.

## Sales quote approval — `sales_quote_approval`

Map request, preparation, commercial review, recorded approval and issue.
Revision, return for review and closure are optional. Seven metrics include
request-to-preparation, review-to-approval, approval-to-issue and counts of
revisions and returns. Illustrative targets are 24 hours to prepare and 72
hours to issue. Review recorded separation of duties between preparation
and approval and repeated issuance. This does not verify pricing authority,
quote acceptance, contracts or revenue.

## Payment dispute operations — `payment_disputes`

Map receipt, triage, evidence preparation, recorded response submission and
recorded outcome. Escalation, reopening and closure are optional. Seven
metrics include intake-to-triage, evidence-to-response, response-to-outcome
and counts of escalation and reopening. Illustrative targets are 24 hours
to triage and seven elapsed days to submit a response; they are not payment
network or statutory deadlines. No actual recovery, settlement, fraud or
financial action is inferred. Do not import payment details or evidence
payloads. Scope to cases expected to have a submitted response.

All three use existing catalog discovery, exact mappings, local compliance
review, reference models, parameter overrides, installation digest and
uninstallation semantics. Each synthetic acceptance case exercises an exact
sequence, late response, missing response, reversed order and extra activity,
plus optional mappings and user-owned metric preservation. No external
provider or live-system acceptance is claimed.
