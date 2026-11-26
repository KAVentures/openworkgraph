# Cross-workflow conditional workforce allocation

A bounded exact optimizer over explicitly selected current saved workflows.
It does not scan all workflows, provision agents, spend money or claim
realized savings. Open the Process catalog's allocation section or POST
the existing JSON-text envelope (document: original JSON text) to
/v1/process-catalog/procurement-workforce/allocation with a local
dashboard session. The response is no-store and nonpersistent.

## Request

Supply currency (three-letter uppercase code),
maximum_first_year_gross_ai_cost_microunits, maximum_annual_ai_cases,
maximum_annual_additional_person_ms, both
confirm_non_overlapping_workloads_and_costs and confirmed set to true,
and workflows (1–8). Each workflow contains workflow_id, revision,
expected_operating_report_sha256, operating_bundle (the complete
operating-cost plan/captures/request), and portfolio_ids (1–8 explicit
conditional portfolios from that report).

Every operating report is recomputed against the exact saved revision.
Stale revisions, changed report pins, mixed currencies, overlapping
declared suite case hashes and unknown portfolio IDs refuse the request.
The decoded JSON document is limited to 8 MiB. The browser uploads the original text so large integer amounts are not rounded or duplicate keys silently discarded. The input reports are not persisted.

## Eligibility and constraints

One portfolio or defer per workflow. Only portfolios with passing declared
quality/trial gates, positive lower-bound first-year scenario savings,
complete financial and capacity evidence and positive AI case volume can
be chosen. Deferral is always an option, but does not establish safety or
cost of the manual baseline.

Gross first-year AI cost includes candidate and routing annual fixed and
one-time costs, routing variable cost, and the upper cost bound for
assigned AI paths. Retained manual paths are excluded from this incremental
envelope; it is not a company-wide total budget. All charges are included
per workflow, even where providers are shared. No netting against
forecast savings or shared discounts.

Additional person-time charges max(0, -lower released person-time)
for each selected workflow. Released capacity elsewhere cannot offset
this constraint: skills, availability and transferability are unverified.
The AI case count is the declared annual volume assigned to AI.

The exact finite search enumerates up to 200,000 eligible combinations;
larger requests are refused rather than silently approximated. Maximize
sum of first-year savings lower bounds, then minimize gross cost, additional
person-time, AI cases, and lexicographic option IDs in workflow-ID order.
All arithmetic is rational; JSON contains numerator/denominator strings.
The response includes each option, exclusion reasons, selected scenario,
resource headroom (AI-case headroom as a decimal string), search counts and report pins. No external authority
or actual production outcome is established.
