# Open supporting cases for a label on one activity

After explicitly retaining a labeled event log, inspect an activity's
**Labels on this activity** table. Each known label has a **Browse cases**
action. When selected activity events have missing metadata, a separate
**Browse cases with missing … label** action opens those cases.

Selection requires the activity and label on the **same retained event**.
A participant appearing on another activity in that case is insufficient.
The same rule applies to team labels and source aliases. This verifies a
relationship between supplied fields, not real people, membership, source
identity, effort or responsibility. Unknown values remain missing labels,
not a shared unknown person.

The combined-map action selects across the current complete-case population.
An exact-variant activity action additionally pins the unchanged stored
`variant_id`; frequency rank or displayed variant position is never used as
identity. Existing applied date/activity/variant/participant/team cohort
filters remain AND conditions. Draft filter input changes do not replace the
population whose displayed support counts were computed.

Every matched case appears once even if several events satisfy the condition.
First/next case pages keep activity, label/missing selection, cohort and variant
scope. Opening a case shows its full event history, including other labels,
other activities and events outside the case's date-selection window. Nothing
is clipped into an apparent employee-specific path. The case-browser heading
identifies the selected activity, supplied label or missing field and optional
variant. Existing request guards discard stale case/event responses.

Preview-only logs display counts without full-retained-case actions; importing
or selecting an activity never silently retains data. Full case browsing uses
the existing separate retention choice. Scope and aliases remain limited to
the selected snapshot, with no source API call or join to a roster, another log,
observed history, workflow ownership or execution policy.

## API

The existing dashboard-only `GET /v1/process-mining/datasets/cases` accepts:

- Required `activity` when applying label attribution.
- `label_dimension`: `participant`, `team` or `origin`.
- Exactly one of a bounded `label_value` alias or `label_missing=true`.

Known values must use the dimension's existing import-scoped alias format.
Missing/partial constraints, unsupported dimensions and native/arbitrary label
values are rejected with generic errors. A dimension must actually have been
retained in that snapshot. A nullable column introduced by a schema upgrade
does not establish that label collection occurred; requesting missing values
for an uncollected dimension is rejected rather than returning every case.

A matching response adds `activity_label_filter`,
`same_event_label_match_required: true` and
`label_is_verified_identity: false`. Existing `cohort_filters`, case ordinals,
key aliases, cursors and metadata remain unchanged. Source/key provenance and
complete event browsing are preserved. An independently supplied transition
condition remains another whole-case AND constraint, not a claim that the label
belongs to that transition occurrence.

Column names use a fixed dimension allowlist; activity/label values are SQL
parameters. All selected fields match inside one event-row `EXISTS` predicate.
Repeated occurrences cannot multiply outer case rows. Dataset metadata and
case rows, and each case/event page, are pinned to one SQLite read snapshot so
concurrent deletion cannot create partial histories. New requests see the
deleted/expired dataset as unavailable. Existing local dashboard
session requirements, expiry/deletion, query bounds and `Cache-Control: no-store`
apply; ordinary AI bearer credentials cannot query retained labeled histories.
No new tables, model/source calls, capture or permission is introduced.

Verification covers labels on the wrong activity in the same case, repeated
matching events, missing-versus-uncollected fields, source aliases, exact stored
variant scope, whole-case date/team conditions, full event histories, pagination,
invalid/partial/injection-shaped constraints, sanitized errors, no writes,
concurrent deletion during case/event reads and
API authentication. Real browser flows exclude a participant who appears only
on another step, compare full-log and variant source-case support, preserve
complete source-tagged histories and retain existing privacy/mobile checks.
