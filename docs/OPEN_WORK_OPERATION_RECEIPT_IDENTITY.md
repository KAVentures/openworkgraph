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
already establishes their kind; Jira and GitHub supply the field. An absent
source status remains `authoritative_provider_status_unavailable` rather
than being upgraded into an identity success.

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
