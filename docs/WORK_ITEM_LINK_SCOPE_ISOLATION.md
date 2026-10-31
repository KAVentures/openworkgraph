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


## Type-sensitive duplicate observation evidence

Duplicate `event_id` claims must have **identical literal payloads**, not
merely Python-equal values. Python considers `True == 1` and `1 == 1.0`
true, including when those values occur in nested metadata dictionaries or
lists. A conflicting duplicate could therefore bypass the previous
first-row-wins conflict check, allowing its surviving record reference to
anchor a same-record, tab or clipboard association.

The duplicate preflight now compares literal JSON-shaped payloads
structurally and type-sensitively, including nested dictionaries and lists.
Dictionary key order is irrelevant; list order and scalar types are not.
Non-finite floats, unsupported Python objects, cyclic or excessively large
nested structures cannot establish identical duplicate evidence. The
comparison has a 10,000-node work bound; uncertain duplicate IDs are
withheld in **all** input orders before any linking. Ordinary unique
observations and genuinely identical JSON duplicates retain their prior
behavior. This is an integrity guard, not a new source identity, model,
permission, or authorization signal.

Synthetic regressions cover nested boolean/integer and integer/float
collisions, reversed input order, positive nested idempotence, dictionary
key order, non-JSON payloads and bounded/cyclic comparison. They do not
authenticate source events, demonstrate real-customer interruption
rejection, or measure held-out F1 uplift. The preregistered assignment
F1 >= 0.90 and contamination <= 0.05 targets remain unverified; no
evaluation truth, split, scoring threshold, provider calls or model spend
changed.


## Contradictory explicit clipboard direction

An opaque clipboard-transfer token can be reused even inside a single
capture scope. Previously, two distinct records with the same token were
automatically merged when their observed actions were **both copies**,
**both pastes**, or a paste occurred before the only copy. Those events do
not establish an observed copy-to-paste transfer.

For token groups containing **any** explicit `copy` or `paste` action,
the linker now requires at least one copy **strictly earlier** than
a compatible paste. For two referenced records, the copy/paste pair must
span the records (or involve an unreferenced event). A copy/paste pair
entirely inside record A cannot attach a copy-only record B. Contradictory
groups retain independent exact-record links, but their transfer edge is
withheld. Equal timestamps cannot establish direction. Chronology is
**only a negative veto** for an already observed shared token, never
positive time-only joining.

The response adds only aggregate
`inconsistent_clipboard_transfer_direction_groups` and the boolean
`link_contract.explicit_clipboard_direction_conflicts_are_unmerged`;
private token values, source locators, titles and clipboard contents are
not returned. Groups containing **only** generic `browser_click` or other
non-directional event types retain legacy behavior; this change does not
authenticate clipboard transfers or guarantee that every two-record token
represents one business case. Source-identity and exact-scope checks still
apply, and multiplexed (>2 distinct records) tokens remain unmerged.

Synthetic regressions cover copy/copy, paste/paste, paste-before-copy,
same-record copy/paste with an unrelated second-record copy, equal-time
ambiguity, ordered cross-record copy/paste, reversed input row order,
privacy-minimized output and legacy non-directional compatibility.
These tests do not establish real-user acceptance or improvement on
preregistered held-out F1; the 0.90 F1 / 0.05 contamination gates remain
unverified. No private truth, scoring, model/API calls, provider permissions,
business writes or automatic execution were changed.


## Two-record clipboard direction cannot be established by loose events alone

The explicit-direction guard also needs to distinguish a *direct* observed
cross-record transfer from an unrelated pair involving an unreferenced
observation. A scoped token containing A-copy, unreferenced-paste and
B-copy-only previously passed because A-copy preceded the loose paste; the
linker then unioned **all** token members, incorrectly attaching B. The
inverse loose-copy → A-paste with unrelated B-copy had the same problem.

For groups containing **two distinct referenced records**, a directionally
explicit token now requires a strictly earlier copy from one referenced
record followed by a paste on the *other referenced record*. An unreferenced
copy or paste cannot stand in for either endpoint to justify joining both
records. If the direct cross-record pair is absent, the entire clipboard
token join is withheld, while independent same-record evidence remains.
For zero or one referenced record, existing copy→paste-to-unreferenced
behavior is preserved. Entirely non-directional legacy groups remain unchanged; mixed groups now
require explicit direction as described below, and the preexisting >2-record
fanout veto still applies.

Three new synthetic regression functions cover two loose-bridge false joins
in both input orders, a genuine A-copy→B-paste with an extra loose event,
and a one-record-to-loose positive control. They do not authenticate source
records or prove that every valid-looking token is a real transfer. No time
alone creates a link. The preregistered held-out F1 ≥ 0.90 and contamination
≤ 0.05 targets remain unverified, with no truth, split or scoring changes,
model spend, provider calls, new permissions or business writes.


