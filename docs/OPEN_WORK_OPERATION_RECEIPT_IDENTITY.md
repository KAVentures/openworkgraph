# Source operation receipt identity consistency

This narrow #247 Milestone 4 / #264 Stage 7 regression guard strengthens the
existing `server/open_work_outcomes.py` operation receipt. It does **not**
introduce a new case graph, provider adapter, permission, or execution path.

A verified operation requires **both** a retained, unambiguous observed
`record_type` for the expected provider/object kind and a fresh, explicitly
source-reported `source_evidence` receipt from that same provider. A missing,
non-string, or different `source_evidence.provider` returns
`availability=unknown`, `reason=provider_receipt_identity_conflict`, and
`operation_verified=false`. If the source receipt supplies an `object_type`,
it must also equal the observed record kind. Older Stripe receipts do not
supply `object_type` and remain supported, because the retained record graph
already establishes their kind; Jira and GitHub supply the field. When a
source receipt additionally supplies `source_evidence.resource_ref`, it must
match the exact requested opaque `owg:r:` reference. A conflicting, null,
malformed or differently scoped supplied reference fails closed to
`unknown/provider_receipt_identity_conflict` without echoing the source
value. The current GitHub/Jira/Stripe adapters omit this optional field, so
their existing authenticated read contract is unchanged; its absence is not
an independent identity assertion and never replaces the canonical
`record_graph` exact-record scope checks. The four-operation synthetic
regressions cover 36 contradictory nested references and eight matching or
absent controls. These do not demonstrate real-provider/tenant acceptance.
An absent source status remains `authoritative_provider_status_unavailable`
rather than being upgraded into an identity success.

Positive retained-record evidence is now admitted only when both
`record_observed` and `record_identity_complete_within_retained_window` are
literal JSON booleans `true`. Values such as `1`, `"true"`, missing/null,
arrays or objects fail closed as `availability=unknown` using the existing
record-not-observed or incomplete-identity reason. The source-event truncation assertion now also requires literal JSON
boolean `false`: missing, null, numeric zero, empty string, or any other
malformed `source_event_limit_reached` value means retained-history
completeness is **unknown**, never a verified operation. This does not prove
that the underlying source has no older events outside the permitted retained
window; it only guards the explicitly bounded exact-record read. These assertions are about
observed evidence integrity, **not** source/tenant authorization; the trusted
record reader and existing consent boundary remain responsible for that.

The existing exact-record, bounded-history, source-refresh opt-in and
freshness checks remain mandatory. A matching provider and object kind
**alone never proves an operation**: the expected source state and provider
operation detail still have to pass their existing independent checks. Even a
verified provider operation is not evidence that an agent caused it, that a
customer received the intended outcome, or that a business objective was met.

`tests/test_open_work_outcome_receipts.py` contains synthetic conflicting
provider and object-kind fixtures for GitHub PRs, Jira issues, Stripe invoices,
and Stripe refunds, including positive compatibility controls, 64 malformed-positive-flag
provider/field/value combinations, 44 malformed truncation-flag cases, four
missing-truncation cases, positive compatibility controls, and privacy
assertions. These are deterministic fake-reader tests, **not** authorized
real-provider checks, live tenant isolation proofs, commercial ROI, or
customer-outcome validation. No new network requests or source privileges are
introduced; the endpoint and optional MCP contract remain unchanged.

## Exact-record scope and provenance

The receipt additionally requires the graph's returned `resource_ref` to equal
the exact requested opaque `owg:r:` reference, and the graph's
`source_rows_scoped_to_exact_record` to be the literal Boolean `true`.
A different, missing or malformed returned reference, or a missing, false,
truthy-string or numeric exact-scope assertion, returns
`unknown/record_reference_scope_conflict` without echoing the mismatched
record identifier or private provider fields. This prevents a cached or
injected graph for a different case, or an unscoped test/integration reader,
from being mistaken for source-confirmed operation evidence. The production
`record_graph` emits both fields on its existing exact-record query, so its
normal response shape remains compatible.

