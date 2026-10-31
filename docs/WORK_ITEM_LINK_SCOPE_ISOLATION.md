# Work-item link scope isolation

Roadmap: [#326 Section A](https://github.com/KAVentures/openworkgraph/issues/326),
[#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264).

## Defect and boundary

Opaque browser-tab and clipboard-transfer identifiers are **not globally
unique**. Previously, the deterministic linker indexed these IDs across the
entire selected event list. Two unrelated organizations, employees, devices
or capture sessions could reuse an identifier and be incorrectly merged.
The weaker app/surface-continuity candidate could also point across these
boundaries when adjacent observations had the same app label.

These are evidence-integrity and isolation defects, not a justification for
inferring a business case, task completion or an agent permission.

## Conservative rule

`server/work_item_links.py` now groups ephemeral clipboard transfers and
browser tabs only when all four recorded scope dimensions match exactly:

- `organization_id`
- `actor_id`
- `device_id`
- `session_id`

A missing legacy value is represented by an empty component, **not a wildcard**
matching a known component. Malformed or overlong scope values prevent
ephemeral joins for that observation. App/surface-continuity candidates obey
the same scope rule and remain **unmerged**.

The existing exact normalized resource-reference path is separate: a stable
reference can still link observations from different actors or sessions, but
conflicting provider/kind/host/organization identities suppress that reference.
The new rule does not grant cross-tenant access or turn record identity into
task identity. No time-only joins, text, titles, clipboard payloads, URLs,
provider calls or writes are introduced.

## Reproducible synthetic checks

Run from the repository root:

```bash
python -m pytest -q tests/test_work_item_links.py tests/test_reconstruction_owg_native.py
```

The new cases reuse the **same** opaque transfer/tab ID across organizations,
actors, devices and sessions; test same-scope positive linking; require that
adjacent same-app observations cannot create cross-scope candidates; reject
unknown/malformed scope as a wildcard; and preserve cross-actor exact-record
grouping. Published projections do not expose raw scope identifiers.

The preregistered held-out assignment F1 >= 0.90 and contamination <= 0.05
targets are **not claimed achieved**. This is a conservative false-positive
isolation fix, not a model-graded semantic linker or a measured F1 uplift.
Synthetic tests are not real-user, real-provider, or customer-pilot validation.
Any change in linked-item recall should be measured on the existing
preregistered family split, without changing answer-key truth or tuning to
holdout failures.

## Invalid or contradictory record claims

A valid-shaped opaque record reference can still be contradictory: one observed
row may claim Jira while another claims GitHub, or may disagree on record kind,
host or organization. Existing exact-record grouping correctly suppresses the
entire conflicting reference. The linker must **not** then interpret those
suppressed rows as ordinary unreferenced observations and attach them by a
shared clipboard-transfer ID, tab ID, or same-app adjacency.

Rows carrying a conflicting reference, or an explicitly malformed reference
object, are now ineligible for **all** ephemeral joins and weak candidates.
Their raw reference and scope values remain private. Other independent valid
references still link, and genuinely unreferenced rows can still attach to a
single valid record in the same scoped tab. This is a fail-closed observation
integrity rule, not proof that the underlying source identity is authentic.

The synthetic regressions include contradictory provider claims, malformed
provider/ref claims, transfer and tab reuse, a weak surface candidate, and
unaffected same-record/tab positive behavior. No held-out ground truth,
preregistered split, thresholds, scoring code or provider credentials change.


## Literal opaque-ID admission

Ephemeral `tab_context_id` and `clipboard_transfer_id` must be **literal
strings** matching the existing bounded token grammars. The linker no longer
stringifies booleans or integers into plausible IDs (for example, `True`
or `7`), which could otherwise fabricate cross-record links inside a
session. A present top-level field is authoritative for selecting that
observation's claim: an invalid, null or empty top-level value is rejected
rather than silently replaced with a metadata fallback. Metadata-only legacy
string IDs and valid explicit top-level string IDs continue to work.

Synthetic tests cover non-string metadata, invalid explicit top-level values
with plausible metadata fallback, valid metadata-only strings, and valid
top-level strings overriding malformed metadata. These are local evidence
validation rules, not proof of a genuine observed copy/paste, tenant
authorization, business case identity, or verified outcome. The F1 and
contamination acceptance targets remain unverified; no private truth or
scoring rules were changed.


## Literal observation and surface identity

The deterministic linker now requires a literal string `event_id` before
admitting a row. Python coercion previously made `True` or integer `7`
look like plausible event IDs, despite neither being an observed identifier.
A malformed ID is omitted rather than assigned to a reconstructed work item.
Existing valid string IDs, including numeric-looking strings, remain supported.

The weak `surface_continuity` candidate likewise requires an actual string
`app` value. Booleans, numbers, lists and objects cannot be stringified into
an app identity that happens to match another event. A missing or malformed app
does not create a candidate; valid same-scope string app names still yield
**unmerged ambiguous candidates**, never automatic work-item membership.

These changes do not authenticate captured metadata, infer task identity,
validate source outcomes, or authorize cross-tenant reads. The new synthetic
regressions cover malformed event IDs, malformed app labels and positive
literal-string compatibility. They are not held-out performance evidence:
preregistered F1 >= 0.90 and contamination <= 0.05 remain unverified, and no
private evaluation truth, split or scoring threshold was changed.


## Literal record-source and organization identity

Exact-record linking also needs type-safe *source* identity. The shared
record-lane parser supports legacy values by converting provider, kind, host
and organization fields to strings. In this multi-signal linker, however,
a non-string value such as boolean `True` or integer `7` is not evidence
that the provider, host or tenant was literally named `"true"` or `"7"`.
Such conversion could merge unrelated records sharing an opaque reference.

Before invoking the existing normalized-record identity parser, the linker
now requires literal string `resource_ref`, `provider` and
`resource_kind` values. Optional `host` and `organization_id` may be
absent or null for legacy observations, but **when supplied as values** they
must be strings. A malformed explicit record claim is tainted: its opaque
reference group is suppressed, and it cannot be reattached through a tab,
clipboard-transfer ID or weak app-continuity candidate. This is conservative
withholding, not proof that any source is genuine. The shared record-lane
module, REST/MCP contracts and source-provider permissions are unchanged.

Synthetic regressions cover boolean provider/kind, integer host/organization,
a single malformed claim alongside valid claims and shared weak signals,
valid literal strings including `"true"` and `"7"`, and missing optional
legacy host/organization. No hidden truth, model, real-provider or business
write is involved. The preregistered F1 and contamination targets remain
unverified until an independent held-out run.


## Conflicting duplicate observation IDs

An `event_id` is the identity of an observed event, not a dedupe preference.
Previously, a second row with the same ID was silently dropped even when it
contradicted the first row's record reference, tenant scope or timestamp.
This allowed a first-row-wins observation to participate in same-record,
clipboard, tab and candidate links despite ambiguous provenance.

The linker now preflights all literal, grammar-valid event IDs. Equal duplicate
payloads remain idempotent. If **any** occurrence of an ID has a different
payload, **every** occurrence of that ID is withheld before reference grouping,
including when one copy has an invalid timestamp. Other valid IDs and their
unrelated evidence remain available. Input order cannot choose which
contradictory claim wins. This conservative rule can reduce recall, but cannot
fabricate a task, a business outcome or a source permission.

Synthetic regressions cover changed record identity, tenant scope, valid and
invalid timestamps, reversed duplicate order, idempotent exact duplicates and
unaffected independent record groups. They are not a held-out F1 improvement
or evidence of real customer/provider validation. The preregistered family
holdout and its private truth remain unchanged.


## Explicit null reference claims

A present `metadata.resource_reference: null` is an explicit but invalid
record-reference claim, not an absent key. Previously, the linker treated
this particular non-object value as ordinary unreferenced evidence. That
allowed a null-claim row to attach to a valid record through the same
browser tab or clipboard-transfer token, or to appear as a weak same-app
candidate. Other malformed non-object claims were already withheld.

The linker now taints **every present non-valid reference claim**, including
explicit `null`, before constructing ephemeral links or weak candidates.
A genuinely absent `resource_reference` key still means no record was
claimed and retains legacy tab/transfer behavior and unmerged surface
candidates. Valid literal record references remain unchanged.

Three synthetic regression functions cover null-claim tab/transfer
withholding in both input orders, surface-candidate withholding, and
positive absent-key behavior. This is conservative evidence hygiene,
not authentication of source records, evidence of business completion,
or a measured improvement in held-out F1. It introduces no provider
calls, new permissions, model spending, or changes to evaluation truth.


## Explicitly malformed ephemeral scope versus absent legacy scope

The four ephemeral-link scope dimensions have two distinct states: a field
**absent** in a legacy observation means its unknown component is represented
by the existing empty sentinel; a field **present with null or an empty
string** is an invalid identity claim and cannot participate in a tab,
clipboard-transfer or same-app continuity link. Previously, the implementation
coalesced both states and could link a malformed/null-scoped observation to a
legacy unscoped observation using a reused opaque tab or transfer token.

This is a conservative fail-closed distinction. Two genuinely legacy
observations with the same missing scope components can still use the existing
ephemeral evidence; an observation with an explicitly null/empty component
cannot use those weak signals. Exact normalized record-reference linking is
separate and retains its existing source/tenant conflict checks, even when a
local ephemeral-scope component is unusable. No missing value authorizes
cross-tenant access.

The synthetic regressions exercise all four dimensions, both null and empty
values, reversed input order, tab and clipboard links, weak surface candidates,
positive legacy-missing controls, and independent same-record positive controls.
They do **not** measure a held-out F1 uplift, authenticate identity, or prove
interruption rejection on real customers. The preregistered family holdout,
assignment F1 >= 0.90 and contamination <= 0.05 gates remain unchanged and
unverified; no private truth or evaluation scoring has been modified.


## Multiplexed clipboard-transfer token fanout

A clipboard-transfer identifier is an opaque capture signal, **not** a
globally unique business-case key. Even within one exact
organization/actor/device/session scope, a token can be reused across
interleaved work. Previously, three distinct valid record references
sharing a token were automatically unioned into one linked work item.
This fabricated cross-record membership despite unresolved fanout.

For each **exact scoped token**, the linker now withholds automatic
clipboard-transfer joining if more than two distinct valid record
references appear in its observations. It still groups each record by
independent exact-record evidence. The public projection adds
`ambiguous_clipboard_transfer_groups` (aggregate count only) and
`link_contract.multiplexed_clipboard_transfer_is_unmerged: true`.
It does not emit the private token or assert which pair is related.
Two-record transfers, including repeated observations of either
record, retain the existing linking behavior. A third record in a
*different* capture scope does not invalidate a valid two-record group.

Synthetic regressions exercise a three-record interleaving in both
input orders, unaffected same-record membership, repeated two-record
copy/paste, and an independent capture session reusing the token.
This is a conservative contamination guard, not a general guarantee
that two-record transfers are genuine; the current linker does not
authenticate provider identity or require semantic copy/paste direction
for every transfer event. Time proximity never supplies a join.

The preregistered held-out assignment F1 >= 0.90 and contamination
<= 0.05 remain **unverified**; no holdout truth, split, evaluation
scoring, provider calls, model spend, new source privileges or business
writes were introduced. Native repository tests and exact-head CI are
required before merging.
