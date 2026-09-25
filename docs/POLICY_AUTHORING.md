# Controlled declared-policy authoring

OpenWorkGraph keeps policy authoring separate from agent execution and from the ordinary REST/MCP read plane.

The declared-policy layer answers **what the organization explicitly says should happen**. Because that source is normative, a model or agent that is merely being observed must not be able to silently rewrite it.

## Security boundary

This version deliberately provides:

- no REST policy mutation endpoint;
- no MCP policy mutation tool;
- no automatic activation of a discovered/source-synchronized policy;
- no noninteractive `--yes`, `--force`, or environment-variable bypass for activation.

Policy changes use a local proposal workflow:

```text
candidate JSON manifest
        ↓
validate + canonicalize
        ↓
immutable proposal material
        ↓
review exact structural diff
        ↓
interactive local apply
        ↓
atomic active-manifest replacement
        ↓
SHA-addressed history + append-only audit
```

This is a control-plane safety mechanism, not an OS sandbox. An agent/process that already has unrestricted control of the same user account, terminal and filesystem may still be able to alter local files outside OpenWorkGraph. Enterprise deployments that need separation from same-user privileged processes should place policy material under a separately administered OS/account boundary.

## Paths

By default:

```text
data/declared_policies.json
data/policy_proposals/
data/policy_history/
```

The active policy path remains configurable with:

```text
WORKFLOW_OBSERVER_POLICY_FILE=/path/to/declared_policies.json
```

Optional proposal/history overrides:

```text
WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR=/path/to/proposals
WORKFLOW_OBSERVER_POLICY_HISTORY_DIR=/path/to/history
```

## Create a proposal

Prepare a complete candidate declared-policy manifest using the existing schema, then run:

```bash
python -m server.policy_admin propose /path/to/candidate.json
```

OpenWorkGraph:

1. parses the candidate as UTF-8 JSON;
2. re-serializes it into deterministic canonical JSON;
3. validates it through the same declared-policy validator used by the runtime;
4. validates the currently active manifest as the proposal base;
5. records the exact base-manifest SHA-256;
6. records the candidate SHA-256;
7. derives a deterministic proposal ID from those two digests;
8. writes local proposal metadata and candidate material with restrictive file permissions where the OS supports them;
9. returns a privacy-minimized structural diff.

Raw `source_ref` values remain in the local candidate/manifest file where required for provenance. They are not copied into proposal metadata or audit records, and local review output uses the existing privacy-minimized declared-policy representation.

## Review

List proposals:

```bash
python -m server.policy_admin list
```

Review one proposal:

```bash
python -m server.policy_admin show proposal-0123456789abcdefabcd
```

The review includes:

- proposal ID;
- base manifest SHA-256;
- candidate manifest SHA-256;
- whether the proposal has become stale;
- added/removed/modified policy identities;
- the validated privacy-minimized candidate policy view.

A proposal becomes **stale** whenever the current active manifest no longer has the exact SHA-256 that was present when the proposal was created. Stale proposals cannot be applied.

## Activate

Activation must run in a real local interactive terminal:

```bash
python -m server.policy_admin apply proposal-0123456789abcdefabcd
```

The CLI displays the current diff and requires typing a confirmation containing both the proposal ID and the candidate digest prefix.

The apply flow then re-reads the proposal and current manifest **after confirmation**. If the manifest changed during review, activation aborts as stale.

There is intentionally no supported noninteractive activation mode.

## Atomicity and rollback material

Before replacing an existing active manifest, OpenWorkGraph archives its exact bytes under:

```text
data/policy_history/manifest-<sha256>.json
```

The candidate manifest is archived there as well. The active manifest is written through a temporary file, flushed, and atomically replaced with `os.replace`.

This means each activated manifest is content-addressed and retained as local rollback material. To restore an older manifest safely, use the historical file as a **new candidate proposal** rather than copying it directly over the active file:

```bash
python -m server.policy_admin propose data/policy_history/manifest-<sha256>.json
python -m server.policy_admin apply <new-proposal-id>
```

That preserves review, staleness checks and the audit trail.

## Audit

Successful activations append one local JSON line to:

```text
data/policy_history/policy_admin_audit.jsonl
```

It records only structural administration metadata such as:

- timestamp;
- proposal ID;
- previous manifest SHA-256;
- new manifest SHA-256;
- confirmation that activation was local/interactive.

It does not record raw policy `source_ref` strings.

## Failure behavior

Activation fails closed if:

- the candidate is malformed or invalid;
- the current active manifest is malformed;
- proposal/candidate material is incomplete;
- the candidate digest does not match proposal metadata;
- the active manifest differs from the proposal base;
- confirmation is incorrect;
- stdin/stdout are not attached to a local interactive terminal.

A failed proposal/apply operation does not modify the active policy manifest.

## Source synchronization

Repository/document source synchronization should feed this same proposal boundary rather than write active policy directly:

```text
source adapter → candidate manifest → proposal → human review → interactive apply
```

That is the intended extension point for a later source-sync layer. Even trusted sync adapters should not bypass proposal review and optimistic-concurrency checks.
