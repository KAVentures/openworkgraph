# Policy source drift and provenance

OpenWorkGraph can distinguish the policy that is currently active from the latest content of explicitly configured policy sources.

This is a **read-only governance view**. It does not create or activate policy, does not fetch remote content, and does not change human or agent observation behavior.

## Why this exists

A policy can remain active even after its authoritative source changes. Without an explicit drift view, a system can continue presenting an old policy as if it were current.

OpenWorkGraph therefore keeps separate answers to two questions:

1. **Semantic freshness** — does the current configured source describe the same declared policy as the active manifest?
2. **Provenance freshness** — can OpenWorkGraph trace the active manifest back to a source-sync receipt, and has that source advanced since then?

These are deliberately not collapsed into one boolean.

## Local command

```bash
python -m server.policy_admin drift-status
```

This command is read-only. It does not run source sync and cannot create proposals.

## Authenticated local API

```text
GET /v1/declared-policies/source-drift
```

The endpoint uses the existing full API read credential. The write-only agent-ingest credential cannot read it.

There is no POST/PUT/PATCH/DELETE drift or policy-mutation route.

## Source statuses

Each configured source is reported as one of:

- `in_sync` — current source and active manifest have no structural policy difference;
- `drifted_proposal_ready` — the current source differs structurally and a matching non-stale proposal already exists;
- `drifted_no_proposal` — the source differs structurally but no matching non-stale proposal exists yet;
- `source_unavailable` — the source could not be read or validated, so freshness is unknown.

`source_unavailable` is **not** interpreted as drift and is **not** interpreted as fresh.

## Semantic comparison

Drift uses the same normalized declared-policy structures and structural proposal diff used by controlled policy authoring.

For example:

```text
Active policy: version 4
Current source: version 5 + one new forbidden-step rule

status: drifted_no_proposal
active_policy_stale_relative_to_source: true
semantic_change_count: 2
```

Formatting differences alone do not make a policy semantically stale.

## Proposal awareness

Drift inspection never creates proposals.

Before source sync:

```text
status: drifted_no_proposal
proposal_available: false
```

After an operator runs:

```bash
python -m server.policy_admin sync-sources
```

the same source can report:

```text
status: drifted_proposal_ready
proposal_available: true
proposal_id: proposal-...
```

Activation still requires the separate interactive workflow:

```bash
python -m server.policy_admin show proposal-...
python -m server.policy_admin apply proposal-...
```

## Provenance receipts

When source sync prepares a proposal, OpenWorkGraph writes a privacy-minimized immutable receipt tying together:

- source ID;
- source content SHA-256;
- candidate-manifest SHA-256;
- proposal ID;
- source-location hash;
- Git commit SHA for Git sources;
- whether the Git worktree differed from committed content.

Drift analysis verifies receipt identity before using a receipt as provenance evidence.

If a receipt is corrupt or tampered with:

- it is not trusted;
- `receipt_integrity_ok` becomes false;
- active-policy provenance may become unknown;
- semantic source-vs-policy comparison can still be reported if the source itself is readable.

This means corrupted provenance cannot silently convert uncertainty into a false claim of freshness.

## Git sources

For `git_file` sources, drift uses the same source rule as synchronization:

```text
HEAD:<relative_path>
```

from the already checked-out local repository.

It does not use an uncommitted worktree edit as authoritative source content. It reports the current commit SHA and whether the corresponding worktree file differs from that committed content.

It never runs `git fetch` or `git pull`.

## Active-policy origin

If the active manifest exactly corresponds to a candidate previously produced by source sync, the receipt allows OpenWorkGraph to report the source provenance that produced it.

For example:

```text
active policy candidate: sha256:...
active source commit: abc123...
current source commit: def456...
source_advanced_since_active_origin: true
```

If the active policy was created manually, predates source receipts, or receipts are invalid, provenance can be unknown even when semantic freshness can still be assessed.

## Privacy

The drift report does not return configured filesystem paths or raw `source_ref` values.

It exposes only privacy-minimized structural data such as:

- source IDs;
- hashes;
- Git commit identities;
- structural policy diffs;
- proposal IDs;
- receipt IDs.

## Non-goals

This layer does not:

- infer policy from observed behavior;
- infer natural-language SOP meaning;
- create proposals during drift reads;
- activate policy;
- block agent execution;
- label a person or agent noncompliant;
- compare observed execution to policy (that is the separate governed-policy comparison layer);
- fetch remote repositories.

## Relationship to the other policy layers

```text
Authoritative local source
        |
        | source sync (proposal generation only)
        v
Reviewed proposal
        |
        | explicit human local activation
        v
Active declared policy
        |
        +---- policy-vs-observed-work comparison
        |
        +---- source-drift comparison ----> current authoritative source
```

The important invariant remains:

**source content, active declared policy, observed workflow behavior, and agent execution are separate evidence classes.**
