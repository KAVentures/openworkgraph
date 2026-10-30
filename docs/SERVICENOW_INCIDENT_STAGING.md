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
