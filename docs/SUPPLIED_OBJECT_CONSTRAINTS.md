# Shape constraints over supplied OCEL relationships

The existing object-rules workspace also checks explicitly supplied directed
object relationships. Choose **Supplied assertions: distinct direct U neighbors
of each T**. This is a separate template; existing event cardinality,
shared-event object cardinality and synchronization retain their meanings.

For example, an imported invoice may explicitly reference an order even when
the two objects share no event. A supplied-relationship rule checks that declared
link. The shared-event rule continues to check the different observed association.
Neither graph is independently verified business truth.

```json
{
  "rules": [{
    "template": "supplied_object_cardinality",
    "object_type": "invoice",
    "related_type": "order",
    "direction": "outgoing",
    "qualifier": "belongs to",
    "min": 1,
    "max": 1
  }]
}
```

POST to `/v1/event-logs/{log_id}/object-constraints` with the local dashboard
session. AI bearer credentials are insufficient. Existing import/append storage
consent remains required; analysis itself does not add a capture, sharing,
business-action or execution grant. The JSON body is bounded to 64 KiB and rejects
duplicate keys and non-finite values. Valid existing rule requests are unchanged.

## Counting semantics

- Every retained object of `object_type` is one unit, including objects with no
  events or assertions. Missing types are disclosed; they do not create units.
- `outgoing` (the default) follows stored source → target; `incoming` follows
  links into the focus object. `both` selects either direction without changing
  the underlying stored assertions.
- Count **distinct related objects** of `related_type`. Multiple qualifiers,
  repeated assertions and opposite directions do not inflate a neighbor count.
  A self-link counts once when the two types match. No transitive links are inferred.
- Omitted/null `qualifier` selects all qualifiers. A supplied string matches
  literally: empty, whitespace and leading/trailing spaces are distinct. Bounds
  are strict integers from 0 to 1,000,000; an omitted maximum is unbounded within
  the supported graph. A no-op minimum0/no-maximum rule is refused.
- Rules inspect the **whole retained log**. The main case/cohort filter does not
  select these graph assertions or merge the objects' case histories.

The result's `violating` count means a degree outside the requested bounds in
the retained assertion set. A missing declaration can cause a shape violation;
this does not prove that a business relationship is missing. `business_compliance`,
`source_completeness` and `relationship_validity` remain `unknown`, and
`independently_verified`/`execution_authorized` remain false. Rates are exact for
the retained focus objects; the new template supplies no population confidence
interval. Existing templates retain their existing interval/ordering behavior.

Static assertions have no observed validity period. This template does not use
them for temporal synchronization, causal precedence or historical reconstruction.
Event/activity/`via_activity` fields are refused rather than silently interpreted.
The underlying [OCEL retention contract](SUPPLIED_OCEL_OBJECT_RELATIONSHIPS.md)
and original OCEL2.0 direction/qualifier definition still apply.

## Exact basis, limits and local exports

Requests using the new template return `supplied_relationship_basis` with the
whole retained relationship-set SHA-256, a separate object ID/type SHA-256,
retained object/assertion counts and local append revision. A newly declared
object with no links changes the population digest/revision even if the assertion
digest is unchanged. Empty assertion sets have a real empty-set digest. These
pins describe retained input, not source completeness or historical validity.
Existing responses without the new template keep their existing shape.

The existing maximum20 rules and50 violating examples apply. Examples are bounded
and explicitly marked when truncated. Before materializing distinct neighbors,
each rule counts oriented matching assertion candidates against the existing
5,000,000 pair-work budget. `both` can count a self/opposite/qualifier occurrence
twice for work estimation; this conservative estimate never inflates the final
distinct-neighbor result. A budget refusal returns `complete:false` and no partial
unit/violation counts; other rules can still complete. A corrupt supplied index
refuses the whole analysis before returning misleading counts. Existing retained
graph limits also apply (1M assertions,10k per source object).

Exhausting memory while evaluating one supplied rule returns an explicit unknown
result without partial counts. If the input snapshot itself cannot be pinned
within available memory, the whole analysis refuses with a bounded error instead
of presenting an unpinned result. Source labels/SQL are not echoed in those errors.

The editor offers all retained types for this template, including those omitted
from observed lifecycle maps because they have no events. Results show their
basis and offer explicit local JSON/CSV downloads. CSV includes the unknown/
unverified/descriptive flags and relevant input digests. Editing the pending
rules invalidates old results/downloads, and held responses cannot repopulate an
edited or replaced view. These downloads may include source labels and local
examples; they are separate from minimized coded shared-case disclosures.

Results are ephemeral analyses. No process rule is automatically approved, no
agent is generated or run, and no conformance certificate or business outcome is
established. Owned synthetic degree/HTTP/browser/scale oracles do not certify full
OCPM, live-provider semantics, customer deployment or competitor parity.
