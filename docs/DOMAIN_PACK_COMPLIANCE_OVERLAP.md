# Installed domain pack compliance overlap (R23)

Open **Find overlapping compliance violations** in the domain-pack area.
Load installed packs, choose one installed pack, select an object type
and two to twelve mapped rules, and run the review. The dashboard shows
unique affected cases, a confirmed-violation multiplicity histogram,
per-rule outcomes and pairwise intersections/unions. The existing
compliance engine evaluates the complete projected population once per
selected rule. No rules or saved configurations are changed.

A case violating two rules counts once in the unique affected population
and twice in rule-violation instances. Unknown outcomes are separate
from confirmed violations and satisfied outcomes. Possible intersections
and unions treat unknown outcomes as possibly violating; these are
logical bounds, not estimated probabilities. Confirmed Jaccard overlap
is unavailable when the confirmed union is empty.

Do not combine distinct object-type populations or interpret overlap as
causal attribution, business loss or verified legal compliance. A
projected case may have incomplete retained history. Case IDs are never
joined across object types.

Inspect cases matching one or two selected rules, with confirmed all,
confirmed any or possible-all classifications, and page through the exact
matching retained case IDs. The API also supports larger subsets of
reviewed rules. JSON and CSV exports contain the aggregate review; share
mapped labels and case evidence only with authorized recipients.

## API

Dashboard-session-only GET and POST
`/v1/event-logs/{log_id}/domain-packs/{pack_id}/overlap`.

GET lists mapped canonical rule indices grouped by exact case type.
POST accepts `{"case_type":"BillingDoc","rule_indices":[4,5]}`.
To retrieve an evidence page, repeat the selection with
`expected_basis_sha256` and an `evidence` object containing
`rule_indices`, `mode` (all_violated, any_violated, possible_all),
`after` (null initially or the returned cursor), and `limit` (1–100).
The reviewed basis pins the installation, resolved definitions and
stored events, objects and event-object links. Changed source or
configuration refuses stale evidence. Active label mapping and hiding
are refused. This read-only analysis is bounded to 500,000 source rows,
two million case-rule outcomes and twelve rules. No partial sample
replaces an over-budget result.

Regression cases include invoice overlap, missing payment evidence,
unknown-state arithmetic, evidence pagination, selection validation and
work bounds. A Chromium scenario exercises the existing installed pack
dashboard. Real customer acceptance is not established by synthetic tests.
