# Signed run snapshot progression and conflict review (R18)

The signed report channel now protects previously retained run evidence against
delayed, contradictory, or truncated replacements. Authentication and a valid
sequence number establish the channel submission, not the freshness of the
runtime's underlying snapshot. A late arrival can describe an older run.

## Unattended ingestion rules

The monitoring importer checks all incoming run IDs against existing rows for
the same deployment, inside the same transaction used for the import.

- A new run ID is accepted only if its reported step chronology is consistent: steps must occur at or after the run start, in nondecreasing order, and no later than a supplied finish time.
- An identical normalized snapshot is accepted without rewriting its stored
  rows. An identical previously unhashed run can acquire the exact declared
  specification attribution when the report supplies it.
- A running snapshot can advance to a terminal snapshot or extend its
  recorded steps, retaining its start identity, known specification claim,
  source, prior non-null reported verification claim and existing step order.
- A last pending step reported as handed to a human may acquire a later
  disposition on the same step identity only for the declared
  `customer_execution_journal` source, whose generated projector uses that
  convention. Other reporting sources cannot rewrite a human handoff through
  the unattended path. The entire advanced timeline must remain consistent,
  including steps appended after the pending disposition.
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

The channel inventory and monitoring dashboard also show the highest 50
retained signed sequence dispositions, including original HTTP status and
receipt time. Sequence IDs are decimal strings to avoid browser integer
rounding. The view omits bodies, digests, raw error details and secrets;
it is not an exhaustive history beyond the bounded replay ledger. A revision
conflict refusal is persisted before attempting secondary incident recording.
If the incident ledger is full or unavailable, the original refusal remains
reviewable in the channel inventory and an exact resend cannot import it.

New malformed chronologies are refused as atomic revision conflicts, including when the same submission contains otherwise valid new runs. An identical legacy snapshot remains idempotent; old retained data is not retroactively repaired. The source label remains a sender declaration, not proof that a specific projector produced the snapshot.

The two extracted chronology/progression functions were checked locally against 7,775 exhaustive timestamp combinations and 250 pending-resolution combinations (8,025 total). This is targeted algorithm validation, not the full signed HTTP/database integration suite.
