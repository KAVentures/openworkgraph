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