These checks are **post-read receipt admission**, not proof that an upstream
reader never accessed a wrong provider or tenant: the trusted graph reader,
source adapters and existing authorization/consent controls must enforce
that boundary *before* any provider refresh. They do not establish
cross-process revocation fencing, complete source history, customer outcome,
agent causality or business completion. Four synthetic provider families cover
wrong/missing record references, malformed/missing exact-scope flags and
positive controls. No source credentials, permissions, REST/MCP schema or
background sync are added.

## Permitted history-window binding

The operation receipt now also requires the returned graph's `source_window`
to be an exact two-key object matching the caller's `since` and `until`
arguments, including explicit null boundaries. Production `record_graph`
already echoes these exact arguments. A missing, malformed, wider, narrower,
or different returned window yields
`availability=unknown / reason=source_window_scope_conflict`, without
reflecting the unexpected window or any provider/customer fields. This
prevents a stale or mis-scoped cached graph from being promoted to verified
operation evidence for a different permitted selection. It does **not**
prove that the trusted reader honored its requested window before source
access, nor does it add tenant authorization or revocation fencing.

The synthetic fake-reader regressions add 28 malformed/mismatched default
window cases, four missing-window cases, 16 bounded-request mismatches and
16 valid matching-window controls across GitHub PR, Jira issue, Stripe invoice
and Stripe refund operations (64 parameterized cases total). These assert
no customer text in responses, no agent/customer/business completion claims,
and unchanged single-read arguments. They are not live-provider acceptance
or real-customer outcome verification. The receipt remains opt-in,
read-only, and non-executing, with no new source grants, API calls, routes,
background sync, or REST/MCP schema additions.

## Authenticated source-read method provenance

The source receipt's provider name and reported status are insufficient to
attest an independently verified provider operation if the read mechanism is
unknown. The existing receipt verifier now requires an exact
`source_evidence.verification_method` emitted by the already opt-in,
authenticated provider adapter:

| Provider | Accepted read method |
| --- | --- |
| GitHub PR | `explicit_opt_in_authenticated_local_github_cli_read` |
| Jira issue | `explicit_opt_in_site_verified_oauth_jira_issue_status_read` |
| Stripe invoice/refund | `opted_in_restricted_key_stripe_account_and_record_get` |

Missing, malformed, cached/export-labelled or cross-provider method claims
return `availability=unknown` with
`reason=source_verification_provenance_conflict`. The untrusted method
string and private source data are not reflected in the result. This is
intentionally conservative: new legitimate adapter methods need an explicit
reviewed contract update, rather than silently being treated as equivalent
authentication evidence.

Synthetic fake-reader tests cover each of the four operation families, absent
method fields, malformed and cross-provider method values, and matching
positive controls. The change does not invoke any new source reads, broaden
grants, create credentials, execute agents, or change the REST/MCP response
shape. A matching string is only **post-read provenance consistency**, not
cryptographic proof of authentication, active tenant authorization, consent,
revocation fencing, an actual customer outcome, or independent live-provider
acceptance. The existing trusted adapters must enforce those properties.


## Allowlisted source-log join admission

The existing `record_graph` computes `source_log_join_supported` from the
exact retained record's provider, kind, and host: GitHub pull requests at
`github.com`, Jira issues at an `.atlassian.net` site, and Stripe invoices
or refunds at `dashboard.stripe.com`. A source-operation receipt now requires
that field to be the literal JSON Boolean `true`. A missing, false, numeric,
string, array or object value returns `unknown/source_join_not_supported`,
even if an injected/cached source receipt otherwise claims a fresh matching
provider state and authenticated read method. This does not expand the source
allowlist or grant access; production `record_graph` already emits this
positive assertion.

