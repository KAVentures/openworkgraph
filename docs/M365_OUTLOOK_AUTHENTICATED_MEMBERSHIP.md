# Authenticated Microsoft 365 uncertain-append membership review

This is a **read-only, operator-initiated** local OWG inspection of source-event identities after an uncertain Microsoft Graph append. Unlike an operator-supplied CSV comparison, it fetches the current `sourcecsv` export through OWG's existing session-authenticated API. The provider is never contacted. No event is appended, no checkpoint is advanced, and **no retry is authorized**.

## Preconditions

- The Microsoft Graph tenant/mailbox/folder grant is current, with the same exact scope as the private uncertain-append event review manifest.
- OWG's local dashboard is running, with `OWG_DASHBOARD_SESSION` available in the process environment.
- The confirmed target log has exactly one matching `m365_<scope-digest>` namespace and supports the existing `source_case` source-identity export.
- The manifest and destination are in the same owner-private directory. The output is a new `authenticated-*.json` file.

Run from the repository root:

```sh
python -m server.m365_outlook_authenticated_membership \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER \
  --grant-file /private/grant.json \
  --review-file /private/review.json \
  --output /private/authenticated-review.json \
  --log-id EXACT_LOG_ID --confirm-log-id EXACT_LOG_ID \
  --port 8765 --consent-local-read --consent-private-export
```

The CLI validates the review manifest, grant and exact log ID; reads authenticated inventory, exports a bounded source-key CSV (at most 16 MiB/100,000 rows), verifies exact namespace and no duplicate keys, and re-reads inventory. It writes a new private report with matching/missing opaque event IDs and export/review digests. No source-event text or credentials are written. Existing destinations refuse overwrite.

## Interpretation and limitations

An authenticated, consistent OWG snapshot establishes **only what that local OWG log exposed at inspection time**. Two inventory reads are not a transactional snapshot: concurrent replacements that preserve counts may escape detection. Even a complete match does not prove which append attempt wrote the events, nor that the uncertain POST did not partially commit elsewhere. A missing key does not establish that retrying is safe. `event_membership_verified` and `safe_to_retry` intentionally remain false. The attempt latch must remain in place; recovery requires separate independently justified authorization.

The CLI fails closed on missing consent, stale/revoked grant, altered manifest, inconsistent inventory, wrong namespace, duplicate keys, oversized/malformed export, unexpected HTTP failure or an existing report. It does not implement production credential rotation or real Microsoft Graph sandbox acceptance.
