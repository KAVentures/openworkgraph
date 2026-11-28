# R11 — Effective policy timelines and as-of process clarification

Open a saved revision in **Process catalog → Clarify this design against
multiple policy sources → Effective-dated policy timeline and change review**.
This workbench answers which supplied policy versions apply under explicit
reviewer-declared date/priority rules, and shows where a process design
requires renewed clarification when the selected sources change.

## Build a timeline

Choose a start date and exclusive end date (up to 1,830 calendar days).
Declare up to five required policy families and up to twenty source versions.
For each version enter a stable identifier, family identifier, title, exact
source text, inclusive effective-from date and optional exclusive
effective-until date. An empty end date means no declared end. Each family
must have one selected version for the overall bundle to be resolved.

Choose one resolution mode per family:

- **Refuse overlapping versions:** no active version means a gap, one means
  unique selection, more than one means ambiguous overlap.
- **Explicit priority:** every version must declare an integer priority from
  0–1,000. The unique highest active priority wins; a tie at the highest
  priority blocks selection. Lower-priority active versions remain visible
  as shadowed. OWG never infers priority from title, version number,
  recency, document content or organization role.

The engine splits the date window at all actual interval boundaries and
coalesces adjacent intervals only when all per-family diagnostics are
identical. It reports exact calendar-day coverage, gaps, ambiguous overlaps,
priority-resolved overlaps, version transitions, active and selected IDs,
and versions never active or never selected in a complete bundle.
A day with both an overlap and a missing family is counted as ambiguous in
the overall mutually exclusive totals; per-family counts show both issues.
Date boundaries use Gregorian dates, not timestamps, local timezone or
business-day calendars. Leap days count normally.

## Review a particular date

Select an as-of date in the timeline and optionally a previous comparison
date. If all required families resolve, the exact selected documents are
passed to OWG's existing deterministic clarification engine. See generated
questions, coverage limitations, exact cited statements and offsets. These
are fresh generated questions; prior human decisions are not imported into
the date review. An unresolved date never generates a partial clarification.

When both dates resolve, the existing source revision comparison provides
exact indexed statement-occurrence changes and renewed subject-review flags.
Question IDs are compared by exact generated evidence: newly generated,
no longer generated, same generated evidence and changed citations, prompts
or coverage. A disappearing question is **not** a resolved question.
Lexical matching and conflict detection remain bounded heuristics, not
independent policy interpretation.

Browse question pages using the exact review digest. Edits invalidate
prepared review state. Optionally load the selected exact source texts into
the ordinary clarification editor with explicit confirmation; this does not
record a decision or modify the process. The ordinary decision ledger
remains bound to model revision and exact document bundle, not to dates,
version-family metadata or priority declarations. A date-specific preview
must not be treated as date-specific authorization.

## Portable review and confidentiality

The input can be imported from a local JSON file. After a successful review,
download a metadata-only timeline manifest (titles, identifiers, dates,
priorities, document digests, exact coverage), or explicitly confirm
downloading reusable input **including full source text**. The latter is a
sensitive local file. Date-review page exports contain cited excerpts.
No full documents, timelines or new decisions are stored on the server by
these endpoints; the browser holds the draft in memory. Loading text into
ordinary clarification and later explicitly deciding a question follows the
existing cited-excerpt retention rules.

## Local API

Both endpoints require an authenticated local dashboard session and return
no-store responses. Strict JSON, maximum 1,536 KiB per request; 300,000 total
source characters, at most 60,000 per source.

- POST /v1/process-catalog/clarify/effective-timeline?workflow_id=...&revision=...
- POST /v1/process-catalog/clarify/effective-date-review?workflow_id=...&revision=...

Example timeline payload (replace digest with the exact saved revision):

    {
      "format":"openworkgraph.effective-policy-timeline.v1",
      "expected_content_sha256":"<saved model SHA-256>",
      "start_date":"2024-01-01",
      "end_date_exclusive":"2024-02-01",
      "families":[{"family_id":"refund","label":"Refund policy","resolution":"unique_active"}],
      "versions":[
        {"version_id":"v1","family_id":"refund","title":"Old policy",
         "text":"A refund above 500 EUR must get manager approval.",
         "effective_from":"2024-01-01","effective_until":"2024-01-15","priority":null},
        {"version_id":"v2","family_id":"refund","title":"New policy",
         "text":"A refund above 1000 EUR must get manager approval.",
         "effective_from":"2024-01-15","effective_until":null,"priority":null}
      ]
    }

Date-review requests include the full same input plus expected_timeline_sha256,
as_of, optional compare_to, question_offset (default 0), question_limit
(1–50, default 25), and expected_review_sha256 for subsequent pages.
The manifest is not a reusable input because it omits full source text.
Version order does not change the digest; family order is significant
because the selected document bundle is ordered.

The dates and priority are user declarations, not proof of legal authority,
actual effective dates, policy consistency, authenticity, completeness,
approval or execution permission. The feature makes no provider calls,
changes no saved model or ledger, and executes no business operation.

## Verification

Regression tests compare exact coverage against an independent daily oracle
over 120 randomized interval populations, cover boundaries and leap years,
gaps, priority ties, changed evidence, pagination, stale digests, minimized
exports, strict inputs and authenticated HTTP. Browser acceptance extends
the existing multi-source reconciliation journey with date-version setup,
timeline review, comparison and stale-review invalidation. Full native
and browser CI must still verify the exact PR head.