The additional synthetic regressions cover 28 malformed/nonaffirmative
join values, four missing-field cases and four valid positive controls
across GitHub PR, Jira issue, Stripe invoice and Stripe refund operations.
They check privacy-safe unknowns, unchanged single-read arguments and no
agent/customer/business outcome authority. These tests are not live-provider
or tenant acceptance. This guard is a **post-read contract assertion**, not
cryptographic evidence that an upstream reader was authorized or that a
provider was not contacted before a malformed graph was returned. Existing
opt-in, tenant/actor scope, source revocation and credential controls remain
the responsibility of the trusted source adapters.

## Source transport outage handling

The existing explicit, opt-in `record_graph` reader may fail during its
bounded source refresh. The receipt now converts only transport-level
`OSError` (including timeout/connection failures) and `httpx.HTTPError`
into `availability=unknown / reason=source_read_unavailable`, with all
operation, business, customer and agent-success flags false. No exception
message, native source ID, customer text or credential is echoed. It makes
exactly the same single reader invocation; there is no retry, background
poll, widened read or new source permission.

Invalid history-window requests (`ValueError`) and unexpected programming
errors (`RuntimeError`) are intentionally not hidden as source outages:
their existing request/error handling remains responsible. The four provider
operation families are covered by 20 synthetic fake-reader transport
exceptions and two negative controls. These tests do not prove live-provider
availability, tenant isolation, revocation fencing or business completion.
A successful authenticated provider state remains narrower than a customer
outcome and never authorizes agent execution.

## Jira resolution-detail consistency

The existing Jira OAuth adapter emits a separate `verified_operational_outcome`
only after its current issue status is `done`, its resolution object is
present, and the source resolution date parses as a timezone-aware timestamp.
A verified Jira operation receipt now additionally requires the adapter's
detail `status=resolved` and a nonempty, bounded (at most 64 characters),
timezone-aware `source_timestamp`. The resolution event must not be more
than five minutes **after** the authenticated source-read `verified_at`
clock; older valid resolutions remain acceptable because the current state
is separately re-read. Contradictory or absent detail fields return
`unknown/provider_resolution_detail_conflict` without echoing source text.

This guards the distinction between Jira's current status category and its
separate resolution evidence; a generic `done` label and a truthy
`verified` flag cannot independently prove resolution. The Stripe
processor-only and GitHub PR-merge contracts are unchanged. Synthetic tests
cover eight contradictory detail statuses, eleven malformed/naive/future
resolution timestamps, two missing-field cases, and five valid timestamp
controls; existing four-provider positive regressions continue to apply.
No provider adapter, OAuth grant, source read, endpoint, agent execution,
or business write is introduced. This is a **post-read consistency check**,
not live Jira tenant validation, a cryptographic provider attestation, an
agent-causality claim, or evidence of customer/business completion.


## Jira resolution scope consistency

The existing opt-in Jira source adapter labels its verified issue-resolution
detail with `scope=jira_issue_only_not_customer_acknowledgement_or_refund`.
The operation verifier now requires **that exact string** alongside the
previously checked resolved status, timezone-aware resolution timestamp,
verified flag, kind and authenticated read method. Missing, malformed or
conflicting scopes (including a Stripe processor scope) return
`availability=unknown / reason=provider_receipt_scope_conflict`, with no
private receipt fields echoed. This is conservative post-read consistency:
the scope label is not cryptographic authorization or proof of customer
acknowledgement, a refund, agent causality, or a completed business objective.

The synthetic Jira fake-reader regression set adds 12 malformed/wrong scope
cases, one missing-field case and one valid issue-only control. The shared
production-shaped Jira fixture now includes the adapter's actual scope, so
previously valid Jira resolution tests remain realistic. No provider adapter,
source permissions, REST/MCP contract, background reads or business writes
change. Native pytest and real-tenant/provider acceptance must still run.


## Bounded explicit source receipt timestamps

