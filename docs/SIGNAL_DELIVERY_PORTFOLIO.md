# R10: local signal delivery operations portfolio

Open **Privacy → Scheduled jobs → External signal delivery operations**. The
existing per-log delivery settings remain the only place to configure a receiver
or explicitly resume it. This new operator workbench requires a local dashboard
session and reads the canonical notification ledger, not a second store.

## Review and triage

The portfolio shows all configured channels (up to 20), their exact local log
identifiers, enabled state, retained queue groups, retry timestamps, attempted
groups, recorded receiver acknowledgements and bounded audit outcomes. It
also reads existing signal schedule metadata, including quiet hours, missing
or paused schedules and missing approval pins. Unknown/corrupt schedule
metadata is distinct from an absent job. The schedule and delivery snapshots
are separate, not a transactional observation across both stores. A due
queue head still needs the sender's current rule/state/quiet-hour preflight.
Counts describe queued state occurrences, not distinct current alerts or
verified business incidents. No case IDs, rule IDs, raw state fingerprints,
receiver hosts/URLs, secrets, source text or metric values are returned.

## Pause all outbound channels

Review the complete enabled-channel population and choose **Pause all enabled
channels**. The enabled population digest binds exact log/channel identity.
Any newly enabled, removed or replaced channel invalidates that selection.
The operation modifies all reviewed enabled channels under the existing
delivery lock in a single ledger write. It preserves keys, pending alerts,
retry metadata and prior deduplication history. Paused channels remain
unchanged. It does not pause local schedules, reevaluate rules, acknowledge
signals or change source permissions. An in-flight attempt can finish before
the pause acquires the delivery lock; already received notices cannot be
recalled. The digest is a consistency pin, not authentication.

## Dismiss selected paused notification groups

Expand any paused channel, select up to 20 groups across channels and choose
**Dismiss selected paused groups**. Each selection includes the exact channel
identity, notification ID and queue pin including attempt state. The whole
batch is refused if any channel is no longer paused, any group changed, any
selection is duplicated or any channel would exceed its existing suppression
capacity. The exact state keys are added to that channel's existing dismissal
ledger, not its successful-delivery history. The queue and minimized audit
records are updated in one atomic file replacement. A prior unacknowledged
attempt may already have reached the receiver; no recall is attempted.
No underlying review is acknowledged, snoozed, cleared or edited.
There is no bulk resume or automatic network operation.

## API

All endpoints require the existing authenticated local dashboard session;
API bearer/MCP clients cannot use them. Responses are no-store.

- GET /v1/schedules/signal-delivery-portfolio
- POST /v1/schedules/signal-delivery-portfolio/pause-all with
  expected_enabled_channels_sha256 and confirm_pause: true.
- POST /v1/schedules/signal-delivery-portfolio/dismiss-batch with
  selections (each containing log_id, channel_id, notification_id,
  queue_sha256) and confirm_dismiss: true.

The overview is read-only. Explicit pause and dismissal are local administrative
controls, not permission to deliver or execute business actions. These
operations reuse the existing in-process lock and atomic ledger replacement;
they do not introduce a distributed transaction or guarantee independent
multi-process writers. Queue or schedule health is not proof of live source
availability, provider acceptance, delivery to a person, or completed work.