## Mixed directional and generic clipboard observations

A scoped transfer token may appear on a mix of explicit `copy`/`paste`
events and generic capture events such as `browser_click`. Previously the
direction veto ran only when **every** event was explicitly directional.
Adding one generic event therefore allowed an otherwise contradictory
A-copy/B-copy token, a paste-before-copy token, or an A-copy/A-paste token
with unrelated B-click to merge distinct source records.

Now **any** explicit `copy` or `paste` in the token group requires an
observed, strictly ordered compatible copy-to-paste pair before the group
can create a clipboard join. With two distinct referenced records, the
pair must directly cross those records. A generic event cannot supply
either missing endpoint or disable the direction veto. Groups with no
explicit copy/paste actions preserve the historical nondirectional
transfer-token behavior. A genuine A-copy -> B-paste remains linkable
when an additional generic observation shares the token.

Three synthetic regression functions exercise contradictory mixed groups
in both input orders, independent same-record retention, and the positive
mixed-but-direct cross-record pair. The existing public aggregate
`inconsistent_clipboard_transfer_direction_groups` counts vetoed groups;
private token values are not returned. The fix does **not** prove token
authenticity, validate provider authorization or ensure that unrelated
generic observations in an otherwise valid group belong to the same task.
The preregistered held-out F1 >= 0.90 and contamination <= 0.05 remain
unverified. No private holdout truth, scoring, thresholds, model/API spend,
source privileges or business writes changed. Native repository CI and
review are required before merging.


## Loose observations cannot hitchhike on a direct cross-record transfer

A valid exact-scoped A-copy -> B-paste proves an ordered transfer between
**A and B**; it does **not** prove that every unreferenced event carrying
the same opaque clipboard token belongs to that case. Previously the linker
verified the direct pair but then unioned the **entire token group**,
including unrelated loose `browser_click`, `copy` and `paste` rows.
A reused token could therefore contaminate an otherwise valid A/B case.

For a token with **exactly two distinct valid referenced records and any
explicit copy/paste action**, the existing strict direct cross-record
direction test must pass first. Only observations with those validated
record references then receive the clipboard-transfer join. Unreferenced
rows stay separate unless **independent evidence** supports them (for
example, an exact-scoped tab containing only one observed record). Same
record evidence remains intact, and input order cannot change the outcome.

The restriction does **not** change the one-record-to-loose observed
copy/paste contract or legacy all-generic transfer groups, where the
pre-existing weaker semantics are retained for compatibility. It adds no
time-only joins, semantic inference, source privileges, authorization,
task-completion assertions or new public/private token fields.

Synthetic regressions exercise loose generic/copy/paste rows in both
input orders, an independently evidenced single-record tab join, repeated
same-record observations, and all-generic legacy behavior. This is a
conservative contamination guard, not proof of authentic clipboard
provenance or a measured improvement in held-out reconstruction F1.
The preregistered F1 >= 0.90 and contamination <= 0.05 targets remain
unverified. No private evaluation truth, split, scoring threshold,
provider call, model/API spend or business write changed. Native
repository CI and review are still required before merging.


## Tainted record claims veto their exact ephemeral token

A row with a contradictory or malformed explicit record reference was already
excluded from tab/clipboard linking. However, simply removing it could make a
multiplexed tab appear to contain **one** valid record, so a loose observation
was automatically attached to that record. Similarly, an apparently valid
A-copy -> B-paste pair could still merge A and B despite a tainted third
record claim sharing the exact clipboard token. Excluding the tainted row
alone did not make either remaining inference safe.

The linker now treats a tainted, timestamp-valid observation as a **veto**
on the corresponding exact (organization, actor, device, session, token)
tab or transfer group. It never uses the tainted observation as a join
endpoint, and it does not convert the tainted record into a loose event.
Independent exact-record grouping and different tokens or scopes are
unaffected. A malformed/missing scope cannot poison a known scoped group,
and an invalid event ID or timestamp is not admitted as an observation.
No new public token, tenant, record label or private content is exposed.

Four deterministic synthetic regression functions cover (1) a formerly
single-record tab with a tainted competing claim, (2) an otherwise valid
direct copy/paste pair with a tainted third claim, both in reversed input
order, (3) noninterference across every scope dimension and different
token values, and (4) two individually well-formed but contradictory
provider identities on the same opaque record reference. They preserve independent same-record joins. The veto is
deliberately conservative and may withhold legitimate links when source
claims are inconsistent; it is not evidence that a source record or
clipboard action was authenticated.

