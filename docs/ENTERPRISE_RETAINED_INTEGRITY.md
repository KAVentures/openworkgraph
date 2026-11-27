# Enterprise retained evidence integrity and containment

Refs #1572, #326 and #370. Retained connector datasets have an explicit source-event identity and source-case contract. This workbench inspects the actual local relational projection, detects structural inconsistencies and lets an operator contain affected feeds without deleting evidence.

## Review

Open Enterprise connector operations and select **Retained evidence integrity and containment**. Load profiles and select one to five retained feeds. Choose inspection/hold or inspection/release and request the review.

The audit compares metadata counts with actual event and object rows; checks the exact profile source and case namespaces, supplied event-key shape, missing and orphan event keys, duplicate event/object identifiers, exact one-to-one event-to-case links, dangling links and unlinked objects, resource pseudonym shape and unexpected event-level attributes. Case-level annotations are not considered violations. A missing log metadata row is reported separately. Counts describe the affected rows, groups or absolute population differences for each check; do not sum overlapping checks as unique damaged events.

Each selected dataset is bounded to one million matching rows per audited table. An over-limit result is explicitly inconclusive and cannot authorize containment or release through this workbench. The row bound is not a guarantee of query runtime or disk work. An empty profile has no retained dataset to audit.

The audit reads the local workspace only, exposes counts and local identifiers rather than raw event payloads, and does not request provider credentials, connect to source systems or authenticate provider history. A structurally consistent result is not cryptographic source authenticity, historical completeness, or proof that event payloads are accurate. Equal-count content modifications may evade these structural checks.

## Containment and release

Only a reviewed set of profiles with structural violations can receive holds. The preview binds the exact finding populations, profile state and revision to a SHA-256 plan. Changing state or structural findings invalidates the plan. Explicit human approvals are required. The apply operation recomputes findings under the workspace lock and atomically changes all selected profiles or none of them.

A hold disables ingestion, advances the profile revision, records a local hold marker and invalidates any admitted local run intent. Existing checkpoints and retained data are preserved. Ordinary enablement, local recurring dispatch and new snapshot preparation are fenced by the hold. A prior remote operation may still be in flight, and existing publication artifacts may still be usable; separately coordinate remote workers and withdraw publications through their own supported workflows. This is not a retroactive revocation or remote cancellation claim.

After a separately reviewed repair, select the release action. Every selected held profile must now pass the structural review, be disabled and have no unreconciled run. The same stale-review and atomic apply protections apply. Release removes the hold and advances the revision but leaves the feed disabled. Explicit enablement and new scheduling/Gateway authorization remain separate. The workbench does not implement data repair, automatic deletion or provider tombstone ingestion. Existing dataset deletion removes the associated hold and leaves the profile in its existing dataset-deleted state.

## Local API

- GET /v1/enterprise-connectors/integrity lists local profile revisions and hold markers.
- POST /v1/enterprise-connectors/integrity/preview?action=hold or release accepts targets, each with profile_id and revision.
- POST /v1/enterprise-connectors/integrity/apply?action=hold or release additionally accepts plan_sha256 and the explicit confirm_exact_findings, confirm_local_containment and confirm_remote_coordination booleans.

All endpoints require the existing human dashboard session. Preview may initialize normal local workspace tables but never alters source payloads or profile configuration.

## Verification

The regression suite exercises actual workspace ingestion with synthetic provider transport, structural damage scenarios, no-provider review, exact plan fencing, active-run invalidation, atomic multi-feed rollback, clean release, deletion and bounded review. It is included in the focused enterprise integration workflow. CI and real deployment acceptance remain separate gates.
