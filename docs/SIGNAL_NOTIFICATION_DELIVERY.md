# R10 — Optional counts-only external signal notifications

OWG supports opt-in schedules, version-pinned saved signal checks, quiet hours, acknowledgement/snooze and a local review inbox. This feature adds an **independently consented** HTTPS webhook for newly **unreviewed** signal states. It is disabled by default and never executes a business action.

## Setup

1. Import/retain a local event log and save at least one signal rule. In OWG's local **Schedules** UI, explicitly create a recurring `event_log_signals` schedule for that **exact log** (minimum 15-minute interval). Creating a webhook does **not** create or enable a schedule, collect work evidence, authorize a source connector or bypass its existing schedule fingerprint.
2. In **Large event logs → Signals → Optional external signal alerts**, enter a webhook HTTPS URL (or a loopback HTTP `127.0.0.1:8787` URL for a locally controlled receiver), and check **both** the external-delivery and counts-only disclosure confirmations. Click **Enable counts-only webhook**.
3. The page displays a unique 64-character HMAC signing secret **once**. Save it securely at the receiving system if you want to verify `X-OWG-Notification-Signature`. OWG stores the secret and webhook destination in the local credential directory; it omits both from inventory and unencrypted backups. An encrypted backup can carry credentials, subject to its separate protection.
4. On each successful **pinned scheduled signal evaluation**, OWG stages new unreviewed signal states into a private bounded outbox. The background sender rechecks the states and the schedule before attempting delivery. No current log or rule means no notification.
5. Review, acknowledge or snooze evidence in the **local inbox**. Use **Remove webhook and erase queued alerts** to revoke the sender secret and discard the pending outbox. This cannot recall messages already accepted by the receiver.

## What leaves the computer

Only a compact signed JSON message:

```json
{
  "format": "openworkgraph.signal-attention.v1",
  "notification_id": "ntf_opaque_random_identifier",
  "created_at": "2026-11-12T09:00:00+00:00",
  "unreviewed_signal_states": 2,
  "message": "Open your local OWG dashboard to review saved signal alerts.",
  "text": "OWG: 2 saved signal state(s) require review on your local device.",
  "case_data_included": false,
  "source_identifiers_included": false,
  "execution_authorized": false
}
```

The opaque notification ID is not an event-log, workflow, case, record, business, person or rule identifier. **No raw log name, rule label/definition, case ID, event data, source account, metric value, captured work text, credentials or evidence is transmitted.** The count and the fact OWG has an alert can still be sensitive; only enable recipients you trust. Receiving services retain what they receive under their own policies.

Headers include `Content-Type: application/json`, `X-OWG-Notification-ID` and `X-OWG-Notification-Signature: v1=<HMAC-SHA256 hex>`, with HMAC calculated over the exact raw request body using the one-time channel secret. The receiver can deduplicate on notification ID. A 2xx HTTP status is treated as **a transport acknowledgement**, not independent verification of human review, business outcome, or third-party retention.

For generic webhook receivers and text-based incoming webhooks such as Slack, the `text` field is provided. A receiver may require its own message-format integration; acceptance is checked through its real HTTP response rather than assumed.

## Staleness, retry and limits

- Only current triggered, unsnoozed, unacknowledged states are queued. After a rule changes or a dataset append makes an evaluation stale, a fresh evaluation is required. An already queued notification is suppressed if its selected states are no longer unreviewed at the delivery preflight.
- Identical states are deduplicated by local hashes derived from the receiver channel ID, rule ID and exact review-state fingerprint. Up to 2,000 acknowledged state keys, 50 pending notification groups and 50 states per group are kept. No automatic rewind after this bounded history.
- A failed, non-2xx or uncertain HTTP response retains the same notification ID and message for an explicit retry by the local scheduler, with exponential 60-second to 1-hour backoff. Five failures pause the delivery channel until a separately confirmed **Resume**. The scheduler never repeats a business operation.
- Quiet hours on the signal evaluation schedule delay **both** new checks and outbound notification attempts. Queued messages remain pending until the existing named timezone's next permitted window; delivery uses the schedule's DST-aware quiet-hour rules. Remove the webhook to revoke queued delivery; pausing the evaluation schedule also suppresses sending.
- Only HTTPS on port 443 (or HTTP on loopback port 8787) is accepted. URLs with credentials, queries, fragments, restricted IP addresses or redirects are refused. The HTTP client disables proxies, validates all resolved addresses, pins the chosen public/loopback IP for the socket connection, and verifies HTTPS certificates against the intended hostname. This limits SSRF and DNS-rebinding attacks; it does not assert the recipient is trustworthy.
- Removing a source log also removes the channel, signing secret, pending alerts and delivered-state hashes. No third-party deletion can be performed by OWG after delivery.
- Actual state can change between the last pre-send recheck and a network write; an in-flight request cannot be recalled. External delivery is not a substitute for checking fresh source facts or review status when making decisions.

## Acceptance gates

Synthetic regression covers independent consents, no default send, strict auth, single-recipient lifecycle, HMAC integrity, no identifiers in the payload, stale/acknowledged/snoozed suppression, repeat dedupe, exponential backoff, disabled schedules, credentials in the auth folder, deleting source logs, URL/DNS safety and actual Chromium UI state/WCAG. Production testing still requires an owner-controlled webhook and representative scheduled logs; do not infer that a specific third-party service accepts every message until its own receipt is observed.