No preregistered truth, family split, scoring, model/API budget, source
permissions, or business state changed. Held-out assignment F1 >= 0.90
and contamination <= 0.05 remain **unverified**, and synthetic regression
coverage is not a real-user pilot or real-provider acceptance. Native
repository pytest and exact-head CI remain required before merge.


## Conflicting duplicate event IDs also veto their own ephemeral tokens

When two observations reuse the same `event_id` with contradictory payloads,
the linker already discards both instead of choosing an arbitrary winner.
That removal alone was not sufficient: the surviving observations could make
the discarded row's browser tab look single-record, or make its clipboard
transfer appear to be an uncontested direct A-copy → B-paste. Either could
create a false work-item membership despite the contradictory source evidence.

For each conflicting duplicate occurrence with a valid timestamp and literal
exact capture scope, the linker now vetoes only its own valid scoped
`tab_context_id` and/or `clipboard_transfer_id`. Both contradictory
occurrences contribute their own scoped token; the veto never crosses
organization, actor, device, session or a different opaque token. A malformed
scope, invalid timestamp or invalid token cannot name a group to veto.
Independent valid same-record links still work. Byte-identical duplicates
remain idempotent, and no new public token, title, clipboard text or source
locator is exposed.

Four synthetic regressions exercise false single-record tab and direct
cross-record transfer joins after conflicting-ID removal, both input orders,
independent same-record evidence, two-tenant veto isolation, a third unaffected
tenant, a separate unaffected token and an invalid-timestamp occurrence.
These cases check **conservative contamination prevention**, not an improved
measured assignment F1. The preregistered held-out F1 ≥ 0.90 and
contamination ≤ 0.05 gates remain unverified. Native pytest, cross-platform
CI, review and current-main compatibility are required before merging.
No private evaluation truth/splits/scoring, model/API spending, provider
access, permissions or business writes were changed.


## Single-record tabs cannot transitively bridge conflicting record components

The single-record-tab heuristic is weaker than an explicit record reference:
an unreferenced observation in tab A is not proof that a clipboard-linked
component already associated with record B belongs to record A. Previously,
the linker applied clipboard unions before tab unions but checked only the
**individual tab rows** for record multiplicity. A tab containing A and a
loose observation already joined to B could therefore merge A/B, even though
no direct A/B transfer was observed. Likewise, two single-record tabs could
each attach to different members of one generic clipboard-only loose component,
fabricating an A/B join.

After clipboard joining and **before** tab joining, the linker now records the
current component roots, up to two distinct literal record references per
component, and up to two different single-record-tab targets per loose
component. A tab attachment is withheld if its loose component already
contains a different record, or if two tabs target that component with
different records. These checks are made against the fixed pre-tab components,
so input order and whichever tab is processed first cannot pick a winning
record. Independent exact-record joins and already supported direct clipboard
transfers remain; an uncontested A tab may still attach a loose component
without a foreign record or competing tab target.

The additive public
`ambiguous_tab_component_attachments_suppressed` count reports withheld
**attachments**, not withheld events or proven incorrect business cases.
`link_contract.competing_tab_component_records_are_unmerged` is true.
Neither field exposes tenant/actor/device/session IDs, clipboard tokens, tab
tokens, titles or clipboard contents. The count is not a quality score.

Three synthetic regression functions cover a foreign B clipboard component
in A's tab, two competing A/B tabs targeting the same generic loose
component, and a positive uncontested A-tab case alongside an independent B
tab, including reversed input order. This is a conservative false-join guard,
not source authentication, full task reconstruction or measured holdout uplift.
It may withhold genuine but insufficiently evidenced relationships.
No time-only joins, source privileges, model/API spending, private evaluation
truth or business writes were added. The preregistered held-out assignment
F1 >= 0.90 and contamination <= 0.05 remain **unverified**. Native pytest,
exact-head CI, review and current-main compatibility are still merge gates.


## Explicit whitespace/control scope rejection

The four ephemeral-link identity dimensions (organization, actor, device,
capture session) must be **literal, nonempty, bounded strings**. In addition to
rejecting explicit null, empty, nonstring and overlong values, the linker now
rejects strings that are entirely Unicode whitespace or contain ASCII C0/DEL
control characters. A repeated malformed value cannot become a common tab,
clipboard-transfer or same-app candidate identity. This is a fail-closed
validation rule for *ephemeral* evidence, not a tenant-authentication mechanism.

No whitespace trimming, case folding, guessed defaults or wildcard matching
is introduced: a legitimate nonblank string with interior spaces is retained
verbatim and distinct from another spelling. Genuinely absent legacy scope
fields keep their existing exact-unknown behavior. Independently validated
same-record evidence still uses its separate source-identity conflict checks;
a malformed actor/device/session scope does not erase such evidence.

