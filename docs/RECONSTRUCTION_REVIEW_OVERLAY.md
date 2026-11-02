# Local human-reviewed reconstruction overlay (Worker C, #326 A / #370 R1)

This is an **operator-facing functional review workflow**, not an automatic
semantic case linker or an authorization to execute anything. The existing
read-only workflow-evidence API already reports ambiguous singleton-to-item
candidates. This CLI turns *only those actually visible direct or two-hop weak candidate paths* into
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
unassigned event IDs, and typed weak candidate item IDs. One-hop entries carry
`evidence_hops: 1` and their weak relation types. Two-hop entries carry
`evidence_hops: 2`, the exact three opaque witness event IDs, the two weak
relation types, and a distinct-intermediary count. The intermediary stays
unassigned unless separately and explicitly reviewed. It contains no raw
work text, URL, hostname, clipboard contents or resource reference. It does
not display hidden, unanchored or non-singleton target components. At most **24 total** direct-plus-two-hop eligible events and four candidate
items per event are displayed; direct and two-hop totals and truncation are
explicit. Direct candidates are shown first; overflow is never silently
represented as zero. An incomplete two-hop scan refuses packet preparation;
select a smaller authorized window. If more than 50 linked items exist, select a smaller
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
For a two-hop confirmation, a simultaneous human rejection of the
intermediary-to-same-item candidate, or confirmation of the intermediary for a
different item, is contradictory and the entire apply is refused regardless
of decision order. Reviewing the intermediary for the same item is an
independent human confirmation, not an automatic transitive assignment.
Do not treat evidence support counts as probabilities. Reviewer confirmation
is a **new human annotation**, not source-verified business identity.

    python -m scripts.reconstruction_review apply \
      --events /private/observations.json \
      --packet /private/review-packet.json \
      --decisions /private/decisions.json \
      --output /private/reviewed-overlay.json

The output keeps automatic memberships and human-confirmed event IDs
**separately**, including distinct one-hop and two-hop confirmation counts and
per-item reviewed event IDs. A two-hop confirmation attaches only the reviewed
loose observation, **never** its intermediary; neither weak edge becomes a
canonical link. The overlay and score identify human-reviewed gains, not
improvements to autonomous reconstruction. They explicitly state that canonical
membership is unchanged, human decisions are not source evidence, and business
actions are unauthorized.
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
  decision inputs are separately size-bounded. Scoring is limited to 500 admitted
  observations and 12 independently labeled workflows per window to bound the
  shared scorer's pairwise-order and assignment work. Malformed, nonfinite or
  unsupported JSON is refused. Outputs are newly created private files.
- Review eligibility comes from the current production linker and its existing
  visible, source-anchored direct weak candidate queue **and** its independently
  witnessed two-hop fragment queue, never from a caller's asserted target or
  time proximity. Two-hop fragments remain review-only and are promoted into
  the **noncanonical human overlay only by explicit operator confirmation**;
  intermediate observations are not transitively assigned. Incomplete fragment
  scans, forged paths, hidden targets and cross-scope weak edges are refused.
- REST, MCP, dashboard, export, capture, Discovery Mode, retention and tenant
  isolation are unchanged. The CLI must be invoked only over evidence the
  operator is already authorized to access. Revocation requires re-export and
  fresh review; old packets cannot be applied to changed evidence.
- Native pytest on Ubuntu/macOS/Windows, browser, collaboration, scale,
  accessibility, starter, installer and release CI must pass at the exact PR
  head before merging. Synthetic tests alone do not establish real-user
  validity.


## Compare independent local sessions (score-batch)

The new score-batch command runs the **real production linker and review
scorer for every selected session**, then reports equal-weight *session macro*
automatic-versus-human-assisted precision, recall, F1, contamination and
interruption rejection. It is useful for finding whether a change helps across
multiple independently annotated local sessions rather than one convenient
example. It does **not** change the automatic linker or make a model accurate.

First, independently annotate each consented session and generate its packet
and decisions as above. Freeze the truth labels before looking at predictions;
avoid reusing the same observation snapshot twice. In a **private local file**,
create a manifest of 2–24 sessions:

    {
      "format": "openworkgraph.reconstruction-review-manifest.v1",
      "sessions": [
        {
          "events": "/private/session-a/events.json",
          "packet": "/private/session-a/packet.json",
          "decisions": "/private/session-a/decisions.json",
          "truth": "/private/session-a/truth.json"
        },
        {
          "events": "/private/session-b/events.json",
          "packet": "/private/session-b/packet.json",
          "decisions": "/private/session-b/decisions.json",
          "truth": "/private/session-b/truth.json"
        }
      ]
    }

    python -m scripts.reconstruction_review score-batch \
      --manifest /private/review-manifest.json \
      --output /private/reconstruction-audit.json

All source snapshots, review packets, decisions and complete truth partitions
are **revalidated and rescored**. If any session is stale, revoked, forged,
incomplete, oversized or duplicated, the entire batch refuses without a partial
output. A batch is limited to 24 sessions, each with at most 500 admitted
observations and 12 annotated workflows for the bounded shared scorer. All
inputs stay local; the output is private, create-only and contains only
anonymous session indices, counts, numeric metrics and flags. No paths,
source/event IDs, digests, work text, hostnames or labels are exported.

