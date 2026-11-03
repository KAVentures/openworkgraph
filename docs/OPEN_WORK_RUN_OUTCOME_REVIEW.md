# Review an imported automation run against Open Work outcome evidence

This opt-in, **local dashboard-only** read-only comparison advances
[#247](https://github.com/KAVentures/openworkgraph/issues/247) milestone 4 and
[#264](https://github.com/KAVentures/openworkgraph/issues/264) Stage 7.
It complements, and never replaces, workflow mapping, Discovery Mode,
the event-log workspace, or existing agent handoff.

## What an operator can do

1. Import an automation run report through the existing local **Automation
   Monitoring** experience. The report is supplied by the automation and is
   **not independently verified** by OWG. Keep its `deployment_id` and
   `run_id` for the comparison; the latter is not echoed in the response.
2. Explicitly review a currently source-scoped Stripe Open Work case, its
   owner-declared expected outcome and any prerequisites, and retain its
   current `revision` and `edit_token`. The existing human-history retention
   policy must allow this review.
3. In a local authenticated dashboard session, POST
   `/v1/open-work/declarations/human-outcomes/compare-imported-run`:

   ```json
   {
     "work_item_ref": "owg:w:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
     "expected_revision": 1,
     "expected_edit_token": "<current owner edit token>",
     "confirm_historical_review": true,
     "confirm_run_comparison": true,
     "max_cases": 1,
     "deployment_id": "dep_0123456789abcdef",
     "run_id": "<exact imported run ID>"
   }
   ```

   Include the existing `Authorization: OWG-Session <local session>`
   header. For up to three selected prerequisites, use `max_cases` up to 4.
   A partially selected dependency graph is disclosed, not silently called
   complete.

The response provides a source-scoped opaque `owg:run:` reference, a
keyed run-snapshot fingerprint, the **automation-reported** run status and
outcome flag, the **human-assessed** case status, and the last **historical**
provider state from the independently HMAC-validated local evidence ledger.
It also includes the prerequisite-first operator action plan.

`provider_check_vs_reported_run_finish` is one of
`no_historical_provider_check`, `run_has_no_reported_finish`,
`before_reported_finish`, or `on_or_after_reported_finish`.
For an explicitly declared `stripe_invoice_paid_reported` goal, a
historical paid observation on/after the run's *reported* finish is flagged
separately. **It is not proof of payment today, settlement, customer success,
or that the automation caused anything.** The run timestamps and the
association between that run and case are supplied/selected by humans or
automation reports, not independently established.

## Prerequisite-first case outcome matrix

For `max_cases` 1–4, `case_outcome_comparisons` is an ordered array of
**exactly the admitted cases** (prerequisites before the selected root).
Each row retains the owner's declared outcome code, the latest separate
human assessment, historical provider state, number of provider checks and
disputes, the last historical check time relative to the imported run's
*self-reported* finish, review attention, unresolved prerequisite references
and existing operator action codes. The boolean
`narrow_historical_paid_observed_after_reported_finish` is meaningful
**only for an explicitly declared Stripe payment-state goal**. A human-only
goal never becomes a payment obligation because a generic provider check is
missing.

`cases_requiring_review` counts selected rows that need their own review
or have unresolved reviewed prerequisites. Thus a root with a retained
historical paid receipt can **still require review** when an earlier case
has no human assessment. `case_review_coverage` is `partial` when
`max_cases` omits declared prerequisites, and
`unscanned_prerequisite_count` reports the omitted count. An incomplete
selection is not evidence that omitted cases were completed or that the
root's dependency graph is satisfied. This is a **read-only review matrix**,
not an independently verified run-to-case match, fresh provider state,
customer completion, payment settlement or agent causality.

Existing root-level fields remain in the response for compatibility;
the new matrix adds per-prerequisite visibility without changing the
request or authorizing any action.

## Privacy and failure behavior

- Local authenticated dashboard only; no MCP or agent execution permission.
  No provider calls, automatic polling, new source permissions, business
  writes, cloud sync, new outcome assessments or ledger appends.
- Exact owner revision, source-account scope, review consent, retention,
  dependency selection and both private HMAC evidence histories are
  re-admitted. The imported run and histories are read twice to detect
  concurrent changes. These independent stores are **not an atomic
  cross-process snapshot**.
- No raw run ID, automation-provided outcome-source label, customer/native
  record IDs, Stripe account ID or credentials appear in the response.
  Opaque run references and fingerprints are keyed to the current local
  source scope and installation secret.
- Missing/invalid imported run, changed owner review, revoked history,
  changed source account, corrupted evidence and concurrent changes fail
  closed. This endpoint does not create an empty automation-monitoring
  database on a missing report.
- The association is **ephemeral and explicitly operator-selected**.
  No durable independently verified run-to-case edge is created. Durable
  attribution and real customer outcome validation remain open milestones.

## Validation

`pytest -q tests/test_open_work_case_run_outcome_review.py` covers actual
FastAPI/private SQLite + imported DuckDB report integration, session/consent,
revocation, source switch, absent/malformed runs, concurrent report change,
privacy and pre/post-run evidence timing. These fixtures are synthetic.
All repository-required native Ubuntu/macOS/Windows pytest, browser,
process-collaboration, scale, accessibility, starter, packaging and release
checks must pass on the exact PR head before merging. A real authorized
provider/customer pilot and held-out A/B/C study remain separate gates.
