# Controlled router pilot evidence review

This workbench accepts customer declarations about an externally authorized
controlled pilot. It does not allocate production traffic, execute a case,
contact a provider, verify source evidence, or perform rollback.

## Freeze

The upstream object contains discovery, holdout, change_plan, captures,
request (the exact router-change decision request), and receipt. The
positive change referral is recomputed. The freeze bundle contains upstream
and request, with:

- pilot_id, coordinator_alias, window_start_ms and window_end_exclusive_ms
  (UTC Unix milliseconds), allocation_seed_sha256;
- cases: unique opaque case_token_sha256 and reviewed_category for each
  distinct business case. Use locally generated high-entropy tokens, never
  identifiers or unsalted hashes of personal data. Each case appears once.
- rollback_target_configuration_sha256 (must equal the incumbent),
  external_controlled_pilot_approval_sha256 (declared reference to separate
  external approval, not proof of authority);
- confirm_unique_cases_no_duplicate_execution,
  confirm_non_overlapping_cost_measurements and confirmed, all true;
- policy: minimum_cases_per_arm_per_category (default 5),
  minimum_challenger_success_basis_points (9000),
  minimum_success_change_basis_points (0),
  maximum_challenger_fallback_basis_points (1000),
  maximum_challenger_p95_elapsed_ms (null),
  require_complete_costs and require_nonnegative_variable_savings (true).

Within each declared category, sort by a seeded hash and alternate arms
with a seeded starting offset. Each positive-volume category must be
represented, and each represented category meets the minimum per arm.
The returned plan is deterministic and should be retained before the
external pilot; seed, category provenance and chronology are not verified.

## Review

Bundle: upstream, pilot_plan, captures. Captures contain format
openworkgraph.procurement-router-pilot-ledger.v1, pilot_plan_sha256, as_of_ms
and observations, one per scheduled slot at most. Each observation contains
slot_id, case_token_sha256, observed_at_ms, actual_router_configuration_sha256,
actual_destination (manual/agent/null), actual_candidate_id and
actual_candidate_configuration_sha256 (both null except agent),
status (completed/failed/withdrawn/unresolved), business_outcome
(success/failure/unresolved/null), critical_incident, forbidden_action,
used_fallback (booleans or null), direct_cost_microunits,
human_person_ms, elapsed_ms (integers or null), and source_evidence_sha256
(unique declared reference). Observations must be in the frozen window and
not after the snapshot cutoff. Null is unknown, not zero or false.

The all-attempt report preserves missing cases; misattributed cases cannot
establish intended-version success or complete costs. Known safety signals
still count even when attribution fails. Direct variable cost excludes
human labor, valued at the frozen routine rate. This is not annual ROI.
Each stratum compares separate arm success-rate and mean-cost bounds;
no statistical or causal inference is made. Expansion review requires a
closed window, terminal cases, exact declared attribution, every category
gate, and zero known or possible critical/forbidden events, manual leakage
and unexpected routing. Known adverse signals or established category
policy failure trigger rollback review; unresolved evidence leads to hold.
The same incumbent target is retained throughout.

## Decision and interfaces

Add request with disposition (refer_for_expanded_pilot_review,
refer_for_rollback_review, or hold), distinct reviewer_alias,
expected_pilot_plan_sha256, expected_report_sha256 and confirmed=true.
Expansion is refused unless every required check passes. A reviewer may
always refer for rollback or hold. No deployment changes are made.

The local saved-process dashboard and authenticated no-store
POST /v1/process-catalog/procurement/{operation} support router-pilot-freeze,
router-pilot-review, router-pilot-decide. Freeze/decision require the latest
saved revision. Review supports historical revisions. CLI uses exclusive
private output directories:

```sh
python -m shared.agent_procurement_router_pilot router-pilot-review --process-export process.json --bundle pilot.json --out-dir reviewed
```

All inputs are declarations. Hash pins detect changes relative to retained
artifacts, not authenticated production execution, actual traffic assignment,
customer identity, independent approval or real-world causal improvement.

After a passing pilot and separate expansion referral, see [multi-window monitoring](AGENT_PROCUREMENT_ROUTER_MONITOR.md) for a finite cumulative schedule and retained checkpoint comparisons.

Positive expansion and subsequent monitor continuation require resolved business outcomes and explicit fallback observations for every enrolled case, independently of the configurable aggregate success and fallback rate thresholds. A terminal completed attempt with an unresolved or absent outcome remains insufficient. These are completeness requirements for declared evidence, not independent outcome verification.