The provider-read `source_evidence.verified_at` and Jira's separately
reported `verified_operational_outcome.source_timestamp` now share one
strict post-read timestamp contract. Each must be a string of at most 64
characters, with an explicit calendar date, `T` separator, hours/minutes/
seconds, optional 1–9 fractional digits and an explicit `Z` or `±HH:MM`
timezone offset. The parsed datetime must be timezone-aware. The existing
source freshness limit (maximum 15 minutes by default, bounded by 1 hour)
and Jira resolution-versus-read clock-skew check remain unchanged.

Python's permissive `datetime.fromisoformat` alone accepts week dates,
compact dates, space separators and other formats that production adapters
do not emit. It can also receive non-string or very long injected metadata.
These forms now fail closed before parsing: malformed source read times yield
`unknown/invalid_provider_receipt_timestamp`; malformed Jira resolution
times yield `unknown/provider_resolution_detail_conflict`. Neither path
reflects the invalid value, provider exception or customer text in the
returned receipt.

The fake-reader regressions cover four provider-operation families, valid
UTC `Z`, explicit offset and fractional-second controls, ambiguous dates,
non-string values, and 100 KB untrusted timestamps. These are source-metadata
admission tests, **not** proof that a provider read occurred, that a tenant
grant was current, that consent or revocation was fenced, or that an agent
caused a verified customer/business outcome. No new source read, permission,
route, background action, credential, retention behavior or execution
authority is introduced.

## Jira non-compounding clock-skew boundary

The existing source-read freshness contract permits an authenticated
`verified_at` timestamp at most five minutes ahead of the evaluation clock,
to accommodate clock skew. Jira's separately verified resolution timestamp
previously had its own independent five-minute allowance relative to
`verified_at`. Combining those allowances could classify a purported
resolution nearly **ten minutes in the future** as verified.

A Jira resolution is now admitted only if its timestamp is at most 300 seconds
after **both** the source read's `verified_at` and the local evaluation clock.
This retains the existing source-read freshness and resolution detail checks,
accepts the exact +300-second boundary, and treats an excessively future-dated
resolution as `unknown/provider_resolution_detail_conflict`. It never treats
the Jira issue resolution as customer acknowledgement, agent causality, or a
completed business objective.

Eighteen synthetic fake-reader cases cover compound +598/+600-second future
claims, just-outside and exact-boundary controls, negative read-clock skew,
and equivalent UTC/+02:00 timestamps. No new network calls, background
polling, grants, credentials, source adapters, execution or business writes.
These are **post-read timestamp-consistency tests**, not live Jira validation,
cryptographic time attestation, tenant authorization or business-outcome proof.


## Pre-read operation-family isolation

A caller can ask to verify a Stripe invoice even when the opaque retained
record actually belongs to a Jira issue or a GitHub PR. Previously, the
operation verifier requested `record_graph(include_source_status=True)`
and checked the returned `record_type` **after** the graph could already
have refreshed the unrelated provider. Returning `unknown` afterward
does not undo that unnecessary provider read.

The existing `record_graph` source-type preflight now also accepts an
optional `required_source_provider_kind` pair. The operation verifier
passes its allowlisted expected provider and object kind **before** the
source adapter dispatch. A mismatch yields the existing
`selected_source_type_not_observed` source unknown, with **zero provider
adapter calls**. The outer operation receipt remains privacy-safe unknown
and never grants customer/business/agent success or execution authority.
The older exact `required_source_type` tuple remains supported; all other
callers have unchanged defaults. No source provider, tenant, or host is
authorized by the new pair: existing exact-host allowlists, tenant checks,
opt-in and credential validation still govern every permitted adapter read.

Synthetic tests exercise 16 observed-versus-requested combinations across
GitHub PR, Jira issue, Stripe invoice and Stripe refund, including same-
provider wrong-kind cases; four matching controls still make one fake
source read, while 12 mismatches make none. Additional fake-graph tests
assert the operation verifier passes the pair and preserves all
non-execution flags. This is not real provider, revocation-race, tenant
or customer-outcome acceptance. It does not add background polling,
business writes, credentials, source grants or new REST/MCP fields.


