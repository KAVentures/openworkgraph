# Separate outcome corroboration

The monitoring producer's statements are not independently verified. An
operator may retain a separate reviewer declaration from a different source
for each closed-window case. This is a reconciliation workflow, not an
authenticated source-system connector or proof of human reviewer identity.

From the retained journal workbench, choose a closed window, prepare a blind
manifest and send it to the designated reviewer through an approved channel.
It contains only unreviewed opaque slot/case identifiers and exact head/report
pins, no producer outcomes, router assignments, categories, or arm labels.
Reviewers should obtain outcome and safety evidence from their own approved
source. A reviewer returns a JSON array with one row per resolved case:

```json
[
  {
    "slot_id": "<opaque slot SHA-256>",
    "case_token_sha256": "<opaque case SHA-256>",
    "business_outcome": "success",
    "critical_incident": false,
    "forbidden_action": false,
    "source_reference_sha256": "<distinct opaque evidence reference>"
  }
]
```

All three fields must be explicitly resolved; omit unresolved cases and
submit them later. The source reference must be distinct from retained
observation, initial pilot and previous corroboration references across
readable local journals. Distinct reference hashes and aliases cannot prove
real independence. The reviewer's alias must differ from the coordinator and
all prior producer grant aliases in that journal. Review batches are immutable;
the same slot cannot be overwritten. Incorrect declarations require an
external correction and a new reviewed plan, not silent replacement.

The workbench pins the currently displayed head/report and requires explicit
confirmation. If the journal changes, reload and obtain current pins. The
read-only manifest is not a credential and no producer receives review access.

A known discrepancy in outcome or critical/forbidden flags, reviewer-declared
safety incident, or subsequently detected same-journal source/attributed
producer alias overlap creates a rollback-review signal and blocks a positive
continuation referral. Missing observations or unreviewed cases are unresolved.
Failed producer attempts are interpreted as failed outcomes, consistent with
pilot evaluation. The original observation and review declarations are never
rewritten; the derived comparison is recalculated against later observations.

Optional **Require complete outcome corroboration** is a one-way, head-pinned
journal policy. It requires a match for every enrolled case before a positive
continuation referral, in addition to all existing monitoring gates.
Otherwise incomplete corroboration alone does not block the earlier gate;
conflicts always do. This applies to retained journal decisions, not ephemeral
offline pilot reports. No automatic rollback or execution is performed.

Dashboard-only API operations router-corroboration-require, -manifest, and
-record use the existing exact JSON-text envelope with workflow_id/revision
query. Require accepts monitor_id, expected_head_sha256 and confirmed=true.
Manifest adds window_id and expected_report_sha256. Record additionally
requires reviewer_alias, confirm_separate_source_review=true, and rows.
All require current process revision, local dashboard session and explicit
operator context. No remote verification or financial outcome claims are made.
