# Issue 226: explicit optional pre-enrollment history sharing

**Default OFF and not rolled out.** This is a gated implementation, NOT
automatic backfill on enrollment. Shipping it enabled requires separate
privacy review, a real consent test and a migration/rollback exercise.

Environment rollout gate: OWG_ENABLE_HISTORY_BACKFILL=1. Without the gate,
neither the dashboard button nor its upload endpoint can share older data.

When the user connects a NEW organization device, the app remembers an
immutable local event-ID boundary. Default Gateway sync still starts after
that boundary, regardless of the optional historical feature.

The dashboard can reveal Review older history ONLY if the rollout gate is
explicitly on, the device is enrolled and not paused/revoked, and a valid
historical enrollment boundary exists. Old device enrollments without a
trusted boundary remain ineligible until re-enrolled.

The user must:
1. Pick a timezone-aware past start and end (maximum 30 days).
2. Preview the number of privacy-eligible events and exclusions.
3. Read an explicit confirmation prompt and approve a single bounded
   upload using a short-lived, single-use preview token.

Preview and confirm BOTH retrieve current organization policy and apply
existing prepare_event_for_gateway plus on-device
project_event_for_upload redaction, with the same local HMAC key and owner
exclusions as forward sync. Preview stores only digest and expiry in the
local state, not any work content. Changes in the eligible projected
evidence, org policy, device credential or date window invalidate consent.
Historical agent transcripts/text, screenshots, clipboard values and
other disallowed raw content are not included.

A confirmation token is consumed using one SQLite transaction, including its
associated preview digest/expiry/date-range state, **before** network upload.
Concurrent confirmations with the same token cannot each upload the same
historical data. Expiry is rechecked after the policy/evidence revalidation;
partially failed uploads still require a fresh explicit preview and consent.

A pause/revocation blocks the operation; previously defined sync skip
ranges remain excluded. Discovery-held events remain local. Upload retries
require a new preview and explicit confirmation. Gateway event-ID
idempotency prevents double attribution. Crucially, this operation
**never changes the normal forward-only sync cursor**.

## Before enabling for users

- Review privacy policy, data-processing agreements, source residency,
  organization administrator disclosure and granular consent language.
- Exercise a seeded company Gateway, including signed-in employee,
  token expiry/revocation, policy changes and interruption halfway
  through upload. Verify every uploaded row is device-redacted.
- Review link indexes and retention/deletion enforcement, including
  rollback from this feature gate.
- The old dashboard text "earlier history stays local" remains true
  unless a person explicitly chooses the preview/confirmation flow.
- Do not turn on the gate in production solely because unit tests passed.

The feature is local-only and not part of the hosted ChatGPT plugin or
a global company retention policy. No source credentials are stored.