## Unsourced structural rows cannot authorize provider refresh

`record_graph` has an internal `_raw_events` input for structural projections
and deterministic tests. Previously, when `include_source_status=True`,
caller-supplied rows that looked like an exact, allowlisted record could
trigger the existing GitHub/Jira/Stripe source adapter **before** the
returned `source_rows_scoped_to_exact_record=False` flag caused the
downstream operation receipt to reject the result. Post-read rejection
cannot undo an unauthorized or unnecessary provider request.

The graph now refuses source refresh whenever `_raw_events is not None`,
including an empty list or a matching synthetic row. It returns
`source_evidence.availability=unknown` with reason
`unscoped_raw_events_cannot_authorize_source_refresh` and marks
`source_log_join_supported=False`. Structural projection and the
existing `include_source_status=False` behavior remain available.
A normal canonical `_record_rows` lookup with the existing explicit
source-refresh opt-in and provider/host restrictions is unaffected.

Twenty deterministic parameterized fake-reader cases cover all four
GitHub/Jira/Stripe operation families: 16 unsourced combinations
(refresh requested or not; matching row or empty) must make **zero**
provider-reader calls, and four canonical DB-lookup controls still
make exactly one fake call. These are contract regressions, not
live-provider/tenant acceptance, cryptographic source attestation,
revocation-race fencing, customer outcomes, or measured commercial uplift.
No new source permissions, network operations, background polling,
REST/MCP fields, agent execution, or business writes are added.


## Explicit contradictory GitHub operation details

The existing GitHub CLI adapter confirms the provider's current PR merge state
without a nested `verified_operational_outcome` object. That absent detail is
still supported. If a separately authorized reader/cache **does** supply that
optional object, its `verified` flag must be the literal Boolean `true`
and its `kind` must be `github_pr_merged`. An explicit false/malformed
flag or different operation kind now yields
`unknown/provider_operation_not_independently_verified`, rather than
silently ignoring conflicting source evidence. A matching optional object
does not expand the provider-only scope: customer outcome, business objective,
agent causality and execution authorization remain false.

Twelve synthetic fake-reader parameterizations cover ten contradictory
optional details, the current production no-detail shape and a matching
detail. These are deterministic contract regressions, **not** proof of
live GitHub provider acceptance, customer completion or commercial uplift.
No new source grants, background reads, writes, or agent permissions.


## Explicit provider-scope disclaimer contradictions

The existing GitHub and Jira source adapters explicitly state
`not_a_claim_of_business_success=True`: a PR merge or Jira resolution is
not proof that the intended business objective or customer outcome occurred.
The existing Stripe invoice/refund adapter explicitly states
`not_a_claim_of_customer_bank_settlement=True`: a processor status is not
proof that the customer received funds or that the business objective
was satisfied.

A separately supplied authorized reader/cache could previously contradict
one of these provider-specific limitations (for example, report
`not_a_claim_of_customer_bank_settlement=False`) while otherwise presenting
a matching, fresh source operation. The operation receipt now returns
`unknown/provider_receipt_scope_conflict` when its relevant disclaimer
is **explicitly present** but not the literal Boolean `True`. Absence is
backward-compatible with older source-shaped fixtures and does not itself
grant business or customer authority. The production adapters already
emit the affirmative disclaimers; no new provider calls or permissions
are introduced. This check is post-read consistency, not cryptographic
source attestation or a guarantee against stale/revoked source grants.

The standalone synthetic contract tests in
`tests/test_open_work_receipt_scope_boundaries.py` cover four operation
families (GitHub PR, Jira issue, Stripe invoice and refund), 28 explicit
false/malformed contradictions and eight valid absent/true controls.
They assert no private customer fields in the output, exact requested
provider/kind pre-read scoping, read-only operation and false
business/customer/agent authority. These tests do not establish live
provider acceptance, tenant isolation under real grants, independent
customer outcomes, or held-out commercial uplift.


