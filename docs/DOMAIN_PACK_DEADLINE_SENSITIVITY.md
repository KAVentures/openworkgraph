# Domain-pack deadline sensitivity (R23)

The local event-log workspace can compare an installed domain pack's
elapsed-time targets without changing its configuration, duplicating logs
or reinstalling metrics.

## Review workflow

1. Install a mapped domain pack in the usual way.
2. Expand **Compare alternative pack deadlines** in the domain-pack area.
3. Load installed packs, choose a pack and one declared parameter, and
   enter one to eight distinct alternatives in its declared unit.
4. Review the installed baseline and alternatives for each affected mapped
   response-within rule. All rows use the same retained source population.
5. Inspect cases by rule, alternative and classification. Export the
   aggregate review as JSON if needed.

The current installed value remains unchanged. This is a read-only
diagnostic, not a way to relax a policy or authorize execution.

## Exact case semantics

Each trigger occurrence requires a later matching response, ordered by
recorded timestamp and event ID. The earliest later response is selected
for each trigger, including repeated triggers. A case satisfies a
threshold only if every trigger has a response and its largest elapsed gap
is within the threshold (equality satisfies the deadline).

For each alternative the review separates:

- Cases with no trigger (nonactivated).
- Activated cases missing at least one later response.
- Cases with all responses recorded but at least one late response.
- Satisfied and violating activated cases.
- Cases newly satisfying or newly violating the rule relative to the
  installed threshold.

Missing later responses remain violations at every tested threshold. They
may instead reflect partial retention, ongoing work or incomplete source
history; the review does not certify business outcomes.

A single case can appear in more than one rule or object-type population.
Do not sum rule counts as unique affected business cases. The review does
not infer causality or forecast behavior under a changed deadline.

## API and evidence

Dashboard-session-only POST:

`/v1/event-logs/{log_id}/domain-packs/{pack_id}/deadline-sensitivity`

Example request:

```json
{"parameter":"payment_terms_days","values":[5,22,60]}
```

The response contains the exact installed configuration token, resolved
definition digest, retained event/object/link digest, baseline and
alternative thresholds, affected rule indices, omitted unmapped rules,
and a deterministic review-basis digest.

For evidence, repeat the same request with
`expected_basis_sha256` and an `evidence` object containing
`rule_index`, `value`, `state`, `after` (null initially or the
returned cursor) and `limit` (1–100). The four evidence states are
`newly_satisfied`, `newly_violating`, `missing_response` and
`late_complete`. Evidence contains exact retained case IDs, trigger
counts, unanswered counts and worst observed response gaps. Share only
where authorized. A changed installation or source requires fresh review.

The review refuses active label mappings or hidden activities, unsupported
parameters, invalid alternative values, duplicate effective thresholds,
more than eight affected mapped rules, and sources above 500,000 retained
event/object/link rows. Results are not sampled. The workspace lock
keeps the review and evidence calculation consistent; no persistent
review records or business data are written. No provider calls occur.

## Verification

Regression coverage includes the existing order-to-cash fixture,
threshold boundaries, missing responses, exact evidence pagination,
changed installations and appended sources, invalid input and source
budgets, strict HTTP authorization, a deterministic randomized oracle for
repeated triggers and tied timestamps, comparison with the existing
compliance engine, and a Chromium dashboard scenario. Synthetic tests
do not establish real-customer acceptance or enterprise-scale performance.

## Observed deadline feasibility

Every reviewed rule also reports the exact minimum whole-second threshold for fixed 50%, 80%, 90%, 95%, 99% and 100% observed satisfaction targets among activated cases. The required case count is the ceiling of target times activated population. All answered occurrences must meet the threshold; ties are handled together and fractional-second gaps round upward. The output respects the declared parameter range and distinguishes targets impossible because of missing responses, targets outside the range, and cases where no rule is activated. Each frontier concerns a single rule, not a simultaneous guarantee across rules sharing a parameter. These are descriptive results, not recommendations to change a deadline or verified future service levels.
