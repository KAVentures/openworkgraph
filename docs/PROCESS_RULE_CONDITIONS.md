# Declared rule checks

Saved process rules may include an optional bounded structured predicate. The
local and organization editors share an offline condition builder. Adding,
removing or changing a condition clears the rule confirmation and exact-draft
review; saving produces a new immutable model revision. Organization reviewers
see the complete predicate, and a changed version needs a new independent review.
Existing text-only rules serialize exactly as before, preserving their model
and runtime specification hashes.

The predicate has `match: "all"` or `"any"`, with one to eight conditions:

```json
{"match":"all","conditions":[
  {"path":"/approval/status","operator":"eq","expected":"approved"}
]}
```

Paths are RFC6901 pointers, at most 240 characters/eight segments. Operators are
`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `exists` and `not_exists`.
Expected values are bounded JSON scalars, or 1–20 scalars for membership.
Ordering accepts numbers only; no string/boolean numeric coercion occurs.
Finite numbers have magnitude at most 10^15. Strings have at most 240 characters.
Null is a present value; a missing fact is unknown except for explicit existence
checks. Arrays/objects cannot satisfy scalar comparisons. All/any combinations
use three-valued logic: false dominates all, true dominates any, and otherwise
an unknown operand leaves the result unknown. There is no expression language,
code evaluation, date conversion, currency conversion or automatic routing.

## Checking an authorized file

On a saved local process, choose case-facts JSON, explicitly authorize its local
analysis, and select **Check facts against saved rules**. Selection alone sends
nothing. The raw file reaches the strict server JSON parser, which rejects
duplicate keys, non-finite numbers, unknown envelope fields and excess bounds.

```json
{"cases":[{"case_id":"case_1","facts":{"approval":{"status":"approved"}}}]}
```

The dashboard-only endpoint is
`POST /v1/process-catalog/check-rules?workflow_id=…&revision=…`, with
`X-OWG-Case-Facts-Authorized: true`. A normal AI bearer cannot use it. The body
limit is 128 KiB, with 1–50 unique opaque case labels. Each facts object permits
at most 5,000 JSON nodes, depth eight, 100 entries per object/list, 120-character
keys and 4,096-character strings. Supply only necessary authorized fields;
unused fields still reach this local endpoint but are not retained.

An optional `activity_ids` list (1–80 IDs) describes a caller-proposed route.
It must follow the saved graph from entry to terminal; otherwise no rules are
evaluated for that case. A valid proposed route limits checks to its transition
rules. Without a proposed route, each rule is checked independently. Text-only
and unconfirmed rules are explicitly unevaluated. Manual review conditions
remain unevaluated regardless of any predicate result.

The response identifies the exact immutable process revision and model hash,
reports each rule's matched/not-matched/unknown or skipped status, and contains
no fact values, expected values or rule text. It also reports whether a newer
saved version exists. No overall compliance verdict is produced. Changing the
selected file or draft invalidates pending results. Downloading the minimized
report is a separate action that creates an external copy.

Facts and comparison reports are not stored in canonical evidence, process
knowledge, browser storage or source adapters. No source read or model-provider
call occurs. Facts are caller-declared: OWG has not authenticated their origin,
joined them to observed cases, approved policy, verified outcomes or granted
execution rights. Structural conformance remains a separate measurement.

## Portable designs and generated agents

JSON specifications retain the predicate. PDDs display it explicitly. BPMN uses
standard sequence-flow documentation prefixed `OWG declared predicate v1:`;
supported import validates that metadata and always restores rules unconfirmed.
Malformed recognized metadata is rejected. No executable BPMN expression is
created and imported source approval is never inherited.

The generated SDK planner pauses on a route containing structured predicates.
The separate customer executor reports
`structured_rule_source_fact_implementation_missing` and refuses execution
before tools or source calls. A successful dashboard check cannot remove this
guard. A customer-specific implementation must establish its authoritative fact
source and review the decision semantics before automating these conditions.

## Shared organization checks

The organization workspace supports the same raw-file analysis against a
selected shared revision, including historical revisions. This is an explicitly
remote transfer: its separate consent states that the selected facts will be
sent to the organization's Gateway. Selecting a file or ticking consent performs
no request. Replacing the file, clearing results, editing a draft, opening a
revision or disconnecting invalidates pending results and resets consent where
appropriate. No credentials or case facts enter browser storage.

`POST /v1/human/processes/{workflow_id}/check-rules?revision=…` requires a
delegated human identity with `org:process:read`, access to that organization's
shared process, and `X-OWG-Case-Facts-Authorized: true`. Service/admin tokens and
personal evidence scopes do not substitute for that permission. Scope and
process access are checked before reading facts and again during the check.
The same 128 KiB body and predicate/fact bounds apply; no source/model calls or
model/review changes occur. Current independent design review is reported as
context and never treated as fact authenticity or execution authorization.

The Gateway stores a minimal inspection audit containing workflow ID, revision
and case count, without facts, case labels, predicate outcomes or the comparison
report. The response sets `minimal_inspection_audit_persisted: true`. All fact
and comparison-retention flags remain false. An explicit minimized-report
download creates an external copy. Shared checks do not read personal local
snapshots or implicitly share employee observations.