## Integer source-receipt age policy

The existing optional `max_age_seconds` override is an **integer-seconds**
policy bounded to 1–3600 (default 900). It now rejects Python booleans,
fractional floats, strings, nulls, nonfinite values and out-of-range integers
with a controlled `ValueError` **before** invoking `record_graph` or any
injected provider reader. There is no implicit rounding or coercion of the
freshness policy, no wider default window, and no additional source read.
The existing +300-second future clock-skew allowance is unchanged.

Synthetic fake-reader regressions cover 52 invalid policy/provider pairs
(with zero source reads) and 24 exact-boundary provider cases at 1, 900 and
3600 seconds, including the first stale second. All successful results remain
provider-operational-state-only; business/customer outcomes and agent
execution authority remain false. These are deterministic contract cases,
not native cross-platform CI, live tenant/provider acceptance, or evidence
of commercial benefit.


## Pre-read retained-window and evaluation-clock admission

The operation verifier now checks the caller's `since`/`until` window **before**
invoking any source-capable record reader. It deliberately reuses
`workflow_evidence._ts` and `WorkflowEvidenceError`, the same validation
contract already used by canonical `record_graph`: malformed boundaries and
equal/reversed bounded windows are rejected without dispatch. This matters for
alternate explicitly authorized readers/caches that do not perform the
canonical database preflight themselves. Existing valid windows and default
`None` boundaries retain their semantics; this does not widen access or
convert missing historical observations into obligations.

An explicitly supplied evaluation `now` must be a timezone-aware
`datetime` before any read. Naive datetimes, strings, numbers and booleans
are rejected with `ValueError` instead of spending a source read and only
then producing a timestamp error. With `now=None`, the existing current UTC
clock is used. The existing 1..3600-second source-age policy and five-minute
future-skew bound are unchanged.

Synthetic fake-reader regressions exercise four provider-operation families:
16 malformed/equal/reversed window scenarios and 16 invalid-clock scenarios
must make **zero** reader calls; eight valid bounded/default-window controls
must preserve a provider-only verified operation, with all customer, business,
agent-causality and execution-authorization flags false. These are committed
contract tests, not proof of native test execution, real provider access,
source-grant epoch fencing, customer outcomes or measured business uplift.


## Explicit history-window values are not omitted scope

The operation verifier now distinguishes an omitted `since`/`until` bound
(`None`) from an **explicitly supplied** falsey or non-string bound. The
canonical `workflow_evidence._ts` parser remains the timestamp validator
for nonempty strings, and the same `WorkflowEvidenceError` is raised before
calling any injected or default source-capable record reader. This closes a
pre-read scope gap where `False`, numeric zero, empty strings or containers
could bypass a truthiness-based guard; datetime objects and byte strings
cannot acquire validity by implicit `str()` conversion either. Both omitted
bounds remain supported, and valid ISO-8601 string windows preserve existing
behavior. No change is made to shared `record_graph` or its other callers.

`tests/test_open_work_receipt_scope_boundaries.py` adds 72 synthetic
provider-operation/window/value combinations requiring **zero** reader calls,
alongside its existing valid-window and offset-clock controls. These tests
are not live-provider, tenant-grant, revocation, customer-outcome, or business
uplift evidence. No new source privileges, network reads, background polling,
agent execution, or business writes are introduced.


## Timezone-explicit history-window preflight

A provider operation receipt is stricter than the general historical-evidence
query parser. The latter accepts some legacy date-only or timezone-naive
strings, which may be interpreted in the local machine's timezone. Before
invoking any source-capable record reader, the operation verifier requires
every explicitly supplied `since`/`until` boundary to be a bounded (at most
64-character) ISO-8601 **datetime** with `T` separator and explicit `Z` or
`+HH:MM`/`-HH:MM` timezone. Minute and second precision, fractional seconds
(up to nine digits), and explicit offsets remain valid. `None` still means
omitted. The existing canonical `_ts` parser is reused for calendar validity
and strict `since < until` ordering; the source receipt's own `verified_at`
parser remains unchanged.

