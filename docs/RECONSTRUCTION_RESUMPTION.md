# Local interruption and resumption reconstruction — #326 A / #264 Stage 2 / #370 R1

This is an **opt-in, local, read-only** algorithm for inspecting where an already
production-linked work item appears to resume after an intervening case or
unassigned observation. It is not an autonomous case join, a source-authoritative
business-case ID, an execution trace or permission for an agent to act.

## Operator workflow

Use an existing, authorized, explicitly consented local JSON observation
snapshot. Do not upload source observations, private packets or outputs to
GitHub, chat, cloud analytics or a third-party model. From the repository root:

```sh
python -m scripts.reconstruction_review prepare \
  --events /private/observations.json --output /private/review-packet.json
python -m scripts.reconstruction_resumption \
  --events /private/observations.json \
  --packet /private/review-packet.json \
  --output /private/resumption-graph.json
```

The command replays the actual production linker, experimental proposal engine
and noncanonical `episodes` partition on the **exact** evidence digest.
It refuses stale/forged packets, changed/deleted/revoked observations,
incomplete two-hop scans and oversized windows. Output is a newly created
private JSON file (0600 on POSIX); existing files are not overwritten.
There is no network request, provider read/write, permission change or
canonical evidence edit.

## Algorithm and example

For `A, A, B, B, unknown, A, A`, the existing episode engine retains **four
separate episodes**. This feature adds **one** resumption candidate from the
first A episode to the last A episode, with two intervening episode IDs,
`other_production_linked_item` and `unassigned_observations` interruption
classes, and counts for the exact/proposed/unknown observations. It does
**not** merge the A episodes or assign the unknown observation to A or B.

An endpoint must have an independently observed, untainted literal source
record and production-linked membership. Both endpoints must match the exact
linked item, literal record, source namespace and complete
organization/actor/device/session capture scope. A proposal-only endpoint
is **not** an anchor. A return across apps may be described when those same
source anchors exist, but app continuity is not used to infer membership.

Every intervening observation must stay in the same complete capture scope,
be untainted and be strictly timestamp-ordered. Tied timestamps, foreign
scope, conflicting literal records, missing anchors, or gaps exceeding 16
intervening episodes / 128 observations cause a counted abstention. Adjacent
same-item episodes with no intervening activity are not counted as resumptions.
The full local window is capped at 2,048 episodes and 512 resumption edges;
above these limits the command refuses rather than silently dropping edges.

The output contains the original chronological noncanonical episodes,
`resumption_edges` with only opaque episode/item IDs, typed interruption
classes and counts, `abstention_counts`, admission counts, review truncation
and explicit `capture_completeness_verified: false`. It excludes raw source
references, titles, URLs, work text, provider object IDs, timestamps and
credentials. The original episode partition may contain opaque event IDs;
keep this artifact private. The evidence SHA binds it to the local snapshot.

## Acceptance and limitations

The real-linker regression covers A→B→A, A→unknown→A, A→B→unknown→A,
app changes, tenant/capture barriers, tied timestamps, bounded gap refusal,
private CLI creation, no overwrite and revocation/stale refusal. Required
native Ubuntu/macOS/Windows pytest, browser, process collaboration, scale,
accessibility, starter and packaging/release CI remain release gates.

This is a **read-only interruption graph**, not evidence of improved autonomous
assignment F1, a measured business-case resumption or a verified business
outcome. The preregistered family-holdout assignment F1 ≥ 0.90, contamination
≤ 0.05, interruption rejection and independent real consenting-user pilot
must be evaluated separately. False negatives are expected when records lack
literal source anchors, the capture scope is incomplete or an interruption
exceeds the budget. Cross-employee case joins are not inferred from time or
matching names. Consent, retention, deletion, tenant isolation, REST/MCP,
dashboard, agent approvals and the canonical production linker are unchanged.