Three synthetic regression functions exercise all four dimensions with
whitespace-only, tab, NUL, DEL and Unicode whitespace inputs across transfer,
single-record-tab and same-app candidate paths, in both input orders. Positive
controls preserve literal nonblank scope, exact matching, and independent
same-record linking. No source/tenant permission, retention or capture behavior
is changed. This does not prove tenant isolation against maliciously fabricated
*valid* identities, and it does not measure any held-out F1 uplift. The
preregistered assignment F1 >= 0.90 / contamination <= 0.05 gates remain
unverified until unchanged held-out scoring and exact-head native CI run.


## Invalid timestamps do not erase contradictory source identity

A validly identified observation can carry a malformed or missing `observed_at`
while still claiming an exact opaque source record. Previously, the linker
discarded that observation **before** checking its provider, kind, host and
organization identity. Two timestamp-valid rows for record A could therefore
be published as one linked item despite a third row claiming the same opaque
reference under a contradictory provider or tenant, solely because the third
row's timestamp could not be ordered.

The exact-reference identity conflict pass now precedes timestamp admission,
consistent with the existing record-lane fail-closed contract. A conflicting
or explicitly malformed source claim naming a literal valid opaque ref
suppresses the affected reference group, even if that observation's timestamp
is invalid. Other independent record groups remain available. A valid
same-identity claim with a bad timestamp does **not** itself create a conflict.

An invalid-time row still contributes **no** observation membership,
chronology, clipboard direction, tab attachment, scoped ephemeral-token veto
or app-continuity candidate. Time is not used to infer a link. Synthetic
regressions cover contradictory provider identity, malformed provider type,
independent record survival, and valid directional transfer unaffected by a
same-identity invalid-time row.

This is conservative source-identity consistency, **not** verification of the
provider, record, workflow, completion or business outcome. It does not alter
private holdout truth, evaluation splits or score thresholds. Native pytest,
held-out assignment F1 and contamination, and exact-head CI remain required;
F1 >= 0.90 and contamination <= 0.05 are not claimed.


## Explicit host and tenant identity admission

The exact-record linker also rejects **explicit nonempty** host or
`organization_id` values that are entirely whitespace or contain ASCII
C0/DEL control characters. Previously a whitespace-only host could be
stripped by the shared legacy normalizer and silently treated as an absent
host; a repeated control-bearing organization string could become an
apparently identical tenant identity. Neither is reliable source provenance.

A malformed explicit identity vetoes the claimed opaque record group,
including otherwise valid observations of that same reference. It cannot
be reintroduced through tab/clipboard/app-continuity signals. Independent
record groups remain available. Genuinely absent, null or empty legacy
optional host/organization values remain backward compatible; this guard
does not rewrite the shared record-lane normalizer.

The committed synthetic regressions exercise matching malformed identities,
one malformed claim amid otherwise valid records, unaffected independent
records, and legacy/valid positives. They do not authenticate source
identities or prove that observed records are business tasks. No private
holdout labels, scoring, API/model usage or provider permissions change.
Assignment F1 ≥ 0.90 and contamination ≤ 0.05 remain **unverified**.


## Conflicting duplicate event IDs cannot hide exact-record identity conflicts

The linker already discards all occurrences of a reused event ID when their
payloads differ, and vetoes valid exact-scoped tab/clipboard tokens on those
occurrences. That did not fully protect **exact source-record identity**:
two discarded rows could claim the same opaque record under different
provider, kind, host or organization identities, while other surviving rows
still formed an apparently unconflicted same-record group.

All conflicting duplicate-ID occurrences now contribute **identity
consistency checks only**, using the same literal source-claim admission as
ordinary observations. A contradictory or malformed explicit claim naming
a valid opaque reference suppresses that reference's entire linked group,
including other otherwise valid observations. A duplicate that merely
claims *different* valid references does not poison either independent
record group; neither discarded occurrence is treated as a link endpoint.
Exact duplicate payloads remain idempotent. Timestamp-invalid duplicate
occurrences can still invalidate an inconsistent source identity, but never
contribute membership, chronology or weak token attachment.

Synthetic regressions cover same-reference provider contradictions in
both input orders, malformed provider/host claims with invalid timestamps,
independent A/B records with a reused event ID, and identical duplicates.
This is a fail-closed consistency rule, **not** authentication of source
identities or proof of task/case boundaries. No titles, clipboard content,
source locators or tenant identifiers are newly exported; no automatic
agent execution, provider calls, new permissions or business writes occur.

Preregistered held-out assignment F1 >= 0.90 and contamination <= 0.05
remain **unverified**. The synthetic tests do not replace native pytest,
cross-platform exact-head CI, independent review or real consenting pilots.
No private evaluation truth, split, scorer or thresholds were changed.
