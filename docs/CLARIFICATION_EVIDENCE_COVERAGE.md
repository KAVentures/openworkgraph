# R11 — Evidence coverage in multi-source policy clarification

The saved process catalog now reviews a wider bounded candidate set for
possible policy contradictions, rather than searching only the first five
citations. This improves the guided knowledge discovery path without
introducing automatic policy interpretation or executable authority.

## Operator experience

Open a saved revision and supply the exact policy/SOP documents as before.
The clarification panel now reports omitted statements, per-subject candidate
coverage and the number of possible conflicting pairs displayed.

A contradiction outside the former five-citation cutoff is brought into the
display when detected. Both sides of at least one conflicting pair are
shown. If not all detected conflict witnesses fit the bounded citation
contract, or a source/subject budget is exhausted, confirmation and revision
are unavailable for affected questions. Narrow the supplied document set to
the relevant sections or defer the question. The server recomputes these
checks independently and refuses direct confirmation of incomplete review.

Rejecting or deferring a question records the human decision but leaves it
visible as unresolved. A source pair affecting multiple rules/activities is
reviewed for each distinct subject; deciding one does not endorse another.
Question identity includes the analysis version and subject type. Decisions
under the previous algorithm remain available in history but do not suppress
the revised questions, even for an unchanged source bundle.

## Source fidelity and limits

- The existing maximum of five documents and 60,000 characters per document
  remains. At most 1,500 qualifying statements are indexed across the bundle.
  Overlong statements (>600 characters) and statements omitted by the budget
  are counted per document. A long statement is not silently clipped and
  presented as an exact complete citation.
- Citations identify source index, title and exact character offsets. Even
  identical documents supplied twice have distinct source identities within
  the ordered bundle. Source text and selected ledger excerpts retain their
  existing local handling.
- Decimal points between digits no longer divide statements. English-style
  decimal/grouped thresholds are compared without losing fractional digits
  or treating equivalent numeric forms as different values. Unit names are
  normalized for percent/day/hour. Multiple distinct thresholds in one
  unit are not reduced to an arbitrary last value.
- A subject can have at most 80 matching candidates checked for conflicts.
  The bounded five-citation display prioritizes conflict witnesses. The
  absence of a detected contradiction does not establish policy consistency.
  If all candidate pairs cannot be checked or displayed, review is blocked
  rather than implying complete evidence.
- Matching and contradiction heuristics remain lexical. Short fragments,
  language variation, cross-sentence context, complex conditions, effective
  dates, currency conversion and legal/policy authority are not fully
  interpreted. No remote model is called. Full source text is not retained
  by this review; selected citations and human answers continue to be retained
  locally in the existing ledger.

No model, policy, permissions, agent tool grants or execution approval are
changed automatically. A confirmed unconfirmed-rule question may still
propose a new process model revision through the existing separate save
workflow.

## Regression coverage

Tests exercise decimal and grouped values, exact citation offsets and distinct
source identities, conflicts beyond the original top five, bounded source and
subject scans, non-suppression of deferred/rejected questions, subject-specific
decisions, direct HTTP confirmation refusal, and the actual browser review
flow. These tests establish synthetic contract behavior, not independent
accuracy against real policies.

Related: [#370 R11](https://github.com/KAVentures/openworkgraph/issues/370).