The report contains session_macro_automatic_baseline,
session_macro_human_assisted_overlay, their signed differences and
minimum_automatic_interruption_rejection. Every session receives equal
weight; this is **not** event-weighted, and independent *scenario families*
are not automatically identified. local_numeric_thresholds checks whether
the *observed local automatic* macro F1 reaches 0.90 and contamination is at
most 0.05, **not** whether the preregistered family holdout passes.
preregistered_family_holdout_verified and
real_consenting_user_pilot_verified always remain false. Human-assisted
gains must never be attributed to automatic linking. The existing frozen
family-holdout evaluator in evals/reconstruction and independently
annotated consenting-user pilot remain mandatory, including interruption
regression and rare interleaved/missing-observation cases.


## Independent two-reviewer consensus (adjudicate)

For higher-stakes ambiguous case linking, two operators can each inspect the
**same authorized original evidence** independently and create separate
decision documents in the ordinary `DECISION_FORMAT` described above. OWG
cannot verify that the files were authored by different people: there are no
reviewer signatures, identities, credentials or independent-review claims.

    python -m scripts.reconstruction_review adjudicate \
      --events /private/observations.json \
      --packet /private/review-packet.json \
      --reviewer-a /private/reviewer-a.json \
      --reviewer-b /private/reviewer-b.json \
      --output /private/adjudication.json

The command replays the production linker and validates **each review** against
the exact current source snapshot, including two-hop/intermediary
contradictions. Only decisions with identical `event_id`, `item_id` and
`action` in both reviews become consensus decisions. Conflicting candidate
items, confirm-versus-reject, and one-sided decisions are explicitly listed as
unresolved and remain **unassigned**. Events neither reviewer considered
remain unreviewed. An agreed rejection is not a new case assignment.

The private, create-only artifact includes both original opaque reviewer
decision documents, their consensus, unresolved opaque event IDs and counts.
It never includes raw work text, source URLs, labels or credentials. Its
`reviewer_independence_verified` field is always false. Do not upload it to
GitHub or a model. To resolve a disagreement, the operators must inspect the
underlying evidence again and produce fresh reviews; no majority guess or
automatic tie-breaker is provided.

Use the resulting adjudication artifact **directly** as `--decisions` with
the existing `apply` or `score` commands, or in a `score-batch` session
manifest. Every use rederives both original reviewer decisions and the
consensus from current source evidence, refusing altered adjudication counts,
forged consensus, invalid reviewer inputs, stale packets or revoked evidence.
The overlay reports `review_decision_provenance: two_reviewer_consensus`
and the number of unresolved reviewer disagreements. Single-review files
continue to work with `review_decision_provenance: single_reviewer`.
Batch reports separate the number of consensus-reviewed sessions and
unresolved disagreements, while **automatic baseline metrics remain separate**
from human-assisted metrics.

This is a review quality and adjudication workflow, **not** an autonomous
case-linking improvement, an independent reviewer authentication system, or
source-verified business truth. It does not change the production linker,
canonical membership, tenant/consent boundaries, REST/MCP/dashboard, or
authorize any agent or business action. Exact-head native CI, frozen family
holdout and real consenting-user acceptance remain outstanding.

## Resolve a contested assignment with an explicit third review (resolve)

Two-reviewer consensus intentionally leaves competing or one-sided decisions
unassigned. For an authorized human arbiter to resolve **only those disputed
observations**, retain the exact original adjudication artifact and create a
third, independently authored local decision document in the same
`openworkgraph.reconstruction-review-decisions.v1` format. Its `decisions`
array must be nonempty and may contain **only event IDs from
`unresolved_event_ids`** in the original adjudication. A third reviewer may
confirm or reject an existing, actually eligible item; they cannot revise
agreed decisions, add candidates or silently assign an intermediary.

    python -m scripts.reconstruction_review resolve \
      --events /private/observations.json \
      --packet /private/review-packet.json \
      --adjudication /private/adjudication.json \
      --third-reviewer /private/third-review.json \
      --output /private/resolution.json

The command reruns the current production linker, validates both original
reviews, their exact consensus and the third review, then validates the
**combined** decisions. If a third reviewer rejects an intermediary that an
already agreed two-hop confirmation depends upon, the entire resolution fails.
Partially resolved disagreements remain explicitly unresolved; there is no
automatic majority vote. Original reviews and disagreements are preserved in
the new private, create-only resolution artifact. Its format is
`openworkgraph.reconstruction-review-resolution.v1`, and it includes
`third_reviewer_resolved_count`, `remaining_unresolved_event_ids` and
`reviewer_independence_verified: false`.

Use `--decisions /private/resolution.json` with `apply` or `score`, or
in a `score-batch` manifest. Every consumer replays and checks all three
human inputs and the combined result against the exact current evidence.
The overlay marks `review_decision_provenance: third_reviewer_resolution`,
and batch reports `third_reviewer_resolution_session_count`. Scoring still
reports the unchanged automatic baseline separately from human-assisted
assignments. No authenticated third-reviewer identity or independent review
is established by file inputs, and neither resolution nor review permits a
provider write, a canonical event change or an agent action.

Acceptance is a real local operator journey: produce contested A/B case
assignments, explicitly adjudicate, resolve a disputed event, apply and score
against independently annotated truth, then revoke or alter source evidence
and verify the exact prior artifact is refused. Native exact-head cross-platform
CI and real consenting-user acceptance remain mandatory before release.
