# ServiceNow incident Table API: offline selected-record staging

This is a **bounded foundation, not a connected ServiceNow integration**. \`server/servicenow_incident_staging.py\` does not open sockets, resolve secrets, persist records, append event logs, schedule jobs, provision a ServiceNow tenant or authorize any new access. It prepares a deterministic GET request for an already-authorized caller and validates a single supplied JSON page. Real source grants, transport, atomic checkpointing and replay remain separate work under [#326](https://github.com/KAVentures/openworkgraph/issues/326) Section B / [#264](https://github.com/KAVentures/openworkgraph/issues/264) Stage 3.

## Scope and API contract

\`SelectedIncidents\` requires one exact lower-case \`*.service-now.com\` host, a bounded local tenant-scope identifier, 1–8 unique lowercase 32-hex incident \`sys_id\` values, **explicit local consent**, and **current read-grant authority**. Its defaults deny access. No arbitrary table, provider URL, endpoint, search expression or inventory enumeration is accepted. The source namespace is a deterministic SHA-256-derived identifier bound to host, tenant scope and the \`incident\` table; the selected IDs are never used as a cross-tenant join rule.

\`plan_page(scope, offset, limit, since)\` produces a GET-only \`/api/now/table/incident\` URL, using a selected-ID \`sys_idIN\` query, optional provider-local update watermark, deterministic \`ORDERBYsys_updated_on\` / \`ORDERBYsys_id\`, explicit four-field projection (\`sys_id,sys_updated_on,sys_mod_count,state\`), raw stored field values, and bounded offset (0–10,000) and page size (1–100). It never follows provider-supplied links. The plan does **not** contain a bearer token or execute a request. It is not a safe authorization grant to a future network layer: the actual transport must independently verify selected scope, current account/tenant/grant, revocation and HTTPS response identity.

\`project_page(scope, raw, limit)\` accepts at most 256 KiB of UTF-8 JSON, rejects duplicate JSON keys, nonfinite JSON, malformed records, oversized pages, unselected IDs, invalid state/revision fields and conflicting duplicate incident rows. Both request watermarks and source-reported update strings must be calendar-valid local timestamps (including leap-day validation); an impossible provider date is rejected rather than staged. When `plan_page(..., since=watermark)` is used, the caller **must** pass the same exact `since=watermark` to `project_page`; the projector then rejects the entire page if any selected row's reported update time is older than the lower bound. This guards against a provider silently ignoring the incremental filter; omitting `since` on projection intentionally preserves full-scan behavior and **does not** verify that a planned filter was honored. Calendar validity does **not** verify the source clock, timezone, chronology or freshness. Extra source fields, including descriptions, callers and other private values, are discarded. Returned staged records contain only namespace, exact incident ID, stable \`sys_id:sys_mod_count\` source revision key, raw reported state, raw reported update string and explicit provenance/unknown flags. These are **source-reported snapshots**, not independently verified business outcomes, complete incident histories, event-log writes or AI permissions.

## Fail-closed Table API response envelope

The offline projector accepts **only** a top-level JSON object with exactly one `result` array, the shape of the supported ServiceNow incident Table API GET success response. A response mixing `result` with `error`, `status`, `next`, or any other top-level field is rejected rather than silently treating a provider error, partial response or untrusted pagination hint as an empty/successful page. This also applies to plan-bound and consecutive-page projections; record-level unexpected/private fields continue to be discarded. Valid `{"result":[]}` remains a valid **empty but not complete** selected snapshot.

This is deliberately strict for the **supported Table API response shape**, not generic ServiceNow REST compatibility. The future HTTPS transport must still independently enforce exact request/response binding, HTTP status, response content type, redirects, current grant/tenant/consent and source revocation; a JSON-only envelope check cannot authenticate a provider or establish ACL completeness.

## Plan-bound offline projection

For new offline consumers, prefer `project_planned_page(scope, raw, plan=plan_page(scope, ...))` over independently passing `limit` and `since` to `project_page`. The planned GET metadata now includes the exact `since` value (or explicit `None`). The helper reconstructs the expected GET plan from the **current exact host, tenant, selected incident IDs, offset, limit and provider-local watermark**. It rejects missing, altered or extra plan fields, then applies the planned lower bound and page size to every returned row. This prevents a caller from accidentally forgetting the watermark at projection time. Existing `project_page` remains supported for standalone fake-response validation; it does not enforce any previously planned filter unless the caller supplies the matching `since` explicitly.

For a plan-bound page, the projector also checks the **raw returned row order** against the planned ascending `sys_updated_on,sys_id` ordering (using validated provider-local strings). A reversed update time or reversed incident ID on an equal timestamp rejects the entire page rather than sorting away evidence that ServiceNow may have ignored `ORDERBY`. Identical repeated rows are allowed and deduplicated as before. A successful page reports `page_order_matches_plan: true`; standalone `project_page` remains backward compatible and does not assert planned ordering. This checks **one page only**: it does not establish source order across offset pages, prevent concurrent-page drift, authenticate the HTTP response, or justify a checkpoint.

The result marks `plan_bound: true`, `planned_offset` and `planned_since`, but still marks `checkpoint_advanced: false`, `historical_completeness: unknown` and unverified provider-local timestamps. **This is not cryptographic plan integrity or a source response receipt**: a caller could construct a different internally consistent plan, the provider may ignore an offset/order/ACL or omit records, and the helper cannot prove which URL was actually fetched. A future transport must bind the actual HTTPS request and response to the authorized plan, independently enforce grant/tenant revocation, and atomically fence any append/checkpoint. Never interpret a matched plan as a verified business outcome or permission to execute.

## Critical completeness and time limits

ServiceNow's [official REST API documentation](https://www.servicenow.com/docs/r/api-reference/rest-api-explorer/c_RESTAPI.html) documents \`sysparm_query\`, \`sysparm_limit\`, \`sysparm_offset\`, ordering and the possibility that invalid query fragments may be ignored. Therefore every returned row is rechecked against the exact selected IDs, even when the URL was constructed locally. A short page only means this page was short; ACL filtering, concurrent updates, shifting offset pages, deleted records, rate limits and source-side query behavior mean **historical completeness remains unknown**. A page at its limit is marked \`may_have_more\`, but neither case advances a checkpoint.

ServiceNow's raw \`sys_updated_on\` string is deliberately **not converted to UTC** here. Its timezone and true chronology have not been independently verified for the configured instance/user. An external connector must establish timezone semantics and resolve timestamp ties, late updates, replays, stale revisions, deletion, conflict handling and rescan strategy before converting snapshots into timestamped event-log records. The incremental bound comparison is a lexical comparison of strictly calendar-validated, fixed-width provider-local timestamps. It is not a UTC comparison, cannot detect a wrong provider clock or timezone, and cannot prove that records missing from a page were fetched. It must stage and atomically commit the full authorized batch through OWG's existing source-ID append path, with a current grant/tenant/profile revision check at commit time; never treat \`source_event_id\` as a provider signature or historical audit event.

The bounded planner does not implement authentication, provider response streaming, redirect policy, TLS verification, OAuth renewal, HTTP status handling, pagination loops, retries/backoff, consent persistence, dataset retention, revocation cleanup, actual event append, UI or MCP. It intentionally cannot be used for real provider access without a separately reviewed implementation. No real ServiceNow sandbox/customer acceptance has occurred.

## Synthetic regression

Run \`PYTHONPATH=. pytest -q tests/test_servicenow_incident_staging.py\`. The owned fake-response fixtures cover selected-only GET construction, consent/grant denial, tenant namespaces, source identity, private-field omission, duplicate and conflicting revisions, oversized/invalid JSON, injected query/host values, page-boundary ambiguity and unknown outcome/completeness flags. They do not substitute for actual ServiceNow instance tests, source ACL audits, enterprise authorization or 300k+/multi-million-event end-to-end ingestion gates.

## Bounded cross-page continuity check

For an explicitly selected, consented, currently granted **offline** batch, `project_planned_pages(scope, [(plan, raw), ...])` checks 1–8 previously supplied page responses. It requires a zero-offset first plan, a constant page size and provider-local `since` bound, exact consecutive offsets, full preceding pages, and successful exact-scope/selected-ID validation for **every** plan and page. Across page boundaries it rejects decreasing `sys_updated_on,sys_id` keys and any repeated selected incident ID, **even if the repeated revision is identical**: an overlapping offset page may hide an unseen incident. **Within an offset batch, even identical repeated incident rows on the same page are refused**: duplicate rows consume offset slots and can conceal an omitted selected incident on a later page. Standalone `project_page` and single `project_planned_page` still deduplicate exact repeated rows for backwards compatibility. An empty/short final page is allowed; no additional page may follow it. The result exposes `pages_checked`, `plan_sequence_checked`, minimized source snapshots, and explicit unknown completeness, unverified timezone, no checkpoint advancement and no append authorization.

This catches several provider-offset/replay inconsistencies but **does not** prove a complete, stable snapshot: concurrent ServiceNow edits, deletions, ACL filtering, a provider ignoring offsets while returning non-overlapping IDs, network tampering, or missing later pages can still evade detection. It does not authenticate request/response pairs, resolve the provider clock or grant any source/tenant privileges. A future transport must separately enforce current tenant/consent/grant at request **and atomic persistence time**, handle revocation/retention, source replay and response identity, and independently prove checkpoint safety. No HTTP calls, storage, business outcome verification or real ServiceNow acceptance were added.


## JSON numeric overflow integrity

Python's default JSON decoder can turn a syntactically valid JSON exponent such as `1e309` into an infinite floating-point value. The offline staging decoder now rejects this numeric overflow **at parse time**, even when it occurs in an extra/private nested field that would otherwise be discarded. Explicit `NaN`/`Infinity` tokens and duplicate object keys remain rejected. Ordinary finite JSON numbers in extra fields remain accepted and discarded; incident identity, state, revision and update-time fields still require their exact string types. Errors do not echo source values.

This is local response validation, **not** a claim that the HTTPS response came from ServiceNow or that selected records are complete, current or safe to append. Fake-response tests cover standalone, plan-bound and bounded multi-page staging; no real provider or tenant is contacted.


## Offline replay consistency (two selected reads)

\`reconcile_replayed_pages(scope, earlier_raw, later_raw, limit=50, since=None)\` stages **both** supplied Table API JSON pages through the existing strict bounded projector under the same explicit consent, current grant, selected incident IDs, host and tenant scope. It does not fetch them or authenticate their origin. It compares only exact stable \`sys_id\` identities: unchanged \`sys_mod_count\` must have unchanged reported state and provider-local update string, and a decreasing numeric modification counter fails closed. A higher counter is reported as **higher source-reported revision**, not verified chronology, completion or a trusted event. Extra/private fields remain discarded.

The minimized result distinguishes identical reported revisions, higher reported revisions, newly observed selected IDs and **missing-unknown** IDs. Absence in the later page does **not** imply deletion, closure, completion or revoked access: pagination, ACLs, provider filtering, concurrent edits and incomplete source history remain possible. Equal timestamps on different IDs never cause a join. Both pages are validated against the same optional lower watermark, but this does not bind an actual HTTP request to a response. The result always declares unknown historical completeness, unverified timezone and business outcome, no checkpoint advancement and no append authorization. The caller must separately enforce transport, current grant/tenant revocation and atomic persistence fences before any real connector use.

Synthetic regression coverage exercises exact replay, higher counters, omission, no time-only joining, same-revision contradictions, counter regression, malformed/unselected/old responses, private-field omission and consent/tenant isolation. No real ServiceNow acceptance or completeness claim follows from these tests.


## Plan-bound two-batch replay comparison

For **synthetic/offline** selected-record offset reads, `reconcile_planned_replays(scope, earlier_pages, later_pages)` accepts two lists of 1–8 `(plan_page(...), raw_bytes)` tuples. Both reads must have the **same exact plan sequence** (host, tenant, selected IDs, namespace, URL, provider-local watermark, offset and page size). Each sequence is independently validated by the existing `project_planned_pages` before comparison by stable source incident ID and reported revision counter. The old standalone `reconcile_replayed_pages` remains compatible and shares the same comparison logic.

Mismatched plans, invalid scope, out-of-order/overlapping offsets, conflicting same-revision evidence, backwards counters and missing consent/current grant fail closed. A missing incident remains **missing-unknown**, not deleted, closed or completed. A higher counter is **source-reported**, not authenticated chronology or a verified business outcome. `same_plans_checked`, `plan_sequence_checked` and `pages_per_read` report only local consistency; historical completeness, source clock/timezone and outcomes remain unverified, with no checkpoint or append authorization.

Matching offline plans **cannot prove** an HTTP client fetched those URLs, that an ACL/provider returned a stable complete snapshot, or that consent/grant remains current during later persistence. This helper does not add credentials, transport, storage, source-revocation fencing or real ServiceNow acceptance. Production transport must authenticate request/response identity and atomically fence consent/tenant/grant at append/checkpoint time.


## Selected incidents absent from both replay reads

Both `reconcile_replayed_pages` and `reconcile_planned_replays` now return
`unobserved_in_either_read_ids`: the sorted exact selected incident IDs that
appeared in **neither** validated response. This is distinct from
`missing_unknown_ids` (observed earlier, absent later) and
`newly_observed_ids` (absent earlier, observed later). For example, two empty
responses for a three-ID selection disclose all three as unobserved rather than
presenting an apparently complete, consistent empty replay. The list is derived
only from the explicitly consented selected scope, never from a provider search
or an inferred record identity. The list is bounded by the existing eight-ID
selection limit and remains tenant/provider namespaced.

An empty `unobserved_in_either_read_ids` list **does not** prove source
completeness, current ACLs, snapshot stability, authentic transport, valid
provider chronology, deletion, a verified business outcome or permission to
append/advance a checkpoint. All existing unknown and non-execution flags
remain unchanged. Synthetic fake-response tests cover both replay entrypoints,
empty, partial and fully observed selections, revocation and tenant scoping.

## Structural and Unicode bounds for discarded JSON

The offline JSON decoder now checks the **entire** parsed provider response before
selecting the four supported incident fields. In addition to the existing 256 KiB
raw-response cap and strict duplicate-key/nonfinite-number handling, it rejects
JSON trees deeper than 32 levels or containing more than 4,096 nodes (including
object keys), and strings or keys containing unpaired UTF-16 surrogate code
points. Python's JSON decoder can otherwise accept an isolated escaped
`\\ud800` or `\\udc00` even though it is not a valid Unicode scalar. A
valid escaped surrogate **pair** is accepted as one Unicode character.

The same fail-closed validation applies to discarded/private extra fields,
standalone staging, plan-bound staging, bounded page batches and both replay
comparison entrypoints. It prevents a syntactically accepted but structurally
excessive or ill-formed ignored payload from being treated as a clean selected
record. Valid small nested extra fields continue to be discarded; they are not
returned, persisted, or granted identity/authorization authority. Synthetic
adversarial tests cover malformed nested values and keys, excessive depth/node
counts, accepted valid supplementary-plane Unicode, and preserved unknown
completeness/checkpoint flags.

These limits deliberately constrain the **supported offline selected-incident
response contract**, not all ServiceNow REST payload shapes. They do not
authenticate a source, verify HTTP status/TLS/ACLs, prevent missing records,
prove source chronology or completeness, authorize event-log append, or
establish real tenant/provider acceptance. No provider requests or business
writes occur in this module.


## Replay disagreement between revision and reported update time

Both offline replay entrypoints now distinguish a **higher source-reported modification counter** from a **cleanly classifiable source-reported advance**. If an exact selected incident has a higher `sys_mod_count` in the later supplied read but an **earlier** calendar-valid, fixed-width provider-local `sys_updated_on`, it is listed under `timestamp_regression_unknown_ids` and **excluded** from `higher_reported_revision_ids`. A higher counter with an equal reported second is still classified as higher: ServiceNow's supplied timestamp has second-level precision, and equal timestamps are not proof of a duplicate. Existing same-counter contradictory evidence and counter regressions continue to fail closed.

This is a **source-evidence disagreement**, not evidence that the incident was rolled back, deleted, resolved, closed, or that the provider actually traveled backwards in time. Instance clock, timezone, ACLs, transport authenticity, snapshot consistency and history are still unverified. The lists contain only the already explicitly selected, tenant-scoped incident IDs. The result retains `historical_completeness: unknown`, `business_outcome_verified: false`, `provider_timestamp_timezone_verified: false`, `checkpoint_advanced: false` and `not_an_authorization_to_append: true`. The caller must review the discrepancy and obtain independently trusted provider semantics before any real append or checkpoint. Fake-response regressions exercise standalone and exact-plan two-read comparisons, both the contradiction and valid equal-second case; they are not real-provider acceptance.

## Exact-type selected-page plan metadata

`project_planned_page` now requires the **exact Python value type** for every
locally reconstructed plan field, in addition to the existing value equality,
selected-ID/host/tenant checks and GET URL reconstruction. Python considers
`True == 1`, `1.0 == 1`, and `0 == False`; a one-incident plan with
`selected_count=True`, or a plan replacing
`provider_timestamp_timezone_verified=False` with numeric zero, therefore
previously passed a plain dictionary equality check. These type substitutions
are now rejected before staging. The same check applies through consecutive
offset-page staging and two-batch replay comparison. Genuine plans created by
`plan_page` remain compatible, and standalone `project_page` retains its
independent bounded response-validation contract.

The check establishes only **typed local plan consistency**, not a signed
request/response receipt, HTTP origin, provider ACL completeness, verified
source time, a current persistence-time grant or permission to append.
Synthetic tests use deliberately Python-equal boolean/number substitutions
and exercise all three plan-bound entrypoints without provider calls.


## Exact selected-scope authority type

Every public offline planner, page projector, bounded page batch and replay
entrypoint now requires the **exact** `SelectedIncidents` type before invoking
its validated consent/current-grant check. A duck-typed object can otherwise
supply `require_authority()` as a no-op and forge the selected host, namespace,
tenant or grant state; a subclass can override that method despite frozen
dataclass fields. Neither is accepted as an authorized selected-record scope.

Synthetic adversarial regressions cover a forged off-domain host, a forged
matching-looking namespace/GET plan across all six entrypoints, and a subclass
that tries to bypass revoked consent. Genuine explicitly consented scopes
retain existing staging behavior. This protects the **local Python contract**;
it does not authenticate HTTPS, independently verify the provider or source
clock, provide a persisted grant/tenant revocation fence, prove completeness,
authorize event-log writes or constitute real ServiceNow acceptance.


## Explicitly selected incident IDs not observed in a staged page

The standalone `project_page` and single-plan `project_planned_page` outputs, and the bounded consecutive `project_planned_pages` batch output, include `unobserved_selected_ids`: sorted exact selected incident IDs absent from the **validated supplied response(s)**. The value is derived solely from the consented, tenant-scoped selected IDs and the strictly validated returned IDs; repeated identical rows do not count as additional observed identities. An empty response lists every selected ID as unobserved. A multi-page result computes the union across its validated pages rather than treating a short last page as evidence of completeness.

This field is a **local omission disclosure**, not a claim that ServiceNow deleted a record, denied access, returned a complete historical scan, or produced a verified outcome. A selected ID can be absent because of ACL filtering, pagination, concurrent updates, source-side query behavior, transport failure, or an incomplete batch. Even when `unobserved_selected_ids` is empty, the source's historical completeness, clock, authenticated transport and authorization at persistence time remain unverified. The existing `historical_completeness: unknown`, `checkpoint_advanced: false`, and `not_an_authorization_to_append: true` flags are unchanged. No source requests, event-log writes, checkpoint advancement or credential access are implemented here.

## Duplicate rows in standalone replay comparison

`reconcile_replayed_pages` now **rejects a supplied replay read with repeated selected incident rows**, even when their minimized revision, state and timestamp are identical. An offset-limited response with `A, A` can displace selected incident `B`; silently deduplicating the replay could then make a missing observation appear to be a clean before/after difference. Both earlier and later supplied pages are checked after strict source projection, before any replay classification. The error is generic and does not echo private provider values.

This deliberately differs from legacy single-page `project_page`, which still deduplicates identical rows and reports `returned_rows` versus `distinct_selected_rows` and `unobserved_selected_ids`. Planned offset batches already rejected duplicates; they remain unchanged. Valid unique-row standalone replay still classifies exact source-reported revisions, with missing records and business outcomes explicitly unknown. Synthetic tests cover duplicate rows in either replay side and preservation of standalone behavior. This is **offline consistency only**: no HTTP authentication, complete snapshot, source-time verification, grant-at-append check, persistence, checkpoint, provider sandbox or business write is supplied.

## Unobserved intermediate source revisions

Both offline replay comparisons now report `revision_gap_unknown_ids` for an
incident observed in **both** selected reads when its later source-reported
`sys_mod_count` exceeds the earlier counter by **more than one**. Such IDs
are excluded from `higher_reported_revision_ids`, which now describes only
adjacent (+1) reported counter advances. A gap may indicate intervening
source modifications that were not captured; it **does not** prove how many
business events occurred, what statuses existed, who acted, or whether any
business outcome completed. Even a gap with an equal provider-local timestamp
is withheld, because the timestamp has only second-level precision.

A counter regression still fails closed. When a higher counter also has an
earlier provider-local `sys_updated_on`, the existing
`timestamp_regression_unknown_ids` classification takes precedence over
the gap bucket: contradictory source-time evidence must not be normalized
away. An incident newly observed only in the later read has no earlier
counter baseline and remains `newly_observed_ids`, not an inferred gap.
Unchanged counters with contradictory status/time still fail closed.

Synthetic fake-response regressions exercise equal-second and later-second
gaps, adjacent updates, contradictory time, and newly observed high counters
through **both** standalone and exact-plan replay. The change does not alter
selected-ID or tenant scoping, source permission, retention, transport, or
the strict no-write/no-checkpoint behavior. `historical_completeness` remains
`unknown`; source chronology, authenticated provider receipts, real sandbox
acceptance and business outcomes remain unverified.


## Bounded discarded JSON integer tokens

The offline response decoder now caps **each JSON integer token** at 128 decimal
digits (excluding an optional minus sign) **before** converting it into a Python
integer. Provider responses may contain irrelevant private or diagnostic numeric
fields, but they are still parsed before minimization. Without a token bound,
the built-in JSON integer parser may allocate large arbitrary-precision values
or hit interpreter-dependent integer-string limits on otherwise byte-bounded
responses. This is a conservative input-resource limit, not a provider schema
assertion. Supported incident identity, modification count, timestamp and state
remain strictly string-typed; normal small integers in discarded fields are
accepted but never returned.

Synthetic tests exercise positive/negative oversized integers in standalone,
plan-bound, offset-batch and both replay paths; boundary-sized integers and
zero remain compatible. Generic errors do not echo the private numeric token.
The 256 KiB response, tree-depth/node, consent/tenant and no-write bounds remain
unchanged. This does not verify HTTP origin, provider ACLs, completeness,
historical changes, current persistence-time authorization or real ServiceNow
acceptance.


## Ambiguous reserved incident field aliases

The offline Table API projector requires canonical, case-sensitive incident keys:
`sys_id`, `sys_updated_on`, `sys_mod_count`, and `state`. Previously a
second spelling such as `SYS_ID` or `State` was silently discarded as an
unselected/private extra while the canonical key supplied the staged identity
or status. That could hide contradictory source evidence. It now **rejects**
casefold-equivalent aliases of these four reserved keys, whether or not the
canonical spelling is also present. The error is generic and does not echo
provider-supplied fields or values. Exact canonical fields and unrelated
private extras remain compatible; extras are still never retained in staged
snapshots.

Synthetic tests cover ASCII and Unicode casefold aliases across standalone,
plan-bound, offset-batch and both replay paths, missing-canonical-field
rejection and compatibility with unrelated discarded fields. This is only
local response-shape integrity. It is **not** a verified ServiceNow response,
provider ACL/tenant receipt, completeness claim, current consent grant at
persistence time, permission to append/checkpoint, or real sandbox acceptance.


## Unicode compatibility aliases of reserved fields

The four reserved source-evidence keys (`sys_id`, `sys_updated_on`,
`sys_mod_count`, `state`) must be spelled **exactly**. In addition to
casefold-equivalent aliases, the offline projector now refuses an extra key
whose Unicode **NFKC normalization followed by casefold** equals a reserved
key. For example, fullwidth `ｓｙｓ＿ｉｄ` and mathematical styled
`𝘴𝘵𝘢𝘵𝘦` cannot silently coexist with canonical keys while carrying
contradictory provider identity or status evidence. This also rejects such an
alias when the canonical key is missing. Unrelated private fields, including
Unicode compatibility spellings that do not normalize to reserved names, are
still accepted and discarded; neither their names nor values are returned.

Synthetic regressions cover all four reserved fields and multiple compatibility
spellings across standalone staging, exact-plan staging, bounded offset batches
and both replay entrypoints. This protects only local source-field integrity:
it does not authenticate the provider, assert completeness or source chronology,
grant a current consent/tenant lease at persistence, authorize an event-log
append/checkpoint, or constitute real ServiceNow sandbox acceptance.


## Revalidating mutable Python scope objects

`SelectedIncidents` is a frozen dataclass, but Python reflection such as
`object.__setattr__` can still alter an existing instance after construction.
All six offline planner/projector/replay entrypoints now recheck the **current**
exact-class host, tenant, selected-ID tuple and consent/grant field types before
using the scope. Validation and authorization methods are called on the exact
`SelectedIncidents` class, so shadowing an instance method cannot skip these
checks. Invalid mutated hosts, tenant strings, ID lists/duplicates and
non-boolean grant/consent values fail closed with generic errors. Ordinary
unmodified scopes and existing projection/replay return contracts are unchanged.

This is **defense-in-depth for local input invariants**, not a security boundary
against an attacker already executing Python in the process. A caller able to
forge a *valid-looking* scope or mutate it concurrently is not authenticated by
these checks. Real enterprise reads still require separately verified current
provider/tenant/consent authority, exact HTTPS request/response identity,
revocation fences and atomic persistence-time checks. No transport, checkpoint,
source append, permission expansion or real ServiceNow acceptance was added.


## Constructor-time selected-scope binding (offline only)

Rechecking the *shape* of a frozen `SelectedIncidents` instance is insufficient:
`object.__setattr__` can replace a previously consented host, local tenant,
selected incident ID, or revoked consent/grant with another syntactically valid
value. The instance could then appear to carry the original read authority to
an entirely different selected scope. The offline scope now stores its exact
constructor-time host, tenant, ordered selected IDs and consent/grant flags,
and every planning, staging and replay entrypoint compares current values to
that binding **without rebinding them**. Missing bindings and post-construction
substitutions are rejected, even when every replacement field is individually
valid. Class-level validation prevents instance-method shadowing from skipping
the check. Unmodified scopes retain their existing read-only behavior.

This is **local defensive consistency**, not a secure authorization token.
Untrusted code running in the same Python process can forge or tamper with
private instance fields, and concurrent mutation cannot be ruled out by an
object snapshot. A real ServiceNow transport and persistence layer must verify
the current externally held tenant/provider grant and consent at the actual
request and again atomically before append/checkpoint, fence revocation, and
authenticate the HTTPS request/response identity. None of those capabilities
is implemented here. Fake-response tests are not real-provider acceptance,
source completeness, customer outcomes, or permission to perform writes.


## Supplied HTTP metadata gate (offline, no authenticated receipt)

The optional pure `project_supplied_http_response(scope, plan=..., request_method=..., request_url=..., response_url=..., status=..., content_type=..., redirect_count=..., body=...)` contract validates **caller-supplied** fake/transport metadata before invoking the existing exact-plan projector. It refuses anything other than a literal `GET` for the planned URL, an identical final URL with zero redirects, an integer HTTP 200, and `application/json` with at most an explicit UTF-8 charset. It also rejects booleans/floats masquerading as status or redirect counts, mixed/HTML media types and unexpected source pages. Valid output carries `supplied_http_metadata_checked: true`, but **`authenticated_transport_receipt: false` and `transport_grant_verified: false`**; the historical completeness, timestamp and checkpoint flags remain unknown/false.

This is intentionally **not an HTTP client, TLS validator, source receipt or proof that the supplied metadata matches a real response**. An untrusted caller can fabricate all of these fields. A future actual ServiceNow transport must separately enforce HTTPS certificate and origin verification, reject redirects before following them, authenticate its own exact request/response pairing, check the current provider account/tenant grant and consent, implement bounded retries/backoff, and fence revocation at atomic persistence time. A successful offline gate never authorizes append, checkpoint advancement, user provisioning, business completion or agent execution.

Synthetic fake-transport tests cover accepted JSON media types, wrong request/final URLs, redirects, 301/401/403/429/500, type-confused status/redirect fields, HTML/non-UTF-8/header-injected media types, wrong tenant or revoked grant, changed plan watermark, unselected records, old source times and malformed JSON. No provider request or customer-controlled IdP/ServiceNow sandbox acceptance is performed.
