# Customer execution journal batch review and reconciliation (R18)

Generated Agents SDK projects can now project a bounded explicit selection of
execution journals into one standard OWG automation run report. Previously the
monitoring exporter accepted one journal at a time, and it did not recognize
the emergency-stop phases emitted by the customer runtime.

## Review before import or delivery

From the trusted extracted runtime project, select journals for the same exact
exported workflow specification and intended deployment:

    python monitor.py --journal execution-journal/FIRST.jsonl --journal execution-journal/SECOND.jsonl --deployment-name "Customer workflow" --review-only --output batch-review.json

The review is a separate local diagnostic artifact, not an importable run
report. It shows one-based selection indices, exact journal-byte digests and
sizes for successfully read complete files, opaque execution IDs, reported run
status and reconciliation flags. It does not contain paths, tool arguments,
raw journal contents or provider result values.

Investigate refused selections and uncertain tool effects before any further
business activity. Do not retry a write simply because its result is missing.

Export the same selection after reviewing it:

    python monitor.py --journal execution-journal/FIRST.jsonl --journal execution-journal/SECOND.jsonl --deployment-name "Customer workflow" --output run-report.json

The existing single-journal command remains supported. The exported document
uses the unchanged automation-run-report.v1 format, accepted by the existing
local monitoring importer and signed report sender. Compare its canonical
document digest with the review's report_sha256 if the exact reviewed snapshot
must match: journals may be appended between two commands. Prefer finalized
journals for delivery. A later import of the same run ID replaces its retained
report; importing an older running snapshot over a final report can therefore
lose newer evidence. The exporter does not itself import or deliver anything.

## Exact selection and bounds

- Explicitly select 1–100 files, no directory sweep. Maximum 8 MiB and 10,000
  records per file, 64 MiB total input budget, and 2 MiB serialized run report
  (the existing signed sender limit).
- Each file is read once per invocation. Its exact bytes are hashed and used
  for validation and projection; torn trailing records, wrong spec, malformed
  records and duplicate execution IDs are refused.
- Both entries of a duplicate-ID pair are refused, even when their bytes are
  identical. No invalid, duplicate or over-budget selection silently drops
  runs or produces a partial importable report. The review can still explain
  which selected indices failed. Review mode exits 2 for an unprojectable
  selection, 0 for a projectable one; the normal export refuses invalid input.
- New output files are created exclusively, mode 0600 on POSIX; Windows
  inherits ACLs. No source journal or prior report is overwritten.

## Stop and interruption semantics

The monitor recognizes both operator-stop phases emitted by the existing
runtime. A reported stop blocks later attempts and cannot coexist with a
declared successful finish. The runtime now writes an explicit terminal
handoff when a stop prevents all initial actions, preserving both the stop
event and the terminal record. In-flight effects are not rolled back or
assumed cancelled. Historical stop-only journals without a terminal record
remain running snapshots requiring review.

The local review flags pending attempts, uncertain tool results, precondition
requests lacking a recorded disposition, missing terminal records, reported
stop and failure/handoff. It does not infer that any action actually reached
the provider or that a remote process is still active. A reported success
is not independent verification of a business outcome.

## Validation

The existing generated monitoring tests now exercise actual fake-tool
executor journals from both early and mid-run emergency stops, duplicate
selection refusal, interrupted attempts, batch bounds, private output
projection, CLI replay and compatibility with the signed sender validator.

Full exact-head CI is required before claiming the implementation validated.
Real-provider reconciliation remains separate, with independent source checks
and explicit customer authorization. This tool never grants execution
permission, replays writes, contacts a provider or performs remote cancellation.

Related: [#370 R18](https://github.com/KAVentures/openworkgraph/issues/370).

## Compare two snapshots of the same execution

Retain the original reviewed journal in a separate local file before comparing it
with a later candidate:

    python monitor.py --journal candidate.jsonl --compare-to original.jsonl --deployment-name "Customer workflow" --review-only --output snapshot-comparison.json

This comparison requires exactly one candidate, explicit review-only mode,
and two valid exact-spec journals. Each input is bounded and read once. The
report distinguishes identical snapshots, exact append-only extensions,
earlier snapshots, divergent histories and different execution identities.
Only identical bytes or an exact byte-prefix extension pass (exit 0); other
valid comparisons produce a diagnostic report and exit 2. Malformed inputs
are refused. Reformatting the same JSON is not exact byte continuity.
The comparison includes both journal hashes, reported states, additional
record count when continuity holds, and candidate reconciliation flags.
Neither file is modified, imported or executed. A matching prefix establishes
continuity relative to the selected original, not authenticity or truth.
Do not retry a possibly completed write based on this comparison alone.

Local targeted validation: 2,472 synthetic snapshot comparisons plus one torn
input check passed using the exact extracted comparison function (length
1,989 characters; FNV-1a checksum 615519281), with simulated journal projection
helpers. Full generated-runtime regression and CLI execution remain pending CI.
