# Signed run snapshot progression and conflict review (R18)

The signed report channel now protects previously retained run evidence against
delayed, contradictory, or truncated replacements. Authentication and a valid
sequence number establish the channel submission, not the freshness of the
runtime's underlying snapshot. A late arrival can describe an older run.

## Unattended ingestion rules

The monitoring importer checks all incoming run IDs against existing rows for
the same deployment, inside the same transaction used for the import.

- A new run ID is accepted normally.
- An identical normalized snapshot is accepted without rewriting its stored
  rows. An identical previously unhashed run can acquire the exact declared
  specification attribution when the report supplies it.
- A running snapshot can advance to a terminal snapshot or extend its
  recorded steps, retaining its start identity, known specification claim,
  source, prior non-null reported verification claim and existing step order.
- A last pending step reported as handed to a human may acquire a later
  disposition on the same step identity, as in the generated customer
  execution journal projector.
- An existing terminal snapshot cannot be replaced by a different signed
  snapshot, even if the incoming channel sequence is higher. Shortened
  histories, altered retained steps, regressed timestamps and conflicting
  existing specification attribution require review.

The entire signed report is refused with HTTP 409 if any run conflicts; no new
run or revision from that batch is committed. A bounded channel incident of
kind run_revision_conflict contains reason counts, not submitted run content.
The signed channel records the refused sequence; exact resends receive the
same recorded refusal outcome. A received but refused sequence is no longer
a missing delivery. After review, submit a corrected report with a new sequence.

Successful signed responses expose revision_counts (new, identical, advanced,
attribution_enriched). runs_imported counts actually written run rows; version
inventory counters still count submitted runs in accepted reports, not unique
retained runs. An exact channel-sequence replay continues to return its existing
duplicate receipt.

## Human corrections

The existing explicitly confirmed, authenticated local dashboard import retains
its manual replacement semantics for reviewed corrections. This is an operator
decision, not a permission granted to a reporting channel. Review the retained
run and the source evidence before replacing it manually. No automatic retry,
business action, provider read or remote cancellation is performed.

## Limits

The guard compares normalized reported snapshots. It does not prove which one
is factually true, establish provider state, or authenticate business outcomes.
Terminal correction is intentionally conservative, including changes to a
reported outcome or finish time; a person may need to review a legitimate
correction. Existing imported data is not retroactively repaired.

The check uses bulk queries only for overlapping run IDs, under the same
transaction as mutation; there is no new schema or migration. Regression tests
cover late arrival after completion, duplicate receipts, atomic batch refusal,
step and identity regressions, legacy hash enrichment, manual correction and
the signed HTTP route.

Roadmap: [#370 R18](https://github.com/KAVentures/openworkgraph/issues/370).
