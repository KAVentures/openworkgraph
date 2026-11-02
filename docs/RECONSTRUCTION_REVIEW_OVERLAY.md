# Local human-reviewed reconstruction overlay (Worker C, #326 A / #370 R1)

This is an **operator-facing functional review workflow**, not an automatic
semantic case linker or an authorization to execute anything. The existing
read-only workflow-evidence API already reports ambiguous singleton-to-item
candidates. This CLI turns *only those actually visible candidate edges* into
explicit, locally reviewed membership overlays and allows independently
annotated sessions to be scored. It never changes the canonical OWG event
store, source records, default API grouping, permissions or consent.

## Workflow

Use an explicitly consented **local JSON array of event observations** that
the operator is already authorized to read. Do not upload raw events, review
packets, reviewer decisions or ground-truth labels to GitHub, chat or a
third-party model. Keep them in a private local directory.

From the repository root:

    python -m scripts.reconstruction_review prepare \
      --events /private/observations.json --output /private/review-packet.json

The packet contains a SHA-256 digest of the *exact JSON evidence snapshot*,
admitted observation/item counts, a bounded list of existing eligible
unassigned event IDs, and typed weak candidate item IDs. It contains no raw
work text, URL, hostname, clipboard contents or resource reference. It does
not display hidden, unanchored or non-singleton target components. At most 24
eligible events and four candidate items per event are displayed; totals and
truncation are explicit. If more than 50 linked items exist, select a smaller
authorized window instead of silently reviewing a partial item population.

After **independent human review of the original authorized evidence**, create
a local decisions file with this structure (copy the actual SHA, event ID and
candidate item ID from the generated packet):

    {
      "format": "openworkgraph.reconstruction-review-decisions.v1",
      "evidence_sha256": "<exact packet evidence_sha256>",
      "decisions": [
        {"event_id": "e03", "item_id": "linked_item_02", "action": "confirm"}
      ]
    }

Use "reject" to reject that suggested item without assigning the event.
An empty decisions array is allowed. One decision per event, maximum 24.
A review of one candidate is not a claim that other candidates are false.
Do not treat evidence support counts as probabilities. Reviewer confirmation
is a **new human annotation**, not source-verified business identity.

    python -m scripts.reconstruction_review apply \
      --events /private/observations.json \
      --packet /private/review-packet.json \
      --decisions /private/decisions.json \
      --output /private/reviewed-overlay.json

The output keeps automatic memberships and human-confirmed event IDs
**separately**, and explicitly says that canonical membership is unchanged,
human decisions are not source evidence, and business actions are unauthorized.
Unconfirmed events remain unassigned. Changing an event, its private metadata,
source scope, retention or deletion state invalidates the snapshot digest;
altered packets, unsupported targets, duplicate decisions and forged
approvals are refused. The output is a new private file (0600 on POSIX);
existing files are never overwritten. No network call is made.

## Measure accuracy on independently annotated sessions

For a *fully independently annotated* local session, supply a truth file that
partitions every admitted event exactly once into workflow event IDs or
noise/interruption event IDs:

    {
      "workflows": [
        {"event_ids": ["e01", "e04"]},
        {"event_ids": ["e02", "e03", "e05"]}
      ],
      "noise_event_ids": []
    }

    python -m scripts.reconstruction_review score \
      --events /private/observations.json \
      --packet /private/review-packet.json \
      --decisions /private/decisions.json \
      --truth /private/independent-truth.json \
      --output /private/review-score.json

This reuses the repository's existing event-level reconstruction scoring:
assignment precision/recall/F1, cross-workflow contamination and interruption
rejection. It reports **automatic baseline** separately from **human-assisted
overlay**. A human-assisted F1 gain is not an improvement in autonomous
reconstruction. The local score is not the preregistered family holdout,
not a statistically powered real pilot, and not a verified business outcome.
For acceptance, independently annotate multiple consenting real sessions,
freeze truth before inspecting predictions, report failure cases and coverage,
and run the existing evals/reconstruction family holdout separately. The
target remains autonomous assignment F1 >= 0.90 and contamination <= 0.05
without interruption-rejection regression; this feature does not claim it.

## Safety and compatibility

- No provider writes, credentials, model calls, new network sync, cloud upload,
  canonical edits, permissions, business approvals or agent actions.
- The source is bounded to 10,000 JSON observations / 8 MiB. Packet and
  decision inputs are separately size-bounded. Malformed, nonfinite or
  unsupported JSON is refused. Outputs are newly created private files.
- Review eligibility comes from the current production linker and its existing
  visible, source-anchored direct weak candidate queue, never from a caller's
  asserted target or time proximity. Two-hop fragments remain review-only and
  are not silently promoted.
- REST, MCP, dashboard, export, capture, Discovery Mode, retention and tenant
  isolation are unchanged. The CLI must be invoked only over evidence the
  operator is already authorized to access. Revocation requires re-export and
  fresh review; old packets cannot be applied to changed evidence.
- Native pytest on Ubuntu/macOS/Windows, browser, collaboration, scale,
  accessibility, starter, installer and release CI must pass at the exact PR
  head before merging. Synthetic tests alone do not establish real-user
  validity.