Date-only, ISO week/basic dates, space-separated or timezone-naive datetimes,
malformed offsets, excessive precision and oversized inputs now raise the
existing `WorkflowEvidenceError` **before** the reader can make a source
request. This deliberately narrows only the opt-in verification boundary, not
general workflow evidence retrieval, REST/MCP, capture, Discovery Mode or
source adapter permissions. It prevents host-timezone ambiguity in the
caller-selected retained-history scope; it is not a tenant authorization,
revocation or real-provider outcome proof.

Four synthetic provider-operation families have 112 invalid-boundary
parameterizations requiring zero fake-reader calls and 20 valid
timezone-aware-window controls that preserve read-only, provider-only receipts.
These are committed regression cases, not evidence of native pytest execution
or live customer/provider acceptance.


## Legacy provider outcome flag consistency (2026-11-01)

The existing authenticated source adapters expose a historical field named
`verified_business_outcome`, whose meaning differs by provider. GitHub PR
merge and Jira issue resolution adapters set it to literal `false`, because
their operational state does not prove business success. Stripe sets it to
literal `true` for an invoice paid or refund processor-succeeded receipt,
**only** for the processor-defined operation; it does **not** prove customer
bank settlement, an agent's causal contribution, or the intended business
objective. The stable output of `source_operation_receipt` continues to
report `business_objective_verified=false`,
`customer_outcome_verified=false` and
`agent_caused_operation_verified=false` for **all** providers.

A source/cache reader that **explicitly** supplies this legacy field with
the opposite Boolean or a non-Boolean value now yields
`availability=unknown`, `reason=provider_receipt_scope_conflict` and
`operation_verified=false`. The optional field may be absent for existing
older test/cache shapes; absence does not add any authority, and the
existing authenticated verification method, exact record, provider/kind,
freshness, operation detail and provider-scope guards still apply.

Forty synthetic, four-operation fake-reader parameterizations cover
28 malformed values, four opposite Boolean contradictions, and eight
matching/absent controls. The tests assert no private customer fields
are copied into receipts and no business/customer/agent authorization is
granted. These are committed regression contracts, not executed native
pytest, live provider/tenant validation, or evidence of commercial uplift.
No new source read, permission, background sync or business write is added.


## Optional business/customer/agent authority assertion conflicts

The operation verifier already returns literal false for
`business_objective_verified`, `customer_outcome_verified`,
`agent_caused_operation_verified`, and `agent_execution_authorized`.
These are **not** provider status fields, and neither a successful provider
operation nor an observed workflow grants any of those authorities.

An injected/cached `source_evidence` receipt or nested
`verified_operational_outcome` detail may include those optional assertions.
If any is explicitly present, it must be the literal Boolean `false`.
Contradictory `true`, truthy numerics, strings, null or structured values
return privacy-safe `availability=unknown` with
`reason=provider_receipt_scope_conflict`, never a verified operation. No
private source values or conflicting customer/agent claims are echoed.
Omitted fields remain compatible with the current authenticated GitHub,
Jira and Stripe adapters. Explicit literal false remains valid.

This guard is distinct from the older provider-specific
`verified_business_outcome` field: Stripe's legacy true means successful
**processor operation**, not customer bank settlement or a verified business
objective. Existing handling of that legacy field remains unchanged.
Synthetic fake-reader regression parameterizations cover four operation
families, both receipt nesting levels, four authority flags, five conflicting
values (160 negative cases), and 16 absent/false positive controls.
These are committed contract tests, **not** live-provider or customer-outcome
validation. The verifier still makes at most its one already-authorized read;
no new permissions, source adapters, background sync, business writes or
agent execution paths are added. Tenant/actor consent, revocation and
retention remain enforced by the existing trusted reader and adapters.
